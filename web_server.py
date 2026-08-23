"""MAS Web UI server.

Uses only the Python standard library. The browser talks to the server through
JSON endpoints and a Server-Sent Events stream for live group messages.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from application.group_service import GroupAppService
from application.options import GroupServiceOptions
from core.member_config import (
    default_members_file,
)
from core.usage import UsageMonitor
from observability.views import usage_payload_view


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "web.log"


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(message)s"
    )
    logger = logging.getLogger("mas.web")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    has_web_log = any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == str(LOG_PATH)
        for handler in root.handlers
    )
    if not has_web_log:
        root_handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        root_handler.setFormatter(formatter)
        root.addHandler(root_handler)
    return logger


class WebState:
    """Shared server state."""

    def __init__(
        self,
        *,
        group_name: str,
        member_config_path: Path,
        extra_member_names: list[str] | tuple[str, ...],
        max_turns: int,
        enable_tools: bool,
        group_max_rounds: int,
    ) -> None:
        self.logger = setup_logging()
        self.service = GroupAppService.from_options(
            GroupServiceOptions(
                group_name=group_name,
                member_config_path=member_config_path,
                extra_member_names=tuple(extra_member_names),
                max_turns=max_turns,
                enable_tools=enable_tools,
                group_max_rounds=group_max_rounds,
                persist_dynamic_members=True,
            ),
            usage_monitor=UsageMonitor(on_record=self._log_usage),
        )
        self.lock = threading.Lock()
        self.busy = False
        self.clients: list["queue.Queue[dict[str, Any]]"] = []
        self._log_built_members()
        self.logger.info(
            "web_state_init group=%s members=%s member_config=%s max_turns=%s tools=%s group_max_rounds=%s",
            self.service.group_name,
            [member.name for member in self.service.member_configs],
            self.service.member_config_path,
            self.service.max_turns,
            self.service.enable_tools,
            self.service.group_max_rounds,
        )

    @property
    def group(self) -> Any:
        return self.service.group

    @property
    def usage_monitor(self) -> UsageMonitor:
        return self.service.usage_monitor

    def _log_built_members(self) -> None:
        for name, member in self.group.members.items():
            self.logger.info(
                "member_built name=%s model=%s url=%s",
                name,
                member.agent.llm.model,
                member.agent.llm.base_url,
            )

    def reset_group(self) -> None:
        with self.lock:
            if self.busy:
                raise RuntimeError("group is running; cannot rebuild")
            self.service.reset_group()
            self._log_built_members()
        self.logger.info(
            "group_reset members=%s",
            [m.name for m in self.service.member_configs],
        )
        self.broadcast({"type": "reset"})

    def clear_group(self) -> None:
        with self.lock:
            if self.busy:
                raise RuntimeError("group is running; cannot clear")
            self.service.clear_group()
        self.logger.info("group_clear")
        self.broadcast({"type": "clear"})

    def add_member(
        self,
        *,
        name: str,
        description: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
    ) -> Any:
        with self.lock:
            if self.busy:
                raise RuntimeError("group is running; cannot add member")
            member = self.service.add_member(
                name=name,
                description=description.strip(),
                base_url=base_url.strip(),
                api_key=api_key.strip(),
                model=model.strip(),
            )
        self.logger.info(
            "member_added name=%s model=%s url=%s persisted=%s",
            member.name,
            member.agent.llm.model,
            member.agent.llm.base_url,
            self.service.member_config_path,
        )
        self.broadcast({"type": "members_changed"})
        return member

    def subscribe(self) -> "queue.Queue[dict[str, Any]]":
        q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=200)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: "queue.Queue[dict[str, Any]]") -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, event: dict[str, Any]) -> None:
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                client.put_nowait(event)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    client.get_nowait()
                with contextlib.suppress(queue.Full):
                    client.put_nowait(event)

    def run_message(self, message: str) -> bool:
        with self.lock:
            if self.busy:
                return False
            if not self.service.enabled_members():
                raise RuntimeError("add at least one member first")
            self.busy = True
        self.logger.info("chat_accepted message=%r", _short(message, 500))
        thread = threading.Thread(
            target=self._run_message_thread,
            args=(message,),
            daemon=True,
        )
        thread.start()
        return True

    def _run_message_thread(self, message: str) -> None:
        try:
            asyncio.run(self._arun_message(message))
        except Exception as e:
            self.logger.exception("chat_failed message=%r", _short(message, 500))
            self.broadcast({
                "type": "error",
                "error": f"{type(e).__name__}: {e}",
            })
        finally:
            with self.lock:
                self.busy = False
            self.logger.info("chat_done message=%r", _short(message, 500))
            self.broadcast({"type": "done"})

    async def _arun_message(self, message: str) -> None:
        def on_member_start(member: Any) -> None:
            self.logger.info("member_start name=%s", member.name)
            self.broadcast({
                "type": "member_start",
                "member": member.name,
            })

        def on_message(msg: Any) -> None:
            self.logger.info(
                "group_message id=%s sender=%s kind=%s propagate=%s dispatch=%s mentions=%s content=%r",
                msg.id,
                msg.sender,
                msg.kind,
                msg.propagate,
                msg.dispatch_mode,
                msg.mentions or [],
                _short(msg.content, 1000),
            )
            self.broadcast({
                "type": "message",
                "message": self.service.message_payload(msg),
            })

        await self.service.arun_message(
            message,
            on_member_start=on_member_start,
            on_message=on_message,
            close_after=True,
        )

    def _log_usage(self, record: Any) -> None:
        self.logger.info(
            "usage label=%s model=%s prompt=%s cached=%s hit=%s miss=%s completion=%s reasoning=%s total=%s",
            record.label,
            record.model,
            record.prompt_tokens,
            record.cached_tokens,
            record.cache_hit_tokens,
            record.cache_miss_tokens,
            record.completion_tokens,
            record.reasoning_tokens,
            record.total_tokens,
        )


class Handler(BaseHTTPRequestHandler):
    state: WebState

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/events":
            self._handle_events()
            return
        if parsed.path == "/api/state":
            self._send_json(self._state_payload())
            return
        if parsed.path == "/api/history":
            self._send_json(self.state.service.history_payload())
            return
        if parsed.path == "/api/usage":
            self._send_json(self.state.service.usage_payload())
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/chat":
                payload = self._read_json()
                message = str(payload.get("message", "")).strip()
                if not message:
                    self.state.logger.warning("api_chat_empty")
                    self._send_json({"error": "message is required"}, status=400)
                    return
                accepted = self.state.run_message(message)
                if not accepted:
                    self.state.logger.warning("api_chat_busy")
                    self._send_json({"error": "group is processing the previous message"}, status=409)
                    return
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/clear":
                self.state.clear_group()
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/new":
                self.state.reset_group()
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/members":
                payload = self._read_json()
                name = str(payload.get("name", "")).strip()
                description = str(payload.get("description", "")).strip()
                base_url = str(
                    payload.get("base_url", payload.get("url", ""))
                ).strip()
                api_key = str(
                    payload.get("api_key", payload.get("key", ""))
                ).strip()
                model = str(payload.get("model", "")).strip()
                if not name:
                    self.state.logger.warning("api_add_member_empty")
                    self._send_json({"error": "name is required"}, status=400)
                    return
                member = self.state.add_member(
                    name=name,
                    description=description,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                )
                self._send_json({
                    "ok": True,
                    "member": self.state.service.member_payload(member),
                })
                return
            if parsed.path == "/api/usage/clear":
                self.state.usage_monitor.clear()
                self.state.logger.info("usage_clear")
                self._send_json({"ok": True})
                return
        except Exception as e:
            self.state.logger.exception("api_error path=%s", parsed.path)
            self._send_json({"error": f"{type(e).__name__}: {e}"}, status=500)
            return
        self._send_json({"error": "not found"}, status=404)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_events(self) -> None:
        q = self.state.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self._write_sse({"type": "hello"})
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    event = {"type": "ping"}
                self._write_sse(event)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.state.unsubscribe(q)

    def _write_sse(self, event: dict[str, Any]) -> None:
        data = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        file_path = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(file_path).startswith(str(WEB_DIR.resolve())):
            self.state.logger.warning("static_forbidden path=%s", path)
            self.send_error(403)
            return
        if not file_path.exists() or not file_path.is_file():
            self.state.logger.warning("static_not_found path=%s", path)
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _state_payload(self) -> dict[str, Any]:
        return self.state.service.state_payload(busy=self.state.busy)


def usage_payload(monitor: UsageMonitor) -> dict[str, Any]:
    return usage_payload_view(monitor)


def _short(value: str, limit: int) -> str:
    text = str(value).replace("\r\n", "\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"


def parse_member_names(value: str) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("member names must be unique")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="MAS Web UI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--group-name", default="default")
    parser.add_argument("--members", type=parse_member_names, default=[])
    parser.add_argument(
        "--member-config",
        type=Path,
        default=default_members_file(),
        help="Group member config file, default: config/group_members.json",
    )
    parser.add_argument("--group-max-rounds", type=int, default=100)
    args = parser.parse_args()
    Handler.state = WebState(
        group_name=args.group_name,
        member_config_path=args.member_config,
        extra_member_names=args.members,
        max_turns=args.max_turns,
        enable_tools=not args.no_tools,
        group_max_rounds=args.group_max_rounds,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MAS Web UI: http://{args.host}:{args.port}")
    Handler.state.logger.info(
        "server_start host=%s port=%s log=%s",
        args.host,
        args.port,
        LOG_PATH,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
        Handler.state.logger.info("server_stop_keyboard_interrupt")
    finally:
        Handler.state.service.close_members()
        server.server_close()


if __name__ == "__main__":
    main()
