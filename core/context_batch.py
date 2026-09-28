"""Identified, non-inference context application with detached JSON provenance."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

from models import AI, Message, User
from core.session_codec import _decode, _encode, _json_value, message_digest


@dataclass(frozen=True)
class ContextBatch:
    operation_id: str
    messages: tuple[Message, ...]
    provenance: dict


@dataclass(frozen=True)
class ContextReceipt:
    operation_id: str
    session_id: str
    entry_ids: tuple[str, ...]
    digest: str


def validate_provenance(value):
    if (type(value) is not dict or not isinstance(value.get('origin'), str)
            or not value['origin'].strip()):
        raise ValueError('context provenance needs a nonempty origin')
    _json_value(value)


def canonical_batch(batch):
    """Validate the entire operation before publishing any Session mutation."""
    if not isinstance(batch, ContextBatch):
        raise TypeError('context batch must be ContextBatch')
    if not isinstance(batch.operation_id, str) or not batch.operation_id.strip():
        raise ValueError('context operation identity must be nonempty')
    if not isinstance(batch.messages, tuple) or not batch.messages:
        raise ValueError('context batch messages must be a nonempty tuple')
    provenance = deepcopy(batch.provenance)
    validate_provenance(provenance)
    messages = []
    for message in batch.messages:
        if (type(message) not in (User, AI) or getattr(message, 'tool_calls', None)
                or getattr(message, 'reasoning', None)):
            raise ValueError('context messages must be user or synthetic assistant text without tools or reasoning')
        messages.append(_decode(_encode(message)))
    encoded = [_encode(message) for message in messages]
    digest = batch_digest([message_digest(item) for item in encoded], provenance)
    return tuple(messages), provenance, digest


def batch_digest(message_digests, provenance):
    """Content identity remains verifiable after message bodies leave the cache."""
    return message_digest({'message_digests': message_digests, 'provenance': provenance})


def ordered_context_unit(projection_ids, entry_ids):
    """A raw unit is either wholly absent or contiguous in canonical order."""
    wanted = set(entry_ids)
    positions = [index for index, entry_id in enumerate(projection_ids) if entry_id in wanted]
    if not positions:
        return True
    start = positions[0]
    return (len(positions) == len(entry_ids)
            and list(projection_ids[start:start + len(entry_ids)]) == list(entry_ids))
