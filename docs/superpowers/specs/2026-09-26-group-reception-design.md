# Group reception and activation

Date: 2026-09-26
Status: Design proposal; no runtime changes implemented by this document.

Boundary review: 2026-09-26. The rules below are proposed acceptance contracts,
not claims that the existing runtime already provides these guarantees.

This refines sections 5 and 7 of the
[Group collaboration design](2026-09-24-group-collaboration-design.md).
It incorporates the agreed direction: collect each member's complete unread
snapshot, allow reception without inference, and support a runtime-generated
assistant response as a context representation for passive reception.
For this public-chat profile, complete member reception replaces the optional
recent-background selection in section 7.1 of the
[operational contracts](2026-09-24-group-operational-contracts-design.md).
The remaining storage, execution ownership and shutdown contracts still apply.

## 1. Scope and evidence

The archived implementation rendered the assigned unread messages in full.
Its synthetic PASS path appended a User message, an assistant tool call and a
tool result, advanced the read cursor, then cleared active context. Consequently,
an original could remain in audit history but disappear from later model input.
See the [legacy investigation](../reviews/2026-09-26-legacy-unread-reference.md).

The current implementation has a durable public log and explicit response work,
but no member reception ledger. It supplies full triggers plus a bounded recent
background window. Its driver returns when no work is eligible. Agent sessions
are independently owned and are not atomically persisted with Group SQLite.

This proposal keeps the public log, opportunities, assignments and peer model.
It adds reception/projection contracts and an owned event-driven service. It
does not implement all legacy policies, dynamic membership, private messages,
automatic crash recovery or exactly-once tool effects.

## 2. Reception and action are independent

Under the initial public-chat visibility rule, directed messages remain visible
to peers. A recipient is a routing target, not an access-control boundary.
Members receive eligible originals even when they are not selected to respond.
Visibility rules are fixed for an invocation. Self-authored public records are
covered too. Their text need not be echoed when a verified private context/archive
binding already covers the content and public source identity. Authorship or an
outgoing publication receipt alone is not that proof. If a replacement session
lacks it, include the original as attributed historical content. Explicit
self-directed work, if allowed by a strategy, still needs activation evidence.

A scheduling cycle captures a high-water sequence S. For a member, its logical
reception batch covers every eligible original after the previous reception
frontier through S, in order. Database pagination may divide this operation;
it may not turn it into a latest-N window or select only triggering messages.
Later arrivals belong to another batch. Response triggers are identified
separately and may refer to messages received in an earlier cycle.

The strategy decides whether to activate a member and for which work. The
runtime constructs and validates complete reception coverage. The resulting
member action can be reception only, reception plus an admitted execution, or
reception with execution deferred. A member's busy state defers execution, not
durable reception. Policy-specific tools cannot advance reception frontiers or
write fabricated responses themselves.

An informational post is passive under OnDemandStrategy. Other explicitly
selected policies may activate members from such posts. Neither interpretation
is a universal runtime rule.

### 2.1 Coverage and ordering invariants

- The frontier means "every eligible source through this sequence is covered",
  not "the largest selected ID". Sequence gaps need no fabricated message, and
  self-echo suppression needs verified existing coverage rather than a skipped ID.
- Every page in a batch uses the same captured high-water and membership/rule
  version. An empty eligible page can advance the examined frontier without
  generating a synthetic assistant entry. Repeating the same empty scan is a no-op.
- Reception pages and their frontier updates commit together. Partial progress
  is a contiguous prefix. References outside the source scope, unknown IDs,
  overlapping conflicting batches, and changed content under an existing ID
  are rejected before application.
- Originals are immutable. Corrections are new messages; they do not rewrite
  earlier inputs, receipts or completed outcomes. A correction changes active
  work only through an explicit policy/runtime action.
- An admitted assignment's input boundary must cover its triggers and declared
  required sources. Urgency cannot silently skip older eligible sources; bounded
  preparation or an explicit budget blockage is required.

## 3. Durable reception, then controlled projection

Two implementation approaches are possible:

