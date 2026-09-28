# Group Reception Implementation Plan

> **For agentic workers:** Use subagent-driven-development for isolated core
> work and review; the primary agent owns Group integration. Execute continuously
> in the current feature checkout. Do not commit, stash, reset, or discard existing
> changes. Tests use the repository virtual environment and disable unrelated
> auto-loaded pytest plugins.

**Goal:** Implement durable complete member reception, synthetic passive history,
verified context application, and owned automatic scheduling from the approved design.

**Architecture:** Group stores source ranges and execution manifests in SQLite.
The base Agent applies identified context batches without inference and retains
their provenance through archives. A member worker serializes preparation and
execution; Group owns scheduling, response obligations, closure and retry policy.

**Tech Stack:** Python, existing Agent/Session/archive code, asyncio, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-group-reception-design.md`

## Global constraints

- Peer Group organization; standalone Agent must remain independently usable.
- All source code, comments, logs, tests and documentation use English.
- Preserve all accepted originals; do not substitute latest-N history for unread coverage.
- Synthetic records never become public messages, real tool calls, or task completion.
- A model/tool failure cannot automatically replay business effects.
- One nonterminal invocation and fixed membership; no new default strategy.
- Fresh reception schema; refuse unsupported old data instead of assuming delivery.
- No automatic crash resume, distributed transactions, new summarization engine or plugin loader.
- Preserve dirty working-tree content. Review against a saved source baseline, not HEAD alone.

## Task 1: Generic context batches and preparation-aware Agent worker

**Files:** Create `core/context_batch.py`, `tests/test_context_batches.py`.
Modify `core/session.py`, `core/session_store.py`, `core/session_codec.py`,
`core/agent.py`, `core/agent_runtime/worker.py`, `run_state.py`, `react_loop.py`,
and context-management helpers as required to preserve provenance and coverage.

**Interfaces:**

```python
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

# Controlled, non-inference, idempotent application and lookup.
Agent.apply_context_batch(batch: ContextBatch) -> ContextReceipt
Session.context_batch_receipt(operation_id: str) -> ContextReceipt | None

# Existing calls keep their behavior. Group supplies batches lazily from a
# fixed manifest. Callbacks run on the worker, not as observational listeners.
AgentWorker.submit(message, *, context=None, context_batches=(),
                   on_context_applied=None, before_inference=None)
# on_context_applied(receipt): persist application acknowledgement.
# before_inference(): validate admission immediately before the first main
# model request (after any required context preparation).
# ExecutionOutcome adds phase ('preparation'/'execution') and error_type.
```

- [x] Add failing tests: applying the same batch twice changes history once;
  changed content under its key is rejected atomically; no model/tool calls occur;
  a reset cannot pass readiness using an old receipt; save/load and compaction
  preserve application identity, origin and coverage.
- [x] Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/test_context_batches.py`
  and confirm the intended missing-interface/behavior failures.
- [x] Implement canonical detached validation and atomic session application;
  reject tool-bearing synthetic messages and malformed provenance. Keep receipts
  separate from the prunable message cache and serialize/validate them.
- [x] Preserve synthetic/source units in compaction, keep raw sources archived,
  and distinguish current working coverage from historical application evidence.
- [x] Add worker tests that cancellation/failed acknowledgement prevents main
  inference, successful preparation permits it, and legacy submissions still work.
  Lazy batches must be consumed with bounded context maintenance, using existing
  Agent compaction and accounting; a protected oversized input fails preparation.
- [x] Run context, worker, session and standalone regressions and obtain core review.

Example behavioral assertion (adapt fixture construction to the published API):

```python
first = agent.apply_context_batch(batch)
assert agent.apply_context_batch(batch) == first
assert len(agent.session.history) == 2
assert model.requests == []
```

## Task 2: Durable reception, stable assignment inputs and source projection

**Files:** Create `group/reception.py`, `group/member_context.py`,
`tests/test_group_reception.py`. Modify `group/state.py`, `records.py`,
`profiles.py`, `dispatch.py`, and `runtime.py` integration points.

**Interfaces:**

```python
await group.receive(scope)                  # Complete durable receive-only catch-up.
await group.reception(scope, member)        # Detached coverage/status information.
# RunProposal gains required_source_ids=(); assignment manifest pins the
# current global source high-water and full trigger/required-source originals.
```

- [x] Add failing tests that passive reception does not invoke peers, and a
  delayed member sees an early source after more than both recent/page limits.
  Test mixed directed requests without settling pending obligations.
