"""Strict v2 idle snapshots; v1 import preserves ambiguous message identities."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from core.agent_runtime.context import validate_tool_pairs
from core.context_archive import FileContextArchive
from core.session import Session
from core.context_batch import validate_provenance, batch_digest, canonical_batch, ContextBatch, ordered_context_unit
from core.session_codec import message_digest
import re
from core.session_codec import (
    _decode, _encode, _fields, id_list, invalid_constant, opaque_id,
    unique_object as _unique_object, validate_checkpoint, validate_overrides,
)


def _restore_v1(data, archive_store):
    _fields(data, ('version', 'history_limit', 'history', 'active'))
    session = Session(history_limit=data['history_limit'], archive_store=archive_store)
    lists = {}
    encoded = {}
    for key in ('history', 'active'):
        if not isinstance(data[key], list):
            raise ValueError(f'{key} must be a list')
        lists[key] = [_decode(item, legacy=True) for item in data[key]]
        validate_tool_pairs(lists[key])
        encoded[key] = [json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)
                        for item in data[key]]
    history_counts, active_counts = Counter(encoded['history']), Counter(encoded['active'])
    history_ids = []
    unique_history = {}
    for index, (message, signature) in enumerate(zip(lists['history'], encoded['history'])):
        entry_id = uuid4().hex
        session._entries[entry_id] = message
        history_ids.append(entry_id)
        if history_counts[signature] == 1:
            unique_history[signature] = index
    # Reuse only unambiguous complete encodings with consistent relative order.
    matches = [unique_history[sig] for sig in encoded['active']
               if sig in unique_history and active_counts[sig] == 1]
    ordered = matches == sorted(matches)
    active_ids = []
    for message, signature in zip(lists['active'], encoded['active']):
        if ordered and signature in unique_history and active_counts[signature] == 1:
            entry_id = history_ids[unique_history[signature]]
        else:
            entry_id = uuid4().hex
            session._entries[entry_id] = message
        active_ids.append(entry_id)
    session._history_ids = history_ids
    session._active_ids = active_ids
    session.imported_history_incomplete = True
    return session


def _restore(data, archive_store=None):
    if not isinstance(data, dict) or type(data.get('version')) is not int:
        raise ValueError('invalid session snapshot version')
    if data['version'] == 1:
        return _restore_v1(data, archive_store)
    if data['version'] != 2:
        raise ValueError('unsupported session snapshot version')
    data = dict(data)
    context_batches = data.pop('context_batches', {})
    _fields(data, ('version', 'session_id', 'revision', 'history_limit', 'entries',
                   'history_ids', 'active_ids', 'checkpoint', 'overrides',
                   'history_archive_id', 'imported_history_incomplete'))
    session = Session(history_limit=data['history_limit'], archive_store=archive_store)
    session.session_id = opaque_id(data['session_id'])
    if type(data['revision']) is not int or data['revision'] < 0:
        raise ValueError('session revision must be a nonnegative integer')
    session.revision = data['revision']
    if type(data['imported_history_incomplete']) is not bool:
        raise ValueError('history import marker must be boolean')
    session.imported_history_incomplete = data['imported_history_incomplete']
    if not isinstance(data['entries'], list):
        raise ValueError('entries must be a list')
    for entry in data['entries']:
        _fields(entry, ('entry_id', 'message'))
        entry_id = opaque_id(entry['entry_id'])
        if entry_id in session._entries:
            raise ValueError('duplicate entry identity')
        session._entries[entry_id] = _decode(entry['message'])
    session._history_ids = list(id_list(data['history_ids']))
    session._active_ids = list(id_list(data['active_ids']))
    if set(session._entries) != set(session._history_ids) | set(session._active_ids):
        raise ValueError('entry table must match history and active identity union')
    validate_checkpoint(data['checkpoint'])
    validate_overrides(data['overrides'], session._entries, session._active_ids)
    session.checkpoint = deepcopy(data['checkpoint'])
    session.overrides = deepcopy(data['overrides'])
    if data['history_archive_id'] is not None:
        opaque_id(data['history_archive_id'])
    session.history_archive_id = data['history_archive_id']
    if not isinstance(context_batches, dict):
        raise ValueError('context batches must be an object')
    context_entry_ids = set()
    for operation_id, record in context_batches.items():
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError('invalid context operation identity')
        _fields(record, ('entry_ids', 'digest', 'provenance', 'message_digests'))
        ids = id_list(record['entry_ids'])
        if not all(ordered_context_unit(projection, ids)
                   for projection in (session._active_ids, session._history_ids)):
            raise ValueError('context source unit must be complete, ordered and contiguous')
        if not ids or not isinstance(record['message_digests'], list) or len(ids) != len(record['message_digests']):
            raise ValueError('invalid context receipt entries')
        for digest in [record['digest'], *record['message_digests']]:
            if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
                raise ValueError('invalid context receipt digest')
        validate_provenance(record['provenance'])
        if record['digest'] != batch_digest(record['message_digests'], record['provenance']):
            raise ValueError('context receipt content identity mismatch')
        if context_entry_ids.intersection(ids):
            raise ValueError('duplicate context entry ownership')
        context_entry_ids.update(ids)
        for entry_id in ids:
            if entry_id in session._entries:
                canonical_batch(ContextBatch(operation_id, (session._entries[entry_id],), record['provenance']))
        for entry_id, digest in zip(ids, record['message_digests']):
            if entry_id in session._entry_batches:
                raise ValueError('duplicate context entry ownership')
            if entry_id in session._entries:
                if message_digest(_encode(session._entries[entry_id])) != digest:
                    raise ValueError('context receipt original mismatch')
                session._entry_batches[entry_id] = operation_id
        session._context_batches[operation_id] = deepcopy(record)
    validate_tool_pairs(session.history)
    validate_tool_pairs(session.active)
    validate_tool_pairs(session.working_messages())
    return session


class JsonSessionStore:
    """Atomic private snapshots. Archive availability is validated on inference."""

    def __init__(self, archive_store: FileContextArchive | None = None):
        self.archive_store = archive_store

    def save(self, session: Session, path: str | Path) -> None:
        with session._lock:
            data = {'version': 2, 'session_id': session.session_id, 'revision': session.revision,
                    'history_limit': session.history_limit,
                    'context_batches': deepcopy(session._context_batches),
                    'entries': [{'entry_id': entry_id, 'message': _encode(message)}
                                for entry_id, message in session._entries.items()],
                    'history_ids': list(session._history_ids), 'active_ids': list(session._active_ids),
                    'checkpoint': deepcopy(session.checkpoint), 'overrides': deepcopy(session.overrides),
                    'history_archive_id': session.history_archive_id,
                    'imported_history_incomplete': session.imported_history_incomplete}
        _restore(data)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2)
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix=f'.{path.name}.', delete=False) as output:
                temporary = output.name
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def load(self, path: str | Path) -> Session:
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'),
                              object_pairs_hook=_unique_object, parse_constant=invalid_constant)
            return _restore(data, self.archive_store)
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f'invalid session snapshot: {error}') from error