| Approach | Benefit | Cost |
| --- | --- | --- |
| Immediately append each passive batch to every Agent session | Simple correspondence with the old implementation | Requires idle ownership, duplicates text in memory, and couples acknowledgements to volatile session state |
| Persist member reception batches, then automatically project them at an owned Agent boundary | Handles busy members and failed projection without losing the reception record; allows batching | Needs an explicit projection receipt and context-ingestion interface |

Recommend the second approach. Acceptance of a reception batch happens during
scheduling, without a model call. Actual session materialization can happen at
an idle preparation boundary, and must finish before the member's next business
inference. It never depends on an Agent deciding to call a history tool.
This is a storage/execution distinction, not permission to omit passive history.

Keep public text once. A reception record stores invocation/member identity,
batch identity, covered sequence interval, visibility version and source
references sufficient to reconstruct the immutable batch. Use intervals and
batch records rather than an eagerly allocated message-by-member matrix.
Partial database processing advances only a contiguous completed prefix.

Frontiers are scoped by invocation and member. For the ordinary persistent
member context, received but unprojected batches survive an invocation's end
and remain part of automatic context preparation in a later invocation. Keep
their original scope visible as historical context; do not reactivate completed
or cancelled work from that scope. A deliberate context reset needs an explicit
history/reconstruction rule rather than silently discarding these batches.

Cancellation can leave accepted originals beyond the reception frontier. Before
preparing a later invocation for the same persistent member, catch up those
closed-scope source ranges as well, in original order. Historical reception
bookkeeping may be appended without reopening the old scope or changing its
outcomes. Do not start from only the last scope that happened to finish its
reception bookkeeping. This is automatic context preparation after an explicitly
opened later invocation, not automatic restart of cancelled execution.

Use distinct facts:

| Fact | Meaning |
| --- | --- |
| Received | A durable member batch guarantees automatic future projection |
| Projected | A verified session binding records application of this batch and its provenance |
| Included in inference | A recorded model request used a particular raw/summary projection; this is not proof of understanding |
| Response satisfied | The existing response predicate accepts actual response evidence |

Do not call all four facts "read". Reception and response work have independent
state. A reception record never settles an opportunity.

A projection receipt is historical application evidence, not by itself proof
that the next model input is ready. Before inference, verify the current working
view: every source in the assignment's captured context manifest is present as
raw content or a permitted summary with validated coverage and retained originals.
Protected task sources must remain present in full. An archive entry alone, with
neither raw content nor its summary in that view, is insufficient. Supported
context resets invalidate readiness even when past application receipts remain
valid for audit. Reconstruct missing context through an explicit verified path,
or block preparation; never reproduce the old "archived therefore visible" gap.

## 4. Synthetic response semantics

For a historical passive batch, the adapter may render the full attributed
source block as User content followed by an assistant representation such as:

```text
[Runtime reception record: this reception operation started no model call.
This is not a member reply. Response obligations are tracked separately.]
```

The canonical record is a runtime reception event. The assistant text is its
provider-facing representation, not a model-authored original. Persist origin,
batch identity and source coverage outside the free-text marker as well. Those
facts must survive export, archive and compaction. Do not fabricate tool calls,
reasoning traces, tool outcomes, public posts or response evidence.
Authoritative origin is structured metadata, never text parsed from a message.
A peer quoting the marker cannot create a reception receipt or settle work.

The marker says only that this reception operation did not run the model. It
does not say "I understood", "I agree", "this work is complete", or "ignore all
requests in this batch". The last formulation would erase real deferred work.

Adjacent compatible passive batches may share one rendered marker while
retaining all their original coverage. Do not merge across a real member run
or change source order. Synthetic records never re-enter the public message
stream, trigger members, consume a business-run slot or masquerade as inference.

For a real activation, the final task input explicitly identifies active request
IDs and includes their full source text. Other supplied messages remain attributed
context, not additional assignments. There is no synthetic completion for this
active input: the actual Agent run produces its response.

Do not replace a real successful, failed or interrupted run with a synthetic
response during re-projection. A projection adapter/version is pinned for the
operation. If a provider cannot accept the representation, fail preparation
with a clear compatibility error; do not call the model just to obtain PASS.
Any alternative representation needs its own explicit, tested adapter version.

