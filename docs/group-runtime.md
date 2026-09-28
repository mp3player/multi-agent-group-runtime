# Group runtime and on-demand collaboration

The local Group foundation composes independent Agents as peers. It is a Python
API with durable public messages and execution state. Manual dispatch is the
default. An explicitly selected `OnDemandStrategy` provides the first automatic
request-driven collaboration mode. Applications may call `drive` once or start
an owned `start_service(scope)` for automatic progress across idle periods.

The [architecture](superpowers/specs/2026-09-24-group-collaboration-design.md) and
[operational contracts](superpowers/specs/2026-09-24-group-operational-contracts-design.md),
revised on September 26, describe the larger target. This page describes the
smaller implemented profile and its limits.

## Interactive Group CLI

```sh
.venv/bin/python -m cli.group
.venv/bin/python -m cli.group --prompt "How many members are in this group?"
.venv/bin/python -m cli.group --members researcher reviewer --timeout 900 --max-runs 50
```

`application.group_chat.GroupChat` constructs independent member Agents from
the current `.env`, binds `OnDemandStrategy`, and owns both the Group and its model
clients. The default names are `alice`, `bob`, and `carol`; the human `user` is
separate. Each Agent has its own session and registry. Its identity, member list,
and collaboration rules are persistent system extensions. The current Group
profile permits conversation and context archive tools; this CLI disables the
standalone workspace tool bundle, which includes unsupported mutations.

| Input | Effect |
| --- | --- |
| `TEXT` | Request one available member in stable membership order |
| `@MEMBER TEXT` | Request that member |
| `@all TEXT`, `/broadcast TEXT` | Request every member |
| `/post TEXT` | Retain background for all members without starting inference |
| `/members` | Inspect actual membership and availability without inference |
| `/history` | Read all public messages across discussions in this process |
| `/status` | Inspect discussion state, limits and bounded request/response evidence with explicit omitted counts |
| `/finish` | Explicitly accept and seal a discussion when completion checks permit |
| `/cancel` | Cancel unresolved work, preserving its history |
| `/help`, `/exit`, `/quit` | Show commands or close the chat |

Recipient selection controls activation, not confidentiality: directed messages
are still public. Output displays committed public messages as `[member] ...`;
private model reasoning and unpublished final answers are not group messages.
Peer-created requests continue automatically until the existing driver returns.
A `waiting` result leaves the discussion open. Failures and unresolved response
requests are shown explicitly and are not automatically replayed. `/finish` is
the user's acceptance decision, not a semantic validator.

`--max-turns` overrides each member's model-turn limit; other Agent/provider
limits come from `.env`. `--max-runs` defaults to 100 member runs per discussion.
`--timeout` defaults to 300 seconds from the first message, including time spent
waiting for human input. An expired, cancelled, limited or finished discussion
is followed by a fresh discussion on the next message, preserving the same member
sessions and previous public history. Expiry during idle input is enforced when
the next message is submitted; no background model execution occurs while idle.

Ctrl+C closes the chat from both idle input and active execution. An in-flight
synchronous provider call must return or reach its configured timeout before its
worker can be released; cancellation prevents further work from being admitted.
EOF and `/exit` also close resources. `--prompt` sends one message or executes one
command and then closes; exit status is 0 for a quiet/completed run, 1 for failures
or unresolved/limited work, and 130 for Ctrl+C.

Each process prints its fresh SQLite history path. `--store PATH` selects another
new path and rejects existing files without modification. The public database is
retained after exit; it is not a restartable snapshot of members' private sessions.
Cross-process conversation restoration remains separate work. This CLI is
independent of `cli.agent` and `main.py`, which continue to start a standalone Agent.

## Current extension boundary

Group now separates durable complete reception, private context preparation and
business activation. The [reception design](superpowers/specs/2026-09-26-group-reception-design.md)
and [implementation validation](superpowers/reviews/2026-09-26-group-reception-implementation.md)
describe this boundary.

