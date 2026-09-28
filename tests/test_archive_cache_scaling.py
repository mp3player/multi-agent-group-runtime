"""Long ancestry scans retain bounded metadata without weakening validation."""
import json
import os
from types import SimpleNamespace

import pytest

import core.context_archive as archive_module
from core.context_archive import FileContextArchive
from core.session import Session
from models import AI, System, User


def history_chain(tmp_path, count):
    session = Session(history_limit=1, archive_store=FileContextArchive(tmp_path / 'archives'))
    session.add(AI('original 0'), to_active=False)
    for index in range(1, count + 1):
        session.add(AI(f'original {index}'), to_active=False)
        session.maintain_history()
    return session


@pytest.mark.parametrize('record_limit,byte_limit,max_decodes', [
    (8, 4 * 1024 * 1024, 5),
    (4096, 1024, 9),
])
def test_full_scans_reuse_metadata_beyond_each_cache_limit(
        tmp_path, monkeypatch, record_limit, byte_limit, max_decodes):
    monkeypatch.setattr(archive_module, '_METADATA_CACHE_RECORD_LIMIT', record_limit)
    monkeypatch.setattr(archive_module, '_METADATA_CACHE_BYTES', byte_limit)
    session = history_chain(tmp_path, 12)
    store = session.archive_store
    session.validate_archives()
    decoded = []
    original_load = archive_module.json.load

    def measured(source, *args, **kwargs):
        decoded.append(source.name)
        return original_load(source, *args, **kwargs)

    monkeypatch.setattr(archive_module.json, 'load', measured)
    for _ in range(3):
        decoded.clear()
        session.validate_archives()
        assert len(decoded) <= max_decodes, 'a long scan must not flush all reusable metadata'
        assert len(store._metadata_cache) <= record_limit
        assert store._metadata_cache_bytes <= byte_limit
        assert all('original ' not in json.dumps(record[1])
                   for record in store._metadata_cache.values())

    originals = []
    for path in (store.root / session.session_id).glob('*.json'):
        data = store.load(session.session_id, path.stem)
        originals.extend(entry['message']['content'] for entry in data['entries'])
    assert sorted(originals) == sorted(f'original {index}' for index in range(12))
    assert [message.message for message in session.history] == ['original 12']
    assert len(session._entries) == 1


def test_compaction_scan_repeated_parent_checks_do_not_flush_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, '_METADATA_CACHE_RECORD_LIMIT', 8)
    session = Session(history_limit=1, archive_store=FileContextArchive(tmp_path / 'archives'))
    session.add_many([System('rules'), User('goal')])
    for index in range(12):
        session.add(AI(f'original {index}'))
        entries = session.context_entries()
        archive_id = session.archive_entries([entries[-1]], reason='summary')
        session.commit_context(expected_revision=session.revision,
                               retained_ids=[entry.entry_id for entry in entries[:-1]],
                               summary='goal', archive_id=archive_id, overrides={})
        session.maintain_history()
    session.validate_archives()
    decoded = []
    original_load = archive_module.json.load

    def measured(source, *args, **kwargs):
        decoded.append(source.name)
        return original_load(source, *args, **kwargs)

    monkeypatch.setattr(archive_module.json, 'load', measured)
    for _ in range(3):
        decoded.clear()
        session.validate_archives()
        assert len(decoded) <= 9, 'repeated checkpoint checks must not promote every scan miss'
        assert len(session.archive_store._metadata_cache) <= 8


@pytest.mark.parametrize('cached', [True, False])
@pytest.mark.parametrize('damage', ['modified', 'malformed', 'deleted'])
def test_scan_cache_still_validates_every_cached_and_uncached_file(
        tmp_path, monkeypatch, cached, damage):
    monkeypatch.setattr(archive_module, '_METADATA_CACHE_RECORD_LIMIT', 3)
    session = history_chain(tmp_path, 6)
    store = session.archive_store
    session.validate_archives()
    path = next(path for path in (store.root / session.session_id).glob('*.json')
                if ((session.session_id, path.stem) in store._metadata_cache) == cached)
    status = path.stat()
    if damage == 'deleted':
        path.unlink()
    else:
        payload = path.read_bytes()
        damaged = payload.replace(b'"version":1', b'"version":2', 1) if damage == 'modified' else b'{'
        if damage == 'modified':
            assert len(damaged) == len(payload)
        path.chmod(0o600)
        path.write_bytes(damaged)
        path.chmod(0o400)
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))
    with pytest.raises((ValueError, OSError)):
        session.validate_archives()
    # A failed walk must also release its scan scope: ordinary loads can still
    # replace cached entries afterwards, including in a full cache.
    uncached = next(candidate for candidate in (store.root / session.session_id).glob('*.json')
                    if candidate != path and (session.session_id, candidate.stem) not in store._metadata_cache)
    store.load(session.session_id, uncached.stem)
    assert (session.session_id, uncached.stem) in store._metadata_cache


def test_metadata_larger_than_cache_budget_still_validates_and_preserves_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_module, '_METADATA_CACHE_BYTES', 128)
    session = history_chain(tmp_path, 3)
    store = session.archive_store
    for _ in range(2):
        session.validate_archives()
        assert not store._metadata_cache
        assert store._metadata_cache_bytes == 0
    originals = []
    for path in (store.root / session.session_id).glob('*.json'):
        originals.extend(entry['message']['content'] for entry in
                         store.load(session.session_id, path.stem)['entries'])
    assert sorted(originals) == ['original 0', 'original 1', 'original 2']


@pytest.mark.parametrize('count', [0, 3])
def test_archive_stores_without_optional_scan_hook_still_validate(tmp_path, count):
    session = history_chain(tmp_path, count)
    store = session.archive_store
    session.archive_store = SimpleNamespace(
        _metadata=store._metadata, load=store.load, write=store.write, read=store.read)
    session.validate_archives()
    assert [message.message for message in session.history] == [f'original {count}']
    if count:
        next((store.root / session.session_id).glob('*.json')).unlink()
        with pytest.raises(FileNotFoundError):
            session.validate_archives()
