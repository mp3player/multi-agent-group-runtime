"""Original messages remain recoverable across context-view replacement."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading

import pytest

from core.session import Session
from core.session_store import JsonSessionStore
from models import AI, Message, System, ToolCall, User


def archive_type():
    from core.context_archive import FileContextArchive
    return FileContextArchive


def result(text="full original observation"):
    message = Message("tool", text)
    message.tool_call_id = "work-1"
    message.tool_success = True
    message.ends_run = True
    return message


def populated(tmp_path, **kwargs):
    session = Session(archive_store=archive_type()(tmp_path / "archives", **kwargs))
    session.add_many([System("rules"), User("original goal"), AI("earlier step"), AI("recent")])
    return session


def compact(session, summary="Goal: preserve original goal"):
    entries = session.context_entries()
    archive_id = session.archive_entries([entries[2]], reason="summary")
    session.commit_context(expected_revision=session.revision,
                           retained_ids=[e.entry_id for e in entries if e is not entries[2]],
                           summary=summary, archive_id=archive_id, overrides={})
    return archive_id


def all_text(store, session_id, archive_id):
    chunks = []
    offset = 0
    while offset is not None:
        page = store.read(session_id, archive_id, offset=offset, limit=17)
        chunks.append(page["content"])
        offset = page["next_offset"]
    return json.loads("".join(chunks))


def test_failed_archive_preserves_originals_and_revision(tmp_path):
    session = populated(tmp_path, quota_bytes=1)
    before = session.context_entries()
    revision = session.revision
    with pytest.raises((OSError, ValueError), match="quota"):
        session.archive_entries(before[2:3], reason="summary")
    assert session.revision == revision
    assert [e.entry_id for e in session.context_entries()] == [e.entry_id for e in before]
    assert [m.message for m in session.history] == ["rules", "original goal", "earlier step", "recent"]
    assert not list((tmp_path / "archives").rglob("*.json"))


def test_stale_commit_and_unarchived_replacement_never_publish(tmp_path):
    session = populated(tmp_path)
    entries = session.context_entries()
    revision = session.revision
    archive_id = session.archive_entries(entries[2:3], reason="summary")
    session.add(AI("newly completed side effect"))
    with pytest.raises(ValueError, match="revision"):
        session.commit_context(expected_revision=revision, retained_ids=[e.entry_id for e in entries[:2]],
                               summary="summary", archive_id=archive_id, overrides={})
    with pytest.raises(ValueError, match="archiv"):
        session.commit_context(expected_revision=session.revision,
                               retained_ids=[e.entry_id for e in entries[:2]],
                               summary="summary", archive_id=archive_id, overrides={})
    assert session.checkpoint is None
    assert session.active[-1].message == "newly completed side effect"


def test_detached_entries_and_working_projection_preserve_originals(tmp_path):
    session = populated(tmp_path)
    detached = session.context_entries()
    detached[2].message.message = "mutation"
    assert session.active[2].message == "earlier step"
    archive_id = compact(session)
    projected = session.working_messages()
    assert [m.role for m in projected] == ["system", "assistant", "user", "assistant"]
    assert "preserve original goal" in projected[1].message
    assert archive_id in projected[1].message
    projected[-1].message = "mutation"
    assert session.active[-1].message == "recent"


def test_repeated_compaction_snapshot_and_archive_chain(tmp_path):
    session = populated(tmp_path)
    first = compact(session)
    first_checkpoint = session.checkpoint["id"]
    session.add(AI("newest"))
    second = compact(session, "Goal: still preserve original goal")
    assert session.checkpoint["parent_id"] == first_checkpoint
    assert len(session.checkpoint["covered_ids"]) == 1
    store = JsonSessionStore(archive_store=session.archive_store)
    path = tmp_path / "snapshot.json"
    store.save(session, path)
    restored = store.load(path)
    restored.validate_archives()
    assert restored.session_id == session.session_id
    assert restored.revision == session.revision
    assert [m.message for m in restored.active] == ["rules", "original goal", "newest"]
    old = all_text(restored.archive_store, restored.session_id, first)
    new = all_text(restored.archive_store, restored.session_id, second)
    assert old["entries"][0]["message"]["content"] == "earlier step"
    assert new["entries"][0]["message"]["content"] == "recent"
    assert first in new["parent_archive_ids"]
    assert new["checkpoint"]["summary"] == "Goal: preserve original goal"
    assert path.stat().st_mode & 0o777 == 0o600


def test_missing_parent_attachment_allows_inspection_but_blocks_validation(tmp_path):
    session = populated(tmp_path)
    first = compact(session)
    session.add(AI("newest"))
    compact(session)
    store = JsonSessionStore(archive_store=session.archive_store)
    path = tmp_path / "snapshot.json"
    store.save(session, path)
    (tmp_path / "archives" / session.session_id / f"{first}.json").unlink()
    restored = store.load(path)
    assert restored.checkpoint["summary"] == "Goal: preserve original goal"
    with pytest.raises((ValueError, OSError), match="archive|No such"):
        restored.validate_archives()
    with pytest.raises(ValueError, match="archive"):
        JsonSessionStore().load(path).validate_archives()


def test_preview_requires_archived_exact_body_and_roundtrips_tool_outcome(tmp_path):
    session = populated(tmp_path)
    session.add_many([AI(tool_calls=[ToolCall("work-1", "work", {"path": "x"})]), result()])
    entries = session.context_entries()
    target = entries[-1]
    wrong = session.archive_entries(entries[2:3], reason="preview")
    with pytest.raises(ValueError, match="archiv"):
        session.commit_context(expected_revision=session.revision,
                               retained_ids=[e.entry_id for e in entries], summary=None,
                               archive_id=wrong,
                               overrides={target.entry_id: {"preview": "short", "archive_id": wrong}})
    archive_id = session.archive_entries([target], reason="preview")
    session.commit_context(expected_revision=session.revision,
                           retained_ids=[e.entry_id for e in entries], summary=None,
                           archive_id=archive_id,
                           overrides={target.entry_id: {"preview": "short", "archive_id": archive_id}})
    assert session.active[-1].message == "full original observation"
    assert session.working_messages()[-1].message == "short"
    path = tmp_path / "preview.json"
    store = JsonSessionStore(archive_store=session.archive_store)
    store.save(session, path)
    restored = store.load(path)
    restored.validate_archives()
    assert restored.working_messages()[-1].message == "short"
    assert restored.active[-1].tool_success is True
    assert restored.active[-1].ends_run is True


def test_history_cleanup_is_deferred_and_never_evicts_last_raw_copy(tmp_path):
    session = Session(history_limit=1)
    session.add(User("audit only"), to_active=False)
    session.add(User("latest"))
    assert len(session.history) == 2
    with pytest.raises(ValueError, match="archive"):
        session.maintain_history()
    assert [m.message for m in session.history] == ["audit only", "latest"]
    session.archive_store = archive_type()(tmp_path / "archives")
    session.maintain_history()
    assert [m.message for m in session.history] == ["latest"]
    path = tmp_path / "history.json"
    store = JsonSessionStore(archive_store=session.archive_store)
    store.save(session, path)
    restored = store.load(path)
    payload = all_text(restored.archive_store, restored.session_id, restored.history_archive_id)
    assert payload["entries"][0]["message"]["content"] == "audit only"
    assert len(json.loads(path.read_text())["entries"]) == 1


def test_active_raw_copy_permits_cache_eviction_without_archive():
    session = Session(history_limit=1)
    session.add_many([User("early"), AI("later")])
    session.maintain_history()
    assert [m.message for m in session.history] == ["later"]
    assert [m.message for m in session.active] == ["early", "later"]


def test_history_cleanup_waits_for_complete_tool_batch():
    session = Session(history_limit=1)
    session.add_many([User("goal"), AI(tool_calls=[ToolCall("work-1", "work", {})])])
    session.maintain_history()
    assert len(session.history) == 2
    session.add(result())
    session.maintain_history()
    assert session.history == []  # complete batch remains in active; no orphan result cache
    assert len(session.active) == 3


def test_archive_is_private_bounded_and_rejects_path_or_session_escape(tmp_path):
    session = populated(tmp_path)
    archive_id = compact(session)
    path = tmp_path / "archives" / session.session_id / f"{archive_id}.json"
    assert path.stat().st_mode & 0o777 == 0o400
    assert path.parent.stat().st_mode & 0o777 == 0o700
    page = session.archive_store.read(session.session_id, archive_id, limit=5)
    assert len(page["content"]) == 5
    assert page["next_offset"] == 5
    for bad in ("../escape", "", "/etc/passwd"):
        with pytest.raises(ValueError):
            session.archive_store.read(session.session_id, bad)
    for params in ({"offset": -1}, {"limit": 12001}, {"limit": 0}, {"limit": True}):
        with pytest.raises(ValueError):
            session.archive_store.read(session.session_id, archive_id, **params)
    with pytest.raises((ValueError, OSError)):
        session.archive_store.read("a" * 32, archive_id)


def test_existing_temporaries_count_toward_archive_quota(tmp_path):
    archive = archive_type()(tmp_path / "archives", quota_bytes=3000)
    (tmp_path / "archives" / ".pending").write_bytes(b"x" * 2999)
    session = Session(archive_store=archive)
    session.add(User("cannot replace"))
    with pytest.raises((ValueError, OSError), match="quota"):
        session.archive_entries(session.context_entries(), reason="test")
    assert session.active[0].message == "cannot replace"


@pytest.mark.parametrize(("disappears", "expected"), [("before_stat", 7), ("after_stat", 13)])
def test_usage_scan_tolerates_disappearing_pending_file(tmp_path, monkeypatch, disappears, expected):
    archive = archive_type()(tmp_path / "archives")
    (archive.root / "published.json").write_bytes(b"1234567")
    pending = archive.root / ".pending-example"
    pending.write_bytes(b"123456")
    original_lstat = Path.lstat

    def disappearing_lstat(path, *args, **kwargs):
        if path == pending:
            if disappears == "before_stat":
                pending.unlink(missing_ok=True)
                return original_lstat(path, *args, **kwargs)
            status = original_lstat(path, *args, **kwargs)
            pending.unlink()
            return status
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappearing_lstat)
    assert archive.usage_bytes == expected
    assert not pending.exists()


def test_v1_ambiguous_equal_text_gets_distinct_imported_ids(tmp_path):
    path = tmp_path / "old.json"
    message = {"type": "User", "role": "user", "content": "same"}
    path.write_text(json.dumps({"version": 1, "history_limit": 3,
                                "history": [message, message], "active": [message]}))
    migrated = JsonSessionStore().load(path)
    JsonSessionStore().save(migrated, path)
    data = json.loads(path.read_text())
    assert data["version"] == 2
    assert data["imported_history_incomplete"] is True
    assert len(set(data["history_ids"] + data["active_ids"])) == 3


def test_v2_rejects_duplicate_identity_and_invalid_working_override(tmp_path):
    session = populated(tmp_path)
    path = tmp_path / "snapshot.json"
    JsonSessionStore().save(session, path)
    original = json.loads(path.read_text())
    original["entries"][1]["entry_id"] = original["entries"][0]["entry_id"]
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError):
        JsonSessionStore().load(path)
    JsonSessionStore().save(session, path)
    original = json.loads(path.read_text())
    original["overrides"] = {original["active_ids"][1]: {"preview": "changed goal", "archive_id": "b" * 32}}
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError):
        JsonSessionStore().load(path)


def test_explicit_reset_does_not_restore_compacted_history(tmp_path):
    session = populated(tmp_path)
    compact(session)
    before = [m.message for m in session.working_messages()]
    revision = session.revision
    session.reset_active_from_history()
    assert [m.message for m in session.working_messages()] == before
    assert session.revision > revision
    session.replace_system("new rules")
    assert session.active[0].message == "new rules"
    assert session.history[0].message == "rules"
    session.reset_to_system()
    assert [m.message for m in session.active] == ["new rules"]
    assert session.checkpoint is None


def test_commit_rejects_missing_ancestor_without_replacing_current_view(tmp_path):
    session = populated(tmp_path)
    first = compact(session)
    session.add(AI("newest"))
    entries = session.context_entries()
    second = session.archive_entries(entries[2:3], reason="summary")
    (tmp_path / "archives" / session.session_id / f"{first}.json").unlink()
    with pytest.raises((ValueError, OSError), match="archive|No such"):
        session.commit_context(expected_revision=session.revision,
                               retained_ids=[e.entry_id for e in entries if e is not entries[2]],
                               summary="replacement", archive_id=second, overrides={})
    assert session.checkpoint["archive_id"] == first
    assert session.active[2].message == "recent"


def test_referenced_archive_cycle_is_rejected(tmp_path):
    session = populated(tmp_path)
    first = compact(session)
    session.add(AI("newest"))
    second = compact(session)
    path = tmp_path / "archives" / session.session_id / f"{first}.json"
    data = json.loads(path.read_text())
    data["parent_archive_ids"] = [second]
    path.chmod(0o600)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="cycle"):
        session.validate_archives()


def test_snapshot_checkpoint_cannot_claim_unarchived_coverage(tmp_path):
    session = populated(tmp_path)
    compact(session)
    path = tmp_path / "snapshot.json"
    store = JsonSessionStore(archive_store=session.archive_store)
    store.save(session, path)
    data = json.loads(path.read_text())
    data["checkpoint"]["covered_ids"] = ["a" * 32]
    path.write_text(json.dumps(data))
    restored = store.load(path)
    with pytest.raises(ValueError, match="cover|archiv"):
        restored.validate_archives()


def test_preview_only_commit_preserves_previous_summary_and_archive_chain(tmp_path):
    session = populated(tmp_path)
    compact(session)
    prior = session.checkpoint.copy()
    session.add_many([AI(tool_calls=[ToolCall("work-1", "work", {})]), result()])
    entries = session.context_entries()
    archive_id = session.archive_entries(entries[-1:], reason="preview")
    session.commit_context(expected_revision=session.revision, retained_ids=[e.entry_id for e in entries],
                           summary=None, archive_id=archive_id,
                           overrides={entries[-1].entry_id: {"preview": "short", "archive_id": archive_id}})
    assert session.checkpoint == prior
    session.validate_archives()


def test_archive_write_failure_removes_temporary_and_keeps_session(tmp_path, monkeypatch):
    session = populated(tmp_path)
    before = session.revision
    def fail(descriptor):
        raise OSError("disk failed")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk failed"):
        compact(session)
    assert session.revision == before
    assert session.checkpoint is None
    assert not list((tmp_path / "archives").rglob("*.json"))
    assert not list((tmp_path / "archives").rglob(".pending-*"))


def test_archive_publication_syncs_new_session_directory_entry(tmp_path, monkeypatch):
    session = populated(tmp_path)
    root = session.archive_store.root
    synced = []
    original_fsync = os.fsync

    def trace_fsync(descriptor):
        status = os.fstat(descriptor)
        synced.append((status.st_dev, status.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", trace_fsync)
    archive_id = compact(session)
    for directory in (root, root / session.session_id):
        status = directory.stat()
        assert (status.st_dev, status.st_ino) in synced
    assert session.checkpoint["archive_id"] == archive_id
    assert all_text(session.archive_store, session.session_id, archive_id)["entries"][0]["message"]["content"] == "earlier step"


def test_archive_constructor_syncs_new_private_ancestors(tmp_path, monkeypatch):
    tmp_path.chmod(0o755)
    root = tmp_path / "private" / "nested" / "archives"
    synced = []
    original_fsync = os.fsync

    def trace_fsync(descriptor):
        status = os.fstat(descriptor)
        synced.append((status.st_dev, status.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", trace_fsync)
    archive_type()(root)
    for directory in (tmp_path, tmp_path / "private", root.parent):
        status = directory.stat()
        assert (status.st_dev, status.st_ino) in synced
    for directory in (tmp_path / "private", root.parent, root):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o755


def test_archive_constructor_syncs_existing_ancestors_without_changing_permissions(tmp_path, monkeypatch):
    root = tmp_path / "private" / "nested" / "archives"
    root.mkdir(parents=True)
    ancestors = (tmp_path, tmp_path / "private", root.parent)
    for directory in ancestors:
        directory.chmod(0o755)
    synced = []
    original_fsync = os.fsync

    def trace_fsync(descriptor):
        status = os.fstat(descriptor)
        synced.append((status.st_dev, status.st_ino))
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", trace_fsync)
    # An existing ancestor may have just been created by another constructor,
    # or remain after that constructor's parent-directory fsync failed.
    archive_type()(root)
    for directory in ancestors:
        status = directory.stat()
        assert (status.st_dev, status.st_ino) in synced
        assert stat.S_IMODE(status.st_mode) == 0o755


def test_archive_root_sync_failure_keeps_originals_and_retries_barrier(tmp_path, monkeypatch):
    session = populated(tmp_path)
    root = session.archive_store.root
    root_status = root.stat()
    revision = session.revision
    original_fsync = os.fsync
    root_syncs = []

    def fail_root_sync(descriptor):
        status = os.fstat(descriptor)
        if (status.st_dev, status.st_ino) == (root_status.st_dev, root_status.st_ino):
            root_syncs.append(True)
            if len(root_syncs) == 1:
                raise OSError("archive root sync failed")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_root_sync)
    with pytest.raises(OSError, match="archive root sync failed"):
        compact(session)
    assert session.revision == revision
    assert session.checkpoint is None
    assert session.active[2].message == "earlier step"
    assert not list(root.rglob("*.json"))
    assert not list(root.rglob(".pending-*"))

    archive_id = compact(session)
    assert len(root_syncs) == 2
    assert session.checkpoint["archive_id"] == archive_id


def test_archive_constructor_sync_failure_leaves_directory_and_retry_syncs_ancestors(tmp_path, monkeypatch):
    root = tmp_path / "private" / "nested" / "archives"
    parent_status = tmp_path.stat()
    original_fsync = os.fsync
    failure = OSError("archive ancestor sync failed")
    parent_syncs = []

    def fail_parent_sync(descriptor):
        status = os.fstat(descriptor)
        if (status.st_dev, status.st_ino) == (parent_status.st_dev, parent_status.st_ino):
            parent_syncs.append(True)
            if len(parent_syncs) == 1:
                raise failure
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_parent_sync)
    store = None
    with pytest.raises(OSError, match="archive ancestor sync failed") as caught:
        store = archive_type()(root)
    assert caught.value is failure
    assert store is None
    assert (tmp_path / "private").is_dir()
    assert not list((tmp_path / "private").iterdir())

    store = archive_type()(root)
    assert store.root == root
    assert len(parent_syncs) == 2


def test_failed_constructor_does_not_remove_another_constructors_usable_root(tmp_path, monkeypatch):
    root = tmp_path / "archives"
    parent_status = tmp_path.stat()
    original_fsync = os.fsync
    first_ready = threading.Event()
    release_first = threading.Event()
    failure = OSError("first constructor sync failed")
    returned_stores = []
    errors = []

    def controlled_fsync(descriptor):
        status = os.fstat(descriptor)
        if (threading.current_thread() is first_constructor and
                (status.st_dev, status.st_ino) == (parent_status.st_dev, parent_status.st_ino)):
            first_ready.set()
            assert release_first.wait(5), "second constructor did not release the first"
            raise failure
        original_fsync(descriptor)

    def construct_first():
        try:
            returned_stores.append(archive_type()(root))
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(os, "fsync", controlled_fsync)
    first_constructor = threading.Thread(target=construct_first, daemon=True)
    first_constructor.start()
    try:
        assert first_ready.wait(5), "first constructor did not reach its parent sync"
        second_store = archive_type()(root)
    finally:
        release_first.set()
        first_constructor.join(5)
    assert not first_constructor.is_alive()
    assert returned_stores == []
    assert errors == [failure]

    session = Session(archive_store=second_store)
    session.add(User("preserve this original"))
    archive_id = session.archive_entries(session.context_entries(), reason="test")
    archived = all_text(second_store, session.session_id, archive_id)
    assert archived["entries"][0]["message"]["content"] == "preserve this original"


def test_replacing_system_retains_last_raw_copy_after_cache_eviction():
    session = Session(history_limit=1)
    session.add_many([System("old instructions"), User("goal")])
    session.prune_history(1, keep_system=False)
    assert [m.message for m in session.history] == ["goal"]
    session.replace_system("new instructions")
    assert any(m.message == "old instructions" for m in session.history)


def test_before_publish_check_can_cancel_after_archive_validation(tmp_path):
    session = populated(tmp_path)
    entries = session.context_entries()
    archive_id = session.archive_entries(entries[2:3], reason="summary")
    revision = session.revision
    def cancelled():
        raise TimeoutError("owner cancelled after storage completed")
    with pytest.raises(TimeoutError, match="owner cancelled"):
        session.commit_context(expected_revision=revision,
                               retained_ids=[e.entry_id for e in entries if e is not entries[2]],
                               summary="replacement", archive_id=archive_id, overrides={},
                               before_publish=cancelled)
    assert session.revision == revision
    assert session.checkpoint is None
    assert session.active[2].message == "earlier step"


def test_session_import_does_not_require_posix_archive_capabilities():
    code = """
