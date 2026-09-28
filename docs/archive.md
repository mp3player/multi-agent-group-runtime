# Archived collaboration implementation

On 2026-09-23, the Group implementation was physically separated into the
sibling directory:

```text
../mas-group-archive-20260923/
```

[Archive notes](../../mas-group-archive-20260923/ARCHIVE.md) describe the frozen
snapshot. It includes the previous scheduling/runtime/domain code, application
services and configuration, prompt modules, CLI, Web frontend/server, member
configuration examples, tests, experiments and historical design/review files.
A frozen copy of shared Agent dependencies stays there too, so the archive does
not import code from the active project. Private member configuration and old
Web logs are preserved there; the real `.env` remains in this project.

The active project has no `--group` dispatch, compatibility exports, Group
runtime, Group Web entrypoint or member configuration directory. Generic Agent
APIs, workspace tools, permission/audit logic, usage views, prompt composition
and session storage remain here.

This is a local frozen archive, not a new independently released package. It
is outside the active Git working tree; retain or back it up separately when
moving the project. `SNAPSHOT.json` and `SEPARATION.json` record file ownership
and hashes. The archive is local and was not published or pushed.

No tests were added or executed in this round. Existing mixed test files were
separated by ownership; their full originals remain in the archive. Static
syntax/import/link inspection and source-file hash reconciliation are the only
checks performed, and do not establish runtime correctness of the new tree.