- [x] Add a new fresh schema with member reception ranges/frontiers, per-assignment
  source manifests and context application acknowledgements. Old nonempty schemas
  require explicit offline import; empty supported stores can initialize safely.
- [x] Receive complete bounded pages atomically with contiguous progress and
  stable operation identities. Catch up accepted sources in older closed scopes
  as history; never reopen their opportunities.
- [x] Render historical source units as attributed User data plus neutral runtime
  assistant markers. Keep protected active sources only in the final task input,
  and preserve their source coverage without adding fake completion. Use stable
  source/character-span identities for bounded large-message units.
- [x] Freeze assignment high-water at commit; do not mutate it for later arrivals.
  Validate required source IDs, receiver/version bounds and complete protected size.
- [x] Verify input text at the actual scripted provider boundary, not only receipts.

```python
await group.receive(scope)
assert not models['c'].requests
# After an explicit later activation of c:
assert early_original in '\n'.join(m.message for m in models['c'].requests[0])
```

## Task 3: Preparation lifecycle, automatic service and safe finishing

**Files:** Modify `group/runtime.py`, `driver.py`, `scheduling.py`, `errors.py`;
extract `group/execution.py` / `group/service.py` if this keeps ownership clear.
Add `tests/test_group_reception_lifecycle.py` and service cases.

**Interfaces:**

```python
await group.start_service(scope)            # Idempotent owned background pump.
await group.retry_preparation(scope, assignment_id)
# Existing drive, launch_ready, wait_idle, finish, cancel and close remain usable.
```

- [x] Add deterministic tests with events/barriers: input arriving during queued
  work or a tool batch cannot change that run; duplicate service start has one
  owner; an idle service wakes on messages and still owns its deadline.
- [x] Use one reservation per member across queued/preparing/running/blocked work.
  Stream the fixed manifest into the generic worker; acknowledge each applied
  context unit through Group control writes; enter running only at the first
  main inference boundary after successful preparation.
- [x] Distinguish recoverable preparation blockage from failed execution and
  controller/storage errors. Keep blocked assignments without spinning or holding
  global active slots; explicit retry reuses the assignment and applied receipts.
- [x] Preserve ownership until actual worker settlement on timeout/cancellation.
  Model/tool effects remain recorded; runtime outcomes do not restore unread flags.
- [x] Run reception catch-up even when strategy selects nobody. A receive-only
  no-op must not repeatedly bump revisions. Keep one-shot drive and service under
  one scheduling authority with bounded wait/retry and explicit status.
- [x] Finish atomically seals admission, captures closing source boundary, drains
  durable reception and owned preparations, then commits completion. New commands
  after sealing fail; duplicates return original receipts; cancellation retains
  actual progress. Close the store after all owners settle.

## Task 4: Boundary coverage, real collaboration and documentation

**Files:** New/updated Group/context tests; `docs/modules.md`, `docs/operations.md`,
and a reception review/acceptance report under `docs/superpowers/reviews/`.

- [x] Map R01-R32 to concrete test names and record any unsupported guarantee
  honestly. Cover duplicate/lost acknowledgement, reset/archive gaps, Unicode
  chunks, blocked member fairness, close races, cross-scope gaps and limits.
- [x] Run all offline tests with auto-loaded plugins disabled. Fix real regressions;
  update tests only for explicitly changed compatibility/input contracts.
- [x] Run a finite collaboration probe using the existing `.env`, without printing
  credentials: passive delayed participation, mixed requests, multiple handoffs
  and an early requirement that must appear in the delayed member's output.
- [x] Obtain independent code review focused on context provenance, concurrency,
  cancellation, acceptance boundaries and regressions. Fix findings and rerun
  relevant checks before a final broad verification.
- [x] Document APIs, limits, no-auto-recovery behavior, validation results and the
  implemented scope. Do not claim that 32 planned cases passed unless executed.

## Execution record

The source baseline and ongoing task/review notes are retained in this plan's
local workspace. This implementation is authorized; do not pause between tasks
to ask whether to continue. No commit, merge or publication is part of this task.


Completed: generic core and Group review corrections verified; final offline
suite 1057 passed / 3 skipped, and the three opt-in configured-provider tests
passed separately. Coverage qualifications and resource/recovery limits are
recorded in `docs/superpowers/reviews/2026-09-26-group-reception-implementation.md`.
No commit was made.
