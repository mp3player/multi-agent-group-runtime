# Group operational contracts

Date: 2026-09-24

Implementation note (2026-09-26): A restricted synchronous-worker foundation is
now implemented; see [Group foundation](../../group-runtime.md) for the supported
profile. The native asynchronous tool pipeline, shared writes and recovery
contracts below remain target design, not implemented guarantees.

Status (updated 2026-09-26): Design corrections authorized after the
[semantics alignment review](../reviews/2026-09-26-group-semantics-alignment-review.md).
This document complements the corrected
[Group architecture](2026-09-24-group-collaboration-design.md). Its target
contracts do not claim new runtime behavior. The recommended initial profile
below leaves the default scheduling policy undecided. Names express contracts,
not finalized implementation signatures.

## 1. Recommended initial profile

| Concern | Proposed choice | Alternative and reason for this choice |
| --- | --- | --- |
| Execution settlement | A generic Agent execution handle with separately observable outcome and cleanup | An iterator-only adapter cannot observe all existing cleanup failures. |
| Tool execution | Native async handlers plus explicitly classified, tracked sync workers | Keeping blocking tools and durable writes on the scheduler loop delays every member; moving all tools indiscriminately breaks execution assumptions. |
| Public communication | Explicit post/request tools; ordinary final answers remain private | Automatic final-answer publication needs additional duplicate/publication rules and risks exposing unintended material. |
| Local persistence | SQLite, one serialized write owner, WAL and FULL synchronization | A custom append log would also need transaction framing, indexes and partial-write recovery. |
| Shared workspace | Whole-run read/write leases plus expected-base checks for mutation | Per-write locking misses the read/modify decision; leases alone cannot validate observations from earlier runs. |
| Membership | Changes only at an idle boundary with no unsettled execution leases | Live membership changes would add cancellation and routing semantics to the first version. |

These choices form a proposed first profile, not a universal framework or a
scheduling default. Execution/storage guarantees are shared; tools, prompts,
context/output adaptation and response rules must be compatible within an
assembly. They are not necessarily independent configuration switches.

### 1.1 Assembly admission

One strategy entry declares its tools and operation versions, prompt/context
rules, output adapter, deterministic coordination logic, state identity and
required execution capabilities. Validate this declaration before binding member
resources. Check supported effects, name collisions, operation handlers, result
routing and response predicates together. A `report` tool cannot promise feedback
routing when the selected strategy lacks that behavior.

The existing explicit-publication profile remains valid. A supported alternate
profile can expose different tools or no Group tools, with deliberate result
publication by an adapter. It must not require editing the Agent loop or bypass
runtime identity, transaction, capacity and ownership checks. This design does
not require a dynamic plugin loader or arbitrary new database commands.

## 2. Generic Agent execution settlement

### 2.1 Public contract

Create an execution handle before starting model/tool work or changing session
input. Acquiring the Agent's execution lease is atomic; competing starts fail
before adding input. The handle survives consumer cancellation and contains:

| Surface | Meaning |
| --- | --- |
| Run identity and event stream | Identifies this execution; retains current event/status meanings. |
| Primary outcome | Normal result, limit, cancellation or failure; recorded once when computation ends. |
| Cleanup snapshot | Pending, complete or failed/unknown, with bounded operation/resource references and errors. |
| Stop request | Idempotent request to stop this run, with an explicit reason. It is not proof of cleanup. |
| Wait for settlement | Wait for tracked operations and cleanup evidence; a caller timeout/cancellation stops only its wait. |

The existing `run`, `arun` and event/stream facades should share the same lease and
settlement machinery. Preserve their result/exception behavior; do not create a
second Agent loop. A synchronous facade must not close an event loop that still
owns tracked cleanup. If bounded shutdown cannot complete, expose the unsettled
handle/owner rather than silently discarding it. Concrete handle access from
each facade belongs in the Agent capability implementation plan.

Keep primary outcome immutable. A completed calculation followed by cleanup
failure remains a completed calculation with failed cleanup; Group cannot reuse
the member or report fully settled execution. Late stop requests never rewrite
an already recorded primary outcome as successful cancellation.

### 2.2 Track the operation that actually owns work

Register each operation before submission, with its run, resource dependencies,
stop mechanism and completion handle. Keep strong references until completion
has been observed and exceptions retrieved. Completed tracking entries can be
released; unresolved entries cannot be evicted to meet a cache limit.

