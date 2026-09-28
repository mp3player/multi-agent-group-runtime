"""Existing synchronous tool pipeline -> execution-bound collaboration commands."""

from __future__ import annotations

import asyncio
import inspect
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from typing import Any, Callable

from group import state as sql
from group.errors import GroupError, LifecycleError
from models import Message
from tools.decorator import ToolFunction
from tools.permissions import ToolPermission
from tools.registry import ToolRegistryError


@dataclass(frozen=True)
class CollaborationTool:
    """Explicit restricted-effect and terminal metadata for one custom tool."""

    func: Callable[..., Any]
    permission: ToolPermission | str
    ends_run: bool = False

    def __post_init__(self):
        _validate_tool_function(self.func)
        if not isinstance(self.permission, (ToolPermission, str)):
            raise GroupError('Collaboration tool permission must be a ToolPermission or side-effect name')
        permission = ToolPermission.from_value(self.permission)
        if permission.side_effect not in ('read_only', 'memory_only'):
            raise GroupError('Collaboration tool permission must declare read_only or memory_only effects')
        if type(self.ends_run) is not bool:
            raise GroupError('Collaboration tool ends_run must be a boolean')
        object.__setattr__(self, 'permission', permission)

    @property
    def __name__(self):
        return self.func.__name__


@dataclass(frozen=True)
class ExecutionContext:
    runtime: Any
    invocation_id: str
    member_id: str
    assignment_id: str


execution_context: ContextVar[ExecutionContext] = ContextVar('group_execution_context')


def _bound():
    try:
        return execution_context.get()
    except LookupError as error:
        raise LifecycleError('Collaboration tools require an active bound Group execution') from error


def _bridge(bound, create_operation):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is bound.runtime._loop:
        raise LifecycleError('Synchronous collaboration tools must run on the declared member worker')
    return asyncio.run_coroutine_threadsafe(create_operation(), bound.runtime._loop).result()


def _publish(content, recipients, reply_to, *, request):
    bound = _bound()
    recipients = tuple(recipients) if isinstance(recipients, (list, tuple)) else recipients
    key = sql.new_id()
    receipt = _bridge(bound, lambda: bound.runtime._command(
        bound.invocation_id, bound.member_id, bound.assignment_id,
        content, key, recipients, reply_to or None, request=request))
    result = {
        'kind': 'outgoing_request_receipt' if request else 'outgoing_post_receipt',
        'sender': bound.member_id, 'recipients': recipients,
        'accepted': True, **asdict(receipt),
    }
    return _publication_receipt(bound, result)


