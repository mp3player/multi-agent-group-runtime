# Group reception implementation and validation

Date: 2026-09-26. Scope: the approved
[reception design](../specs/2026-09-26-group-reception-design.md).
The existing dirty feature checkout was preserved; no commit, reset or migration
of user data was performed. Review used a saved source baseline rather than HEAD,
because the preceding Group implementation was already untracked.

## Implemented behavior

- Complete per-member reception is durable and independent of response slots,
  policy discovery, private context and business inference. Per-member global
  frontiers follow the ordered public log; per-source-scope batch records provide
  invocation coverage without skipping cancelled earlier scopes.
- Assignment admission captures a fixed source boundary and protected trigger /
  required-source IDs. Bounded worker preparation applies all historical spans
  as identified attributed User/synthetic assistant units. Protected sources
  remain raw in the current task. Later messages do not mutate admitted input.
- Generic Agent context batches retain provenance, content identity and receipts
  through snapshots, compaction and raw-cache pruning. Whole ordered units are
  validated. Historical application evidence alone cannot authorize missing or
  contradictory current context. Group verifies exact stored bindings and the
  whole frozen projection again immediately before business inference.
- Preparation has explicit queued/preparing/running/blocked/settled states.
  Recoverable preparation blockage retains the task and source barrier, releases
  active capacity, and requires explicit retry. Retrying reuses the assignment
  and successful context receipts. Business effects are never automatically replayed.
- Policy discovery acknowledges oldest-unprocessed bounded pages atomically with
  decisions. OnDemand still activates only durable requests. The optional owned
  service receives passive messages, wakes on new work and retains its idle
  deadline. It shares one driver with one-shot callers.
- Normal finish seals admission before a finite reception drain. Cancellation,
  deadlines and runtime close retain actual ownership; a waiting client's
  cancellation cannot fabricate completion or release a busy member.

## Verification

**Final offline verification: 1057 passed, 3 skipped in 28.72 seconds**, after
the last close/claim correction. The skipped cases are the separately executed
opt-in live-provider tests. Scoped source whitespace validation also passed.

Commands:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s \
  tests/test_group_live_reception.py tests/test_group_live_continuation.py