- **Async model/stream operations:** preserve one owning task/context for provider
  iteration and closure. Do not move only `aclose()` into another task when a
  provider relies on task-local context. If a dedicated driver is used, place the
  entire execution there and validate facade/context compatibility.
- **Thread operations:** retain the underlying worker future separately from
  the cancellable async waiter. Cancelling the waiter does not release the
  worker's resource or workspace lease. Observe actual worker completion even
  after the original run consumer exits.
- **Owned subprocesses:** retain process and pipe-drain handles. Stop the owned
  process group where supported, escalate under the configured stop policy,
  then wait for confirmed exit and pipe cleanup. A timeout is not exit evidence.
- **Accepted storage commands:** retain their submission/operation identity
  through commit resolution; cancelling a tool waiter cannot erase a commit.

Python documents both cancellation shielding and the need to retain task
references. A running executor future cannot simply be cancelled. These are
mechanics for implementing the proposed tracking, not proof of MAS cleanup.
See [asyncio cancellation shielding](https://docs.python.org/3/library/asyncio-task.html#shielding-from-cancellation)
and [executor future cancellation](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel).

### 2.3 Stop, settlement and resource reuse

On stop: prevent further model/tool submissions, signal owned cancellable work,
record the primary outcome, and continue tracking cleanup. Repeated caller
cancellation cannot cancel the settlement bookkeeping itself. A configured
cleanup wait limit bounds caller waiting; it does not convert pending work to
complete or clear its references.

The Agent is reusable only after all owned operations finish and required
provider/stream cleanup is confirmed. Its normal admission guard must honor this
lease as well as the Group adapter; direct standalone calls cannot bypass it.
Group records the primary outcome promptly, but releases execution reservations
only after confirmed settlement. A failed or uncertain cleanup quarantines the
affected member and resources with a visible reason.

The application resource owner tracks leases on shared clients/executors/stores.
Owned resources close once, after their last lease ends; borrowed resources are
never closed by Group. If a resource itself is unhealthy, all members depending
on it stop admitting new work. Unrelated healthy members may continue in an open
invocation. Quarantining only one Agent is insufficient for a shared broken client.

Pending cleanup may later complete normally. Failed/unknown cleanup does not
become healthy after a timer: recovery requires positive cleanup evidence or a
new isolated resource with the old resource still tracked until safely retired.
An invocation that is closing remains unsettled while any such operation remains.
Arbitrary in-process code can ignore cancellation; this contract makes that
failure observable rather than promising forced termination or automatic recovery.

## 3. Async tools without changing collaboration semantics

Add a generic asynchronous dispatch path to the existing tool runtime, retaining
one permission/workspace/audit/result pipeline. Composition declares execution
requirements for the entire pipeline, including custom executors, validation and
permission hooks, result adapters and audit sinks. Classifying only the registered
handler as async or fast is insufficient. The model cannot choose these modes
or effect classifications.

| Execution kind | Rule for handlers and surrounding pipeline stages |
| --- | --- |
| Native async I/O | Await in the Agent execution context; register owned work with the execution handle. |
| Fast synchronous, nonblocking | Invoke directly only when explicitly declared suitable for the loop. |
| Blocking synchronous | Use an explicitly permitted bounded worker, retain its real completion future, and propagate a separate copied workspace/context per operation. |
| Unknown/custom thread-affine code | Require an explicit execution declaration; do not silently move it between threads. Unsupported Group bindings fail at composition. |

Preserve serial tool order within one Agent turn and the existing permission,
failure and ending-tool semantics. Never execute an operation twice while trying
sync/async fallbacks. Cancellation propagates through the lifecycle contract; it
is not an ordinary tool failure that implies no effect happened. Preserve tool
call/result pairing with an interrupted/uncertain outcome when necessary.

Group post/query/request tools use the async collaboration API so they can await
storage without blocking the scheduler. Pure yield may remain a fast local tool.
Keep sync-only standalone bindings usable. Reject async-only bindings from an
unsupported synchronous entrypoint before executing them; do not call a nested
event loop per tool. This changes the earlier preference for synchronous Group
publication, while preserving the tools/policy/runtime dependency boundary.

Bound worker counts and queued bytes/jobs independently of member capacity.
Use separate storage and general-tool worker capacity, so slow tools cannot
occupy every worker needed to commit their own outcomes. Adapters for custom
executors must preserve existing override behavior; composition cannot bypass
an injected executor by reaching directly into its registry.

Prefer a native async subprocess adapter for the built-in terminal tool. The
existing POSIX process-group stop behavior and bounded output capture remain
required. Unsupported platforms must report the restriction. Threads support
known cooperative blocking calls; they are not a hard-stop boundary or sandbox.

### 3.1 Full-pipeline admission and ordering

Validate every configured stage's execution mode, thread/context affinity and
bounded input/output contract at composition. Inline execution requires a known
nonblocking implementation, not merely a synchronous signature. Unclassified
custom hooks fail Group composition with the component and missing declaration
identified. The whole supported path must be compatible with the chosen profile.

| Stage | Required behavior |
| --- | --- |
| Argument validation and permission | Complete the configured checks before any effect. Denial, check failure or cancellation before authorization cannot start the handler. |
| Executor and handler | Preserve injected executor validation and invoke the effect once. Opaque custom executors require an adapter/declaration for their whole execution boundary; do not bypass them or run their internal checks a second time. |
| Result conversion | Apply the declared bounded adapter in a compatible execution context. Do not invoke arbitrary blocking conversion or `__str__` code on the scheduler thread. |
| Audit capture and output | Capture bounded identified records in tool order; perform potentially blocking sink I/O through the tracked audit path in section 3.2. |

After an asynchronous permission/admission wait, recheck stop state and required
leases before starting the effect. Worker execution receives the appropriate
workspace/context; thread-affine components cannot be moved just to meet a
latency goal. An async declaration does not excuse blocking inside the coroutine.

Record whether an error occurred before or after the effect. Result formatting
or audit failure after an effect does not prove that the effect failed or did
not happen. Preserve its known result/operation references, report the failing
stage and never re-execute it as a formatting, logging or dispatch fallback.
Authorization and custom validation remain mandatory; observational output is
not allowed to replace their decisions.

### 3.2 Bounded, ordered audit output

For configured audit sinks, reserve bounded record/queue capacity asynchronously
before admitting a tool effect. Use a dedicated bounded ordered writer or an
equivalent declared async sink, separate from authoritative Group storage and
the capacity needed for permission/execution control. Payload limits include
arguments, results and errors. No unbounded task-per-record fallback is allowed.
Release unused reservations on rejection/cancellation before the effect starts.
Once an effect starts, retain capacity for its outcome/interruption record even
if its caller is cancelled; after write submission, the actual write owns that
capacity until completion is observed. An adapted custom executor has one declared
owner for each stage, avoiding duplicate authorization or audit/reservation paths.

Capture a bounded audit envelope and sequence at the execution boundary. Keep
per-run record order through writing; a consumer must not observe later tool
completion ahead of an earlier record from the same run. The initial profile
awaits that tool's audit attempt asynchronously before proceeding to its next
tool. This can delay the originating run, but must leave unrelated scheduler
work and stop/status handling responsive.

When capacity is exhausted, wait asynchronously before the effect or return a
clear pre-effect admission failure according to configured limits. Do not block
the event loop, discard queued records or retry effects to generate replacement
records. A confirmed sink failure is exposed through bounded error counters/status
and does not replace a completed effect's result; failed audit delivery is not
reported as successful persistence. Public originals, acceptance receipts and
execution outcomes still use the authoritative transaction path in section 5.

Track admitted writes and their actual worker completion through the execution
settlement machinery. Cancelling an audit waiter does not prove that the write
stopped; retain its writer/resource lease and report pending cleanup. Shutdown
stops new audit admission and drains or reports the remaining identified work
before closing an owned sink. Sink flush and close obey the same execution
classification and operation tracking. A stuck sink may leave its originating
execution unsettled, subject to the existing quarantine contract; it must not freeze the
scheduler or silently become a flushed/closed resource.

## 4. Publication, receipts and duplicate commands

### 4.1 Explicit public communication

In the proposed initial profile, post/request tools are the only member-origin
publication path. Final answers, reasoning and arbitrary tool output stay in the
private Agent session; the application may inspect the member result separately.
No streamed fragment is an independently dispatchable public message.

A profile that requires a public reply checks for an accepted reply linked to
the required opportunity. A normal Agent final answer without that post records
`completed` execution but missing reply evidence; it does not fulfill that
collaboration rule. Prompt composition explains the rule. A bounded follow-up
attempt is an explicit policy decision, not an invisible retry of tool effects.

Execution settlement and response satisfaction remain separate. A response
collection identifies its request, expected participants/opportunities and the
rule/version that evaluates its evidence. Store accepted evidence references,
missing responses and explicit failure/refusal/partial-result decisions. A
private-only completed run is missing public-reply evidence under this profile;
it is not successful feedback simply because its execution slot settled.

An alternate declared output adapter can select a completed final result for
publication. Give that effect a stable identity tied to the run and adapter
version, retain provenance, and commit it through the ordinary publication path.
Define whether existing explicit posts satisfy or exclude that publication;
do not deduplicate by equal text. Never expose reasoning or arbitrary tool
outputs as a side effect. The selected publication becomes an observation whose
activation effect is decided by the strategy, not an unconditional broadcast run.

An automatic finish proposal identifies its completion rule and evidence set.
Runtime checks current revision, accounted-for work and references before closing;
the strategy evaluates the domain-specific response predicate. The current manual
`finish` API relies on its application to make that judgment and does not yet
implement this automatic-completion contract.

### 4.2 Command identity and acceptance

Every submitted command has an authenticated origin, invocation ID, operation
key and canonical payload fingerprint. The adapter assigns the tool operation
key once from execution-owned call identity/position; provider-supplied tool IDs
alone are not sufficient. A transport retry retains the key. A new model tool
call is a new operation even when its text is identical. Application callers
supply their own retained operation key under their authenticated namespace.

Runtime processes a command in this order:

1. Check the caller's authority to submit/query that scoped operation.
2. Look up a committed operation with the same key. Return its original receipt
   for an identical payload, even if its invocation has since closed. Reject a
   conflicting payload without changing the original record.
3. For new work, validate membership, scope admission, references, operation
   support and size/capacity limits against authoritative state.
4. Commit original content, response slots when applicable, the operation record,
   receipt and new revision in one transaction. A combined post/request cannot
   commit only its informational half.
5. Publish the committed revision to runtime state/wakeup processing and deliver
   the receipt. Queue submission or a write in progress is not acceptance.

The receipt contains operation, message/request and invocation IDs, committed
revision, and whether the command creates dispatch-eligible work. It never claims
that a target has started unless a separate assignment record proves it.

Cancellation before a queued command begins can withdraw it with an explicit
not-accepted result. Once processing may have committed, waiter cancellation or
a lost receipt means unresolved delivery, not proof of failure. Keep tracking;
query/retry the same operation key to discover the committed receipt. Do not
blindly submit another key. The operation query is available through the same
authorized collaboration interface, not a policy implementation.

Deduplication is local transactional command identity. It does not guarantee
exactly-once model calls, filesystem writes or arbitrary external effects.
Closing-scope rules from architecture section 8.1 still apply to new commands;
returning an old receipt neither reopens the scope nor redispatches its work.

## 5. Local storage and state ownership

### 5.1 One authoritative transaction path

Use Python's SQLite binding with one application-owned write connection on a
dedicated worker. A bounded async command queue connects Group tools, scheduler
plans and execution outcomes to this owner. The worker is an internal part of
Runtime, not a second scheduler or policy engine. All authoritative state changes,
including invocation closure, follow this path.

Use a local filesystem, WAL mode, `synchronous=FULL`, foreign-key checking and
an explicit bounded busy timeout; verify the effective settings on open. Never
silently downgrade the selected durability profile. SQLite WAL permits readers
alongside one writer; FULL adds WAL synchronization on commit. Its guarantees
depend on the filesystem/storage honoring synchronization, and WAL is not the
chosen format for a shared network filesystem.
[SQLite WAL](https://www.sqlite.org/wal.html),
[SQLite synchronization settings](https://www.sqlite.org/pragma.html#pragma_synchronous).

Logical records cover originals/reply links, command receipts, invocations,
opportunities, assignments/outcomes and policy state/revisions. Keep original
content immutable; current-state indexes and recorded transitions can be updated
transactionally. This is not a requirement to implement general event sourcing,
an ORM, or an interchangeable storage framework.

Policy reads detached bounded snapshots, evaluates without I/O, and submits a
versioned plan. Each snapshot page and its authoritative counts come from one
consistent read transaction/revision, released after materializing the bounded
view. The write owner revalidates at commit. Async submission means a
plan can become stale: it must be rejected and re-evaluated, never overwrite a
newer message or closing scope. Update runtime mirrors before delivering command
receipts or exposing a newer snapshot; use the existing revision-safe wake rule.
Successful database commit is authoritative even if receipt delivery is cancelled.

Launch requires a committed assignment, open launch gate and current member/
resource leases. Never hold a database transaction over model inference, tool
execution, cleanup, or waiting for workspace access. Group cancellation closes
the in-memory launch gate promptly and submits authoritative closure through
the same transaction path; commands racing it follow their actual commit order.

### 5.2 Failure and capacity behavior

- Queue full: apply bounded backpressure or explicitly reject before acceptance.
  Once enqueued, account for bytes/jobs until the command resolves. Reserve queue
  slots/bytes for bounded outcomes and closure/control operations when admitting
  assignments, so ordinary publications cannot consume all settlement capacity.
  Coalesce repeated stop signals; the control path must not become an unbounded
  second queue. Reserved capacity does not bypass transaction/admission ordering.
- Stale plan: commit nothing from that plan and reevaluate. Ordinary bounded
  contention can retry within a deadline; unchanged invalid plans cannot spin.
- Write/commit error: return a definite rejection only if rollback is confirmed.
  An uncertain result is resolved by operation lookup after store recovery;
  never report acceptance without a committed receipt.
- Store unavailable: stop new admission/launch, signal active runs to stop,
  retain pending outcome/cleanup tracking and expose storage failure. An external
  effect whose outcome could not be recorded is explicitly uncertain; no replay.
- Quota pressure: account for database, WAL, snapshots and archive footprint.
  Reserve a configured allowance for bounded settlement/control records before
  admitting more work. Unexpected disk failure can still defeat that allowance
  and follows the store-failure path. Never delete accepted originals to proceed.

Release readers promptly, use bounded indexed queries, and checkpoint WAL under
the storage owner's control. Checkpointing moves database pages; it does not
remove logical message history. No transaction or complete transcript stays
open/in memory for the whole conversation. Exact caps are explicit configuration,
with acceptance evidence for bounded growth rather than invented throughput claims.

On startup, retained nonterminal work is visible but not automatically resumed.
Mark interrupted execution as requiring reconciliation, not successful or safely
retryable. Restoring data is not reconstructing live resource ownership; automatic
crash recovery and external-effect replay remain outside the initial contract.

## 6. Workspace consistency

The application supplies a workspace identity and access profile for each member
run. For a shared workspace, read-only runs hold shared read leases and any run
capable of mutation holds an exclusive write lease for the entire run, including
pending cleanup. Acquire all required workspace leases in a stable order before
consuming an active execution slot. Queued work remains part of its collection.
Normalize roots and detect aliases/overlap at composition; independently named
workspaces must not bypass coordination over the same files. A waiting writer
prevents newly arriving readers from indefinitely bypassing it. Group tools
return command receipts rather than waiting for another member's execution
while the current run holds a workspace lease.

Do not upgrade from read to write halfway through a run. A read-only profile has
no permitted mutating tool path; requesting mutation requires another explicitly
admitted run. Terminal/custom tools with unknown effects require write access.
Leases protect a fresh read/model-decision/write sequence among participating
members; a lock around only `write_file` would leave stale-read overwrites possible.
Observations retained in private context from a previous run are potentially stale.

The shared mutation profile must refresh affected content under the current
lease and require expected-base validation before applying an edit, overwrite,
delete or move. Use a runtime-issued file observation token with canonical path
and content version/digest; creation asserts absence. Mismatch or missing base
evidence returns a conflict/needs-read result with no mutation. Tokens are
preconditions, not reservations across runs. This requires a checked mutation
capability or binding; the current unconditional `write_file` is not sufficient.

This deliberately serializes write-capable runs on the same workspace. For
parallel mutation, use separate writable roots or worktrees and explicit artifact/
patch exchange; merging remains a separately requested action. Shared read-only
inputs and public result references can still support concurrent analysis.

Bind the correct workspace context inside each worker. Preserve existing path
validation and permissions. Unchecked full-file writes and arbitrary mutating
terminal/custom handlers cannot claim expected-base protection: the proposed
shared mutation profile rejects these bindings. Run them in separate writable
areas, or explicitly select a weaker serialized-only profile with that limitation
visible. These leases/checks coordinate participating tools, not external editors
or unrestricted OS access. Full sandboxing, protection from every external race
and automatic conflict merging are not implied by this proposal.

## 7. Context projection and eventual snapshots

### 7.1 Reception, inference and provenance

The proposed [public-chat reception profile](2026-09-26-group-reception-design.md)
replaces optional recent-background selection with complete member reception
and defines verified projection, preparation ownership and closing drain rules.
The following contracts remain the general baseline; the addendum is a design
proposal rather than a description of the current implementation.

Construct each member input from explicitly identified triggers plus a bounded
selection of background, with sender/reply/source IDs. Track projection per run
so repeated activation does not repeatedly append the entire shared transcript.
Visibility tracking is still not delivery or completion tracking. Content from
another member is attributed content, not an elevated system instruction.

The strategy declares whether a public message remains available for later
projection, is staged through a no-inference adapter, or creates a proposed run.
The driver supplies bounded public-message observations even for posts without
response opportunities. History-query tools remain supplementary; an idle member
cannot invoke them to decide whether it should be activated.

Record a projection manifest identifying member, assignment or reception
operation, source message IDs, source revision and adapter/version. Distinguish
eligible, staged and included-in-request facts. Apply projection through supported
Agent interfaces under exclusive member ownership. Inputs arriving during a run
remain for a later safe admission; never mutate an in-flight provider request or
an active tool-call/result sequence from the Group loop.

A synthetic history representation is an optional adapter technique. Identify
runtime-authored entries and keep that provenance available to later summaries
and history export. It cannot count as model understanding, a model-authored
acknowledgment, public speech or response fulfillment. Fabricating a tool call
must not execute side effects or falsify an audit record of model activity.
A separately authorized runtime operation uses its real origin and outcome.
The adapter must be tested against the supported history/provider format.
No synthetic PASS mechanism is mandated. A no-inference operation must never
enter the model execution path just to make the model choose silence.

Do not advance delivered positions merely because representation preparation
succeeded. Record application failure or uncertain application explicitly and
do not blindly repeat an uncertain session mutation. Group and private-session
storage are not assumed to share a transaction. Later crash reconciliation
remains outside the current profile.

Before Agent invocation, budget rendered input together with system/tools,
retained private context, summary allowance and output reserve. If it cannot fit,
use references and bounded retrieval. Accepted oversized work remains recorded
with a clear failed/needs-input disposition; ingestion cannot silently truncate
the original. The Agent's existing private compaction/archive remains responsible
for its private history; Group does not implement a second private-history engine.

### 7.2 Eventual snapshots

Whole-Group snapshot export can follow the core runtime. Its contract is:

1. Freeze admission, queued launches and configuration at an idle boundary with
   confirmed Agent settlement; queued assignments may remain as data. Drain
   accepted storage writes and freeze private session/archive mutation.
2. Capture Group records, policy identity/state, invocation/work records and each
   member session plus the archive originals required by its references.
3. Produce a manifest with schema/config versions, source revision and content
   hashes. Store configuration references without copying credentials.
4. Build a self-contained bundle in a temporary destination, verify all references
   and publish it atomically. Failure leaves the previous snapshot intact.
5. Restore into a suspended Group after validating schema, hashes and membership/
   tool bindings. Missing archives are an error. Never launch queued work, replay
   effects or mutate the caller's existing Group as a side effect of loading.

No cross-store live transaction is needed for this idle snapshot. The freeze
must include private session/archive mutation; a main database copy alone is not
a complete Group snapshot. Automatic run recovery remains deferred.

## 8. Diagnosable progress and acceptance gates

Expose bounded status views identifying invocation, request/opportunity,
assignment, Agent run and operation IDs. Include why work waits: phase/member,
capacity, workspace lease, storage or cleanup. Distinguish a provider outcome
from resource health. Observational log/telemetry failure cannot erase a committed
command or become the only execution-control path.

Record model/tool/queue/commit/cleanup durations separately and attribute main
and summary usage to the member and invocation. This allows measuring the cost
of FULL synchronization and workspace serialization instead of assuming either
is free. Diagnostic buffers are bounded; originals remain in durable storage.

| Failure/interleaving | Required evidence |
| --- | --- |
| Caller cancels while a worker still runs | Worker remains tracked, member/workspace lease stays held, later completion is observed. |
| Provider close fails after a model error | Both errors remain visible; shared affected resources are not reused or prematurely closed. |
| One slow tool overlaps another member's model call | Scheduler and status handling remain responsive for the supported execution profile. |
| Fast/async handler has a slow permission hook or custom executor | Full-pipeline classification routes permitted blocking work safely; no effect starts before authorization or after a stop during the wait. |
| Slow audit sink or full audit queue | Unrelated model/status/cancellation work remains responsive, records and memory stay bounded, and pre-effect admission provides backpressure. |
| Formatting or audit fails after a real effect | The effect occurs once, its known outcome remains available, and the failed stage is reported without effect replay. |
| Audit write outlives cancellation | Its actual operation/resource lease remains tracked; shutdown does not report a false flush or close. |
| Unsupported thread-affine hook is composed | Composition fails clearly before execution; no silent worker move or bypass of executor validation occurs. |
| Command commits but tool receipt is lost | Same operation key returns the original receipt; one public record and one request set exist. |
| Same operation key arrives with different payload | Explicit conflict; original content/receipt unchanged. |
| Publication races invocation closure | New work is accepted before the fence or rejected after it; a retry of an old accepted command returns its receipt. |
| Disk/queue limits are reached | No false acceptance, dropped original or unbounded queue; settlement capacity and failure reporting remain observable. |
| Two members intend to edit one file | Run leases prevent overlapping participating writers; stale checked edits fail instead of overwriting unnoticed. |
| A reads, B edits, then A resumes with old private context | A refreshes and passes expected-base checks before mutation; stale/missing evidence cannot overwrite B's change. |
| Snapshot export fails or archive is missing | No partial snapshot is published; restore neither loses history nor starts work. |
| A plain post arrives without response opportunities | The driver exposes its identified bounded observation; policy evaluation needs no hidden store access. |
| Background arrives while a member is idle or running | The selected delivery rule stages or projects it at a safe boundary; no unrequested model call or in-flight history mutation occurs. |
| A synthetic representation enters later summarization | Its runtime provenance remains identifiable; it is never evidence of a member judgment or real model/tool execution. |
| A member completes privately under a public-reply contract | Execution settles, missing reply evidence remains explicit, and automatic normal completion is refused until the declared predicate is satisfied. |
| Two broadcasts and their replies interleave | Each collection checks its own expected participants and linked evidence; no latest-broadcast or global-idle heuristic substitutes for correlation. |
| New replies continually request more execution | Atomic cumulative admission and deadline limits stop the chain with a limited outcome, preserving accepted work and cleanup ownership. |
| A strategy changes its tools or output adapter | Compatible assembly succeeds without core execution/storage edits; unsupported promises fail before activation. |

Implement and validate in dependency order: generic Agent settlement and async
tool dispatch; transactional commands/storage; Group scheduling/admission;
workspace/context integration; then optional snapshot export. This is a design
dependency order, not an implementation authorization or detailed work plan.
Use deterministic faults/interleavings first, then repeated end-to-end model
tasks. Prior single-Agent soak evidence does not establish these new guarantees.

## 9. Review follow-up

The [2026-09-26 feasibility review](../reviews/2026-09-26-group-feasibility-extensibility-review.md)
identified F1: handler-only execution classification leaves synchronous hooks
and audit output able to block the scheduler. Section 3 now covers the full
pipeline, sections 3.1–3.2 define ordering/backpressure/cleanup, and section 8 adds
the corresponding acceptance scenarios. This closes the design gap; no runtime
implementation or responsiveness test is claimed. The historical review is
retained unchanged.

The [semantics alignment review](../reviews/2026-09-26-group-semantics-alignment-review.md)
identified five extension-boundary corrections. Assembly admission is defined in
section 1.1, output/response evidence in section 4.1, and context/provenance in
section 7.1. The architecture's sections 4 and 8.2 define richer driver inputs
and cumulative conversation bounds. Section 8 above adds their acceptance gates.
These changes resolve the design wording and contracts; the manual foundation
still lacks the corresponding runtime extensions and automatic driver.
