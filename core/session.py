"""Identified originals, bounded audit cache and transactional context views.

Public ``active`` and ``history`` lists remain readable. Mutating those lists or
contained messages directly is unsupported; controlled methods track revision.
"""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Callable
from uuid import uuid4

from core import defaults
from core.context_archive import FileContextArchive
from core.context_batch import ContextBatch, ContextReceipt, canonical_batch, ordered_context_unit
from core.session_codec import _encode, id_list, message_digest, validate_overrides
from models import AI, Message, Reasoning, System


def validate_tool_pairs(messages):
    # Import lazily: agent_runtime.__init__ also exposes runtime classes using Session.
    from core.agent_runtime.context import validate_tool_pairs as validate
    return validate(messages)


@dataclass(frozen=True)
class SessionEntry:
    entry_id: str
    message: Message
    operation_id: str | None = None
    provenance: dict | None = None


@dataclass(frozen=True)
class _ContextCommitCandidate:
    session_id: str
    revision: int
    retained_ids: tuple[str, ...]
    checkpoint: dict | None
    overrides: dict
    receipts: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class _HistoryMaintenanceCandidate:
    session_id: str
    revision: int
    retained_ids: tuple[str, ...]
    history_archive_id: str | None


_CONFIGURED_HISTORY_LIMIT = object()


def render_context(entries: list[SessionEntry], checkpoint: dict | None = None,
                   overrides: dict | None = None) -> list[Message]:
    """Render detached provider input; summaries remain assistant history."""
    messages = []
    for entry in entries:
        message = deepcopy(entry.message)
        if overrides and entry.entry_id in overrides:
            message.message = overrides[entry.entry_id]['preview']
        messages.append(message)
    if checkpoint is not None:
        insertion = 0
        while insertion < len(messages) and isinstance(messages[insertion], System):
            insertion += 1
        messages.insert(insertion, AI(
            f"[Historical summary; source archive {checkpoint['archive_id']}]\n{checkpoint['summary']}"))
    return messages


