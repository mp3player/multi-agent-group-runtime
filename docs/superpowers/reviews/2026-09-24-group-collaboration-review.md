# Group Collaboration Design Review

Date: 2026-09-24

Reviewed draft: [Group collaboration design](../specs/2026-09-24-group-collaboration-design.md).
Reviewed SHA-256: `8576647cc5a0d29dfe19368c6c3faccf0eb7bf2c7aa046d799d2597c4eeb4396`.

## Assessment

Keep the architectural direction. Composing independent Agents, separating
collaboration commands from scheduling decisions, and giving one runtime
authority over accepted state form a coherent foundation for local peer
collaboration. No finding requires a return to the archived implementation or
a supervisor/subagent model.

The draft is not yet an implementation contract. One integration assumption
needs correction, and five behavioral contracts need closure. Most are
elaborations of decisions the draft already marks open, rather than internal
contradictions. Calling the design mature would require executable acceptance
evidence that does not exist yet.

Review covered three independent lenses (policy/tool contracts, concurrency,
history/context) plus direct inspection of current Agent integration. Findings
were checked against the actual draft; acknowledged choices are distinguished
from defects. Prior single-Agent acceptance does not establish Group correctness.

| Dimension | Judgment | Practical implication |
| --- | --- | --- |
| Agent base and dependency direction | Sound | Group composes the public Agent API; no Group branches belong in its execution loop. |
| Tools versus policy | Sound | Tools express accepted collaboration intent; policies propose execution; runtime commits facts. |
| Policy replacement | Sound boundary | Shared request meanings and recorded outcomes survive replacement. |
| Function composition | Incomplete | A replaceable `decide` function does not yet demonstrate composable strategy functions. |
| Concurrency and lifecycle | Good invariants, incomplete transitions | Atomic admission is specified; waiting, closing and exception settlement need precise behavior. |
| History and context | Sound separation | Preserve originals while bounding queries, candidate discovery and member input. |
| Performance | Plausible, unmeasured | Bounded snapshots and rule evaluation avoid needless model calls; blocking tools and storage work can still stall the event loop. |
| Implementation scope | Feasible with focused components | No distributed framework, plugin loader or second private-history engine is needed. |

## Findings

Priorities describe implementation sequencing, not existing production incidents:
P1 should be resolved before building the execution adapter; P2 should be
resolved before implementing the affected protocol.

### R1 — P1: Terminal outcomes need both iterator and exception handling

**Evidence:** draft lines 433–436 recommend direct consumption of
`Agent.arun_events()` and describe its terminal statuses. In
[react_loop.py](../../../core/agent_runtime/react_loop.py), line 197 yields
`run_end` only after normal execution. Lines 207–230 publish an end event to
observers during exceptional cleanup, then propagate the original exception.
The iterator does not yield that exceptional terminal event.

**Failure scenario:** an assignment starts, the provider raises, and Group
settlement only handles yielded `run_end`. The Agent releases its own running
guard, but the Group assignment/reservation can remain active or the exception
can escape the scheduler without a recorded outcome.

**Minimal correction:** define a Group execution adapter that normalizes normal
terminal events, execution exceptions, cancellation and explicit stream closure.
Launch failures also need outcomes when no Agent run ID exists yet. Record one
assignment outcome, await actual cleanup before releasing execution ownership,
and propagate cancellation as appropriate. Ordinary observer callbacks remain
observational. No Group-specific change to Agent core is required.

**Evidence from an offline probe:** five cases exercised the real Agent API with
a deterministic model; no provider or `.env` configuration was used.

| Case | Iterator terminal status | Observer terminal status | Caller exception | Agent active afterward |
| --- | --- | --- | --- | --- |
| Normal completion | completed | completed | None | False |
| Model raises ValueError | None | error | ValueError | False |
| Model raises CancelledError | None | cancelled | CancelledError | False |
| Model raises AgentTimeoutError | None | timeout | AgentTimeoutError | False |
| Explicit close after run_start | None | closed | None | False |

