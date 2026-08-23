"""MAS CLI entrypoint.

Usage:
    python main.py
    python main.py --no-tools       # Disable workspace tools
    python main.py --max-turns 50   # Set the ReAct turn limit
    python main.py --group          # Enable multi-agent group mode
"""

from __future__ import annotations

import asyncio
import readline
import argparse
from pathlib import Path
from typing import Any

from application.agent_service import AgentAppService
from application.group_service import GroupAppService
from application.options import AgentServiceOptions, GroupServiceOptions
from core.member_config import default_members_file
from observability.views import tool_audit_report_lines


BANNER = r"""
 __  __   ___   ___   ___
|  \/  | / __| / __| | _ \
| |\/| || (__ | (__  |  _/
|_|  |_| \___| \___| |_|
""".strip("\n")

HELP = """\
Commands:
  /exit, /quit      Exit
  /clear            Clear the current session
  /new              Start a new empty session
  /history          Show history and active message counts
  /tools            Show registered tools
  /stream           Toggle streaming mode
  /help             Show help
Any other input is sent to the agent.
"""

GROUP_HELP = """\
Commands:
  /exit, /quit      Exit
  /clear            Clear group transcript, memory, and events
  /new              Rebuild the group and all member agents
  /history          Show group transcript
  /members          Show group members in order
  /addmember NAME [DESCRIPTION] [--url URL] [--key KEY] [--model MODEL]
                    Add a member with optional role and model config
  /tools            Show tools registered for each member
  /usage            Show LLM usage/cache statistics
  /audit [N]        Show recent tool audit records, default 20
  /config           Show current group runtime configuration
  /debug [N]        Show group debug snapshot, default 10 events/audit records
  /groupstats       Show group dispatch statistics
  /usage-clear      Clear usage records
  /help             Show help
Any other input enters the group chat and is dispatched by unread, directed, and PASS rules.
"""


def run_sync(agent: Any, message: str, use_stream: bool) -> None:
    """Run one synchronous agent turn."""
    if use_stream:
        for chunk in agent.run_stream(message):
            if chunk.tool_calls:
                names = ", ".join(
                    f"{tc.name}({tc.arguments_str})" for tc in chunk.tool_calls
                )
                print(f"\n[tool call] {names}\n", end="", flush=True)
            elif chunk.reasoning:
                print(chunk.reasoning, end="", flush=True)
            elif chunk.message:
                print(chunk.message, end="", flush=True)
        print()
    else:
        result = agent.run(message)
        print(result)

def _print_group_message(msg, *, show_pass: bool, shown: dict[str, int]) -> None:
    if msg.kind == "user":
        return
    is_pass = _is_pass(msg.content)
    if is_pass and not show_pass:
        return
    suffix = " non-propagating" if not getattr(msg, "propagate", True) else ""
    print(f"\n[{msg.sender}]{suffix}")
    print(msg.content, flush=True)
    shown["count"] += 1


def _finish_group_print(shown_count: int) -> None:
    if shown_count == 0:
        print("(all members passed)")
    else:
        print()


def _is_pass(content: str) -> bool:
    normalized = content.strip().strip("`").strip()
    return normalized.upper() == "PASS" or normalized == "group_pass()"


def _parse_member_names(value: str) -> list[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    if len(set(names)) != len(names):
        raise argparse.ArgumentTypeError("member names must be unique")
    return names


def print_usage_report(service: GroupAppService) -> None:
    """Print usage records and aggregate totals."""
    for line in service.usage_report_lines():
        print(line)


def print_tool_audit_report(service: GroupAppService, command: str) -> None:
    """Print recent group member tool audit records."""
    limit = 20
    parts = command.split(maxsplit=1)
    if len(parts) == 2:
        try:
            limit = int(parts[1])
        except ValueError:
            print("[error] usage: /audit [N]")
            return
    for line in tool_audit_report_lines(
        service.tool_audit_records(limit=limit),
        limit=limit,
    ):
        print(line)


def _parse_optional_limit(command: str, usage: str, default: int) -> int | None:
    parts = command.split(maxsplit=1)
    if len(parts) == 1:
        return default
    try:
        return int(parts[1])
    except ValueError:
        print(f"[error] usage: {usage}")
        return None


def print_debug_report(service: GroupAppService, command: str) -> None:
    """Print a group debug snapshot."""
    limit = _parse_optional_limit(command, "/debug [N]", 10)
    if limit is None:
        return
    for line in service.debug_report_lines(limit=limit):
        print(line)


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="MAS CLI")
    parser.add_argument(
        "--no-tools", action="store_true",
        help="Disable workspace tools",
    )
    parser.add_argument(
        "--max-turns", type=int, default=20,
        help="ReAct turn limit, default 20",
    )
    parser.add_argument(
        "--no-stream", action="store_true",
        help="Use non-streaming mode",
    )
    parser.add_argument(
        "--group", action="store_true",
        help="Enable multi-agent group mode",
    )
    parser.add_argument(
        "--group-name", default="default",
        help="Group name, default: default",
    )
    parser.add_argument(
        "--members", type=_parse_member_names, default=[],
        help="Comma-separated member names appended after the member config file",
    )
    parser.add_argument(
        "--member-config",
        type=Path,
        default=default_members_file(),
        help="Group member config file, default: config/group_members.json",
    )
    parser.add_argument(
        "--show-pass", action="store_true",
        help="Show PASS responses in group mode for debugging",
    )
    parser.add_argument(
        "--group-max-rounds", type=int, default=100,
        help="Max group dispatch steps per user message, default 100",
    )
    args = parser.parse_args()

    if args.group:
        asyncio.run(group_cli_main(args))
        return

    service = AgentAppService.from_options(
        AgentServiceOptions(
            max_turns=args.max_turns,
            enable_tools=not args.no_tools,
            use_stream=not args.no_stream,
        )
    )
    agent = service.agent

    print(BANNER)
    print(f"tools: {service.tool_names()}  streaming: {service.use_stream}  max_turns: {args.max_turns}")
    print(HELP)

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print("Bye.")
            break
        if user_input == "/clear":
            service.clear_session()
            print("(session cleared)")
            continue
        if user_input == "/new":
            service.new_session()
            print("(new session created)")
            continue
        if user_input == "/history":
            history_count, active_count = service.history_counts()
            print(f"history: {history_count} messages, active: {active_count} messages")
            for entry in service.history_entries():
                print(f"  [{entry.index}] {entry.role}: {entry.text}")
            continue
        if user_input == "/tools":
            print(f"registered tools: {service.tool_names()}")
            continue
        if user_input == "/stream":
            print(f"streaming mode: {service.toggle_stream()}")
            continue
        if user_input == "/help":
            print(HELP)
            continue

        try:
            run_sync(service.agent, user_input, service.use_stream)
        except KeyboardInterrupt:
            print("\n(interrupted)")
        except Exception as e:
            print(f"\n[error] {type(e).__name__}: {e}")


