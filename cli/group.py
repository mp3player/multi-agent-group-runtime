"""Interactive local Group chat using the configured real model provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import select
import stat
import sys
import time

from application.agent_config import AgentAppConfig
from application.group_chat import DEFAULT_MEMBERS, GroupChat
from core.logger import setup_logging
from group import GroupLimits
from group.diagnostics import status as group_status
from group.errors import GroupError, StoreFailedError


HELP = """Commands:
  TEXT                 Ask one available member (on-demand scheduling)
  @MEMBER TEXT         Ask a specific member
  @all TEXT            Ask every member
  /broadcast TEXT      Ask every member
  /post TEXT           Publish background without activating members
  /members             Show Agent members and availability
  /history             Show public messages from this CLI session
  /status              Show bounded request evidence, execution state and limits
  /finish              Explicitly accept and finish the current discussion
  /cancel              Cancel the current discussion; keep its history
  /help                Show help
  /exit, /quit         Stop the chat and release resources
"""


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--members', nargs='+', default=DEFAULT_MEMBERS, metavar='NAME',
                        help='Peer names (default: alice bob carol)')
    result.add_argument('--store', type=Path, help='New SQLite path; defaults to a fresh temporary directory')
    result.add_argument('--prompt', help='Send one message or command and exit')
    result.add_argument('--max-turns', type=int, help='Override the per-member model-turn limit')
    result.add_argument('--max-runs', type=int, default=100, help='Member-run limit per discussion (default: 100)')
    result.add_argument('--timeout', type=float, default=300,
                        help='Discussion lifetime in seconds, including idle time (default: 300)')
    return result


def print_message(message):
    target = f' -> {", ".join(message.recipients)}' if message.recipients else ''
    print(f'[{message.sender}]{target} {message.content}', flush=True)


def diagnostic_text(value, *, limit=240):
    """Keep untrusted diagnostic fields on one bounded, terminal-safe line."""
    text = json.dumps(str(value), ensure_ascii=True)[1:-1]
    marker = '...[truncated]'
    return text if len(text) <= limit else text[:limit - len(marker)] + marker


def print_status(observation):
    snapshot = observation.snapshot
    deadline = (f'{max(0, snapshot.deadline_at - time.time()):.0f}s'
                if snapshot.deadline_at is not None else 'none')
    print(f'[group] {diagnostic_text(snapshot.state)}; reason={diagnostic_text(snapshot.reason or "none")}; '
          f'runs={snapshot.admitted_count}/{snapshot.max_runs}; '
          f'active={snapshot.active_count}; queued={snapshot.queued_count}; '
          f'pending={snapshot.pending_count}; blocked={snapshot.blocked_count}; deadline_in={deadline}')
    print(f'[group] unresolved={observation.missing_reply_count}; errors={observation.error_count}; '
          f'details={len(observation.details)}/{observation.detail_count}; omitted={observation.omitted_count}')
    for row in observation.details:
        evidence = row.evidence
        if row.opportunity_id is None:
            response = 'no-trigger'
        elif not observation.require_public_reply:
            response = 'not-required'
        elif evidence is None:
            response = 'provisional' if row.assignment_id else 'not-assigned'
        elif evidence.satisfied:
            response = 'resolved' if evidence.resolved_by else 'satisfied'
        else:
            response = 'unresolved' if row.reply_id else 'missing'
        fields = [
            ('request', row.opportunity_id or 'none'), ('message', row.message_id or 'none'),
            ('member', row.member_id or 'unassigned'), ('assignment', row.assignment_id or 'none'),
            ('opportunity', row.opportunity_state or 'none'), ('state', row.assignment_state or 'unassigned'),
            ('outcome', row.outcome or 'none'), ('response', response), ('reply', row.reply_id or 'none'),
        ]
        if row.resolution_reply_id:
            fields.extend((('resolution_reply', row.resolution_reply_id), ('resolved_by', row.resolved_by)))
        if row.other_publication_id:
            fields.extend((('other_publication_sample', row.other_publication_id),
                           ('other_reply_to', row.other_reply_to or 'none')))
        if row.error:
            fields.append(('error', row.error))
        print('[status] ' + '; '.join(f'{name}={diagnostic_text(value)}' for name, value in fields))


class TerminalInput:
    """Read POSIX stdin without blocking cancellation or leaving a reader thread."""

    def __init__(self):
        self.buffer = bytearray()
        self.eof = False

    async def read(self, prompt):
        print(prompt, end='', flush=True)
        loop, descriptor = asyncio.get_running_loop(), sys.stdin.fileno()
        regular_file = stat.S_ISREG(os.fstat(descriptor).st_mode)
        while b'\n' not in self.buffer and not self.eof:
            if regular_file:
                chunk = os.read(descriptor, 4096)
            else:
                ready = loop.create_future()
                def readable():
                    if ready.done():
                        return
                    try:
                        try:
                            ready.set_result(os.read(descriptor, 4096))
                        except OSError as error:
                            ready.set_exception(error)
                    except asyncio.InvalidStateError:
                        # SIGINT can cancel the waiter during the synchronous
                        # read, after the initial done() check.
                        if not ready.cancelled():
                            raise
                try:
                    loop.add_reader(descriptor, readable)
                except PermissionError:
                    # epoll rejects always-ready devices such as /dev/null.
                    # Read only when select confirms immediate readiness.
                    if not select.select([descriptor], [], [], 0)[0]:
                        raise
                    chunk = os.read(descriptor, 4096)
                else:
                    try:
                        chunk = await ready
                    finally:
                        loop.remove_reader(descriptor)
            self.buffer.extend(chunk)
            self.eof = not chunk
        if not self.buffer:
            raise EOFError
        boundary = self.buffer.find(b'\n')
        size = boundary + 1 if boundary >= 0 else len(self.buffer)
        line = bytes(self.buffer[:size])
        del self.buffer[:size]
        return line.decode(sys.stdin.encoding or 'utf-8', errors=sys.stdin.errors or 'strict')


class Console:
    def __init__(self, chat):
        self.chat = chat
        self.cursors = {}

    async def emit_new(self):
        scope = self.chat.scope
        if scope is not None:
            async for message in self.chat.history(scope=scope, after=self.cursors.get(scope, 0)):
                print_message(message)
                self.cursors[scope] = message.sequence

    async def drive(self):
        """Display committed public messages while the owned driver progresses."""
        group, scope = self.chat.runtime, self.chat.scope
        driver = asyncio.create_task(group.drive(scope))
        waiter = None
        try:
            while not driver.done():
                snapshot = await group.snapshot(scope)
                await self.emit_new()
                if driver.done():
                    break
                waiter = asyncio.create_task(group.wait_for_change(snapshot.revision))
                await asyncio.wait((driver, waiter), return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                waiter = None
            result = await driver
            await self.emit_new()
            if result.status == 'waiting':
                print('[group] Waiting for your next message. Use /finish when the discussion is complete.')
            else:
                print(f'[group] {result.status}: {result.reason}')
                if result.missing_reply_ids:
                    print(f'[group] {len(result.missing_reply_ids)} unresolved response request(s).')
                page = await group.assignments(scope)
                while True:
                    for assignment in page.items:
                        if assignment.error:
                            print(f'[error] {assignment.member_id}: {assignment.error}')
                    if page.exhausted:
                        break
                    page = await group.assignments(scope, after=page.next_cursor, high_water=page.high_water)
                print('[group] Inspect /status and /history; /cancel closes unresolved work without retrying it.')
            return 0 if result.status in ('waiting', 'completed') else 1
        except BaseException:
            # Cancelling a drive waiter alone does not stop the owned driver.
            # Settle members before returning to blocking terminal input.
            try:
                await group.cancel(scope)
                await asyncio.gather(driver, return_exceptions=True)
            except BaseException:
                await self.chat.close()
                raise
            raise
        finally:
            for task in (driver, waiter):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (driver, waiter) if task is not None), return_exceptions=True)

    async def handle(self, line):
        chat, group = self.chat, self.chat.runtime
        name, _, argument = line.partition(' ')
        argument = argument.strip()
        if name == '/help':
            print(HELP)
            return 0
        if name in ('/exit', '/quit'):
            print('Bye.')
            return 0
        if name == '/members':
            members = await group.members()
            print(f'{len(members)} Agent members (the human user is separate):')
            for member in members:
                print(f'  {member.id}: {member.state}')
            return 0
        if name == '/history':
            async for message in chat.history():
                print_message(message)
            return 0
        if name == '/status':
            if chat.scope is None:
                print('[group] Ready; no discussion has started.')
            else:
                print_status(await group_status(group, chat.scope))
            return 0
        if name in ('/finish', '/cancel'):
            if chat.scope is None:
                print('[group] No discussion has started.')
            elif name == '/cancel':
                await group.cancel(chat.scope)
                print('[group] Discussion cancelled. The next message starts another discussion.')
            else:
                await group.finish(chat.scope, revision=(await group.snapshot(chat.scope)).revision)
                print('[group] Discussion explicitly finished. The next message starts another discussion.')
            return 0
        passive, recipients = False, ()
        if name == '/post':
            content, passive = argument, True
        elif name == '/broadcast' or name == '@all':
            content, recipients = argument, chat.member_names
        elif name.startswith('@'):
            content, recipients = argument, (name[1:],)
        elif name.startswith('/'):
            raise ValueError(f'Unknown command: {name}; use /help')
        else:
            content = line
        previous = chat.scope
        await chat.send(content, recipients=recipients, passive=passive)
        if chat.scope != previous:
            print('[group] New discussion started; earlier public history remains available.')
        await self.emit_new()
        if passive:
            print('[group] Background published; no member activated.')
            return 0
        print('[group] Working...', flush=True)
        return await self.drive()


async def interact(chat, *, prompt=None):
    print(f'Group chat | {len(chat.member_names)} Agent members: {", ".join(chat.member_names)}')
    print(f'Public history: {chat.path}')
    print('Strategy: on-demand. Use /help for commands. Ctrl+C exits after in-flight work settles.')
    console = Console(chat)
    if prompt is not None:
        return await console.handle(prompt.strip())
    terminal = TerminalInput()
    while True:
        try:
            line = (await terminal.read('group> ')).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ('/exit', '/quit'):
            break
        if not line:
            continue
        try:
            await console.handle(line)
        except (ValueError, GroupError) as error:
            if isinstance(error, StoreFailedError) or chat.runtime.store.failed or chat.closing:
                raise
            print(f'[error] {type(error).__name__}: {error}')
    print('Bye.')
    return 0


async def run(args):
    config = AgentAppConfig.from_env().with_agent_options(max_turns=args.max_turns)
    setup_logging(config.logging.level)
    limits = GroupLimits(max_runs=args.max_runs, invocation_timeout=args.timeout)
    async with await GroupChat.create(config, members=args.members, store=args.store, limits=limits) as chat:
        return await interact(chat, prompt=args.prompt)


def cli_main(argv=None):
    args = parser().parse_args(argv)
    try:
        status = asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\n[group] Interrupted; chat closed.')
        status = 130
    except Exception as error:
        print(f'[error] {type(error).__name__}: {error}')
        status = 1
    raise SystemExit(status)


if __name__ == '__main__':
    cli_main()