| Concern | Implemented now | Separate future work |
| --- | --- | --- |
| Strategy assembly | Selectable tools, guidance, validated source envelope, decision rule | Output adapters, switching, capability negotiation |
| Discovery | Bounded oldest-unprocessed pages with atomic explicit acknowledgment | Strategy-specific collections and durable deferred-candidate schemas |
| Reception | All accepted originals through a fixed boundary, including prior closed scopes | Alternate visibility and provider adapters |
| Context | Identified User/synthetic assistant units; verified raw/summary coverage | Alternative provider representations |
| Activation | Explicit opportunities or stable policy origin; queued/preparing/running/blocked states | Additional concrete scheduling strategies |
| Service | One shared driver, optional owned idle service, deadline and sealed completion | Automatic process-crash recovery |

A public post does not start a model call. `receive` stores source references and
coverage; preparation is deferred until a member has an admitted assignment.
Before business inference, its worker incorporates every captured public source
into private context, using bounded units and the existing Agent compactor when
necessary. Synthetic assistant markers are runtime bookkeeping, not replies,
consent, task completion or tool calls. Source originals remain in SQLite and
compacted private originals remain archived. Summary coverage proves inclusion,
not lossless semantic retention or model understanding.

## Responsibilities

| Module | Responsibility |
| --- | --- |
| `group.records` | Detached records, limits, proposals and pages |
| `group.policy` | Full strategy protocol and manual proposal protocol |
| `group.profiles` | Declared tool/prompt/input/publication behavior |
| `group.scheduling` | Detached observation, decision and driver result records |
| `group.strategies` | Opt-in on-demand decision rule and communication profile |
| `group.driver` / `group.service` | Shared one-shot scheduling and owned idle lifetime |
| `group.discovery` | Atomic policy observation progress and finite page boundaries |
| `group.reception` | Complete per-member source coverage and immutable assignment manifests |
| `group.member_context` | Bounded attributed source units and current coverage verification |
| `group.execution` / `group.lifecycle` | Preparation barriers, outcomes, retry and sealed completion |
| `group.recovery` | Explicit response reconciliation using validated public evidence |
| `group.runtime` | Membership, command admission, execution and lifecycle |
| `group.dispatch` | Validate a complete plan and commit its effects atomically |
| `group.state` | Versioned schema, record queries and transaction helpers |
| `group.store` | Bounded serialized SQLite ownership and durability |
| `group.tools` | Execution-bound collaboration tools using the existing tool pipeline |
| `core.agent_runtime.worker` | Generic serial preparation and synchronous Agent execution |
| `core.context_batch` / `core.session` | Identified context application, provenance and verifiable receipts |

Core imports no Group code. The standalone CLI remains available. Group does not
subclass Agent or depend on `AgentAppService`; applications supply independently
constructed Agent instances.

## Supported execution profile

Creation requires `worker_safe=True`. This declaration covers the **whole**
synchronous, non-streaming member pipeline: provider, permissions, tools, result
formatting, custom executor, audit sink, session and context archive. Each member
has one serial worker thread, separate from the SQLite worker and Group loop.
Custom code must be safe to execute on that worker and return only after its
owned work has finished. Known coroutine functions in the supported pipeline
are rejected. The declaration is not a sandbox or an inspection of arbitrary
custom code.

Existing permissions, workspace binding, custom executor overrides, result
conversion and audit run once in their normal order. Synchronous audit I/O can
delay that member, but cannot occupy the SQLite worker or Group event loop.
This profile does not implement the proposed native asynchronous tool pipeline
or its per-stage audit reservation protocol.

Members need distinct Agents, sessions and registries. Shared providers remain
borrowed and must support concurrent calls if concurrent member execution is
enabled. Group closes its workers and store, not borrowed providers or archives.
Guarded standalone execution and Agent setters reject while the worker owns the
Agent, including idle intervals between assignments. Close Group before reusing
an Agent elsewhere. Direct mutation of sessions, registries, callbacks or other
borrowed internals while bound is unsupported.

Only tools declared `read_only` or `memory_only` can bind in this first profile;
the framework supplies its own collaboration tools. Unclassified tools,
workspace mutations and external effects are rejected. The default standalone
workspace tool bundle includes mutating tools and therefore cannot bind as-is.
Construct a read-only registry, or use `build_agent` with workspace tools disabled.
An application must not relabel unsafe effects just to pass this check. Profile
tools are trusted collaboration extensions and must obey the same restrictions;
the runtime cannot inspect arbitrary callback behavior. Shared
writable workspaces require the separate leases and expected-base-write design.

