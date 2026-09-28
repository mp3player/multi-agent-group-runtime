# Group foundation implementation plan

> **For agentic workers:** Use test-driven development for each slice and requesting-code-review before completion. Track completed work here. No commits or releases are requested.

**Goal:** Build a usable local peer collaboration runtime without a concrete scheduling policy.

**Architecture:** An independent `group` package owns durable public commands, requests, dispatch validation and invocation lifecycle. Existing Agents remain independent execution components. Policies only consume detached snapshots and return proposals. The application may submit a plan directly; receiving a message never implicitly selects a member.

**Tech Stack:** Python 3.10+, asyncio, standard-library SQLite and bounded thread workers, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-group-collaboration-design.md` and its operational companion. The user's 2026-09-26 instruction authorizes the foundation below; earlier design-only status is historical.

**Scope note after the semantics review:** This completed plan records the manual
foundation as implemented. Later September 26 corrections to the linked specs
define further strategy-assembly, observation, context, response-evidence and
conversation-limit contracts. The checked tasks below do not claim those
extensions are implemented. Current differences are listed in
[Group foundation](../../group-runtime.md#current-extension-boundary).

## Global constraints and implementation profile

- Preserve the dirty single-Agent foundation in the current refactor branch. Do not reset, stash, migrate the archive, commit, or touch credentials/editor databases.
- No policy implementations or implicit default policy. Manual plans are a control API, not an automatic selection rule.
- All source, logs, docstrings and documents are English. Group never becomes an Agent subclass; core never imports Group.
- Store originals and receipts durably; bounded pages and admission limits do not discard accepted messages.
- First execution binding: explicitly declared worker-safe **complete synchronous, non-streaming** Agent pipeline on a dedicated member worker. This preserves custom executors, permissions, audit, formatting and context archiving in their existing serial order, off the Group loop. Unsupported async handlers/providers fail binding. Cancellation is cooperative between operations; actual worker completion is the settlement boundary. An indefinitely blocked operation retains its lease. Borrowed provider/session/archive resources are not closed by Group.
- This restricted binding is an incremental alternative to the full async-tool profile, not an implementation of that profile. Native async execution handles, per-stage async adapters/audit queues, shared writable-workspace leases/expected-base writes, dynamic membership, plugin loading, CLI/UI, portable snapshots and automatic crash recovery remain deferred. Foundation accepts only read-only/memory/collaboration tools; unsafe or unclassified side effects fail composition.
- Membership fixed for a runtime. One nonterminal invocation per store; reopening unfinished work fails closed for explicit reconciliation. One process owns each local SQLite store (POSIX advisory lock); no distributed deployment.
- Scope IDs and member execution identities bind commands. Directed messages stay public, final Agent answers stay private. A closing scope accepts only plain posts from its already-running members.
- SQLite serialized worker: bounded queued jobs, reserved control capacity, WAL/FULL/FK verification, shield admitted jobs from caller cancellation, durable mutation/receipt/revision transactions. No model or tool work inside a transaction.

## Task 1: Generic synchronous Agent worker and ownership

Files: `core/agent_runtime/worker.py`, `core/agent_runtime/run_state.py`, minimal `react_loop.py` changes if necessary, `tests/test_agent_worker.py`.

- [x] Write failing behavioral tests for ownership, worker settlement, custom tool dispatch, cancellation during blocking work and failure outcomes.
- [x] Implement `AgentWorker(agent, *, worker_safe: bool)` which reserves exclusive ownership until `await close()`. Reject absent/false declaration and coroutine providers/tools. No Group imports. Public synchronous Agent APIs remain unchanged outside a lease.
- [x] `submit(message: str, *, context: contextvars.Context | None = None) -> AgentExecution` rejects concurrent submissions before input mutation. Runs `agent.run_events(message, stream=False)` on its one worker and consumes the actual terminal event, not observer callbacks. The copied context covers the entire run.
- [x] `AgentExecution.request_stop()` is idempotent; `await wait()` shields the real worker future and returns frozen `ExecutionOutcome(status: str, result: str = '', error: str | None = None)`. `settled` and `outcome` expose actual completion. Status preserves completed/tool_stop/max_turns/error/timeout/cancelled. Stop checks must occur before input/model/tool admission, never release a lease for a cancelled waiter. Preserve primary completion when stop arrives late.
- [x] `AgentWorker.close()` stops and waits for its active run, shuts down its executor and releases ownership; waiter cancellation cannot abandon owned cleanup. It never closes borrowed LLM/session resources. Apply generic ownership via AgentRunState so direct standalone entry/setters cannot bypass it. Do not duplicate the ReAct loop.
- [x] Run new tests and existing execution/compatibility/timeout tests. Report limits honestly.

## Task 2: Durable records and store owner

Files: `group/records.py`, `group/errors.py`, `group/store.py`, `tests/test_group_store.py`.

- [x] Tests first: transaction rollback, durable reopen, idempotent receipt conflict, cancellation after enqueue, queue limits/control capacity and exclusive ownership.
- [x] Immutable command/receipt/message/opportunity/assignment/snapshot/plan records and validation limits. Tuples rather than mutable nested lists; JSON policy state serialized on boundaries.
- [x] A single bounded SQLite worker with async `read`/`write` operations, verified PRAGMAs and a reserved control lane. Strong completion references survive caller cancellation. Exclusive store owner, versioned schema, indexed bounded queries. All DB access uses this owner.

## Task 3: Policy-free collaboration runtime

Files: `group/runtime.py`, `group/state.py`, `group/policy.py`, `group/__init__.py`, `tests/test_group_runtime.py`.

- [x] Tests first: explicit requests do not launch, stale/invalid plans roll back, capacity and same-member serialization, idempotent command acceptance across close, scoped replies, completion guards, cancellation settlement, durable outcomes and repeated invocations.
- [x] Runtime creation validates distinct Agents/sessions/registries and safe tool capabilities, creates workers, opens fixed persisted membership and refuses nonterminal crash state.
- [x] Open invocation; external and execution-bound post/request commands commit message, opportunities, receipt and revision atomically. Same key/payload returns original receipt; key conflict fails. Bound context never accepts caller-supplied sender/scope identity.
- [x] Bounded snapshots/history/opportunity/outcome pages with high-water sequence, cursor/exhaustion and total outstanding counts. Revision-based waiting cannot miss a committed change. Originals are never pruned.
- [x] Validate and commit a revision-bound DispatchPlan atomically: explicit run proposals, matching pending opportunities, terminal dispositions and serialized policy state. Pending deferrals remain pending. Policy-originated runs require stable origin keys. Queue limits and per-member reservations reject conflicts; commit is separate from launch.
- [x] Launch committed assignments subject to capacity. Build bounded input from full trigger records and instruction; reject oversized contexts before commit. Record terminal execution metadata, settle assigned opportunities, retain private final answers in Agent session. No automatic publication or retry.
- [x] Normal completion requires zero pending/queued/running and a current snapshot revision. Forced closing cancels pending/queued, signals running members and waits for actual settlement. Accepted records survive caller cancellation; do not clear leases early. Storage failure stops admission and launch, preserves uncertain execution references.

## Task 4: Collaboration tools, integration and documentation

Files: `group/tools.py`, `docs/group-runtime.md`, `examples/group_manual.py`, focused Group tests; small README/module index updates.

- [x] Test a real two-Agent exchange driven by explicit plans with deterministic provider responses: post/request/member query/history query/yield, target validation, actual reply IDs, private final answer and reused provider tool IDs.
- [x] Install tools through existing ToolRegistry and ToolRuntime, using a run-bound context and a thread-safe bridge to the Group loop. Tool names: group_post, group_request, group_members, group_history, group_message, group_yield. Stable operation identity is generated by the bound execution/tool invocation, never a provider call ID alone. Receipts promise acceptance only. Yield ends this Agent run only.
- [x] Document installation/profile restrictions, full example, policy protocol and intentionally missing scheduling. No new application subclass or CLI.
- [x] Independent review, meaningful deterministic lifecycle/repeated-run tests and full single-Agent regression suite. Document verified behavior versus deferred full operational contracts.

## Verification and decisions

- Baseline: 854 passed on Python 3.14. Disable unrelated globally installed pytest plugins with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, explicitly load `pytest_asyncio.plugin`; otherwise ROS auto-loading fails before collection due to unrelated missing PyYAML.
- Use the existing refactor checkout because its uncommitted single-Agent changes are the required foundation; a HEAD-only worktree would omit them.
- Store/records and runtime are implemented together locally; independent Agent worker work can proceed without editing Group files. Review is delegated after implementation.

## Completion

All four foundation slices are implemented and reviewed. Python 3.14: 892 passed; Python 3.10: 891 passed, one existing cancellation-count test skipped. Final focused 3.14 regression: 95 passed. See [review and verification](../reviews/2026-09-26-group-foundation-review.md) for resolved findings, the explicit-close test correction and deferred profile capabilities. No scheduling policy, commit or release was added.