## 5. Mixed batches and pending work

A single batch may contain a request to B, a request to C, and an informational
update. C must receive all eligible content, but only its admitted requests are
active work. A newer message directed elsewhere cannot cancel an earlier
pending request to C. Conversely, background wording cannot manufacture work
that the runtime never admitted.

Keep activation causes and pending response opportunities independent of
reception frontiers. For policy-originated work, use stable origin keys and
durable deferred state where necessary. Advancing a policy's discovery cursor
must not forget a candidate waiting for member capacity.

Both receive-only and active paths preserve all eligible sources. The policy
selects action; it does not select arbitrary holes in the received history.
Future visibility models need their own explicit contract rather than reusing
recipient metadata as implicit privacy.

### 5.1 Work accounting under deferred execution

Explicit requests retain their existing stable response slots. A deferred slot
keeps its target and dependency, such as member availability; it is not silently
rerouted. A broadcast has independent slots for its expected participants, so
one response cannot satisfy everyone. Multiple slots may share an assignment
only when their identities remain distinct and each requires its own evidence.

Policy-originated reactions use stable keys identifying the policy instance,
source/collection, target and logical attempt. Re-evaluation does not create a
new attempt. A deliberate retry has a new identity linked to its predecessor.
Advancing a discovery cursor requires either a committed decision or durable
information sufficient to reconsider deferred candidates. If that information
cannot fit its quota, retain the unprocessed source position and report pressure.
Reception progress is independent and must not consume the candidate silently.

Free follow-up speech remains allowed. If A requests B and then publishes a
supplement after B's input boundary, that supplement is received for a later
preparation; it is not retroactively inserted into B's run. OnDemandStrategy does
not automatically create another response obligation from that post. A workflow
requiring B to wait for a complete bundle must put the necessary material in the
request or explicitly define a collection/handoff boundary in its strategy.

## 6. Ownership, failures and recovery

Only the member's executor may mutate its Agent context. During an active run,
new messages can be received durably but cannot alter that run's input snapshot
or interrupt an unfinished assistant/tool batch. Projection and execution are
serialized per member. Other members may continue independently.

The input barrier starts when an assignment is admitted, not only when its
worker starts. Batches beyond that assignment's captured high-water must not
be inserted ahead of it while it is queued. Its preparation uses a versioned
manifest of the intended source prefix; later reception is still accepted but
materialization waits for the next safe boundary.

Member preparation obtains a runtime reservation before session mutation, even
when it is not attached to a business assignment. Run admission, passive
projection, compaction and context reset share that reservation authority.
An idle projection cannot race a plan into installing sources newer than the
plan's input boundary. Validate the expected reservation/session generation
before publishing a prepared input or starting the main model call.

Projection needs a generic, idempotent context-batch operation on the base Agent
boundary: operation identity, content identity, provenance and an application
receipt. It must not import Group policies or scheduling into the single Agent.
The current direct Session.add/add_many calls and context_transform hook alone
do not supply this complete contract. New metadata must have explicit codec
and archive support; ad hoc attributes are insufficient.

Reception records/frontiers commit atomically in Group storage. Context-batch
application validates the whole batch before changing the session, rejects
identity reuse with different content, and returns the same receipt on a
same-session retry. Projection progress is bound to a session identity and
verifiable coverage, including any intervening archive/compaction operation.
Do not assume a Group transaction and an in-memory Session mutation are one
atomic transaction.

For one bounded projection unit, the protocol is:

1. Persist its source manifest and stable operation identity, then reserve the
   member. A repeated identity with a different manifest is a conflict.
2. Under the Agent owner, validate all entries, provenance and expected session
   generation. Apply the complete unit and its local application receipt as one
   session mutation. No partial User/assistant pair may become a valid receipt.
3. Commit the returned session binding and coverage in Group storage. A failed
   or unknown acknowledgement leaves the operation unconfirmed, not unapplied.
4. Reconcile that same identity against the Agent receipt before retrying. A
   matching receipt confirms application; unknown/contradictory evidence blocks
   execution rather than appending speculatively.