## On-demand strategy

```python
from group import GroupLimits, GroupRuntime, OnDemandStrategy

async with await GroupRuntime.create(
    "discussion.sqlite", {"alice": alice, "bob": bob}, worker_safe=True,
    strategy=OnDemandStrategy(),
    limits=GroupLimits(max_runs=100, invocation_timeout=300),
) as group:
    scope = await group.open_invocation("review-001")
    await group.post(scope, "Background information", key="background")
    await group.request(scope, "Review the proposal", key="review", recipients=("alice",))
    result = await group.drive(scope)
    # Inspect public history and application acceptance conditions here.
    if result.status == "waiting":
        await group.finish(scope, revision=(await group.snapshot(scope)).revision)
```

Plain posts and public replies create no response opportunities. A directed
request targets those members; an empty target list selects one idle member in
membership order. `group_broadcast` explicitly requests every other member.
The rule assigns one request per available member per run. A busy member's
request remains pending and becomes eligible after its actual execution settles.
This first rule provides no load-balancing or fairness guarantee for untargeted
requests beyond stable membership order.

`drive` continues through peer-created requests and returns when no selected
work remains. For automatic idle wakeup use `service = await group.start_service(scope)`.
Repeated starts share the same live service; `await service.wait()` observes its
terminal result. Cancelling a waiter does not stop the owner. Use `cancel(scope)`
or `close()` to stop it. Without a service, call `drive` again after new requests. Concurrent callers share its owned task. The possible results
are `waiting` (quiet, open), `needs_input` (missing replies, failed execution or
unhandled pending work or blocked preparation), or a terminal reason such as `limited`, `timeout`,
`cancelled` or `completed`. It never treats silence as task completion or silently
retries a missing reply. On-demand requires a committed public message linked by
`reply_to` from the actual assigned execution. This is communication evidence,
not a judgment that its contents answer the question. A private final answer does
not satisfy it. Failed/partial runs remain visible for application handling.

The on-demand instructions distinguish assigned `trigger_messages` from background
and outgoing requests. Sending a request or receiving its acceptance receipt does
not add work to the sender's current execution. Members may make relevant public
follow-ups, including replies to their own outgoing requests; these posts do not
activate another run or create response obligations. Members should yield when
their current work is done. This is model guidance; the runtime does not enforce
a universal one-post limit. Explicit new requests still obey the invocation's
run budget and deadline.

An ordinary strategy, input-rendering or plan error closes the invocation with
reason `error`, waits for actual execution settlement, then re-raises to the
caller. A persistence failure stops admission/launch; the application must still
await `close` for owned-resource cleanup.

Temporary command/store saturation raises `QueueCapacityError`, a subtype of
`CapacityError` indicating rejection before admission. The driver preserves
accepted work, waits with a 10–100 ms backoff and reobserves before trying again.
The invocation deadline still applies. Lifecycle reads use one reserved read
slot, separate from normal operations and reserved control writes. Historical
driver queries cannot consume cancellation or settlement admission capacity.
Lifecycle writes, close-time reads and `wait_for_change` revision reads retry
pre-admission saturation using reserved capacity. Idle services keep their
original absolute deadline while waiting for a read slot; temporary normal-queue
pressure does not terminate the service. These paths do not replay accepted
transactions or retry database failures. Invalid
oversized plans and cumulative limits are separate errors;
they are not retried as queue contention. Application/member commands can still
receive a capacity rejection and must handle that result explicitly.

### Resolving a missing response

An application can arrange an explicit follow-up request or manual repair run,
then accept its real public reply as replacement evidence:

```python
# missing_opportunity belongs to the original settled execution.
# replacement_reply is an actual stored message returned by history/message.
receipt = await group.resolve_response(
    scope, missing_opportunity.id, replacement_reply.id, key="resolve-reply-001",
)
result = await group.drive(scope)
# Inspect result and application acceptance conditions before explicit finish.
```