import builtins
original = builtins.__import__
def import_without_fcntl(name, *args, **kwargs):
    if name == 'fcntl':
        raise ImportError('simulated non-POSIX platform')
    return original(name, *args, **kwargs)
builtins.__import__ = import_without_fcntl
from core.session import Session
assert Session().working_messages() == []
from core.context_archive import FileContextArchive
try:
    FileContextArchive('unused-archive-root')
except OSError as error:
    assert 'POSIX' in str(error)
else:
    raise AssertionError('archive constructor must explain missing platform capability')
"""
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_archive_constructor_explains_missing_safe_filesystem_flags(tmp_path, monkeypatch):
    archive = archive_type()
    monkeypatch.delattr(os, "O_NOFOLLOW")
    with pytest.raises(OSError, match="POSIX"):
        archive(tmp_path / "archives")
    assert not (tmp_path / "archives").exists()


def test_warm_archive_validation_does_not_redecode_unchanged_ancestor_bodies(tmp_path, monkeypatch):
    session = populated(tmp_path)
    for index in range(30):
        compact(session, f"goal and progress {index}")
        session.add(AI(f"next {index}"))
    session.validate_archives()
    import core.context_archive as module
    decoded = []
    original = module.json.load
    def measured(source, *args, **kwargs):
        decoded.append(source.name)
        return original(source, *args, **kwargs)
    monkeypatch.setattr(module.json, "load", measured)
    for _ in range(3):
        session.validate_archives()
    assert len(decoded) <= 2, "unchanged archive bodies should not be decoded on every run"
    oldest = session.archive_store.root / session.session_id
    first = min(oldest.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
    first.unlink()
    with pytest.raises((OSError, ValueError)):
        session.validate_archives()


def test_cache_eviction_reuses_verified_compaction_copy_without_new_archive(tmp_path):
    session = populated(tmp_path)
    archive_id = compact(session)
    before = session.archive_store.usage_bytes
    session.history_limit = 2
    session.maintain_history()
    assert session.archive_store.usage_bytes == before
    assert session.history_archive_id is None
    path = tmp_path / "reused.json"
    store = JsonSessionStore(archive_store=session.archive_store)
    store.save(session, path)
    restored = store.load(path)
    restored.validate_archives()
    archived = all_text(restored.archive_store, restored.session_id, archive_id)
    assert archived["entries"][0]["message"]["content"] == "earlier step"


def test_warm_validation_rejects_corrupted_ancestor_even_with_same_size_and_mtime(tmp_path):
    session = populated(tmp_path)
    first = compact(session)
    session.add(AI("next"))
    compact(session)
    session.validate_archives()
    path = session.archive_store.root / session.session_id / f"{first}.json"
    original_stat = path.stat()
    payload = path.read_bytes()
    corrupted = payload.replace(b'"content":"earlier step"', b'"content":11111111111111', 1)
    assert len(corrupted) == len(payload)
    path.chmod(0o600)
    path.write_bytes(corrupted)
    path.chmod(0o400)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(ValueError):
        session.validate_archives()


def test_warm_receipt_never_permits_evicting_changed_raw_message(tmp_path):
    session = populated(tmp_path)
    compact(session)
    session.history[2].message = "externally changed original"
    session.history_limit = 2
    session.maintain_history()
    assert session.history_archive_id is not None
    archived = all_text(session.archive_store, session.session_id, session.history_archive_id)
    assert archived["entries"][0]["message"]["content"] == "externally changed original"


def test_detached_context_preparation_does_not_publish_and_publish_performs_no_io(tmp_path, monkeypatch):
    session = populated(tmp_path)
    entries = session.context_entries()
    archive_id = session.archive_entries(entries[2:3], reason="summary")
    revision = session.revision
    candidate = session.prepare_context_commit(expected_revision=revision,
                    retained_ids=[e.entry_id for e in entries if e is not entries[2]],
                    summary="summary", archive_id=archive_id, overrides={})
    assert session.revision == revision
    assert session.checkpoint is None
    assert session.active[2].message == "earlier step"
    def unexpected_io(*args, **kwargs):
        raise AssertionError("publication must not access archives")
    monkeypatch.setattr(session.archive_store, "_open", unexpected_io)
    monkeypatch.setattr(session.archive_store, "_metadata", unexpected_io)
    session.publish_context_commit(candidate)
    assert [m.message for m in session.active] == ["rules", "original goal", "recent"]


def test_stale_detached_context_candidate_keeps_newly_completed_effects(tmp_path):
    session = populated(tmp_path)
    entries = session.context_entries()
    archive_id = session.archive_entries(entries[2:3], reason="summary")
    candidate = session.prepare_context_commit(expected_revision=session.revision,
                    retained_ids=[e.entry_id for e in entries if e is not entries[2]],
                    summary="summary", archive_id=archive_id, overrides={})
    session.add(AI("effect completed while worker ran"))
    with pytest.raises(ValueError, match="revision"):
        session.publish_context_commit(candidate)
    assert session.checkpoint is None
    assert session.active[-1].message == "effect completed while worker ran"


def test_detached_history_preparation_archives_before_explicit_publish(tmp_path, monkeypatch):
    session = Session(history_limit=1, archive_store=archive_type()(tmp_path / "archives"))
    session.add(User("audit-only original"), to_active=False)
    session.add(User("latest"))
    revision = session.revision
    candidate = session.prepare_history_maintenance()
    assert session.revision == revision
    assert session.history_archive_id is None
    assert len(session.history) == 2
    def cancelled():
        raise TimeoutError("cancelled after write")
    with pytest.raises(TimeoutError):
        session.publish_history_maintenance(candidate, before_publish=cancelled)
    assert len(session.history) == 2
    def unexpected_io(*args, **kwargs):
        raise AssertionError("publication must not access archives")
    monkeypatch.setattr(session.archive_store, "_open", unexpected_io)
    monkeypatch.setattr(session.archive_store, "_metadata", unexpected_io)
    session.publish_history_maintenance(candidate)
    assert [m.message for m in session.history] == ["latest"]
    assert session.history_archive_id is not None


def test_stale_history_candidate_never_drops_a_new_message(tmp_path):
    session = Session(history_limit=1, archive_store=archive_type()(tmp_path / "archives"))
    session.add(User("audit-only original"), to_active=False)
    session.add(User("latest"))
    candidate = session.prepare_history_maintenance()
    session.add(AI("completed result"))
    with pytest.raises(ValueError, match="revision"):
        session.publish_history_maintenance(candidate)
    assert [m.message for m in session.history] == ["audit-only original", "latest", "completed result"]