Application receipts remain reconcilable after compaction via verified coverage
metadata. Pruning a working-message cache cannot erase this evidence. The
authoritative receipt comes from the controlled operation result, not a logging
or event-listener callback whose failure may be swallowed.

If projection fails, retain the received batch and block dependent execution.
Report a member preparation blockage separately from a Group storage failure;
independent eligible members may continue. Do not cancel the whole invocation
merely because one member's input needs a different budget or repaired context.
An uncertain acknowledgement is reconciled using the application receipt, not
by blindly appending the same history. A different or rolled-back session must
not inherit an old projection frontier without matching evidence.

If inference fails after projection, keep the successful reception/projection
facts and record the failed assignment independently. Preserve posts and tool
effects. Any business retry is an explicit new attempt; it is not caused by an
"unread" flag being restored.

Stored reception records can reconstruct Group-origin context, not lost private
Agent reasoning or unknown tool outcomes. The current refusal to automatically
resume unfinished invocations remains. Durable reception is not a claim that
automatic process-crash recovery has been implemented.

### 6.1 Preparation, cancellation and ownership

An admitted business assignment reserves its member and cumulative run allowance
while it is queued/preparing. Preparation has a bounded global capacity; a
blocked member keeps its input barrier but does not occupy a running preparation
or inference slot forever. Other members remain eligible. Report the blocked
reason and retry dependency, and resume only after that dependency changes or
an explicit retry is requested. Do not repeatedly admit replacement assignments.
An unrelated message/revision change does not satisfy a blocked preparation's
retry dependency. Any automatic transient retries stay within a finite configured
preparation/provider retry budget, without creating another business assignment.

Cancelling a waiter stops only that wait. Explicit assignment/invocation
cancellation closes launch admission and requests owned preparation/inference
to stop. Check cancellation and the invocation deadline before each summary
call, context commit and main inference. Storage/application operations already
accepted must be reconciled even after cancellation; their outcomes cannot be
erased to meet a timeout. Do not declare the member idle while real work or
cleanup still owns it.

If a blocked attempt is cancelled, release its input barrier only after owned
operations settle. Already applied context and original messages remain; a
subsequent attempt captures a fresh boundary rather than mutating the cancelled
attempt. Deadline expiry is a terminal execution reason, not a reason to drop
message history or reset a projection frontier.

## 7. Context budgets and compaction

"All unread" defines logical source coverage, not an unlimited single User
message. The current Agent protects its latest User input, so a giant injected
batch can exceed capacity before older-history compaction helps.

At preparation time, materialize bounded historical units and the explicit
current task. Within budget, supply all originals. When the accumulated history
exceeds budget, use archive-backed compaction with source coverage before
business inference. Complete the preparation of the captured reception snapshot;
do not silently begin the task with only the newest material.

Synthetic markers must not survive without their provenance or be summarized
as a member's judgment. The current compaction unit builder principally protects
tool-call/result pairs; generic reception-unit/provenance handling therefore
requires deliberate integration, not just a new prompt string. Summaries retain
source references and must not imply response completion from a runtime marker.

Protect the current task and its admitted trigger sources. If those alone cannot
fit, report an explicit input-budget blockage and retain the work. If compaction
fails, retain originals and the last valid projection; do not advance projection
coverage for a failed preparation unit. Successful earlier units keep their
application receipts, allowing bounded preparation to resume without duplicate
insertion; business inference remains blocked until the whole captured snapshot
is ready. Retain explicit per-unit byte and total provider-input budgets rather
than silently removing the existing input limit. A summary preserves references
but is not information-theoretically lossless, and its qualitative usefulness
needs tests.

A task may also declare required source references, such as the original brief.
These references supplement complete reception; they do not replace it. Include
them in the protected input manifest, validate their visibility and budget, and
never silently shorten them. For historical sources that are summarized, record
that the main model received a summary rather than claiming it saw every original.

Partial source chunks carry explicit character ranges and source identities;
byte limits remain UTF-8 byte budgets, not cursor offsets. A whole message is
covered only when all its required spans have been committed. Reject missing or
contradictory coverage. If an archive is missing/corrupt or a transform removes
protected input, block preparation rather than substituting an empty history.
The supported projection path must verify coverage after context transformation.
An arbitrary transform without a verifiable source/provenance mapping cannot
claim this profile's delivery guarantees and must be rejected at composition.

