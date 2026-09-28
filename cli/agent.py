"""Standalone Agent CLI with one async lifecycle for all turns."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import aclosing
import json
import sys

from application.agent_config import AgentAppConfig
from application.agent_service import AgentAppService
from core.logger import setup_logging
from observability import tool_audit_report_lines, usage_summary_view

HELP = """Commands:
  /save PATH, /load PATH  Save or restore an idle session snapshot
  /new, /clear           Start a session with the configured system prompt
  /history               Show history and active message counts
  /context               Show token budget, compaction and archive status
  /compact [FOCUS]       Compact idle context, optionally preserving a focus
  /tools                 Show registered tools
  /stream                Toggle streaming
  /usage, /usage-clear   Show or clear recorded provider usage
  /audit [N]             Show recent tool audit records (default 20)
  /help                  Show help
  /exit, /quit           Exit
"""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Standalone Agent CLI')
    result.add_argument('--no-tools', action='store_true', help='Disable workspace tools')
    result.add_argument('--max-turns', type=int, default=None, help='Override configured ReAct turn limit')
    result.add_argument('--no-stream', action='store_true', help='Use non-streaming responses')
    result.add_argument('--prompt', help='Run one prompt and exit')
    return result


def create_service(args) -> AgentAppService:
    config = AgentAppConfig.from_env().with_agent_options(
        max_turns=args.max_turns, enable_tools=False if args.no_tools else None)
    setup_logging(config.logging.level)
    return AgentAppService(config, use_stream=not args.no_stream)


async def run_turn(service: AgentAppService, message: str) -> None:
    if not service.use_stream:
        print(await service.arun(message))
        return
    async with aclosing(service.arun_stream(message)) as stream:
        async for chunk in stream:
            if chunk.tool_calls:
                print('\n[tool call] ' + ', '.join(c.name for c in chunk.tool_calls), flush=True)
            if chunk.reasoning:
                print(chunk.reasoning, end='', flush=True)
            if chunk.message:
                print(chunk.message, end='', flush=True)
    print()


def command(service: AgentAppService, text: str) -> bool:
    name, _, argument = text.partition(' ')
    argument = argument.strip()
    if name in ('/save', '/load'):
        if not argument:
            raise ValueError(f'usage: {name} PATH')
        if name == '/save':
            service.save_session(argument)
        else:
            service.load_session(argument)
        print(f'({name[1:]}: {argument})')
    elif name in ('/new', '/clear'):
        service.new_session()
        print('(new session created)')
    elif name == '/history':
        history, active = service.history_counts()
        print(f'history: {history} messages, active: {active} messages')
        for entry in service.history_entries():
            print(f'  [{entry.index}] {entry.role}: {entry.text}')
    elif name == '/context':
        print(json.dumps(service.context_status(), ensure_ascii=False))
    elif name == '/tools':
        print(f'registered tools: {service.tool_names()}')
    elif name == '/stream':
        print(f'streaming mode: {service.toggle_stream()}')
    elif name == '/usage':
        print(json.dumps(usage_summary_view(service.usage_summary())))
    elif name == '/usage-clear':
        service.clear_usage()
        print('(usage records cleared)')
    elif name == '/audit':
        limit = int(argument) if argument else 20
        if limit <= 0:
            raise ValueError('usage: /audit [positive N]')
        records = service.tool_audit_records(limit=limit)
        print('\n'.join(tool_audit_report_lines(records, limit=limit)))
    elif name == '/help':
        print(HELP)
    else:
        return False
    return True


async def acommand(service: AgentAppService, text: str) -> bool:
    name, _, argument = text.partition(' ')
    if name == '/compact':
        await service.acompact(argument.strip())
        print('(context compaction complete)', file=sys.stderr)
        return True
    return command(service, text)


def context_progress(event) -> None:
    if event.type.startswith('context_compaction_'):
        payload = {'event': event.type, **(event.context or {})}
        if event.error:
            payload['error'] = event.error
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


async def interact(service: AgentAppService, *, prompt: str | None = None) -> None:
    unsubscribe = service.agent.subscribe(context_progress)
    try:
        if prompt is not None:
            await run_turn(service, prompt)
            return
        print('Standalone Agent')
        print(HELP)
        while True:
            try:
                text = input('>>> ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\nBye.')
                break
            if text in ('/exit', '/quit'):
                print('Bye.')
                break
            if not text:
                continue
            try:
                if not await acommand(service, text):
                    await run_turn(service, text)
            except KeyboardInterrupt:
                print('\n(interrupted)')
            except Exception as error:
                print(f'[error] {type(error).__name__}: {error}')
    finally:
        unsubscribe()
        await service.aclose()


def cli_main(argv=None) -> None:
    args = parser().parse_args(argv)
    try:
        asyncio.run(interact(create_service(args), prompt=args.prompt))
    except KeyboardInterrupt:
        print('\n(interrupted)')


if __name__ == '__main__':
    cli_main()
