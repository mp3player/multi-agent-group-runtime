"""Pure selection and rendering for a bounded model working view."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
import json

from core.agent_runtime.context import project_context, validate_tool_pairs
from core.llm_runtime.payload import to_dict_list
from core.session import render_context
from models import ToolCall

ARCHIVE_PLACEHOLDER = '0' * 32


@dataclass(frozen=True)
class PreparedContext:
    messages: list
    tools: list | None
    input_tokens: int
    input_budget: int


@dataclass(frozen=True)
class CompactionRequest:
    """One non-streaming maintenance call, never a business tool turn."""
    messages: list
    max_tokens: int

    def options(self):
        return {'max_tokens': self.max_tokens, 'temperature': 0.0}


@dataclass(frozen=True)
class ContextIORequest:
    """Detached archive I/O; publication stays in the owning run task."""
    operation: Callable


def render(entries, checkpoint, overrides, transform):
    return project_context(render_context(entries, checkpoint, overrides), transform)


def complete_units(entries):
    """Group assistant calls with all results, also within one user span."""
    validate_tool_pairs([entry.message for entry in entries])
    units = []
    batch = []
    pending = set()
    for entry in entries:
        message = entry.message
        if pending:
            batch.append(entry)
            if message.role == 'tool':
                pending.remove(message.tool_call_id)
            if not pending:
                units.append(batch)
                batch = []
            continue
        calls = ([message] if isinstance(message, ToolCall)
                 else getattr(message, 'tool_calls', None) or [])
        if calls:
            batch = [entry]
            pending = {call.id for call in calls}
        elif (entry.operation_id is not None and units
              and units[-1][-1].operation_id == entry.operation_id):
            units[-1].append(entry)
        elif message.role == 'reasoning' and units:
            units[-1].append(entry)
        else:
            units.append([entry])
    return units


def pinned_ids(entries):
    pinned = {e.entry_id for e in entries if e.message.role == 'system'}
    newest = next((e for e in reversed(entries) if e.message.role == 'user'), None)
    if newest is not None:
        pinned.add(newest.entry_id)
        if newest.operation_id is not None:
            pinned.update(e.entry_id for e in entries if e.operation_id == newest.operation_id)
    return pinned, newest


def select_source(entries, checkpoint, overrides, counter, tools, budget, summary_cap,
                  *, target=None, transform=None):
    """Retain the latest complete unit and a budgeted suffix plus user anchor."""
    pinned, newest = pinned_ids(entries)
    units = [u for u in complete_units(entries) if not all(e.entry_id in pinned for e in u)]
    retained = set(pinned)
    positions = {entry.entry_id: i for i, entry in enumerate(entries)}
    anchor_position = positions[newest.entry_id] if newest else -1
    reserve_summary = summary_cap + 120
    target = int(0.55 * budget) if target is None else target
    for index, unit in enumerate(reversed(units)):
        trial = retained | {e.entry_id for e in unit}
        messages = render([e for e in entries if e.entry_id in trial], None, overrides, transform)
        cost = counter.estimate(messages, tools).tokens + reserve_summary
        protect_current = index == 0 and positions[unit[0].entry_id] > anchor_position
        if not protect_current and cost > target:
            break
        retained = trial
    kept = [e for e in entries if e.entry_id in retained]
    source = [e for e in entries if e.entry_id not in retained]
    return source, kept, newest


def source_text(entries, overrides):
    """Quoted source data: it is never replayed as assistant/tool operations."""
    lines = []
    for entry in entries:
        message = deepcopy(entry.message)
        override = overrides.get(entry.entry_id)
        if override:
            message.message = override['preview']
        wire = to_dict_list([message])
        if wire:
            record = {'entry_id': entry.entry_id, 'message': wire[0]}
            if entry.operation_id is not None:
                record['context'] = {'operation_id': entry.operation_id, 'provenance': entry.provenance}
            lines.append(json.dumps(record, ensure_ascii=False))
    return '\n'.join(lines)


@dataclass(frozen=True)
class SourceUnit:
    references: list[dict]
    text: str


def source_units(entries, overrides):
    units = []
    for unit in complete_units(entries):
        references = []
        for entry in unit:
            message = entry.message
            calls = ([message] if isinstance(message, ToolCall)
                     else getattr(message, 'tool_calls', None) or [])
            references.append({'entry_id': entry.entry_id, 'role': message.role,
                               'tool_call_id': getattr(message, 'tool_call_id', None),
                               'tool_calls': [call.id for call in calls],
                               **({'context': {'operation_id': entry.operation_id, 'provenance': entry.provenance}}
                                  if entry.operation_id is not None else {})})
        units.append(SourceUnit(references, source_text(unit, overrides)))
    return units