async def group_cli_main(args: argparse.Namespace) -> None:
    """Multi-agent group CLI."""
    service = GroupAppService.from_options(
        GroupServiceOptions(
            group_name=args.group_name,
            member_config_path=args.member_config,
            extra_member_names=tuple(args.members),
            max_turns=args.max_turns,
            enable_tools=not args.no_tools,
            group_max_rounds=args.group_max_rounds,
            persist_dynamic_members=False,
        )
    )

    print(BANNER)
    print(
        f"group: {service.group.name}  members: {list(service.group.members.keys())}  "
        f"max ReAct turns: {args.max_turns}  group dispatch steps: {args.group_max_rounds}"
    )
    print(GROUP_HELP)

    while True:
        try:
            user_input = input("group>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print("Bye.")
            break
        if user_input == "/clear":
            service.clear_group()
            print("(group transcript, memory, and events cleared; member private sessions kept)")
            continue
        if user_input == "/new":
            await service.areset_group()
            print("(group and all member agents rebuilt)")
            continue
        if user_input == "/history":
            print(service.transcript(limit=None))
            continue
        if user_input == "/members":
            for member in service.group.members.values():
                status = "enabled" if member.enabled else "disabled"
                model = member.agent.llm.model
                url = member.agent.llm.base_url
                print(f"- {member.name} ({status}): {member.description} model={model} url={url}")
            continue
        if user_input.startswith("/addmember"):
            try:
                member = service.add_member_from_command(
                    user_input.removeprefix("/addmember").strip()
                )
                print(
                    f"(added member {member.name}: "
                    f"model={member.agent.llm.model}, url={member.agent.llm.base_url})"
                )
            except Exception as e:
                print(f"[error] {type(e).__name__}: {e}")
            continue
        if user_input == "/tools":
            for name, tools in service.member_tool_names().items():
                print(f"- {name}: {tools}")
            continue
        if user_input == "/usage":
            print_usage_report(service)
            continue
        if user_input == "/audit" or user_input.startswith("/audit "):
            print_tool_audit_report(service, user_input)
            continue
        if user_input == "/config":
            for line in service.config_report_lines():
                print(line)
            continue
        if user_input == "/debug" or user_input.startswith("/debug "):
            print_debug_report(service, user_input)
            continue
        if user_input == "/groupstats":
            print(service.stats_report())
            continue
        if user_input == "/usage-clear":
            service.clear_usage()
            print("(usage records cleared)")
            continue
        if user_input == "/help":
            print(GROUP_HELP)
            continue

        if not service.enabled_members():
            print("[error] add at least one member with /addmember first")
            continue

        try:
            shown = {"count": 0}
            await service.arun_message(
                user_input,
                on_member_start=lambda member: print(
                    f"\n[dispatch] {member.name} is running...",
                    flush=True,
                ),
                on_message=lambda msg: _print_group_message(
                    msg,
                    show_pass=args.show_pass,
                    shown=shown,
                ),
            )
            _finish_group_print(shown["count"])
        except KeyboardInterrupt:
            print("\n(interrupted)")
        except Exception as e:
            print(f"\n[error] {type(e).__name__}: {e}")

    await service.aclose_members()


if __name__ == "__main__":
    cli_main()