class Session:
    def __init__(self, history_limit: int | None = defaults.HISTORY_MESSAGE_LIMIT, *,
                 archive_store: FileContextArchive | None = None) -> None:
        if history_limit is not None and type(history_limit) is not int:
            raise ValueError('history_limit must be an integer or null')
        self.session_id = uuid4().hex
        self.revision = 0
        self.history_limit = history_limit
        self.archive_store = archive_store
        self.checkpoint: dict | None = None
        self.overrides: dict[str, dict] = {}
        self.history_archive_id: str | None = None
        self.imported_history_incomplete = False
        self._entries: dict[str, Message] = {}
        self._history_ids: list[str] = []
        self._active_ids: list[str] = []
        # Receipts exist only while their raw entries remain in the bounded working/cache union.
        self._archive_receipts: dict[str, tuple[str, str]] = {}
        self._context_batches: dict[str, dict] = {}
        self._entry_batches: dict[str, str] = {}
        self._lock = RLock()

    @property
    def history(self) -> list[Message]:
        return [self._entries[entry_id] for entry_id in self._history_ids]

    @property
    def active(self) -> list[Message]:
        return [self._entries[entry_id] for entry_id in self._active_ids]

    def add(self, message: Message, *, to_active: bool = True) -> None:
        if not isinstance(message, Message):
            raise TypeError('session entries must be Message objects')
        with self._lock:
            entry_id = uuid4().hex
            self._entries[entry_id] = message
            self._history_ids.append(entry_id)
            if to_active:
                self._active_ids.append(entry_id)
            self.revision += 1
            # Never perform storage I/O here: completed tool effects must be recorded.

    def add_many(self, messages: list[Message], *, to_active: bool = True) -> None:
        for message in messages:
            self.add(message, to_active=to_active)

    def apply_context_batch(self, batch: ContextBatch) -> ContextReceipt:
        messages, provenance, digest = canonical_batch(batch)
        with self._lock:
            previous = self._context_batches.get(batch.operation_id)
            if previous is not None:
                if previous['digest'] != digest:
                    raise ValueError('context operation identity reused with different content')
                if not self.context_batch_covered(batch.operation_id):
                    raise ValueError('context operation has no current working coverage')
                return self.context_batch_receipt(batch.operation_id)
            ids = tuple(uuid4().hex for _ in messages)
            record = {'entry_ids': list(ids), 'digest': digest, 'provenance': provenance,
                      'message_digests': [message_digest(_encode(m)) for m in messages]}
            self._entries.update(zip(ids, messages))
            self._history_ids.extend(ids)
            self._active_ids.extend(ids)
            self._entry_batches.update((entry_id, batch.operation_id) for entry_id in ids)
            self._context_batches[batch.operation_id] = record
            self.revision += 1
            return ContextReceipt(batch.operation_id, self.session_id, ids, digest)

    def context_batch_receipt(self, operation_id: str) -> ContextReceipt | None:
        with self._lock:
            record = self._context_batches.get(operation_id)
            if record is None:
                return None
            return ContextReceipt(operation_id, self.session_id, tuple(record['entry_ids']), record['digest'])

    def context_batch_covered(self, operation_id: str, *, require_raw: bool = False) -> bool:
        """Historical receipts alone cannot authorize a reset or missing working view."""
        with self._lock:
            record = self._context_batches.get(operation_id)
            if record is None:
                return False
            if not all(ordered_context_unit(ids, record['entry_ids'])
                       for ids in (self._active_ids, self._history_ids)):
                return False
            remaining = dict(zip(record['entry_ids'], record['message_digests']))
            for entry_id in self._active_ids:
                if entry_id in remaining and entry_id not in self.overrides:
                    if message_digest(_encode(self._entries[entry_id])) != remaining[entry_id]:
                        return False
                    del remaining[entry_id]
            if not remaining:
                return True
            if require_raw or self.checkpoint is None:
                return False
            self.validate_archives()
            checkpoint = self.checkpoint
            context_digest = message_digest({'operation_id': operation_id, 'provenance': record['provenance']})
            while checkpoint is not None and remaining:
                archive = self._archive_metadata(checkpoint['archive_id'])
                for entry_id in checkpoint['covered_ids']:
                    if entry_id in remaining:
                        if (archive['entry_digests'].get(entry_id) != remaining[entry_id]
                                or archive['entry_context_digests'].get(entry_id) != context_digest):
                            return False
                        del remaining[entry_id]
                checkpoint = archive['checkpoint']
            return not remaining

    def context_entries(self) -> list[SessionEntry]:
        with self._lock:
            result = []
            for entry_id in self._active_ids:
                operation_id = self._entry_batches.get(entry_id)
                provenance = (deepcopy(self._context_batches[operation_id]['provenance'])
                              if operation_id is not None else None)
                result.append(SessionEntry(entry_id, deepcopy(self._entries[entry_id]), operation_id, provenance))
            return result

    def working_messages(self) -> list[Message]:
        with self._lock:
            return render_context(self.context_entries(), self.checkpoint, self.overrides)

    def active_messages(self) -> list[Message]:
        return self.working_messages()

    def history_messages(self) -> list[Message]:
        return self.history

    def _archive_refs(self) -> list[str]:
        refs = {item['archive_id'] for item in self.overrides.values()}
        if self.checkpoint is not None:
            refs.add(self.checkpoint['archive_id'])
        if self.history_archive_id is not None:
            refs.add(self.history_archive_id)
        return sorted(refs)

    def archive_entries(self, entries: list[SessionEntry], *, reason: str) -> str:
        if self.archive_store is None:
            raise ValueError('a context archive store is required to preserve replaced originals')
        with self._lock:
            id_list([entry.entry_id for entry in entries])
            encoded = []
            for entry in entries:
                message = _encode(entry.message)
                if entry.entry_id not in self._entries or message != _encode(self._entries[entry.entry_id]):
                    raise ValueError('archive entry is not an exact current original')
                item = {'entry_id': entry.entry_id, 'message': message}
                operation_id = self._entry_batches.get(entry.entry_id)
                if operation_id is not None:
                    item['context'] = {'operation_id': operation_id,
                                       'provenance': deepcopy(self._context_batches[operation_id]['provenance'])}
                encoded.append(item)
            revision = self.revision
            checkpoint = deepcopy(self.checkpoint)
            overrides = deepcopy(self.overrides)
            parents = self._archive_refs()
        return self.archive_store.write(self.session_id, entries=encoded, reason=reason,
                                        revision=revision, checkpoint=checkpoint,
                                        overrides=overrides, parent_archive_ids=parents)

    def _load_archive(self, archive_id: str) -> dict:
        if self.archive_store is None:
            raise ValueError('required context archive store is unavailable')
        return self.archive_store.load(self.session_id, archive_id)

    def _archive_metadata(self, archive_id: str) -> dict:
        if self.archive_store is None:
            raise ValueError('required context archive store is unavailable')
        return self.archive_store._metadata(self.session_id, archive_id)

    def validate_archives(self) -> None:
        """Check the complete referenced chain before resuming inference."""
        with self._lock:
            refs = self._archive_refs()
            checkpoint = deepcopy(self.checkpoint)
            previews = [(entry_id, item['archive_id'], message_digest(_encode(self._entries[entry_id])))
                        for entry_id, item in self.overrides.items()]
        self._validate_archive_chain(refs)
        self._validate_checkpoint_archive(checkpoint)
        # Every active preview must still point at its complete original body.
        for entry_id, archive_id, digest in previews:
            records = self._archive_metadata(archive_id)['entry_digests']
            if records.get(entry_id) != digest:
                raise ValueError('preview archive does not preserve its original body')

    def _validate_checkpoint_archive(self, checkpoint):
        if checkpoint is None:
            return
        archive = self._archive_metadata(checkpoint['archive_id'])
        covered = archive['entry_digests']
        if not set(checkpoint['covered_ids']).issubset(covered):
            raise ValueError('checkpoint coverage is missing archived originals')
        parent_id = archive['checkpoint_id']
        if checkpoint['parent_id'] != parent_id:
            raise ValueError('checkpoint archive parent identity mismatch')

    def _validate_archive_chain(self, roots):
        pending = [(archive_id, False) for archive_id in roots]
        states = {}
        with getattr(self.archive_store, '_metadata_scan', nullcontext)():
            while pending:
                archive_id, exiting = pending.pop()
                if exiting:
                    states[archive_id] = 2
                    continue
                if states.get(archive_id) == 1:
                    raise ValueError('context archive reference cycle')
                if states.get(archive_id) == 2:
                    continue
                data = self._archive_metadata(archive_id)
                self._validate_checkpoint_archive(data['checkpoint'])
                states[archive_id] = 1
                pending.append((archive_id, True))
                pending.extend((parent_id, False) for parent_id in data['parent_archive_ids'])

    def commit_context(self, *, expected_revision: int, retained_ids: list[str],
                       summary: str | None, archive_id: str,
                       overrides: dict[str, dict],
                       before_publish: Callable[[], None] | None = None) -> dict:
        """Synchronous compatibility wrapper for detached preparation/publication."""
        candidate = self.prepare_context_commit(expected_revision=expected_revision,
                    retained_ids=retained_ids, summary=summary,
                    archive_id=archive_id, overrides=overrides)
        return self.publish_context_commit(candidate, before_publish=before_publish)

    def prepare_context_commit(self, *, expected_revision: int, retained_ids: list[str],
                               summary: str | None, archive_id: str,
                               overrides: dict[str, dict]) -> _ContextCommitCandidate:
        """Worker-safe validation; no canonical Session state or receipts change."""
        with self._lock:
            if type(expected_revision) is not int or expected_revision != self.revision:
                raise ValueError('stale context candidate revision')
            session_id = self.session_id
            source_ids = list(self._active_ids)
            entries = {i: deepcopy(self._entries[i]) for i in source_ids}
            prior_checkpoint = deepcopy(self.checkpoint)
            source_context = {i: {'operation_id': self._entry_batches[i],
                                  'provenance': deepcopy(self._context_batches[self._entry_batches[i]]['provenance'])}
                              for i in source_ids if i in self._entry_batches}
        retained_ids = list(id_list(retained_ids))
        overrides = deepcopy(overrides)
        retained = set(retained_ids)
        if [i for i in source_ids if i in retained] != retained_ids:
            raise ValueError('retained ids must be an ordered subset of current context')
        removed = [i for i in source_ids if i not in retained]
        protected = {i for i in source_ids if isinstance(entries[i], System)}
        latest_user = next((i for i in reversed(source_ids) if entries[i].role == 'user'), None)
        if latest_user:
            protected.add(latest_user)
            latest_operation = self._entry_batches.get(latest_user)
            if latest_operation is not None:
                protected.update(i for i in source_ids if self._entry_batches.get(i) == latest_operation)
        for record in self._context_batches.values():
            active_unit = set(record['entry_ids']) & set(source_ids)
            if active_unit & retained and not active_unit <= retained:
                raise ValueError('context must preserve complete identified source units')
        if not protected.issubset(retained):
            raise ValueError('context must retain system instructions and latest user request')
        if summary is not None and (not isinstance(summary, str) or not summary.strip()):
            raise ValueError('context summary must be nonempty text')
        if removed and summary is None:
            raise ValueError('removing context originals requires an archived summary')
        validate_overrides(overrides, entries, retained_ids)
        self._validate_archive_chain([archive_id, *[item['archive_id'] for item in overrides.values()]])
        archive = self._load_archive(archive_id)
        if archive['revision'] != expected_revision or archive['checkpoint'] != prior_checkpoint:
            raise ValueError('archive is from a stale context revision')
        archived = {item['entry_id']: item['message'] for item in archive['entries']}
        archived_context = {item['entry_id']: item.get('context') for item in archive['entries']}
        for entry_id in removed:
            if archived.get(entry_id) != _encode(entries[entry_id]):
                raise ValueError('removed context original is not preserved in archive')
            if entry_id in source_context and archived_context.get(entry_id) != source_context[entry_id]:
                raise ValueError('removed context provenance is not preserved in archive')
        for entry_id, override in overrides.items():
            referenced = archive if override['archive_id'] == archive_id else self._load_archive(override['archive_id'])
            records = {item['entry_id']: item['message'] for item in referenced['entries']}
            if records.get(entry_id) != _encode(entries[entry_id]):
                raise ValueError('preview original is not preserved in archive')
        checkpoint = prior_checkpoint
        if summary is not None:
            checkpoint = {'id': uuid4().hex, 'summary': summary, 'archive_id': archive_id,
                          'parent_id': prior_checkpoint['id'] if prior_checkpoint else None,
                          'covered_ids': removed, 'retained_ids': list(retained_ids)}
        messages = render_context([SessionEntry(i, entries[i]) for i in retained_ids], checkpoint, overrides)
        validate_tool_pairs(messages)
        receipts = ({entry_id: (archive_id, message_digest(body))
                     for entry_id, body in archived.items() if entry_id in entries}
                    if summary is not None else {})
        return _ContextCommitCandidate(session_id, expected_revision, tuple(retained_ids),
                                       checkpoint, overrides, receipts)

    def publish_context_commit(self, candidate: _ContextCommitCandidate, *,
                               before_publish: Callable[[], None] | None = None) -> dict:
        """Publish a prepared view without I/O, under a final ownership check."""
        with self._lock:
            if before_publish is not None:
                before_publish()
            if self.session_id != candidate.session_id or self.revision != candidate.revision:
                raise ValueError('stale context candidate revision')
            self._active_ids = list(candidate.retained_ids)
            self.checkpoint = deepcopy(candidate.checkpoint)
            self.overrides = deepcopy(candidate.overrides)
            self._archive_receipts.update(candidate.receipts)
            self.revision += 1
            self._release_unreferenced_entries()
            return deepcopy(self.checkpoint) if self.checkpoint is not None else {}

    def _release_unreferenced_entries(self) -> None:
        referenced = set(self._history_ids) | set(self._active_ids)
        self._entries = {i: m for i, m in self._entries.items() if i in referenced}
        self._entry_batches = {i: op for i, op in self._entry_batches.items() if i in referenced}
        self._archive_receipts = {i: proof for i, proof in self._archive_receipts.items() if i in referenced}

    def maintain_history(self) -> None:
        candidate = self.prepare_history_maintenance()
        self.publish_history_maintenance(candidate)

    def prune_history(self, max_messages: int | None, *, keep_system: bool = True) -> None:
        """Synchronous compatibility wrapper; never discard the last raw copy."""
        candidate = self.prepare_history_maintenance(max_messages, keep_system=keep_system)
        self.publish_history_maintenance(candidate)

    def prepare_history_maintenance(self, max_messages=_CONFIGURED_HISTORY_LIMIT, *,
                                    keep_system: bool = True) -> _HistoryMaintenanceCandidate | None:
        """Prepare cache eviction off-thread; completed I/O never publishes itself."""
        with self._lock:
            if max_messages is _CONFIGURED_HISTORY_LIMIT:
                max_messages = self.history_limit
            if max_messages is None or max_messages <= 0 or len(self._history_ids) <= max_messages:
                return None
            session_id, revision = self.session_id, self.revision
            history_ids = list(self._history_ids)
            active_ids = set(self._active_ids)
            entries = {i: deepcopy(self._entries[i]) for i in history_ids}
            receipts = dict(self._archive_receipts)
            unit_starts = {i: self._context_batches[operation]['entry_ids'][0]
                           for i, operation in self._entry_batches.items()}
            refs = self._archive_refs()
            history_archive_id = self.history_archive_id
        try:
            validate_tool_pairs([entries[i] for i in history_ids])
        except ValueError:
            # Incremental assistant/results commits can temporarily exceed the target.
            return None
        system = next((i for i in history_ids if isinstance(entries[i], System)), None) if keep_system else None
        rest = [i for i in history_ids if i != system]
        count = max(0, max_messages - (system is not None))
        suffix = rest[-count:] if count else []
        while suffix and (entries[suffix[0]].role == 'tool' or isinstance(entries[suffix[0]], Reasoning)
                          or suffix[0] != unit_starts.get(suffix[0], suffix[0])):
            suffix.pop(0)
        retained = ([system] if system else []) + suffix
        retained_set = set(retained)
        removed = [i for i in history_ids if i not in retained_set]
        exclusive = [i for i in removed if i not in active_ids]
        if exclusive and receipts:
            self._validate_archive_chain(refs)
            unarchived = []
            for entry_id in exclusive:
                receipt = receipts.get(entry_id)
                digest = message_digest(_encode(entries[entry_id]))
                if (receipt is None or receipt[1] != digest or
                        self._archive_metadata(receipt[0])['entry_digests'].get(entry_id) != digest):
                    unarchived.append(entry_id)
            exclusive = unarchived
        if exclusive:
            history_archive_id = self.archive_entries(
                [SessionEntry(i, entries[i]) for i in exclusive], reason='history-cache')
        return _HistoryMaintenanceCandidate(session_id, revision, tuple(retained), history_archive_id)

    def publish_history_maintenance(self, candidate: _HistoryMaintenanceCandidate | None, *,
                                    before_publish: Callable[[], None] | None = None) -> None:
        """Commit a prepared cache update without filesystem work."""
        if candidate is None:
            return
        with self._lock:
            if before_publish is not None:
                before_publish()
            if self.session_id != candidate.session_id or self.revision != candidate.revision:
                raise ValueError('stale history maintenance candidate revision')
            self._history_ids = list(candidate.retained_ids)
            self.history_archive_id = candidate.history_archive_id
            self.revision += 1
            self._release_unreferenced_entries()

    def prune_active(self, max_messages: int | None, *, keep_system: bool = True) -> None:
        """Legacy compatibility: active messages are never discarded by count."""

    def clear_active(self) -> None:
        """Explicit user operation; active view clears but audit cache stays intact."""
        with self._lock:
            self._active_ids = []
            self.checkpoint = None
            self.overrides = {}
            self._archive_receipts = {}
            self.revision += 1
            self._release_unreferenced_entries()

    def reset_active_from_history(self) -> None:
        """Reset the projection from its checkpoint and retained originals."""
        with self._lock:
            self.revision += 1

    def replace_system(self, text: str | None) -> None:
        if text is not None and not isinstance(text, str):
            raise ValueError('system instructions must be text or null')
        with self._lock:
            for entry_id in self._active_ids:
                if isinstance(self._entries[entry_id], System) and entry_id not in self._history_ids:
                    self._history_ids.append(entry_id)
            self._active_ids = [i for i in self._active_ids if not isinstance(self._entries[i], System)]
            if text:
                entry_id = uuid4().hex
                self._entries[entry_id] = System(text)
                self._active_ids.insert(0, entry_id)
                self._history_ids.append(entry_id)
            self.revision += 1
            self._release_unreferenced_entries()

    def reset_to_system(self) -> None:
        """Explicit user reset; never used to recover context overflow."""
        with self._lock:
            self._active_ids = [i for i in self._active_ids if isinstance(self._entries[i], System)][:1]
            self.checkpoint = None
            self.overrides = {}
            self._archive_receipts = {}
            self.revision += 1
            self._release_unreferenced_entries()

    def __len__(self) -> int:
        return len(self._history_ids)

    def __repr__(self) -> str:
        return f'Session(history={len(self._history_ids)}, active={len(self._active_ids)}, revision={self.revision})'