The replacement must reply to the original request and come from a later,
successfully settled execution of the original assigned member in this same
invocation. Another member, an external post, an unrelated message, a running
execution or a failed execution cannot supply replacement evidence. A follow-up
request has its own response obligation, so its execution should reply to both
the original and the follow-up request, or the application can use a manual run
with an `origin_key` to perform the repair.

Resolution records a separate durable link; original messages, errors, outcomes
and admitted-run counts remain unchanged. `ResponseEvidence` exposes
`resolved_by` (the replacement assignment) and `resolution_reply_id` separately
from the original evidence. A failed run with multiple response obligations is
accounted for only after all its obligations have valid evidence. No inference,
publication or task completion is triggered by resolving a response. Ordinary
later posts do not silently repair earlier work.

The resolution receipt's `message_id` identifies the accepted replacement reply;
`opportunity_ids` identifies the original obligation. Retrying the same operation
key and arguments returns the same receipt, including after closure/reopening.
Changing arguments or replacing an already accepted resolution is a conflict.
New resolutions require an open, unexpired invocation. Additional member runs
obey the existing run budget; this API does not refund failed attempts.

Run the offline acceptance example:

```sh
uv run python -m examples.group_on_demand
```

It stores background without inference, drives Alice's directed request and
broadcast through Bob and Carol, then verifies exactly four member executions
and no reply feedback loop.

Each CLI run creates a fresh temporary database and prints its path. An optional
`--store /path/to/new-group.sqlite` must name a nonexistent path; existing paths
are rejected before runtime creation. These examples construct new private Agent
sessions on each process start. Retained SQLite history supports inspection,
but does not restore those private sessions or their context coverage receipts.

## Profiles and observations

`CollaborationProfile` selects installed tools, English guidance, background
count, synchronous `render_system_prompt()` and `render_input(MemberInput)`
methods, optional final-text publication and required reply evidence. A strategy combines a profile with
`name`, `version` and `decide(SchedulingView) -> SchedulingDecision`. Selection is
fixed for a binding. These trusted synchronous callbacks must return promptly;
they receive detached records, not workers or the store. Arbitrary plugin code
is not sandboxed. Existing manual `SchedulingPolicy` functions remain usable
through explicit plan submission.

Fixed collaboration guidance is installed as a **system prompt extension** for
the member's entire worker binding. It is composed with the Agent's existing
role, without changing its configured base prompt or builder. Context compaction
pins this combined system message verbatim; the extension also counts toward
the fixed input budget. Oversized fixed instructions block execution rather than
being truncated or summarized. Group tasks and attributed public sources remain
user/history data and are never promoted into this system extension.

The system renderer receives no task or source records and runs once before
binding. It must return nonempty text synchronously. Custom profiles should put
persistent collaboration rules there, reserving `render_input` for task-specific
input. Workers remove only their owned extension before releasing the Agent,
including construction rollback and cancelled-close cleanup. A close waiter
being cancelled does not remove rules from a still-running member. Removed
system text remains in audit history but is not reused as an inherited base.
The worker's synchronous-pipeline validation includes system replacement and
extension installation/removal. Async overrides are rejected before ownership
is reserved, so they cannot silently skip the bound collaboration instructions.

In the on-demand profile, a mention, reply link, or prose handoff inside
`group_post` remains passive. Peer work requires `group_request` or
`group_broadcast`. Task-specific roles should state their handoff protocol
explicitly; the runtime does not infer new assignments from natural language.
`waiting` means no work is eligible, not that a business result has been verified.
The application must validate results before treating an invocation as successful.
For staged workflows, include actual upstream report message IDs in the next
assignment. These stable locators let members retrieve originals directly even
after compaction; they are distinct from identifiers inside the source documents.
This avoids relying on model-driven rediscovery of earlier artifacts.

Compaction retains system rules but its summary can paraphrase evidence. Exact
protocol strings, identifiers and source formatting should be checked against
`group_message` or `read_context_archive`. Original sources remain available.
The summary reserve is a planning target, not an independent hard cutoff:
above-target drafts must fit the exact prospective request and make sufficient
progress. Further summary inputs and the final archived request remain bounded.
An invalid draft may be shortened once within the existing four-call limit;
failure retains the original working context.