Passive reception itself performs no model calls. Necessary summarization is a
separate preparation/maintenance operation with its own deadline, cancellation
and usage accounting; it has no business tools or public-response authority.
Defer it until preparation is needed rather than summarizing on every post.

Do not reimplement private-history summarization inside Group. Extend the Agent's
generic context-unit/provenance and preparation interfaces, and reuse its archive
and budget machinery. Reception storage holds public-source references; private
reasoning, tool transcripts and archives remain owned by the member Agent.

## 8. Automatic progress and stopping

Provide an explicitly started, runtime-owned service for an open invocation.
It evaluates initial input, new public messages, member outcomes and relevant
capacity changes. Keep the existing one-shot drive API. Wait against durable
revisions without missing changes; do not poll repeatedly because a member has
received-but-unprojected context.

Exactly one scheduling owner operates on an invocation. Starting the service
twice joins/reuses that owner. One-shot driving and manual plans use the same
admission authority; they cannot run a competing scheduling loop. The service
owns its deadline while idle. Cancelling an awaiting client does not stop it;
explicit cancellation/Group close does, with owned cleanup. No detached service
may outlive the runtime and its store.

Reception and policy discovery have separate progress. Discovery must paginate
the complete unprocessed source range, not only recent messages. Committing
reception-only progress is valid driver work, but re-observing the same batch
must be idempotent and must not repeatedly bump revisions. Coalesce notifications.

Pending requests to busy members remain discoverable after their source event
has been processed. Deferred execution is reconsidered when its dependency
changes. A projection/storage error is visible and does not become an unbounded
retry loop. A storage failure prevents further admission.

Cycles use finite captured source ranges and bounded page/member work. Rotate
eligible member preparation fairly and yield between bounded cycles. Incoming
messages cannot continuously extend an already admitted preparation target.
Stale-plan retries have backoff/deadline bounds and re-read current state; never
relax revision checks to force an old plan through. Under sustained overload,
apply admission backpressure rather than promise progress at an unlimited input
rate. No user callback, projection or model operation runs inside a SQL transaction.

Reject invalid plans without partial effects. A broken strategy/renderer or an
internal invariant failure is a controller error, not a harmless member budget
blockage. Stop new admission and settle/cancel owned work through the existing
error lifecycle; do not spin on the same invalid plan or abandon active workers.

Track pending metadata, buffered bytes, queued preparation, model/summary usage
and storage footprint separately. Passive receptions cost no inference but do
consume metadata/storage and eventual context budget. Reserve bounded control
capacity for outcomes and closure; reject new work explicitly when its admission
budget is unavailable. Never make room by deleting accepted originals. Persist
references to public text rather than eagerly copying every message per member.

A policy that automatically activates peers on public messages must define
whether replies propagate further, its response/collection scope, and an ending
condition. Same-event deduplication cannot stop an endless chain of new replies.
Runtime deadlines, cancellation and run limits remain independent safeguards.

Reception-only events do not imply further inference. Unprojected passive
context does not prevent quiescence or business completion. A queued/preparing
or blocked business assignment is still unfinished work. Expose waiting for
input, waiting for a busy target, preparation blockage and response failure
separately instead of reporting all quiet states as success.

### 8.1 Closing without losing accepted messages

Normal finish validates the expected revision, response evidence and absence of
unsettled business work, then atomically seals new message/work admission and
captures a closing high-water F. Drain reception coverage through F in bounded,
owned control operations before publishing terminal completion. This is a
reception drain, not an instruction to invoke all idle members or materialize
their private context. The normal finish path therefore needs a closing phase;
the current immediate transition to terminal completion is insufficient.
Seal optional idle preparation admission too. Reconcile and join preparation
operations already accepted before committing terminal completion; cancel or
leave unstarted projections as durable future context without claiming they ran.

