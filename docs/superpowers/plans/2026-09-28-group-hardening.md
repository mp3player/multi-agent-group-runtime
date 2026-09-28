# Group Hardening Implementation Plan

**Goal:** Reduce historical preparation overhead and improve factual request
feedback/CLI diagnostics while preserving model-directed scheduling.

**Spec:** `docs/superpowers/reviews/2026-09-28-group-hardening-scope.md` and its
linked scope review, approved for implementation in the conversation.

## Constraints

- Preserve explicit request activation, passive posts, unrestricted legal replies,
  terminal yield, strict execution-bound response evidence and original messages.
- No production model/configuration changes or automatic correction runs.
- Keep all code, tests and documentation in English; retain Python 3.10 compatibility.
- Work in the existing dirty feature checkout; do not commit, reset, stash or
  create a clean checkout that omits the current uncommitted Group implementation.
- Tests use the real Group/store/worker path with scripted provider boundaries.
- Existing validation, cancellation, compaction and archive-integrity checks remain.

## Tasks and ownership

### Task 1: Historical preparation

Files: `group/member_context.py`, optionally `core/session.py`, and new
`tests/test_group_history_scaling.py`. These files belong to the history worker.

- [x] Add failing tests for store-round-trip scaling, covered-source cancellation,
  fresh receipt validation and preserved missing/corrupt coverage detection.
- [x] Batch application lookups per bounded source page. Do not retain a full
  history copy or stale application snapshots between preparation passes.
- [x] Reduce repeated coverage work only with a sound revision/integrity boundary;
  retain complete post-compaction verification. Measure before adding complexity.
- [x] Run focused reception/context tests and report red/green results.

### Task 2: Read-only diagnostics

Files: new `group/diagnostics.py`, `cli/group.py`, and new
`tests/test_group_diagnostics.py`. These files belong to the diagnostic worker.

- [x] Add failing real-runtime tests for pending-zero/missing-reply cases,
  blocked/failed/multi-trigger assignments, alternate profiles, bounded output,
  repaired evidence and read-only behavior.
- [x] Add `async def status(runtime, scope, *, limit=20)` in `group/diagnostics.py`.
  Read one consistent bounded observation through the existing store; include
  snapshot plus attributed response/blocked/error details and explicit counts
  or omitted-item information. Do not use unbounded scheduling_view as a shortcut.
- [x] Extend CLI `/status` to display the facts without activation, inference,
  resolution, or declaring business completion. Escape untrusted diagnostic text
  and bound individual fields and total displayed details.
- [x] Run diagnostic and existing CLI regression tests.

### Task 3: Current-request feedback

Files: `group/tools.py`, `group/profiles.py`, new `group/feedback.py`, and new
`tests/test_group_feedback.py`. Root owns these files.

- [x] Add failing tests for in-progress publication facts, unrelated legal replies,
  multiple triggers, same-batch yield, alternate profiles and diagnostic failure.
- [x] Query only the current bound assignment; report provisional linked/unlinked
  trigger facts rather than using settled-only evidence as a live query.
- [x] Enrich the existing outgoing receipt with bounded optional feedback. Preserve
  the accepted base receipt if enrichment fails or does not fit; never replay the
  committed command or swallow cancellation/persistence failure state.
- [x] Clarify fixed instructions and the current protected trigger envelope without
  adding business orchestration or new model-facing tools.
- [x] Run receipt/profile/strategy tests, including maximum audience receipts.

### Task 4: Integration and acceptance

- [x] Review each implementation against the scope and its limitations.
- [x] Run the full offline regression and finite forty-discussion endurance test.
- [x] Measure preparation growth and store operations without promising constant
  cost. Keep the prior real-model task failures as baseline evidence.
- [x] If a bounded real-model check is run, report it separately from deterministic
  tests and do not claim statistically established improvement without a baseline.
- [x] Update documentation and retain final validation evidence.

## Coordination and rulings

| Pair | Shared interface | Resolution |
| --- | --- | --- |
| 1 / 2 | GroupRuntime/store, read only for task 2 | Disjoint owned files |
| 1 / 3 | Agent session and store observation | Task 3 adds no session mutations |
| 2 / 3 | Response identity semantics | Both require exact scope/member/run/trigger matching; final evidence remains authoritative |
| All / 4 | Existing dirty checkout | Root integrates and reviews; no commits or broad cleanups |

Ruling: work in place on `refactor/agent-runtime-foundation` because the approved
implementation is largely uncommitted; a new clean worktree would omit it.

## Progress

- Tasks 1–4 complete. Full regression: 1206 passed, 14 skipped. Finite endurance:
  40 discussions, 176 assignments, 23 compactions, passed in 209.18 seconds.
- Independent review found no must-fix findings. The discovered stdin/SIGINT
  cancellation race was fixed and covered by deterministic pipe tests.
- Ruling: retain existing Session/archive validation without a new coverage cache;
  measured page batching reduces crossings while preserving integrity boundaries.
- Ruling: no live-model A/B claim or added model calls in this implementation turn.
  Same-batch yield and absent return requests remain explicit capability limits.
- Evidence: `docs/superpowers/reviews/2026-09-28-group-hardening-implementation.md`.