Group history and message retrieval target one quarter of the member's current
usable input budget, counting serialized tool output as well as respecting the
character limit. A history preview may be shorter than 512 characters; follow
`content_complete`, `next_content_offset`, `next_cursor`, and `high_water`.
Message chunks always advance or explicitly fail when even a minimal chunk
cannot fit. Originals remain in the store. This target reduces immediate
recompaction of oversized pages; it does not guarantee that several tool results
and the remaining context fit together. The final request budget remains
authoritative. Prefer these direct Group tools for public originals; private
context archives may contain additional copies of past retrieval results.

Custom profile tools require an explicit declaration such as
`CollaborationTool(custom_function, 'read_only', ends_run=False)`, imported from
`group.profiles` or `group.tools`. Only `read_only` and `memory_only` effects are
supported; undeclared custom callables are rejected. Existing `ToolPermission`
metadata can be supplied and is preserved. Built-in tools, including exact
`ToolFunction` wrappers, retain their known metadata and `group_yield` termination.
Applications remain responsible for coherent profile capabilities: requiring
public replies while offering neither publication tools nor final publication
will produce `needs_input`. General capability negotiation is not implemented.

The default profile retains the six original tools and private finals. The
on-demand profile adds broadcast and requires linked public replies. A profile
can choose no Group tools and `publish_final=True`: only selected final text,
never reasoning, is published through normal admission before run settlement.
A nonempty final already covered by explicit replies is not duplicated. An
oversized/rejected final publication is recorded as an assignment error rather
than counted as a successful reply. Late plain results may still be recorded as
cleanup while an invocation closes; they cannot schedule more work.

Admission freezes a source high-water and complete trigger/required originals.
`RunProposal.required_source_ids` can additionally protect older sources in the
current task. Later arrivals cannot change queued, preparing or running input.
A custom `render_input` receives detached data and must preserve the complete
final JSON envelope: scope/member/instruction/revision, trigger and required
source records. It can customize task presentation but cannot remove or rewrite sources.
`Agent.context_transform` is rejected for Group bindings. Runtime limits, profile
and strategy bindings cannot be replaced while the runtime owns work.

Before inference, historical sources become attributed User messages paired
with neutral synthetic assistant markers. Large sources use complete character
spans with independent UTF-8 limits. Current trigger/required sources remain
raw in the final User input. Sources already covered by a prior real task use
its exact session/receipt binding; a historical receipt alone cannot authorize
a reset or contradictory current projection. Group checks the complete manifest
again after preparation and any compaction, immediately before main inference.

Historical sources and their current application bindings are read together per
bounded page. Bindings are reread on the final validation pass rather than cached
across compaction or context application. Cancellation is checked before each
page and source, including already-covered sources. Session coverage and archive
integrity checks remain active; page batching reduces store round trips without
making preparation independent of retained history size.
`background_messages` is retained as a compatibility constructor field; it no
longer limits reception. `background_omitted` is false for runtime-built inputs.

`scheduling_view.messages` is the oldest unacknowledged page, not a recent window.
A strategy can set `DispatchPlan.observed_through=view.messages.next_cursor` to
acknowledge that page atomically with runs, dispositions and policy state. A
page-only acknowledgment is valid progress. The cursor is pinned to the captured
high-water until that range is exhausted. A deferred reaction must remain in
pending opportunities or durable bounded `policy_state`; otherwise do not
acknowledge its source. Reception never advances this policy cursor implicitly.
OnDemand can acknowledge posts safely because only durable requests activate it.
All pending requests (bounded by `max_pending`), run/reply evidence and member
reservations remain available independently of the message page.

## Explicit execution

```python
from group import DispatchPlan, GroupRuntime, RunProposal

# alice and bob are independently configured core.agent.Agent instances.
async with await GroupRuntime.create(
    "discussion.sqlite", {"alice": alice, "bob": bob}, worker_safe=True
) as group:
    scope = await group.open_invocation("review-001")
    receipt = await group.request(
        scope, "Review the proposal", key="user-request-001",
        recipients=("alice", "bob"),
    )
    snapshot = await group.snapshot(scope)
    plan = DispatchPlan(scope, snapshot.revision, runs=(
        RunProposal("alice", "Review correctness", (receipt.opportunity_ids[0],)),
        RunProposal("bob", "Review usability", (receipt.opportunity_ids[1],)),
    ))
    await group.commit_plan(plan)
    await group.launch_ready(scope)
    await group.wait_idle()
    snapshot = await group.snapshot(scope)
    # Read/handle any new requests before attempting normal completion.
    await group.finish(scope, revision=snapshot.revision)
```

