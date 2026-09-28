# On-demand collaboration implementation plan

> **For agentic workers:** Use test-driven development and requesting-code-review. Work in the existing refactor checkout; no commits, releases, archive migration or default-policy changes.

**Goal:** Implement the archived on-demand behavior as one explicitly selected, newly written peer strategy and exercise the corrected extension boundaries.

**Architecture:** A strategy bundles a communication profile and a pure decision function. A generic driver observes bounded invocation state and admits proposals through the existing runtime. Plain posts do not activate members; directed/untargeted response requests do. An explicit broadcast tool creates one response opportunity for every other member. Quiescence returns control without claiming task completion.

**Tech Stack:** Python 3.10+, asyncio, SQLite, existing Agent workers, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-group-collaboration-design.md` and `2026-09-24-group-operational-contracts-design.md`.

## Global constraints

- Preserve the uncommitted single-Agent/Group foundation and use the existing `refactor/agent-runtime-foundation` branch. No credentials or editor database access.
- All code, comments, logs, tests and documentation are English.
- Core imports no Group code; members remain peers with serial owned execution.
- No copied archived implementation, default automatic strategy, synthetic PASS, frontend, shared writes or crash recovery.
- The existing manual API remains supported. A strategy is selected explicitly at construction; its driver is started explicitly.
- Public originals and accepted effects are preserved. Inference completion is not response fulfillment.
- Existing baseline: 38 focused Group/worker tests pass.

## Scope and concrete contracts

`GroupRuntime.create(..., strategy=OnDemandStrategy())` chooses the strategy.
`await runtime.drive(scope)` drains eligible work, including requests created by
running peers, then returns a detached `DriveResult`. A waiting/needs-input result
keeps the invocation open. Caller cancellation only cancels its wait; Group close
owns stopping the driver and workers. Explicit `finish` validates required public
reply evidence for the selected profile. It never infers the user's task is done.

`CollaborationProfile` declares tools, instructions, bounded background projection,
whether selected final text is public, and whether a response requires a linked
public reply. The manual default uses the existing six tools/private finals.
On-demand adds `group_broadcast` and requires linked responses. A test profile
with no Group tools and selected-final publication checks that assembly works.

`SchedulingView` provides a detached snapshot, all pending opportunities bounded
by the configured outstanding-work limit, bounded recent public messages, and
reply/failure evidence bounded by the cumulative admitted-run limit. This first
strategy does not need general streamed candidate discovery or staged collections.
`SchedulingDecision` expresses a plan or a wait reason. No automatic task-finish
predicate is shipped in this strategy.

Admission persists each invocation's strategy identity, maximum cumulative runs
and deadline. A small additive schema upgrade preserves completed version-1
stores and still rejects unfinished recovery. All proposals, including manual
ones, obey the durable cumulative limit; failed/cancelled runs do not refund it.
The active driver enforces deadline expiry and retains ownership until actual
worker settlement. Missing replies yield needs-input, never an invisible retry.

## Task 1: Communication profile and bounded context

Files: create `group/profiles.py`; modify `group/tools.py`, `group/runtime.py`,
`group/dispatch.py`; add `tests/test_group_strategy.py`.

- [x] Write failing integration cases for configurable tool installation, a
  prior public post appearing as background without waking an idle member, and
  a no-Group-tool profile publishing only its designated final result.
- [x] Verify failures identify missing profile/composition behavior.
- [x] Implement frozen profile/input records and deterministic default rendering;
  install/uninstall only the declared tools with rollback, preserve source IDs
  in stored input, and route optional final publication through command admission.
- [x] Keep trigger originals complete. Bound background by count and rendered
  bytes; report an omitted history window with retrieval references. Never delete
  public originals or overwrite member session state.

## Task 2: Invocation observations and resource bounds

Files: modify `group/records.py`, `group/state.py`, `group/dispatch.py`,
`group/runtime.py`, `group/errors.py`; add coverage in `tests/test_group_strategy.py`.

- [x] Add failing tests for cumulative admission across successive plans,
  preserved counts after cancelled work, schema upgrade retaining originals,
  bounded observations beyond one page, and required reply evidence.
- [x] Persist invocation limits and strategy identity; reject an over-budget
  plan atomically with `InvocationLimitError`. Supply detached scheduling views
  in one store read and verify explicit finish against profile evidence.
- [x] Keep existing manual behavior and ownership/error guarantees covered by
  the existing foundation tests.

## Task 3: Pure on-demand strategy and generic driver

Files: create `group/scheduling.py`, `group/strategies.py`, `group/driver.py`;
modify `group/runtime.py`, `group/__init__.py`;
extend `tests/test_group_strategy.py`.

- [x] Start with failing real-Agent scenarios: one initial responder, directed
  peer requests, explicit broadcast, silent plain posts, busy-member retention,
  requests beyond one page, missing replies, member failure and endless new requests.
- [x] Implement deterministic target-preserving selection and a driver that
  reobserves on committed changes, launches queued assignments up to capacity,
  yields between bounded decisions, and returns on quiescence without completing
  the invocation. No state change is inferred from a tool name or response prose.
- [x] Add cancellation/deadline/close tests; coalesce concurrent drive callers,
  shield owned execution from cancelled waiters, and close with explicit limit/
  timeout reasons when necessary. A blocked worker must retain its lease.

## Task 4: Example, documentation and review

Files: create `examples/group_on_demand.py`; update `docs/group-runtime.md`,
`README.md`, and this plan; record review/verification in the review directory.

- [x] Run an offline end-to-end example using real Agents, tools, SQLite and the
  new driver. Include ordinary information, directed work and broadcast replies.
- [x] Run focused tests, then the complete Python 3.14 suite and the appropriate
  Python 3.10 compatibility checks. Use
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin`.
- [x] Request independent review of changed files against the spec; fix findings
  and run the covering checks. Document remaining restricted-profile boundaries.

## Verification examples

```python
strategy = OnDemandStrategy()
runtime = await GroupRuntime.create(path, members, worker_safe=True, strategy=strategy)
scope = await runtime.open_invocation('review')
await runtime.post(scope, 'Background only', key='background')
assert not any(member.session.history for member in members.values())
await runtime.request(scope, 'Review the background', key='request', recipients=('b',))
result = await runtime.drive(scope)
assert result.status == 'waiting'
# Explicit application decision; required linked responses are checked.
await runtime.finish(scope, revision=(await runtime.snapshot(scope)).revision)
await runtime.close()
```

## Progress

Implementation and independent review are complete for this first strategy slice.
Python 3.14: 970 passed. Python 3.10: 969 passed, one existing cancellation-count
skip. The offline four-run example and the configured live-model directed/broadcast
scenarios passed. See the [verification report](../reviews/2026-09-26-on-demand-strategy-review.md).
This does not complete every contract in the larger Group design.
