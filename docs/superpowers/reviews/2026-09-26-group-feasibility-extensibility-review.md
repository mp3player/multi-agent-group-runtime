# Group feasibility and extensibility review

Date: 2026-09-26

Reviewed documents:

- [Architecture](../specs/2026-09-24-group-collaboration-design.md), SHA-256
  `50cc26de54d9c3d8f9c6d6127d225ca113b53622bf9a1f2c5526e8bbc934ce73`.
- [Operational contracts](../specs/2026-09-24-group-operational-contracts-design.md),
  SHA-256 `abbb535e8cf33a79aa3bfbfa9fdc4c0a7712dac898514d417fa6afee663df986`.

## Verdict

Feasible for the intended local, in-process peer collaboration system. The
Agent execution base, runtime-owned collaboration state, stable commands and
replaceable policy functions have coherent responsibilities. No finding requires
discarding this architecture or reverting to the archived Group implementation.

Extensibility is strong within shared public-message and lifecycle semantics:
selection, ordering, response collection and stages can vary without changing
the Agent loop or duplicating the Group runtime. This is not a promise that all
future organization, privacy, execution and deployment changes are policy swaps.

The work is larger than a Group wrapper. Observable Agent settlement, compatible
async tool dispatch and checked workspace mutation are real base/binding work,
already acknowledged by the design. Their acceptance evidence is a prerequisite
to claiming reliable Group execution. The documents are suitable for phased
implementation planning after the narrow clarification below; they are not
performance measurements or proof of implemented reliability.

## One remaining design refinement

### F1 — P2: Execution classification must cover the full tool pipeline

**Design evidence:** operational lines 110–119 classify handler execution modes
while retaining the permission/workspace/audit/result pipeline. The responsiveness
acceptance at line 364 concerns the overall scheduler, not just the handler.

**Source evidence:** [tools/runtime.py](../../../tools/runtime.py), lines 72–88,
calls permission checks and audit output synchronously around handler execution.
[tools/audit.py](../../../tools/audit.py), lines 97–103, calls a configured sink
inline; lines 127–131 perform file output in the built-in JSONL sink. Catching a
sink exception preserves an effect's result, but does not prevent a slow sink
from blocking the event loop.

**Counterexample:** an otherwise fast or native-async tool completes, then its
audit sink blocks on storage. All other members and cancellation handling on the
same event loop stall. Classifying only the handler as nonblocking admits this
composition despite the proposed responsiveness requirement.

**Minimal remedy:** execution declarations and composition validation cover
executor overrides, permission hooks, result adapters and sinks as well as the
handler. Required permission checks still precede effects. Suitable blocking
work uses explicitly allowed tracked capacity; thread-affine unsupported
components are rejected rather than silently moved. Audit output may use a
bounded ordered writer, preserving the distinction between observational audit
and authoritative Group records. Do not bypass custom executor validation or
retry an effect because its observational output failed.

**Acceptance:** a slow configured audit sink or permission hook cannot freeze an
unrelated member under a supported profile. Queue limits, ordering and shutdown
tracking remain observable. This is a narrow contract clarification, not an
architectural blocker or a newly demonstrated production incident.

## Material tradeoffs, not additional defects

### Availability when cleanup never finishes

Operational lines 101–106 retain uncooperative work and its resources rather
than pretending it stopped. Architecture lines 816–818 permit one nonterminal
invocation per Group, while lines 852–864 reject new work during closing and keep
the scope nonterminal until cleanup finishes.

Together, these choices mean that a run stuck in cleanup can prevent that Group
from accepting its next invocation indefinitely. Healthy peers may progress in
an open invocation; that does not restore intake once the scope is closing.
This is an intentional safety/availability tradeoff, not a contradiction.

The initial supported profile should use operations with known completion/stop
behavior and expose quarantined resources and recovery requirements clearly.
Do not advertise bounded shutdown or automatic service recovery for arbitrary
in-process tools. If continued intake despite permanently uncooperative work
becomes a requirement, design an isolated execution boundary or a separate
administrative suspension/resource-ownership protocol. Simply clearing leases
or declaring the old invocation successful is not a valid shortcut.

### Parallel member count does not guarantee parallel coding

Operational lines 273–311 deliberately hold a workspace write lease for an
entire write-capable run and require checked mutations. Two coding members with
write access to the same workspace therefore serialize even during model
inference. Raising the member limit cannot remove that lease constraint.