This is direct plan submission by the application. Manual `snapshot` pages
contain metadata; content-dependent decisions can use `scheduling_view` and
bounded history queries.
`commit_plan` never runs a model. `launch_ready` starts already-admitted assignments
in their committed order, up to execution capacity. `wait_idle` waits only for
launched work. If a plan exceeds active capacity, explicitly call `launch_ready`
again after settlement to launch its remaining queued assignments. No new
selection or retry is inferred by any of these operations.

Run the complete offline example without an endpoint or `.env` access:

```sh
uv run python -m examples.group_manual
```

It uses deterministic provider responses with real Agents, tools, transactions
and execution. Like the on-demand example, it uses a fresh temporary database by
default or accepts a new `--store` path. It prints the database location and
retains it after exit; operating-system cleanup may remove temporary directories.

## Public communication and policy state

`post` creates a public message. `request` creates a public message and one
response opportunity per recipient; an empty recipient list creates one
opportunity whose member is left to a later plan. Directed messages remain
visible to all members. `reply_to` must name an existing message in this
invocation. In the default and on-demand profiles, final answers and private tool
outputs are not broadcast.

External operation keys are scoped by invocation and authenticated caller.
Member commands are additionally scoped by their runtime-owned assignment.
Reusing a committed key with identical content returns its original receipt,
including after invocation closure; different content is a conflict. A cancelled
waiter may have committed: retry the same key to resolve acceptance. A receipt
promises durable acceptance, not that another member has started or will answer.

Plans reserve members and pending opportunities, apply terminal dispositions,
and replace serialized JSON policy state in one transaction. Any stale revision,
invalid recipient, conflicting reservation or controller validation failure rejects
the whole plan. A valid protected source envelope that exceeds `input_bytes`
creates an individually blocked assignment with its source manifest; other
eligible members can proceed. It is never truncated or submitted as an empty task. An opportunity cannot be assigned and
disposed in the same plan. Leaving it pending expresses deferral; only refusal,
cancellation or an actual run outcome settles it.

A run without a response opportunity needs a stable `origin_key`, unique in its
invocation. This allows future review, reaction or phase work without creating
fake member requests. No such policy is provided. This version provides discovery cursors but no policy switching, response
collections or phase rules; each strategy defines its decisions explicitly.

Snapshots contain total pending, queued, active and blocked counts plus bounded candidate
and assignment pages. Query `opportunities`/`assignments` for more pages. Reuse the
high-water mark and next cursor to finish a finite scan; high-water excludes new
appends, not later state changes. A plan still needs the original snapshot
revision. `wait_for_change(revision)` installs its wait without a lost-wakeup
window and wakes on persistence failure or runtime close. Only an explicit
`drive` or `start_service` starts the selected strategy loop.

## Member tools

| Tool | Meaning |
| --- | --- |
| `group_post` | Publish deliberately, without creating response opportunities |
| `group_request` | Publish and request responses; acceptance only |
| `group_members` | Query execution availability; does not reserve a member |
| `group_history` | Bounded public message previews, IDs and continuation cursors |
| `group_message` | Read a complete original using character-offset chunks |
| `group_yield` | End this member run; never completes the Group |
| `group_broadcast` (on-demand) | Request every other member; rejects when there are no peers |

Identity and scope are supplied by the execution context, not model arguments.
Tools bridge from the member worker to the owning event loop, awaiting the same
durable command path as application calls. Each publication gets a run-scoped
operation identity independent of provider tool-call IDs.

Publication tool results wrap the existing receipt fields with `kind`
(`outgoing_post_receipt` or `outgoing_request_receipt`), `sender`, and `recipients`.
These identify the committed outgoing message, not a new instruction for the
calling member. An empty recipient list still means policy selection for a
request; a broadcast receipt lists every other member. Acceptance does not claim
that a peer has started or completed its execution. The application-facing
`Receipt` value and stored receipts are unchanged.

