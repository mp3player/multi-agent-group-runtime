"""Owned composition for a local conversation between peer Agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from tempfile import mkdtemp
import time
from uuid import uuid4

from application import agent_builder as builders
from application.agent_config import AgentAppConfig
from group import GroupLimits, GroupRuntime, OnDemandStrategy


DEFAULT_MEMBERS = ('alice', 'bob', 'carol')


async def _close_models(models):
    results = await asyncio.gather(*(model.aclose() for model in models), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


class GroupChat:
    """Own the Group and its providers; quiet discussions remain open."""

    def __init__(self, runtime, path, models):
        self.runtime = runtime
        self.path = path
        self.models = tuple(models)
        self.member_names = tuple(runtime.workers)
        self.scope = None
        self.scopes = []
        self._closing = None

    @property
    def closing(self):
        """Whether shutdown has started, including an already closed chat."""
        return self._closing is not None

    @classmethod
    async def create(cls, config: AgentAppConfig, *, members=DEFAULT_MEMBERS,
                     store=None, limits=None):
        limits = limits or GroupLimits()
        if isinstance(members, str):
            raise ValueError('Members must be a sequence of distinct names')
        names = tuple(members)
        if (not 1 <= len(names) <= limits.max_members or len(set(names)) != len(names)
                or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', name)
                       or name.lower() in ('user', 'all') for name in names)):
            raise ValueError('Use distinct member names (letters, digits, underscore, hyphen); user and all are reserved')
        if not all((config.llm.base_url, config.llm.api_key, config.llm.model)):
            raise ValueError('Configure BaseURL, BaseKey and BaseModel in .env')
        if config.agent.context_window is None:
            raise ValueError('Configure MAS_CONTEXT_WINDOW in .env before starting Group chat')
        path = Path(store).expanduser() if store is not None else Path(mkdtemp(prefix='mas-group-chat-')) / 'group.sqlite'
        if path.exists() or path.is_symlink():
            raise FileExistsError('Group chat creates fresh member sessions. Choose a new --store path or omit --store; existing history is retained.')
        # The Group effect profile excludes workspace mutation tools. Keep the
        # independent Agent builder and caller configuration unchanged.
        member_config = config.with_agent_options(enable_tools=False)
        agents, models = {}, []
        try:
            for name in names:
                agent = builders.build_agent(member_config, usage_label=name)
                models.append(agent.llm)
                agent.add_system_prompt_extension(
                    f'You are Group member {name}, an equal peer in a live local group conversation. '
                    f'The Agent members are {", ".join(names)}. The human participant is user and '
                    'is not an Agent member. Use group_members to inspect current membership and '
                    'availability. Answer membership questions about this actual Group. '
                    'Only the registered conversation and context archive tools are available here.')
                agents[name] = agent
            path.parent.mkdir(parents=True, exist_ok=True)
            # Reserve a new path atomically rather than racing a check and open.
            with path.open('xb'):
                pass
            runtime = await GroupRuntime.create(path, agents, worker_safe=True,
                strategy=OnDemandStrategy(), limits=limits)
            return cls(runtime, path.resolve(), models)
        except BaseException:
            await _close_models(models)
            raise

    async def _ensure_scope(self):
        if self._closing is not None:
            raise RuntimeError('Group chat is closing')
        if self.scope is not None:
            snapshot = await self.runtime.snapshot(self.scope)
            if snapshot.state != 'terminal' and snapshot.deadline_at <= time.time():
                await self.runtime.drive(self.scope)
                snapshot = await self.runtime.snapshot(self.scope)
            if snapshot.state != 'terminal':
                return self.scope
        self.scope = await self.runtime.open_invocation(f'chat-{uuid4().hex}')
        self.scopes.append(self.scope)
        return self.scope

    async def send(self, content, *, recipients=(), passive=False):
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Enter a nonempty message')
        if isinstance(recipients, str):
            raise ValueError('Recipients must be a sequence of member names')
        recipients = tuple(recipients)
        if len(set(recipients)) != len(recipients) or any(name not in self.member_names for name in recipients):
            raise ValueError(f'Unknown or duplicate member; available: {", ".join(self.member_names)}')
        scope = await self._ensure_scope()
        publish = self.runtime.post if passive else self.runtime.request
        receipt = await publish(scope, content, key=uuid4().hex, recipients=recipients)
        if passive:
            await self.runtime.receive(scope)
        return receipt

    async def history(self, *, scope=None, after=0):
        """Read every page without confusing per-discussion sequence boundaries."""
        if scope is None and after:
            raise ValueError('A history cursor requires a discussion identity')
        for current in (scope,) if scope is not None else tuple(self.scopes):
            cursor, high_water = after, None
            while True:
                page = await self.runtime.history(current, after=cursor, high_water=high_water)
                for message in page.items:
                    yield message
                if page.exhausted:
                    break
                cursor, high_water = page.next_cursor, page.high_water

    async def close(self):
        if self._closing is None:
            self._closing = asyncio.create_task(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        try:
            await self.runtime.close()
        finally:
            await _close_models(self.models)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