This is a defensible initial correctness choice. Use read-only peer analysis
where appropriate, or separate writable roots/worktrees with explicit artifact
exchange when parallel mutation matters. Do not weaken the lease to individual
writes without protecting the read/decision/write sequence and stale prior-run
observations. Existing unconditional `write_file` is not a compatible checked
mutation binding merely because its replacement write is atomic.

### Durability and bounded memory have measurable costs

The proposed single write owner and commit-before-receipt semantics provide a
clear transaction boundary. FULL synchronization, queue wait, retained originals,
candidate scans and workspace leases all carry costs; async submission avoids
blocking the scheduler but does not make storage commits concurrent or free.

The design already supplies bounded queries, queue/byte limits, reserved control
capacity, checkpoints and separate storage/tool workers. These are sufficient
mechanisms to begin implementation. Measure commit/queue/model/cleanup durations
and behavior under bursty input before making scale or throughput claims. There
is no evidence here for a specific supported member count or cost reduction.

## Extensibility assessment

Impact is relative architectural scope, not a delivery-time estimate.

| Extension | Expected scope | Boundary to preserve |
| --- | --- | --- |
| Round-robin, addressed selection, priority ordering | Small policy change after runtime exists | Pure functions, stable request meanings and recorded state |
| Staged review and response collection | Moderate policy work | Expected participants, partial failures, queued work and completion evidence |
| New compatible tools/providers | Agent composition/capability work | Full execution declarations, permissions, context and cleanup ownership |
| Other peer organizations with shared visibility/lifecycle | Policy plus application composition | No organization-specific branches in Agent core |
| Local GUI | Application/service adapter | Commands, receipts, status and event presentation remain separate from scheduling |
| Shared versus separate workspaces | Application profiles and tool bindings | Lease and checked-edit guarantees must match the selected profile |
| Model-based selector | New asynchronous decision operation/result lifecycle | Pure policy cannot perform model I/O; record usage/cancellation and reject stale results |
| Private channels or restricted subgroups | Cross-cutting visibility/access contracts | Storage queries, context, commands and membership must all enforce visibility |
| Another persistence backend | Contained runtime storage work | Preserve atomic acceptance, receipts, revisions and consistent bounded reads |
| Live membership/policy changes | New state-transition rules | Current idle-only replacement does not imply hot replacement |
| Distributed execution or recursive organizations | Substantial ownership/transport/recovery work | Local handles and leases cannot simply become remote references |

The last rows are intentionally outside the first scope, not missing features
that should be added now. Stable semantic records make those directions possible
to investigate; they do not eliminate their design cost.

## Implementation feasibility and acceptance boundaries

| Work area | Current footing | Main proof required |
| --- | --- | --- |
| Independent Agent/application composition | Existing facade, ModelClient port, per-member sessions/registry and neutral builder | Standalone Agent still operates without Group imports |
| Execution settlement | Proposed capability; current guard can release after exceptional cleanup | Real worker/provider ownership survives cancellation, including sync facade lifetime |
| Async dispatch | Existing synchronous executor and override surface | Supported overrides still validate before effects; provider iteration/closure preserve task context |
| Transactional collaboration | New Group records/runtime | Lost receipts, stale plans, closure races and bounded capacity preserve accepted work |
| Policy composition | Defined deterministic pipeline | Swap addressed/staged policies and then one ordering function without runtime/tool changes |
| Protected workspace mutation | Existing paths/permissions, but new checked bindings required | Concurrent and cross-run stale-base scenarios cannot silently overwrite peers |
| Snapshot export | Optional later feature | Consistent frozen data, complete archive references and suspended restore |

Source inspection confirms the compatibility constraints are real:
`core/agent_runtime/runtime.py:151` deliberately dispatches through the injected
executor; `tests/test_runtime_compat_dispatch.py:96` covers custom validation
across four execution facades. `tests/test_agent_execution.py:284` exercises
provider task/context ownership. These existing tests were read, not rerun here;
they do not establish correctness of the proposed new async/settlement paths.

Keep the first acceptance slice focused: generic settlement/async prerequisites,
transactional addressed collaboration, then staged collaboration on the same
runtime. A read-only analysis/review task can validate coordination before
introducing shared writable workspaces. Snapshot export, remote transport and
plugin discovery need not delay that slice. No default policy is selected by
this review, and no universal workflow abstraction is needed.

## Review scope

Two independent read-only reviews covered implementation feasibility and
extension boundaries. Their findings were checked against the draft and current
source; storage/workspace/performance and lifecycle tradeoffs were reviewed
separately. Only this review document was added. The two designs and runtime/test
files remain unchanged. No model calls, benchmark, execution probe or regression
suite was run; conclusions concern architecture and integration feasibility.