The timeout case injected the typed exception; it was not a wall-clock timeout
test. Explicit closure used `aclose()`. This probe verifies the integration
contract, not Group recovery, and does not demonstrate a defect in Agent cleanup.

**Acceptance addition:** each path settles a started Group assignment once,
preserves accepted posts, and permits a subsequent member run after cleanup.

### R2 — P2: Function composition needs an executable contract

**Evidence:** draft lines 261–266 say helpers require input/output contracts and
ordering, while lines 278–303 define only the outer `decide` boundary.

**Failure scenario:** a selection function proposes B while a phase/completion
function considers the phase finished. The result depends on whether completion
sees the old state, proposed assignments or a provisional collection. Two large
policy functions could pass the existing walkthroughs without satisfying the
user's desired function-level composition.

**Minimal correction:** specify one small deterministic pipeline, including
intermediate data, state ownership and conflict rules. For example, reduce
recorded outcomes into phase/collection progress, derive eligible work, select
run proposals, then evaluate waiting/completion against recorded and proposed
work. This ordering is a candidate for discussion, not a new approved design.
Policy state and dependent assignments still commit together. Ordinary functions
are sufficient; arbitrary plugin composition is unnecessary.

**Acceptance addition:** replace only a selection/ordering function in each
walkthrough while retaining collection/completion functions, tool contracts and
runtime. Conflicting proposals produce a deterministic result or explicit error.

### R3 — P2: Request dispositions need shared records

**Evidence:** draft lines 246–251 promise observable later deferral/refusal.
Lines 298–303 list runs, waits, policy state and completion, without an explicit
request-disposition proposal. Lines 355–359 require replacement to retain
unresolved inputs and avoid replaying settled work.

**Failure scenario:** a staged policy accepts a request to B, later refuses it,
and records that decision only in private policy state. A replacement policy
cannot distinguish that refusal from unresolved work without understanding its
predecessor's internal schema.

**Minimal correction:** include request dispositions in the normalized records
and runtime-committed policy proposals. Distinguish temporary deferral from
terminal refusal, retain request identity and reason, and specify their effects
on pending-work accounting. This does not require a task DAG.

**Acceptance addition:** refuse a previously accepted request, replace the policy
at an allowed boundary, and verify that the refusal remains observable without
replaying the request or interpreting the old policy's private state.

### R4 — P2: Waiting needs a revision-safe progress rule

**Evidence:** draft lines 305–307 require event-driven wakeups; lines 328–331
require revalidation of stale plans. Their connection is unspecified.

**Failure scenario:** snapshot revision 10 is selected; a member finishes at 11;
the old plan is rejected; the scheduler waits after consuming the only wakeup.
Eligible work then remains pending without another state change.

**Minimal correction:** stale rejection caused by a newer revision triggers
reevaluation. Entering a wait must check for unseen relevant state changes at
the same authoritative boundary. A single owner performing snapshot, pure
decision and commit without yielding may exclude this interleaving entirely;
document that choice if used. Do not introduce a distributed synchronization
mechanism for a local runtime.

**Acceptance addition:** a completion between selection and waiting either
invalidates the wait or leads to another decision, never an indefinite sleep.

### R5 — P2: Bounded candidate discovery needs continuation semantics

**Evidence:** draft lines 288–292 bound policy views; lines 345–348 prohibit
policy storage I/O; lines 480–483 permit backlog batching. Wakeups otherwise
follow state changes.

**Failure scenario:** the first candidate window contains only deferred requests;
an eligible request lies in the next window. The policy waits and no external
state changes, so the eligible request is never discovered. The draft permits
this implementation even though it does not require it.

**Minimal correction:** expose continuation/exhaustion information, or guarantee
that runtime candidate construction discovers eligible work across windows.
Advancing discovery must schedule bounded further evaluation while yielding to
other work. Exhausting an unchanged candidate set must stop scanning until a
relevant change. A discovery cursor must not settle deferred requests or become
a delivery/completion cursor.

