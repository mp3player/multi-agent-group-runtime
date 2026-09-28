"""Strict message encoding shared by snapshots and immutable archives."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

from models import AI, Message, Reasoning, System, ToolCall, User
from models.message_codec import encode_tool_call as _encode_call


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('invalid snapshot fields')


def _text(value):
    if not isinstance(value, str):
        raise ValueError('snapshot text must be a string')
    return value


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError('tool arguments must contain only JSON values and string keys')


def _call(data):
    _fields(data, ('id', 'name', 'arguments'))
    if not _text(data['id']) or not _text(data['name']):
        raise ValueError('tool id and name must be nonempty')
    if not isinstance(data['arguments'], (dict, str)):
        raise ValueError('tool arguments must be an object or string')
    _json_value(data['arguments'])
    return ToolCall(data['id'], data['name'], data['arguments'])


def _encode(message, *, legacy=False):
    data = {'type': type(message).__name__, 'role': message.role, 'content': message.message}
    if type(message) is AI:
        data.update(reasoning=message.reasoning, tool_calls=None if message.tool_calls is None else [_encode_call(c) for c in message.tool_calls])
    elif type(message) is ToolCall:
        data.update(_encode_call(message))
    elif type(message) is Message and message.role == 'tool':
        data['tool_call_id'] = getattr(message, 'tool_call_id', None)
        if not legacy:
            data['metadata'] = {'tool_success': message.tool_success, 'ends_run': message.ends_run}
    return deepcopy(data)


def _decode(data, *, legacy=False):
    if not isinstance(data, dict):
        raise ValueError('snapshot message must be an object')
    kind = data.get('type')
    roles = {'System': 'system', 'User': 'user', 'AI': 'assistant', 'Reasoning': 'reasoning', 'ToolCall': 'assistant', 'Message': 'tool'}
    if kind not in roles or data.get('role') != roles[kind]:
        raise ValueError('invalid snapshot message type or role')
    extras = {'AI': ('reasoning', 'tool_calls'), 'ToolCall': ('id', 'name', 'arguments'), 'Message': ('tool_call_id',) if legacy else ('tool_call_id', 'metadata')}
    _fields(data, ('type', 'role', 'content', *extras.get(kind, ())))
    content = _text(data['content'])
    if kind == 'AI':
        calls = data['tool_calls']
        if calls is not None and not isinstance(calls, list):
            raise ValueError('tool_calls must be a list or null')
        return AI(content, _text(data['reasoning']), None if calls is None else [_call(c) for c in calls])
    if kind == 'ToolCall':
        if content:
            raise ValueError('standalone tool call content must be empty')
        return _call({key: data[key] for key in extras[kind]})
    if kind == 'Message':
        result = Message('tool', content)
        result.tool_call_id = _text(data['tool_call_id'])
        if not result.tool_call_id:
            raise ValueError('tool result id must be nonempty')
        if not legacy:
            metadata = data['metadata']
            _fields(metadata, ('tool_success', 'ends_run'))
            if any(type(value) is not bool for value in metadata.values()):
                raise ValueError('tool outcome metadata must be boolean')
            result.tool_success = metadata['tool_success']
            result.ends_run = metadata['ends_run']
        return result
    return {'System': System, 'User': User, 'Reasoning': Reasoning}[kind](content)



def opaque_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("invalid opaque archive/session/entry identifier")
    return value


def id_list(value):
    if not isinstance(value, list):
        raise ValueError("entry ids must be a list")
    for item in value:
        opaque_id(item)
    if len(value) != len(set(value)):
        raise ValueError("duplicate entry identity")
    return value


def validate_checkpoint(checkpoint):
    if checkpoint is None:
        return
    _fields(checkpoint, ('id', 'summary', 'archive_id', 'parent_id', 'covered_ids', 'retained_ids'))
    opaque_id(checkpoint['id'])
    opaque_id(checkpoint['archive_id'])
    if checkpoint['parent_id'] is not None:
        opaque_id(checkpoint['parent_id'])
    if not _text(checkpoint['summary']).strip():
        raise ValueError("checkpoint summary must be nonempty")
    covered = id_list(checkpoint['covered_ids'])
    retained = id_list(checkpoint['retained_ids'])
    if set(covered) & set(retained):
        raise ValueError("checkpoint covered and retained ids overlap")


def validate_overrides(overrides, entries=None, active_ids=None):
    if not isinstance(overrides, dict):
        raise ValueError("working overrides must be an object")
    for entry_id, item in overrides.items():
        opaque_id(entry_id)
        _fields(item, ('preview', 'archive_id'))
        _text(item['preview'])
        opaque_id(item['archive_id'])
        if entries is not None and (entry_id not in active_ids or entries[entry_id].role != 'tool'):
            raise ValueError("working override must reference a retained tool result")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate snapshot/archive field')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f'invalid JSON constant: {value}')


def message_digest(encoded):
    """Fingerprint the complete strict encoding without retaining its body."""
    payload = json.dumps(encoded, sort_keys=True, ensure_ascii=False,
                         allow_nan=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()