An incoming message committed before the seal is included in F; a new command
after the seal is explicitly rejected. Returning an already committed receipt
for a duplicate command does not reopen admission or redispatch work. Stale
finish proposals commit nothing and must be reconsidered. A command is never
silently transferred to a later invocation.

The finisher owns its deadline and drain even if its caller stops waiting.
Cancellation/deadline before terminal completion stops the normal finish and
records the actual cancellation/timeout reason after owned work settles; it
must not report complete delivery. Once a terminal result is committed, a later
cancel cannot rewrite it. Store failure leaves completion unconfirmed and the
runtime unavailable for new work, rather than fabricating a successful finish.

Cancellation retains actual reception frontiers and all accepted originals.
Later historical catch-up follows section 3 and never restarts old tasks.
Shutdown joins owned service/preparation tasks and real worker cleanup before
disposing the store. No close operation may wait for a receipt from a store it
has already closed.

### 8.2 Configuration and compatibility boundaries

Keep membership and visibility fixed in the first version. Policy/adapter
replacement and session reset cannot occur during queued, preparing, running or
unconfirmed context operations. Pin policy, adapter and visibility versions in
manifests; reject unknown versions rather than interpreting old data as new.
Dynamic membership and hot strategy switching remain separate features.

An existing store with message history but no reception metadata is not evidence
that every member received it. The initial implementation must explicitly reject
that format for this profile or perform a separately validated, suspended import;
it must not initialize frontiers to the latest message. A fresh database is the
safe initial rollout. This also avoids claiming a migration from the demo archive.

Before base-Agent changes, preserve existing standalone execution, private
compaction and tool-pair guarantees across supported sync/async and streaming
paths. Group activation remains outside the base Agent; only generic context
application, provenance and receipts belong in that base.

## 9. Acceptance cases

These are planned checks, not tests executed for this design revision. Every
case must assert the source/coverage state and any response-work state separately.