An outgoing tool receipt can also include optional `feedback` about the current
bound execution. It reports the assignment, observation revision, provisional
publication counts, whether this post's reply link matches an assigned trigger,
and a bounded list of trigger IDs without a linked publication. The list includes
an explicit completeness flag. These observations are not new assignments or
proof that the execution succeeded or the answer is correct. When the profile
does not require public replies, an unlinked trigger is not a missing obligation.
The current reply targets are `trigger_messages[].message_id`; a trigger's own
`reply_to` describes an earlier conversational link.

Feedback collection never retries the committed write. If an optional read fails
or output space is insufficient, the accepted base receipt remains available;
feedback is omitted or marked `available: false`. Persistence failures still
stop runtime admission. No corrective inference is forced: a publication and
`group_yield` in the same model response end the execution without another model
turn to consume that feedback. Other legal reply targets remain permitted.

The CLI `/status` command observes one consistent read-only store snapshot. It
shows up to twenty attributed request/manual-assignment rows, prioritizing
blocked executions, errors and missing replies, and reports omitted rows. A
request can lack settled response evidence while the pending queue is empty.
Live reply links are provisional; settled rows use the same execution identity,
outcome and explicit-resolution contract as the driver. Diagnostic text is escaped
and bounded. For programmatic inspection, `group.diagnostics.status(runtime,
scope, limit=20)` accepts limits from 1 to 100; aggregate counts cover the current
discussion and their SQL cost can still grow with its history.

History tools return complete JSON envelopes that fit the configured tool result
budget. Previews explicitly report incomplete content; `group_message` continues
at `next_offset` until `exhausted`. Originals remain unchanged in SQLite. Binding
rejects a result budget too small to carry receipts and metadata. The Python
`history`/`message` APIs expose original records directly.

## Settlement and storage

One invocation can be nonterminal at a time. Normal `finish` requires the current
revision, an unexpired deadline and zero pending, queued, preparing, running or
blocked work. It seals admission, captures a finite closing source boundary and
drains durable reception before committing `completed`. Stale revisions are
rejected; callers must reconsider the current snapshot. New post-seal commands
fail while duplicates return their original receipts. A cancelled finish waiter
does not stop the owned drain; explicit scope cancellation/deadline can stop
normal completion without falsely reporting full delivery. Profiles
requiring public replies additionally check linked execution evidence. It is an explicit application
decision; there is no automatic completion heuristic. Forced `cancel` fences
new work, cancels pending/queued/blocked work and signals only that invocation's actual
executions. Already-running members may finish plain cleanup posts; new response
requests and external posts are rejected once the invocation is closing.

Stop is cooperative between synchronous operations. A blocked provider, tool,
formatter or audit sink retains its member lease until it actually returns.
Cancelling `wait`, `drive`, `finish`, `cancel` or `close` only cancels the caller's wait. In particular,
Python cannot forcibly kill an arbitrary blocking thread. Do not stop the host
event loop while owned cleanup or a tool's bridge is still running. Await close
to settlement; an uncooperative operation can keep it waiting indefinitely.

Completed work is not relabeled cancelled when a stop arrives too late.
Outcomes preserve `completed`, `tool_stop`, `max_turns`, `error`, `timeout` and
`cancelled`. Failures after business inference settle their opportunities; retries require new
explicit proposals. Recoverable preparation failures retain the assignment and
response slots as `blocked`, without holding global active capacity. Inspect
`Assignment.error` and `DriveResult.blocked_assignment_ids`, then explicitly call
`retry_preparation(scope, assignment_id)` after resolving the cause. The retry
reuses the frozen manifest, task identity and successful context receipts;
unrelated posts never retry it automatically. An unchanged protected byte-budget
block remains blocked. Cancel and open a newly configured runtime when the fixed
budget must change. Public outcomes contain status/error metadata, not final
answer text. No audit callback is used as authoritative execution evidence.

