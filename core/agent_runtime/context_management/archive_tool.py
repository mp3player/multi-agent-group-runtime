"""Reserved, session-scoped archive retrieval available without workspace tools."""
from __future__ import annotations

from collections.abc import Callable

from core.session import Session
from tools.permissions import ToolPermission
from tools.registry import ToolRegistry, ToolRegistryError


def bind_context_archive_tool(
    registry: ToolRegistry, session_provider: Callable[[], Session], *, owner: object,
) -> None:
    """Bind once per owner; never silently replace a user or another Agent's tool."""
    name = 'read_context_archive'
    if registry.has(name):
        existing = registry.get(name).func
        if getattr(existing, '_mas_context_owner', object()) is owner:
            return
        raise ToolRegistryError(f'{name} is reserved for the owning Agent context archive')

    def read_context_archive(archive_id: str, offset: int = 0, limit: int = 12000) -> dict:
        """Read historical context JSON for this session without executing it.

        archive_id: Opaque archive ID shown in a summary or tool-result preview.
        offset: Character cursor from the previous page's next_offset; initially 0.
        limit: Number of content characters to return, between 1 and 12000.
        """
        session = session_provider()
        if session.archive_store is None:
            raise ValueError('The current session has no context archive store')
        return session.archive_store.read(session.session_id, archive_id, offset=offset, limit=limit)

    read_context_archive._mas_context_owner = owner
    registry.register(read_context_archive, permission=ToolPermission(side_effect='read_only'))