**Acceptance addition:** deferred requests fill the first page and an eligible
request occupies a later page. It progresses without a new user message, while
deferred requests remain pending and unchanged scans do not spin indefinitely.

### R6 — P2: Completion needs an admission boundary and work scope

**Evidence:** draft lines 511–518 mention completion scope and invocation cleanup
but do not define which accepted requests belong to that scope.

**Failure scenario:** completion commits at revision 20; a new request is accepted
at 21 before the invocation returns. Revalidation was correct at commit time,
but the receipt does not establish whether this work belongs to the closing
invocation, a subsequent invocation or pending Group work awaiting another run.

**Minimal correction:** define scope membership and atomically close admission
to a finishing scope. Later input is explicitly rejected or associated with an
open/subsequent scope, with observable pending status and a defined activation
path. Include accepted but unassigned requests, not just queued/running
assignments. A simple invocation generation may suffice.

**Acceptance addition:** input arriving immediately before and after completion
commit has a deterministic owner/disposition; none is acknowledged into a scope
that has already stopped processing it.

## Open choices that are not new defects

- **Publication and durability:** choose the public final-answer rule and local
  publication/source identity. Define the commit boundary for original content
  and associated response requests before acknowledging acceptance. Do not
  deduplicate by text or promise exactly-once external effects.
- **Blocking tools and cancellation:** current synchronous handlers execute on
  the event-loop thread (`react_loop.py`, lines 191–196). One blocking call can
  delay every member and cancellation handling. Initial deadlines must be
  described as cooperative at these boundaries unless execution isolation is
  implemented. Stop admitting new work when control returns; do not report an
  active operation as cancelled and cleaned up merely because a deadline passed.
  Threads alone do not make arbitrary external effects cancellable.
- **Member input budget:** the current Agent adds a run input as a User message
  (`turn_machine.py`, line 71), and compaction pins the newest User message
  (`context_management/policy.py`, lines 73–78). Passing an entire Group backlog
  as one input cannot be repaired by ordinary private-history compaction.
  Implement the draft's explicit input budget, references and paged retrieval.
- **Storage and snapshots:** select storage for the required transaction and
  durability behavior. For eventual idle snapshots, freeze admission at the
  capture boundary and define included or immutable referenced member snapshots
  and archive availability checks. Crash resume remains a separate feature.
- **Workspace mutation:** decide writable roots or serialized mutation before
  exercising concurrent coding members. Private sessions do not isolate files.

## Comparison and implementation judgment

[Pi's repository](https://github.com/earendil-works/pi) exposes separate Agent
runtime, provider API and coding application packages. This supports borrowing
its composition boundary; it does not validate MAS Group behavior.

[AutoGen's documented Group Chat pattern](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html)
distinguishes public messages, requests to speak and termination. It is a
sequential manager example, explicitly presented as a starting point rather than
a production application. It supports the separation of responsibilities, not
a claim that this design's concurrent scheduling is already proven. Both sources
were checked during this review on 2026-09-24.

Within a shared public conversation, ordering, member selection, response
collection and stages can reasonably vary through policy functions. Different
privacy rules, execution isolation or resource ownership need their own explicit
configuration/contracts; scheduling alone cannot express every organization.

Implementation is feasible using the existing Agent facade, independent member
sessions/tools, one runtime owner, a local store and ordinary policy functions.
The main effort lies in state transitions and integration tests, not model-call
plumbing. Avoid a class/plugin for every logical responsibility, a generic policy
combinator language, distributed messaging and automatic replay recovery in the
first version.

The next design revision should close R1–R6 through two complete traces:
addressed cooperation and staged collection using the same runtime. Each trace
should include refusal, failure, waiting and closing. Retain the undecided default
policy. Then implement and validate the first focused slice. There is no evidence
for a throughput, cost or long-running Group reliability claim at this stage.

## Changes and verification scope

Only this review document was added. The reviewed draft and runtime code were
not edited. Review included source inspection, independent design reviews,
primary-reference checks and the five-case offline integration probe above.
No live model, full regression suite or Group acceptance run was performed.