def _publication_receipt(bound, result):
    """Optional observations must never turn a committed post into a rejection."""
    base = sql.json_text(result)
    try:
        fits = _retrieval_fit(bound)
        unavailable = sql.json_text({**result, 'feedback': {'available': False}})
        if not fits(unavailable):
            return base
        from group.feedback import publication
        try:
            feedback = _bridge(bound, lambda: bound.runtime.store.read(lambda db: publication(
                db, bound.invocation_id, bound.member_id, bound.assignment_id, result['message_id'],
                require_reply=bound.runtime.profile.require_public_reply,
                limit=min(20, bound.runtime.limits.page_size))))
        except Exception:
            # Store failures still poison the store and stop runtime admission.
            # This receipt describes the publication that already committed.
            return unavailable
        while True:
            rendered = sql.json_text({**result, 'feedback': feedback})
            if fits(rendered):
                return rendered
            ids = feedback['unlinked_trigger_ids']
            if not ids:
                return unavailable
            feedback = {**feedback, 'unlinked_trigger_ids': ids[:len(ids) // 2],
                        'unlinked_trigger_ids_complete': False}
    except Exception:
        # Even an unavailable token budget cannot undo the accepted command.
        return base


def group_post(content: str, recipients: list[str] = (), reply_to: str = '') -> str:
    """Publish a public message. Directed recipients do not create a private channel or wake a member."""
    return _publish(content, recipients, reply_to, request=False)


def group_request(content: str, recipients: list[str] = (), reply_to: str = '') -> str:
    """Publish a public response request. Empty recipients let a future policy choose; acceptance never promises execution."""
    return _publish(content, recipients, reply_to, request=True)


def group_broadcast(content: str, reply_to: str = '') -> str:
    """Request public responses from all other current members; acceptance never promises execution."""
    bound = _bound()
    recipients = tuple(member for member in bound.runtime.workers if member != bound.member_id)
    if not recipients:
        raise GroupError('A broadcast requires at least one other member')
    return _publish(content, recipients, reply_to, request=True)


def group_members() -> str:
    """Read member execution availability. This is a snapshot, not a reservation."""
    bound = _bound()
    return sql.json_text([asdict(member) for member in _bridge(bound, bound.runtime.members)])


def group_history(after: int = 0, limit: int = 0, high_water: int = 0) -> str:
    """Read public previews and IDs; limit=0 uses the configured page size. Use group_message for complete content. Reuse high_water and next_cursor for a stable scan."""
    bound = _bound()
    if type(limit) is not int or type(high_water) is not int:
        raise ValueError('Page limit and high-water mark must be integers')
    page = _bridge(bound, lambda: bound.runtime.history(bound.invocation_id, after=after, limit=limit or None, high_water=high_water or None))
    fits = _retrieval_fit(bound)
    result = {'items': [], 'high_water': page.high_water, 'next_cursor': after, 'exhausted': False}
    for message in page.items:
        item = asdict(message)
        count = min(512, len(message.content))
        while True:
            item.update(content=message.content[:count], content_complete=count == len(message.content),
                        next_content_offset=count)
            candidate = {**result, 'items': result['items'] + [item], 'next_cursor': message.sequence}
            if fits(sql.json_text(candidate)):
                break
            if result['items'] or count == 0:
                candidate = None
                break
            count //= 2
        if candidate is None:
            break
        result = candidate
    if len(result['items']) == len(page.items):
        result.update(next_cursor=page.next_cursor, exhausted=page.exhausted)
    if page.items and not result['items']:
        raise LifecycleError('Tool result budget cannot hold a message descriptor')
    rendered = sql.json_text(result)
    if not fits(rendered):
        raise LifecycleError('Tool result budget cannot hold a history envelope')
    return rendered


def _retrieval_fit(bound):
    runtime = bound.runtime.workers[bound.member_id].agent.runtime
    manager = runtime.context_manager
    available = manager.model_budget().input_budget(runtime.max_tokens, extra_reserve=manager.extra_reserve)
    target = available // 4

    def fits(text):
        if len(text) > runtime.tool_runtime.max_result_chars:
            return False
        message = Message(role='tool', message=text)
        # Reserve generous identifier framing. This is a retrieval-page target;
        # the complete request, including batches, still has its own hard check.
        message.tool_call_id = '0' * 128
        return manager.counter.estimate([message]).tokens <= target
    return fits


def group_message(message_id: str, offset: int = 0, limit: int = 4096) -> str:
    """Read complete original content in character-offset chunks. Continue at next_offset until exhausted."""
    bound = _bound()
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError('Invalid content offset or limit')
    message = _bridge(bound, lambda: bound.runtime.message(bound.invocation_id, message_id))
    if offset > len(message.content):
        raise ValueError('Content offset is beyond the message')
    fits = _retrieval_fit(bound)
    count = min(limit, bound.runtime.limits.message_bytes, len(message.content) - offset)
    while True:
        value = {'message_id': message.id, 'content': message.content[offset:offset + count],
                 'next_offset': offset + count, 'exhausted': offset + count == len(message.content)}
        result = sql.json_text(value)
        if count == 0 and offset < len(message.content):
            raise LifecycleError('Tool result budget cannot advance through message content')
        if fits(result):
            return result
        if count == 0:
            raise LifecycleError('Tool result budget cannot hold a content envelope')
        count //= 2


def group_yield() -> str:
    """End this member run without completing the Group or publishing a message."""
    _bound()
    return 'Current member run yielded; Group completion is a separate decision.'


_TOOLS = (group_post, group_request, group_members, group_history, group_message, group_yield)
tool_names = tuple(tool.__name__ for tool in _TOOLS)


def _validate_tool_function(tool):
    name = getattr(tool, '__name__', None)
    if not callable(tool) or not isinstance(name, str) or not name.isidentifier():
        raise GroupError('Collaboration tools need valid callable names')
    try:
        original = inspect.unwrap(tool)
    except ValueError as error:
        raise GroupError('Collaboration tool wrapper chain is invalid') from error
    if any(inspect.iscoroutinefunction(value) or inspect.isasyncgenfunction(value)
           for value in (tool, original, getattr(tool, '__call__', None),
                         getattr(original, '__call__', None))):
        raise GroupError('Collaboration tools must be synchronous')


def _tool_declaration(tool):
    if isinstance(tool, CollaborationTool):
        return CollaborationTool(tool.func, tool.permission, tool.ends_run)
    _validate_tool_function(tool)
    # ToolFunction only forwards calls. Arbitrary decorated functions may add
    # effects, so their __wrapped__ attribute cannot supply a safe declaration.
    original = inspect.unwrap(tool, stop=lambda value: type(value) is not ToolFunction)
    if any(original is builtin for builtin in (*_TOOLS, group_broadcast)):
        return CollaborationTool(tool, 'memory_only', ends_run=original is group_yield)
    raise GroupError('Custom collaboration tools require an explicit permission declaration')


def _normalize_tool_set(tool_set):
    try:
        selected = tuple(tool_set)
    except TypeError as error:
        raise GroupError('Collaboration tools must be an iterable of callables or declarations') from error
    names = set()
    for tool in selected:
        name = _tool_declaration(tool).__name__
        if name in names:
            raise GroupError(f'Duplicate collaboration tool name: {name}')
        names.add(name)
    return selected


def install_tools(registry, tool_set=None):
    selected = _normalize_tool_set(_TOOLS if tool_set is None else tool_set)
    for tool in selected:
        if registry.has(tool.__name__):
            raise ToolRegistryError(f'Tool already exists: {tool.__name__}')
    installed = []
    try:
        for tool in selected:
            declaration = _tool_declaration(tool)
            registry.register(declaration.func, name=tool.__name__,
                              permission=declaration.permission, ends_run=declaration.ends_run)
            installed.append(tool.__name__)
    except BaseException:
        for name in reversed(installed):
            registry.unregister(name)
        raise


def uninstall_tools(registry, tool_set=None):
    for tool in _normalize_tool_set(_TOOLS if tool_set is None else tool_set):
        registry.unregister(tool.__name__)