```

The real-provider run used the project's existing `.env` without logging its
credentials: **3 passed in 24.38 seconds**. The new all-real collaboration case
used four assignments: planner, reviewer, delayed integrator, then planner
acceptance review. Provider calls were planner 2, reviewer 1, integrator 1. The
integrator received the early `QUEUE-731` constraint automatically after multiple
pages of intervening discussion and included its identifier in the public answer.
All required public replies were satisfied and final member reception was complete.
The two existing continuation cases additionally exercised directed/broadcast
requests without forcing the requester to stop speaking immediately.

These are finite functional and lifecycle checks, not hours/days endurance tests,
provider-independent semantic guarantees, or proof of production crash recovery.

## Design scenario coverage

The table maps each design scenario to executed tests or an explicit structural
boundary. Component evidence is identified where the combined production fault
was not injected end to end. Test files are under `tests/`.

| Scenario | Evidence and qualification |
| --- | --- |
| R01 | `test_delayed_activation_receives_early_sources_and_freezes_queued_input`; all-real delayed integrator test checks provider input and public output. |
| R02 | `test_receive_covers_every_page_without_model_calls_or_settling_requests`; existing busy-target and beyond-one-page pending-work tests. |
| R03 | `test_discovery_observes_every_page_without_reactivating_posts`; delayed activation and live multi-page case. |
| R04 | Repeated `receive` and repeated `drive` assert no model call or revision growth when caught up. |
| R05 | Delayed-activation test admits a run, posts a supplement, and checks that only the next run sees it. |
| R06 | `test_post_during_real_tool_batch_waits_for_next_input_boundary`. |
| R07 | Idle eager private projection is intentionally absent: receive-only writes references; the existing member reservation and worker own all actual insertion. Queued-boundary and tool-batch tests exercise those barriers. |
| R08 | Unicode/marker-text test, durable command idempotency tests, generic context batch idempotency test. |
| R09 | `test_reconcile_applied_batch_after_lost_ack_and_keep_same_task`. |
| R10 | Generic invalid-batch atomicity, failed authoritative callback and identified-task retry tests; Group lost-ack test retains the applied prefix. |
| R11 | Component test `test_compaction_retains_synthetic_units_and_archive_provenance` reapplies the same receipt after compaction and pruning; Group lost-ack and Group compaction tests exercise both integration boundaries. |
| R12 | Replacement-session tests cover different session IDs and changed content under the same session ID; generic snapshot order/content checks; unfinished-store reopen rejection. |
| R13 | Source iteration includes sender-owned messages without an authorship shortcut. No separate replacement-session/own-public-message end-to-end case was added. |
| R14 | Existing failed-run/no-auto-retry tests, explicit response reconciliation retaining original outcomes, and tool/audit effect ownership tests. No provider failure restores unread state. |
| R15 | Generic cancellation during acknowledgment/summary publication; cancelled Group waiters; runtime close joins real workers. |
| R16 | `test_preparation_blockage_preserves_task_and_does_not_consume_other_member_capacity`; blocked driver ignores unrelated posts. |
| R17 | Queued-boundary supplement test and existing voluntary follow-up/directed/broadcast continuation tests, including real provider execution. |
| R18 | `test_compacted_passive_sources_keep_verified_coverage_before_main_inference`; generic lazy-ingestion compaction and provenance tests. Summary meaning remains model-dependent. |
| R19 | Protected task alone and protected render-byte-budget tests block before main inference; required-source test checks full raw envelope. |
| R20 | `test_unicode_spans_reconstruct_all_sources_and_marker_text_is_only_data` verifies contiguous character spans and exact reconstruction. |
| R21 | Generic missing/corrupt archive and provenance validation tests; Group rejects transforms and omitting/mutating profile renderers. Archive-loss and Group admission are primarily component-tested. |
| R22 | Parameterized sealed-finish tests reject new input and return duplicate receipts without reopening. |
| R23 | Sealed-finish waiter/scope cancellation and `test_finish_deadline_during_drain_never_reports_completed`; last-page deadline is rechecked before terminal commit. |
| R24 | `test_receive_catches_accepted_sources_from_cancelled_scope_without_reopening_it`. |
| R25 | Repeated service start shares its live owner; cancelled service waiter and idle deadline tests; concurrent drive and close ownership regressions. |
| R26 | Queue saturation/deadline/control-capacity regressions, fixed queued input, source/discovery pagination. This is finite backpressure testing, not an unlimited-arrival-rate guarantee. |
| R27 | Existing SQLite failure wakeup/cleanup tests and owned effect/audit tests. Current Group only admits declared read-only/memory tools; arbitrary external-effect crash recovery is unsupported. |
| R28 | Existing cumulative request-loop limit, broadcast slots, partial/missing reply and response-resolution tests. |
| R29 | Nonempty legacy version rejection, unknown manifest adapter blockage, read-only runtime configuration and snapshot compatibility tests. |
| R30 | Real configured OpenAI-compatible synchronous provider case and generic message/receipt serialization/compaction tests. Other Group provider representations are not claimed supported. |
| R31 | Generic receipt/reset and archive-only coverage tests; Group exact-binding replacement tests. |
| R32 | Existing invalid-plan/strategy failure lifecycle tests; protected renderer omission/mutation rejected atomically. |

## Review corrections

Independent core review found that reordered snapshot entry IDs could falsely
preserve coverage. Restore/live coverage now require complete contiguous ordered
units; history pruning cannot leave an orphan synthetic marker.

Independent Group review found and verified corrections for:

1. A custom renderer could omit or rewrite a protected original. Detached rendering
   and an independently validated structured source envelope now reject it.
2. A source could inherit an unrelated current receipt with the same operation ID.
   Exact session, digest, entry IDs and current coverage are now checked.
3. A reception backlog held the launch lock and delayed cancellation. Reception
   runs outside that lock; cancellation can fence queued work while it waits.
4. Close could begin during a claim/manifest await and still launch a worker.
   Both interleavings now have barrier regressions; close is checked after each
   await and again at main-inference authorization.

Review records are retained in `.superpowers/sdd/2026-09-26-group-reception/`.

## Compatibility, cost and remaining scope

- SQLite schema 4 is fresh-reception aware. Empty supported older stores can
  initialize; nonempty versions 1–3 require explicit offline import. No importer
  or automatic unfinished-invocation recovery is included.
- Group requires a verified source envelope and rejects arbitrary Agent context
  transforms. Custom renderers may customize guidance while retaining that
  envelope. `background_messages` remains accepted but no longer discards history.
- Reception does no inference; preparation can make separate summary calls using
  the Agent's existing accounting. A blocked protected input is not truncated.
- Sources are paged and private units are bounded, but verification scans the
  captured public range. Very large histories still incur growing scan costs;
  source/application metadata and durable originals grow with accepted work.
  No global disk quota or receipt-eviction policy was added.
- Synthetic markers are durable runtime provenance, not evidence that a model
  understood a source. Summaries are not semantically lossless. Applications still
  own answer acceptance and explicit Group completion.
- Fixed membership, one nonterminal invocation and cooperative synchronous workers
  remain the supported profile. Close waits for uncooperative provider/tool I/O
  to return; it cannot kill an arbitrary Python thread.