Each new invocation durably records strategy/profile identity, `max_runs`
(default 100) and an absolute deadline (`invocation_timeout`, default 300 seconds).
Settled, failed and cancelled assignments still count toward cumulative admission.
Budgets are not refunded. The active driver or idle service stops new work at the deadline and
returns only after actual execution settles; it cannot forcibly kill a blocked
thread. With no active driver or service, admission/reception/launch/finish check expiry, and the
application owns cancellation. Idempotent receipt replay remains available after
expiry. The cumulative limit terminates driving when pending work would require
another run; exactly using the budget without more work can return `waiting`.

SQLite uses WAL, FULL synchronization, foreign keys, a bounded busy timeout and
one worker with bounded normal capacity, reserved control-write capacity and
one independent control-read slot. All accepted operations retain FIFO order
on that worker; reserved capacity does not bypass a slow SQLite transaction.
Messages, opportunities, receipts and revision share a transaction. Normal
overload rejects before acceptance; owned lifecycle writes retry admission
while preserving cancellation and actual-worker settlement. Repeated concurrent
launch/cancel calls coalesce. Reads use indexed bounded pages, and runtime caches
retain active operations rather than accumulated conversation history.

The store has one local POSIX owner enforced by an advisory file lock. Use a
single canonical database path on a local filesystem; network filesystems,
hard-link aliases, external database mutation and distributed owners are outside
this profile. Persistence errors stop admission/launch and wake parked callers.
Already-running work still needs cleanup; accepted external effects are not
replayed or assumed undone.

Reopening a completed store preserves public originals, receipts and outcomes;
schema version 4 adds reception, discovery and input/application manifests. Empty
supported older stores can initialize safely. Nonempty versions 1–3 require an
explicit offline import; no import tool is included. Rejection leaves originals
and receipts intact and never assumes that old messages were received.
Reopening unfinished invocations raises `RecoveryRequiredError`; there is no
automatic replay or resume. Group storage does not snapshot private Agent
sessions. Safe crash reconciliation, portable group snapshots and global disk
quotas remain separate work. Monitor store growth: this version never deletes
accepted public history to make space. It fails closed on storage failure.

## Verification

The deterministic suite covers two-Agent tool exchange, explicit request/plan
separation, durable originals, capacity, atomic plan rejection, scoped stopping,
worker ownership, failure wakeups, complete paginated tool output, partial
composition rollback, cancelled waiters and repeated invocations. Strategy
tests additionally cover actual directed/broadcast requests, passive posts,
no reply loop, missing response evidence, cumulative limits, deadlines and
cancelled driver waiters, queue contention and explicit response recovery. It runs without a live model. This is acceptance of
the supported profile, not proof of unrestricted plugin,
shared filesystem or production crash-recovery behavior.

An opt-in model regression covers continuation after sending peer work without
yielding in the same tool batch:

```sh
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/test_group_live_continuation.py
```

This reads the project's `.env` provider configuration. The setup turn and peer
answers are scripted to isolate the condition; the requester's continuation uses
the real model. The test allows voluntary public follow-ups and checks assigned
runs, linked responses, yielding, and passive behavior after quiescence. It saves
message/response traces in the pytest temporary directory and is skipped in the
default offline suite. Model compliance remains provider-dependent.


Reception verification also includes delayed activation across many pages,
Unicode span reconstruction, lost context acknowledgment, independent blocked
members, exact session binding, protected renderer validation, compaction,
service wakeups and sealed completion/cancellation races. Run the finite all-real
local-provider collaboration test with:

```sh
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/test_group_live_reception.py
```

It reads `.env`, runs planner/reviewer collaboration, delays an integrator beyond
both page and former background limits, verifies the original at the provider
boundary and in the integrator's public answer, then executes a follow-up review.

Challenging distributed-evidence acceptance is available separately:

```sh
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s tests/test_group_live_collaboration.py
```

All decisions, peer reports, tool calls and summaries use the configured model.
The suite covers an optimization proposal rejected and revised through an
independent peer audit, concurrent incident investigation with complementary
private evidence, and migration synthesis after repeated real compaction and a
late policy update. It checks numerical results, original document references,
auditor identity and approval ordering, exact source retrieval before final
publication, persistent system rules, and quiescence without reply loops.
Each test writes provider traces and a `case-result.json` with an explicit
`validation_passed` flag into its pytest temporary directory. These files are
diagnostic artifacts; they do not contain provider credentials.