| ID | Boundary or injected failure | Required result |
| --- | --- | --- |
| R01 | A requests B while C and D are idle | C and D receive without model calls; their later uncompressed inputs contain the original automatically. |
| R02 | A batch contains an older request to C and a newer request to B | C's pending slot survives passive reception and is eligible when C becomes available. |
| R03 | Backlog exceeds both recent-history and discovery page limits | Process a fixed high-water across all pages; an early requirement is not omitted. |
| R04 | A page is empty or contains only sources already covered in private context | Advance only justified examination/coverage; generate no empty synthetic turn or repeated revision. |
| R05 | Messages arrive after run admission but before worker launch | The queued run retains its admitted source boundary; later context is prepared afterwards. |
| R06 | A message arrives between a real assistant tool call and its result | Reception may commit; session insertion waits and tool pairing remains valid. |
| R07 | Idle passive projection races business admission | One member reservation wins; reject/reconsider the stale operation without installing future sources into an older run. |
| R08 | Duplicate notifications, duplicate commands, or quoted runtime-marker text | Stable identities deduplicate; text cannot create control records; no extra activation or public PASS. |
| R09 | Agent applies a unit but the Group acknowledgement is lost | Reconcile the same operation receipt; no duplicate session append. |
| R10 | A unit fails validation, or preparation stops between committed units | No partial unit receipt; retain successful prefix receipts and resume only missing work. |
| R11 | Compaction prunes raw entries before a lost acknowledgement is reconciled | Verified coverage still supports the application receipt; a cache miss is not proof of non-application. |
| R12 | Session is replaced, restored to an older snapshot, or an unfinished process is reopened | Reject unsupported projection evidence; keep execution suspended rather than replay effects. |
| R13 | A member's own old public message lacks coverage in its replacement session | Include that source as history; authorship alone cannot suppress it. |
| R14 | Model failure occurs after projection and an accepted public/tool effect | Keep projection and effects; record the failed task; do not restore unread state to cause an automatic replay. |
| R15 | Caller cancellation occurs before submission, during application, or during summary I/O | Distinguish unaccepted work from accepted work; reconcile accepted mutations and keep real ownership until settlement. |
| R16 | One member is blocked by input size while another is eligible | Preserve the blocked attempt and barrier; release global active capacity; unrelated revisions do not trigger an endless retry. |
| R17 | A publishes a supplement after requesting B and B's admission | Preserve free speech; record the supplement for later context without hot-patching B or inventing another OnDemand request. |
| R18 | Passive history needs compaction before first business inference | Account for every source through raw or validated summary coverage; do not summarize markers as consent/completion. |
| R19 | Trigger plus required sources alone exceed the protected budget | Report an explicit preparation blockage; retain originals and pending work without truncation. |
| R20 | A source is split across Unicode/escaped-content chunks | Character coverage and UTF-8 budgets remain distinct; reconstruct every span before claiming whole-source coverage. |
| R21 | An archive is absent/corrupt or a transform removes a protected source | Block the affected preparation; do not fall back to empty/latest-only history. |
| R22 | New input races normal finish, including a duplicate previously accepted command | Pre-seal commits enter F; post-seal new commands are rejected; duplicate receipts return without reopening or redispatching. |
| R23 | Finish is cancelled/times out during reception drain or context cleanup | No false completed/delivered result; preserve actual frontiers and join owned operations. |
| R24 | A cancelled scope has accepted messages never entered in its reception ledger | A later explicitly opened scope catches them up as history, without reviving cancelled work. |
| R25 | Start service twice, call drive concurrently, or cancel a service waiter | One scheduling owner remains; the idle deadline and cleanup owner are not abandoned. |
| R26 | Sustained input, stale plans, a full control queue, or storage pressure | Bound cycles and retries, keep admitted preparation targets fixed, preserve settlement capacity and explicitly reject excess new work. |
| R27 | Store fails after an external effect, or while closing | Stop admission, retain real cleanup/outcome ownership and uncertainty; no replay or fabricated completion. |
| R28 | New replies repeatedly request more replies, or one broadcast target never replies | Enforce invocation limits and independent response slots; passive receipts never satisfy missing participants. |
| R29 | Old database format, unknown adapter version, or hot configuration replacement | Reject unsupported reinterpretation and mutation during owned work; never initialize reception to the newest source by assumption. |
| R30 | Synthetic reception is followed by real inference on each supported provider path | No inference/public tool activity during reception; later response remains usable; marker provenance survives serialization/compaction. |
| R31 | A previously applied original remains only in an archive after context reset | Historical receipt alone cannot authorize inference; restore current raw/summary coverage or block. |
| R32 | A strategy repeatedly returns an invalid plan or a renderer fails unexpectedly | No partial admissions; report a controller error and retain ownership through cancellation/cleanup rather than retrying indefinitely. |

Use actual prepared provider inputs as evidence, not just stored transcripts or
successful assignments. Later model-based checks must assess requirement
coverage; reception does not guarantee the model follows every requirement.

For the first implementation, deterministic fault-injection and provider-input
assertions should cover these lifecycle cases before live collaboration checks.
Live checks should include delayed participation after many passive batches,
mixed directed requests, archive-backed preparation and a requirement-sensitive
collaborative task. No hourly/day-long soak is required for this design's first
acceptance; repetition should exercise every transition rather than idle time.

## 10. Implementation boundary

Begin with reception storage, context-batch projection and the existing OnDemand
strategy. Extend DispatchPlan validation and the driver to accept reception-only
progress, explicit preparation/blocked state and member reservations. Add the
owned service and normal-finish reception drain with their lifecycle tests.
Integrate and test provenance-aware context preparation before claiming
large-backlog support.
Then use an explicitly selected automatic policy to validate reuse of these
boundaries; do not silently alter OnDemandStrategy's post semantics.

The largest integration risk is the Agent context boundary: batch application,
provenance, compaction and uncertain acknowledgements. Public-log storage and
strategy selection alone cannot satisfy this design. Provider acceptance of the
synthetic representation and its effect on later responses remain empirical
validation requirements, not assumptions established by this proposal.

The minimum implementation introduces four concepts: a complete reception
batch, a verified context application receipt, an assignment input manifest,
and one owned scheduling/preparation lifecycle. Reuse existing public messages,
response slots, Agent archives and execution controls. A general event bus,
distributed transaction framework, second summarization engine, new strategy
plugin loader or automatic crash-resume system is not required.
