"""Private, quota-bounded, immutable attachments for replaced session originals."""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Lock
from uuid import uuid4

from core.session_codec import (
    _decode, _fields, _text, id_list, invalid_constant, message_digest, opaque_id,
    unique_object, validate_checkpoint, validate_overrides,
)


_METADATA_CACHE_RECORD_LIMIT = 4096
_METADATA_CACHE_BYTES = 4 * 1024 * 1024


class FileContextArchive:
    """Attachments have opaque identities; published files are never overwritten."""

    def __init__(self, root: str | Path, *, quota_bytes: int = 268435456):
        if type(quota_bytes) is not int or quota_bytes <= 0:
            raise ValueError("archive quota must be a positive integer")
        try:
            import fcntl
        except ImportError as error:
            raise OSError("Context archive storage requires a POSIX filesystem with flock support") from error
        if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY')):
            raise OSError("Context archive storage requires POSIX O_NOFOLLOW/O_DIRECTORY support")
        self._fcntl = fcntl
        self.root = Path(root).absolute()
        self.quota_bytes = quota_bytes
        if self.root.is_symlink():
            raise ValueError("archive root must not be a symlink")
        missing = []
        directory = self.root
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                if directory.is_symlink() or not directory.is_dir():
                    raise
            # On failure leave the directory: another constructor may already
            # have synced and accepted it, even while it is still empty.
            self._sync_directory(directory.parent.resolve())
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("archive root must not be a symlink")
        self.root.chmod(0o700)
        # Existing ancestors can belong to an in-flight/failed constructor.
        # Sync only this canonical path's finite ancestor chain, never siblings.
        for directory in self.root.resolve().parents:
            self._sync_directory(directory)
        self._metadata_cache = OrderedDict()
        self._metadata_cache_bytes = 0
        self._metadata_lock = Lock()
        self._metadata_scans = 0

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @property
    def usage_bytes(self) -> int:
        """Count regular files; an unlocked status scan is only a point-in-time estimate."""
        total = 0
        for path in self.root.rglob('*'):
            try:
                status = path.lstat()
            except FileNotFoundError:
                # A concurrent writer can unlink its .pending file after discovery.
                continue
            if stat.S_ISREG(status.st_mode):
                total += status.st_size
        return total

    @contextmanager
    def _lock(self):
        descriptor = os.open(self.root / '.quota.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._fcntl.flock(descriptor, self._fcntl.LOCK_EX)
            yield
        finally:
            self._fcntl.flock(descriptor, self._fcntl.LOCK_UN)
            os.close(descriptor)

    def _path(self, session_id, archive_id):
        opaque_id(session_id)
        opaque_id(archive_id)
        directory = self.root / session_id
        if self.root.is_symlink() or directory.is_symlink():
            raise ValueError("archive directory must not be a symlink")
        return directory / f'{archive_id}.json'

    def write(self, session_id: str, *, entries: list[dict], reason: str,
              revision: int, checkpoint: dict | None, overrides: dict,
              parent_archive_ids: list[str]) -> str:
        """Durably publish an attachment without changing its owning Session."""
        archive_id = uuid4().hex
        path = self._path(session_id, archive_id)
        data = {'version': 1, 'session_id': session_id, 'archive_id': archive_id,
                'reason': reason, 'revision': revision, 'entries': entries,
                'checkpoint': checkpoint, 'overrides': overrides,
                'parent_archive_ids': parent_archive_ids}
        self._validate(data, session_id, archive_id)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        temporary = None
        published = False
        with self._lock():
            if self.usage_bytes + len(payload) > self.quota_bytes:
                raise OSError("context archive quota exhausted; increase archive quota or choose another store")
            path.parent.mkdir(mode=0o700, exist_ok=True)
            path.parent.chmod(0o700)
            try:
                with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.pending-', delete=False) as output:
                    temporary = Path(output.name)
                    output.write(payload)
                    output.flush()
                    os.fchmod(output.fileno(), 0o400)
                    os.fsync(output.fileno())
                # Linking publishes atomically without ever replacing an existing ID.
                os.link(temporary, path, follow_symlinks=False)
                published = True
                temporary.unlink()
                self._sync_directory(path.parent)
                # Also persist the session directory's entry, even after a
                # previous write created it but failed to complete this sync.
                self._sync_directory(self.root)
            except BaseException:
                if published:
                    path.unlink(missing_ok=True)  # this write was never returned/published to a Session
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return archive_id

    def _open(self, session_id, archive_id):
        path = self._path(session_id, archive_id)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ValueError("archive attachment is not a regular file")
        return os.fdopen(descriptor, 'r', encoding='utf-8')

    def load(self, session_id: str, archive_id: str) -> dict:
        """Decode one attachment for commit or chain validation (no tool execution)."""
        with self._open(session_id, archive_id) as source:
            before = self._fingerprint(os.fstat(source.fileno()))
            data = json.load(source, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            after = self._fingerprint(os.fstat(source.fileno()))
        if before != after:
            raise OSError('context archive changed during validation')
        self._validate(data, session_id, archive_id)
        self._remember_metadata(data, after)
        return data

    @staticmethod
    def _fingerprint(status):
        return (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns,
                status.st_ctime_ns, status.st_mode, status.st_nlink)

    @contextmanager
    def _metadata_scan(self):
        """Keep full ancestry scans from evicting metadata they will reuse."""
        with self._metadata_lock:
            self._metadata_scans += 1
        try:
            yield
        finally:
            with self._metadata_lock:
                self._metadata_scans -= 1

    def _remember_metadata(self, data, fingerprint):
        checkpoint = data['checkpoint']
        metadata = {
            'parent_archive_ids': tuple(data['parent_archive_ids']),
            'checkpoint_id': checkpoint['id'] if checkpoint else None,
            'checkpoint': ({key: deepcopy(checkpoint[key]) for key in
                            ('archive_id', 'parent_id', 'covered_ids')} if checkpoint else None),
            'entry_digests': {entry['entry_id']: message_digest(entry['message'])
                              for entry in data['entries']},
            'entry_context_digests': {entry['entry_id']: message_digest(entry['context'])
                                      for entry in data['entries'] if 'context' in entry},
        }
        # Bodies and summary text never enter this bounded cache.
        weight = len(json.dumps(metadata, separators=(',', ':')).encode('utf-8'))
        key = data['session_id'], data['archive_id']
        with self._metadata_lock:
            previous = self._metadata_cache.pop(key, None)
            if previous is not None:
                self._metadata_cache_bytes -= previous[2]
            # An ancestry walk touches every file, sometimes twice for a
            # checkpoint. Bypass admission when it would evict reusable metadata;
            # each uncached file is still decoded and validated normally.
            if self._metadata_scans and (
                    self._metadata_cache_bytes + weight > _METADATA_CACHE_BYTES or
                    len(self._metadata_cache) >= _METADATA_CACHE_RECORD_LIMIT):
                return metadata
            if weight <= _METADATA_CACHE_BYTES:
                self._metadata_cache[key] = (fingerprint, metadata, weight)
                self._metadata_cache_bytes += weight
            while (self._metadata_cache_bytes > _METADATA_CACHE_BYTES or
                   len(self._metadata_cache) > _METADATA_CACHE_RECORD_LIMIT):
                self._metadata_cache_bytes -= self._metadata_cache.popitem(last=False)[1][2]
        return metadata

    def _metadata(self, session_id: str, archive_id: str) -> dict:
        """Re-stat every attachment; decode only changed/uncached immutable files."""
        path = self._path(session_id, archive_id)
        status = path.lstat()
        if not stat.S_ISREG(status.st_mode):
            raise ValueError('archive attachment is not a regular file')
        fingerprint = self._fingerprint(status)
        key = session_id, archive_id
        with self._metadata_lock:
            cached = self._metadata_cache.get(key)
            if cached is not None and cached[0] == fingerprint:
                self._metadata_cache.move_to_end(key)
                return cached[1]
        data = self.load(session_id, archive_id)
        # Oversized metadata is deliberately not retained; return this one view.
        with self._metadata_lock:
            cached = self._metadata_cache.get(key)
            if cached is not None:
                return cached[1]
        return self._remember_metadata(data, fingerprint)

    @staticmethod
    def _validate(data, session_id, archive_id):
        _fields(data, ('version', 'session_id', 'archive_id', 'reason', 'revision',
                       'entries', 'checkpoint', 'overrides', 'parent_archive_ids'))
        if type(data['version']) is not int or data['version'] != 1:
            raise ValueError("unsupported context archive version")
        if data['session_id'] != session_id or data['archive_id'] != archive_id:
            raise ValueError("context archive identity mismatch")
        if type(data['revision']) is not int or data['revision'] < 0:
            raise ValueError("invalid context archive revision")
        _text(data['reason'])
        if not isinstance(data['entries'], list):
            raise ValueError("archive entries must be a list")
        ids = []
        for entry in data['entries']:
            _fields(entry, ('entry_id', 'message', *(['context'] if 'context' in entry else [])))
            if 'context' in entry:
                from core.context_batch import ContextBatch, canonical_batch, validate_provenance
                _fields(entry['context'], ('operation_id', 'provenance'))
                if not isinstance(entry['context']['operation_id'], str) or not entry['context']['operation_id'].strip():
                    raise ValueError('invalid context operation identity')
                validate_provenance(entry['context']['provenance'])
                canonical_batch(ContextBatch(entry['context']['operation_id'], (_decode(entry['message']),),
                                             entry['context']['provenance']))
            ids.append(opaque_id(entry['entry_id']))
            _decode(entry['message'])
        id_list(ids)
        validate_checkpoint(data['checkpoint'])
        validate_overrides(data['overrides'])
        parents = id_list(data['parent_archive_ids'])
        if archive_id in parents:
            raise ValueError("archive cannot reference itself")
        required = {item['archive_id'] for item in data['overrides'].values()}
        if data['checkpoint'] is not None:
            required.add(data['checkpoint']['archive_id'])
        if not required.issubset(parents):
            raise ValueError("archive is missing checkpoint/preview parent references")

    def read(self, session_id: str, archive_id: str, *, offset: int = 0,
             limit: int = 12000) -> dict:
        """Read a bounded character page of canonical JSON using constant memory."""
        if type(offset) is not int or offset < 0:
            raise ValueError("archive offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 12000:
            raise ValueError("archive page limit must be between 1 and 12000")
        # The identity header has fixed shape, and no arbitrary path is accepted.
        header = json.dumps({'version': 1, 'session_id': session_id, 'archive_id': archive_id}, separators=(',', ':'))[:-1] + ','
        with self._open(session_id, archive_id) as source:
            if source.read(len(header)) != header:
                raise ValueError("context archive identity/header mismatch")
            source.seek(0)
            skipped = 0
            while skipped < offset:
                chunk = source.read(min(65536, offset - skipped))
                if not chunk:
                    raise ValueError("archive offset exceeds content length")
                skipped += len(chunk)
            content = source.read(limit)
            next_character = source.read(1)
        return {'session_id': session_id, 'archive_id': archive_id, 'offset': offset,
                'content': content, 'next_offset': offset + len(content) if next_character else None}
