# Group collaboration: architecture proposal

Date: 2026-09-24

Implementation note (2026-09-26): The user subsequently authorized a policy-free
foundation. Its implemented scope and deliberately deferred contracts are listed
in [Group foundation](../../group-runtime.md). This revision distinguishes that
implemented scope from the corrected target contracts below.

Status (updated 2026-09-26): The user authorized correcting the design boundaries
identified in the [semantics alignment review](../reviews/2026-09-26-group-semantics-alignment-review.md).
These corrections define the target beyond the implemented manual foundation;
they do not claim that its runtime interfaces already provide these capabilities.
No concrete scheduling policy is selected or introduced. Names and layouts
describe responsibilities, not a frozen public API.

Revision: The review findings R1–R6 are addressed below as proposed behavioral
contracts. See section 10.1 for the resolution map. This is design closure, not
evidence of implemented or tested Group behavior.

Operational refinement: The companion
[operational contracts](2026-09-24-group-operational-contracts-design.md)
proposes concrete settlement, async-tool, publication, storage and workspace
mechanisms. Its initial profile refines the choices below without selecting a
default scheduling policy or authorizing implementation.

## 1. Confirmed direction

- The broader architectural intent is a reusable single-Agent execution base.
  Standalone use remains a supported application of that base. A shared Group
  runtime should support different collaboration forms through replaceable
  scheduling policies. The current Group design should exercise that boundary.
- Implement a new peer Group collaboration system using the current single-Agent
  foundation. Members cooperate through group messages and their own execution.
- Treat the archived Group as a reference for product ideas only. Its code,
  public interfaces, tool names, configuration, storage and directory structure
  impose no compatibility requirements on the new implementation.
- Scheduling policy covers member selection, response collection, waiting,
  phase transitions and completion recommendations. It is broader than choosing
  the next speaker. Do not select a default policy in this design round.
- A collaboration strategy assembles compatible scheduling rules, tools,
  prompts, context projection and output handling. A pure decision function is
  one component of that strategy, not its complete extension interface.
- Agent execution does not provide human-like continuous attention. Message
  acceptance, context delivery, model activation and public expression are
  separate operations. New text alone does not require another model call.
- Do not turn the Group into a supervisor/subagent hierarchy. A member may
  coordinate a particular discussion without acquiring permanent authority over
  other members.
- Preserve original messages when limiting memory or model context. All new
  source, comments, diagnostics and documentation use English.
- This revision corrects the design. Runtime changes and concrete strategy
  implementations remain separate work from the existing manual foundation.

## 2. Architecture recommendation

Use an independent `group/` package that composes existing `Agent` instances.
Application composition creates members and their resources, then supplies them
to the Group. Dependencies point from Group to the public Agent API; the Agent
core does not import Group types.

The intended composition is:

```text
Application configuration and interaction
                 |
       Collaboration strategy assembly
       /                         \
 Tools, prompts,             Decision rules,
 context/output adapters     state and completion
       \                         /
          Shared Group runtime
                 |
        Independent Agent instances
```

Discussion, addressed cooperation and staged review should use this runtime
through compatible strategy assemblies. Their shared execution and storage
mechanisms should not be reimplemented for each collaboration form. Assemblies
may expose different tools and input/output rules; selection order alone does
not define their differences.

| Approach | Tradeoff | Assessment |
| --- | --- | --- |
| Direct application glue around several Agents | Few initial concepts; policy, state and cleanup are easily mixed together | Suitable for a disposable experiment |
| A focused Group runtime with a policy boundary | Explicit ownership and testable scheduling without replacing the Agent | Recommended |
| A general actor/distributed-workflow platform | Requires transport, supervision and recovery contracts beyond local collaboration | Reconsider only when actual requirements justify it |

Composition is the proposed runtime relationship. Whether users eventually load
it as a plugin is a separate packaging decision. Inheritance from `Agent` is
unnecessary: the Group owns several sessions and runs, rather than behaving as
one additional Agent session.

The logical responsibilities are:

| Component | Responsibility |
| --- | --- |
| Group records | Original public messages, identities, reply relations and recorded execution outcomes |
| Dispatch state | Pending work, reservations and member-run outcomes; separate from display/read positions |
| Collaboration strategy | Assemble compatible tools, prompts, input/output adapters, decision rules and versioned state |
| Decision function | Propose runs, waits, phase changes and completion from detached observations |
| Decision driver | Obtain bounded observations, evaluate rules, submit plans and wait for relevant changes |
| Group runtime | Commit accepted plans/state, reserve work, enforce capacity, run members and settle outcomes |
| Member context | Apply the assembled projection rules to identified triggers and background through supported Agent interfaces |
| Collaboration interface | Stable member-bound commands, queries and acceptance receipts handled by the runtime |
| Group tools | Express the assembled strategy's supported operations through runtime-validated commands |
| Application service | Configuration, resource ownership, user interaction and lifecycle |

These are responsibility boundaries, not a requirement to create a class or
plugin interface for every row.

### 2.1 Agent base and organizational extensions

The proposed base is an independently usable execution component with a stable
behavioral contract. It does not require a new class named `BaseAgent` or a
hierarchy of organization-specific Agent subclasses.

| Layer | Owns | Representative consumer |
| --- | --- | --- |
| Agent execution | Model/tool loop, private session, context budgeting, local run state, errors and execution events | Current `Agent` |
| Collaboration mechanisms | Member identities, public records, assignment/run state, capacity and lifecycle | Shared Group runtime |
| Organization rules | Selection, response collection, phase progression and completion criteria | Replaceable scheduling policy |
| Application | Configuration, resource construction/ownership, interaction and presentation | Standalone CLI/service or Group CLI/service |

The single-Agent application service is one consumer of the base, not the
mandatory parent of every other application. Group can compose Agent instances
through an application builder without depending on standalone CLI behavior.

Distinguish three extension mechanisms:

- Capabilities extend what one Agent can do through supported tools, prompt
  composition, model adapters and context projection.
- Organization rules configure how the shared runtime coordinates its Agents.
  Strategies may combine different tools, context/output adapters and control
  flows. Runtime identity, admission, ownership and durable-effect guarantees
  remain shared. Visibility capabilities are declared at composition; a new
  private-channel contract is not implied by changing selection rules.
- Plugin loading packages or discovers either kind of extension. It does not
  replace the execution or coordination contracts.

The base contract should cover request admission, observable run outcomes,
busy-state rejection, cancellation/stream closure and supported idle
configuration changes. Ordinary extensions should not need private runtime
fields or direct edits to another Agent's history. Required reliability state
must come from execution results/events, not best-effort logging callbacks.

Agent definitions and immutable configuration may be reused across
organizations. A live Agent contains mutable session/run state: one instance
must not be concurrently owned by independent organizations. Shared model
clients or storage services require explicit lifecycle and access boundaries.
Group membership does not grant access to another member's private session.

The base can evolve when an organization exposes a genuinely general execution
need. For example, support for asynchronous tool execution would be an Agent
capability if later required and deliberately designed. Speaker selection,
group reply routing and group completion remain organization responsibilities.
Adding a Group-specific mode flag throughout the Agent would weaken this
boundary.

Independent Agent tests establish the execution foundation, but do not prove
organizational correctness: message delivery, simultaneous effects, fairness,
group budgets and group shutdown need their own acceptance evidence. Flexible
composition also does not guarantee lower token cost; each member still incurs
its own context and inference work.

Use contrasting Group policies as the first real test of the boundary. A staged
review policy should reuse the same runtime as addressed collaboration.
Different execution infrastructure or message-visibility requirements may need
additional capabilities beyond a policy; changing topology alone should not
require another runtime. A universal organization superclass, topology language,
distributed runtime or plugin marketplace is not a prerequisite.

Useful architecture checks for later implementation are:

- The standalone Agent remains usable with no Group module imported.
- Replacing Group scheduling policy does not modify the Agent execution loop.
- Switching between supported collaboration forms does not modify the Group
  scheduler's execution, resource cleanup or persistence mechanisms.
- Group capabilities attach through supported composition interfaces.
- Organization shutdown respects borrowed/owned resources and active runs.
- Configuration and session state do not leak between independent members.

### 2.2 Strategy assembly and compatibility

Expose one composition entry, such as a factory or class, for each collaboration
strategy. It may reuse ordinary helpers internally; no plugin registry, universal
workflow language or separate extension class for every function is required.
The entry declares these responsibilities together:

| Declaration | Required contract |
| --- | --- |
| Identity and configuration | Strategy/version and serializable configuration/state identity |
| Member capabilities | Available tools, operation meanings and required runtime capabilities |
| Agent interaction | Prompt composition, input projection and permitted public-output selection |
| Coordination | Event eligibility, decision rules, response predicates and wait/completion conditions |
| Limits | Strategy limits within mandatory runtime admission and deadline ceilings |

Assembly validates compatibility before member execution. A tool promising to
route a report needs a supported correlated feedback path. Unsupported operations
must be absent or explicitly rejected, not accepted with silently different
meaning. The same operation contract retains its meaning; different cooperation
semantics may use different operations. Not every strategy needs Group tools.

There are two extension checks: changing rules inside one supported protocol
should leave its tools and adapters unchanged; changing collaboration protocols
may replace those components together without rewriting storage or execution.
Neither promises support for arbitrary transports, isolation or visibility models.

The initial foundation's six tools and private-final-answer behavior form one
explicit-publication profile. They must become a selectable assembly rather
than unconditional runtime policy. A different output adapter may publish only
the designated final result under its declared contract; private reasoning and
unrelated tool results are never implicitly exposed by that choice.

## 3. Public discussion and private execution

Each member retains its own Agent session, prompt, tools, permissions and
context management. The public group record contains deliberate communication
and recorded outcomes. Private reasoning and arbitrary private tool output are
not automatically broadcast to all members.

A public message needs stable identity, group-local sequence, authenticated
sender identity, content and optional target/reply references. Member-run origin
is recorded so that messages can be traced to the execution that produced them.
The runtime supplies sender identity; a model cannot impersonate another member
by passing a different sender argument.

Directed public messages identify intended respondents. They do not establish a
private channel: visibility and wake-up eligibility are distinct. Any future
private-channel feature needs its own visibility contract.

Replies reference the actual earlier message. Concurrent discussions must not
infer their reply destination from whichever broadcast happened most recently.
A separate discussion identifier can be added if message reply links prove
insufficient; a full task graph is not assumed.

The recommended initial profile uses explicit post/request tools for public
communication. An ordinary final answer remains in the member's private session;
it does not automatically fulfill a required public reply. Intermediate stream
fragments are display events, not dispatch triggers. Deduplicate retries by
stable command identity, never text equality. The companion operational
contracts define receipts and missing-reply behavior; automatic final-answer
publication can be considered later as a distinct profile.

### 3.1 Separating collaboration tools from scheduling policy

The following is a proposed refinement in response to the coupling concern;
tool names, fields and capabilities remain subject to design review.

Tools and decision rules belong to a compatible assembly and can share its
semantic helpers. Tool calls submit commands; they never recursively run another
member or directly apply a scheduling decision. The decision driver observes
accepted facts and submits proposals through runtime validation. This effect
boundary, rather than a universal fixed tool set, prevents execution coupling.

| Integration | Consequence | Assessment |
| --- | --- | --- |
| Tools call the scheduler or directly run other members | Tool behavior and prompts depend on scheduling internals; execution can become reentrant | Avoid for the shared tool surface |
| One strategy entry assembles compatible tools, adapters and decision rules | Supports different protocols while preserving explicit operation meaning and provenance | Recommended extension boundary |
| Shared command/query helpers and detached observations | Reuses common semantics and runtime guarantees across compatible assemblies | Reusable components, not a mandatory vocabulary for every strategy |

Proposed dependency and control flow:

```text
Agent tools / user interface
          |
          v
Collaboration commands and queries
          |
          v
Runtime validates and records accepted facts -> State snapshot
          ^                                      |
          |                                      v
Runtime validates and applies a plan <------ Policy functions
          |
          v
Member execution -> recorded outcomes -> next decision
```

The collaboration interface is an in-process application boundary. It does not
require an external message broker, distributed transport or general event-bus
framework. The runtime is the authority for state writes; tool adapters submit
commands and policies return plans. Logging/observational callbacks must not
be the only delivery path for accepted requests.

The core vocabulary should distinguish:

- **Post:** publish information with explicit addressing and reply relations.
- **Response request:** publish a request for participation with explicit targets
  or an explicit request to select a suitable participant. This is not a promise
  that model execution has started.
- **Member query:** return a bounded status/role view. Availability is a snapshot,
  not a reservation that remains valid after the tool returns.
- **Yield:** end the current member turn under the Agent's normal tool/turn
  boundary. It neither completes the whole Group nor permanently disables the
  member.

These are semantic operations; posting and requesting a response might share
one tool schema or use separate tools. Broadcasting addresses multiple members;
whether a broadcast informs them or asks them to respond must be explicit.
Public addressing continues to be distinct from private-message visibility.

Stable meanings must survive policy replacement. A policy may choose when and
in what order to process a response request. It must not silently reinterpret
an explicit request for B as permission to run C, turn a notification into a
request attributed to its author, or erase unresolved work. Other members may
independently react to public information under the chosen policy without
claiming that the original author requested their response.

If a strategy assembly does not support an operation, composition must expose that
restriction and the operation must be refused explicitly; familiar tool names
must not silently acquire different meanings. Compatible strategies may share
the post/request/query/yield vocabulary; specialized tools are permitted when
their required semantics and runtime support are declared together.

Command validation checks identity, membership, references, permissions and
admission limits before acknowledging acceptance. The receipt identifies the
accepted message/request. Scheduling and execution outcomes are separate
records. Do not report that a target was started or that an assignment exists
unless the runtime has actually recorded that fact. Intentional later deferral
or refusal is observable and remains linked to the original request.

For example, A asks B to review a result. The collaboration interface records
that request and returns its receipt. If B is busy, the request remains pending.
An addressed policy can propose B when it becomes available; a staged policy
can retain the same request until the review phase opens. The message, target,
reply relation and tool contract are identical in both cases. The difference
is the execution decision, not the meaning of the request. A failed B run is
recorded and does not automatically cause the tool or policy to replay effects.

Policy functions can share candidate construction, ordering, selection and
phase-evaluation helpers through the ordered composition in section 4.4.
Returning no candidates cannot silently dispose of
unresolved requests; exclusion/defer reasons must remain distinguishable.
The helpers consume normalized collaboration records, not tool names, raw tool
argument dictionaries, rendered prompt text or another policy's private state.

The policy evaluates whether to spend a model turn; the activated Agent then
decides what useful response to produce or whether to yield. Asking every
member to decide whether it wants to speak already incurs every member's model
call. The two decisions therefore remain distinct even in autonomous groups.

## 4. Decision function and driver contract

The conceptual boundary is:

```text
policy.decide(snapshot, policy_state) -> dispatch_plan
runtime.validate_and_commit(dispatch_plan) -> accepted assignments and state
runtime.launch_ready_assignments() -> member execution
```

This is a design sketch, not an implementation signature. The policy expresses
what should happen; the runtime owns the authoritative state and performs it.
Here `policy` is the assembly's decision component. A runtime-owned driver builds
its observations, obtains requested pages, applies admitted effects, handles
stale decisions and waits. Replacing this function alone is not a substitute
for assembling the strategy's Agent interaction and completion contracts.

### 4.1 Inputs and decisions

The snapshot contains detached member status and declared role/capability
metadata, bounded public-message and request windows, assignment/run outcomes,
capacity and the relevant state revision. It exposes
neither mutable Agent objects nor the Group's writable internal state. Message
bodies can be provided in bounded views where selection needs them; each
decision must not copy the complete group history.

Public posts must be observable even when they create no response opportunity.
Include sender, message/reply/run identity and the relevant operation type and
version. Content-dependent rules receive bounded content with an explicit
completeness marker, or request additional observations through the driver.
Policies do not parse rendered prompts to recover message semantics. Referenced
originals remain available, and a missing window cannot be interpreted as no
new information. Multi-page observations must pass revision revalidation before
any dependent decision is committed.

It also includes the current invocation ID/state, authoritative counts of
unsettled requests and queued/active assignments, and candidate/outcome page
continuations. Counts cover the invocation, not just the visible page; a bounded
view must not present omitted work as an empty Group.

Policy state records organization-specific progress, such as the current
round, phase or response collection. It is explicit, serializable and identified
by policy/version. It does not contain live Agent objects, tasks or clients.

| Plan element | Meaning |
| --- | --- |
| Run proposals | Members to run, trigger messages, response-collection references and reasons |
| Context proposals | Identified background/trigger projections, or supported no-inference reception, with provenance and admission conditions |
| Request dispositions | Deferred, refused or cancelled opportunities with stable identity and reason; see section 5.1 |
| Wait conditions | Concrete messages, member outcomes or user input needed for further progress |
| State transition | Proposed next policy state, including phase or collection progress |
| Completion recommendation | Requested outcome, scope and the evidence/reason for ending |
| Discovery progress | Advance the supplied continuation or declare the supplied candidate scan exhausted |

These are required semantic decision categories, not a finalized dataclass.
The current `Snapshot`/`DispatchPlan` implement only a subset. A future driver
must not hide missing categories in application callbacks or infer completion
from an empty run list. A request for more observations performs bounded reads,
not model execution or authoritative state changes.

An empty plan is not proof of completion. A wait is not an instruction to cancel
accepted work or close the application. Scheduler wakeups follow relevant state
changes rather than repeatedly polling an unchanged policy snapshot.

A proposal identifies a member, triggering message IDs and the selection
reason. Policies that offer one response opportunity to several candidates can
also request an exclusive reservation for that opportunity. Such reservations
prevent duplicate dispatch; they are not claims of exclusive ownership over a
real-world task or workspace file. A response collection has stable identity and
an explicit participant/assignment set; unrelated later replies do not complete
it merely because the whole Group temporarily becomes idle.

### 4.2 Admission and state ownership

Policy decides eligibility, priority, waiting/collection rules and phase
progression. Runtime owns the following invariant checks for every policy:

- member exists, is enabled and is available for the proposed reservation;
- referenced messages belong to this group and remain eligible for admission;
- no duplicate assignment or conflicting response reservation is admitted;
- pending-work/run limits permit admission, and execution capacity permits launch;
- shutdown/cancellation state permits a new launch.

State may change after selection. Admission must revalidate the plan's relevant
conditions. Assignments, request dispositions, dependent policy state and
discovery progress are accepted together, or none changes. The policy must not
advance into a phase that assumes work was launched when that work was actually
rejected.

An accepted assignment may wait for execution capacity. For example, three
accepted peer responses can run with two active slots; the third remains queued
and still belongs to the collection. Queue capacity remains bounded. Launch
rechecks that the member is idle, so no Agent instance runs twice concurrently.

Decision functions propose state transitions but never commit delivery or policy state,
mutate a member session, invoke an Agent or close resources. The runtime records
queued, started and terminal outcomes. A failed launch remains an explicit
outcome for the policy; it must not disappear from an expected response set.
Context proposals use declared adapters at supported Agent boundaries. Validate
source references and input budgets before committing dependent delivery state;
preparing a representation alone is not evidence of a successful model call.

### 4.3 Policy implementation and replacement

Rule-based, broad-wakeup and explicit-addressing policies can share this
boundary with staged policies. The initial recommendation is deterministic,
bounded rule evaluation over detached inputs, with no storage/model/tool I/O
inside the decision function. Collection and completion logic can use internal helpers;
they do not each need an independent plugin interface.

A later model-based selector would need explicit latency, usage and cancellation
accounting. It is not a dependency of the first rule-based implementation.
Policy errors are reported, not silently replaced by another strategy.

Replaceability does not imply arbitrary hot swapping. The initial recommendation
is to switch only when there are no active or queued assignments, with unresolved
inputs explicitly accounted for. Preserve recorded history and previous policy
state; initialize the new policy explicitly without replaying settled work or
interpreting another policy's phase state as its own.
Replacing a complete assembly additionally checks tool/operation versions,
context/output contracts and ownership of unresolved response collections.
Recorded commands keep their original semantics and provenance. Reject an
incompatible replacement instead of reinterpreting history under new tool names.

### 4.4 Composing policy functions

A strategy may compose ordinary deterministic functions inside `decide`.
The sequence below is one useful implementation pattern, not a required
algorithm for every strategy. Other event/state logic is valid if it returns
bounded proposals satisfying the same admission and completion contracts.

| Order / function | Input | Output and ownership |
| --- | --- | --- |
| 1. Advance progress | Snapshot outcomes and prior policy state | Proposed phase/collection state and consumed outcome positions |
| 2. Classify candidates | Snapshot candidate window and proposed progress | Eligible existing opportunities or proposed policy-originated opportunities, plus explicit defer/refuse proposals and reasons |
| 3. Order and select | Eligible opportunities, proposed progress and admission capacity | Ordered run proposals, selected new opportunities and proposed collection enrollment |
| 4. Conclude | Snapshot plus the entire provisional result of steps 1–3 | Scoped wait or completion recommendation, or continue |

The provisional result includes existing work, proposed assignments and
dispositions. Completion therefore sees what selection just proposed. Functions
return detached values; each step consumes the preceding result and only owns
the output described above. Outcome consumption positions commit with policy
state, so re-evaluation after a rejected plan does not double-apply outcomes.
Selection can extend enrollment but cannot rewrite unrelated phase progress.

A small assembler produces one plan and verifies the composition rules:

- Do not run and terminally refuse/cancel the same opportunity.
- Do not both propose new assignments and finish the same invocation.
- A collection wait can accompany runs that supply its expected responses; a
  wait does not suppress unrelated eligible proposals.
- Ordinary completion must account for all pending work and unexamined candidate/
  outcome windows, not just the current page. Runtime checks authoritative counts;
  forced stops use section 8.1 without waiting for a discovery scan to finish.
- Incompatible outputs are explicit policy errors, not resolved by whichever
  function happened to run last. No state is committed from an invalid plan.

Addressed and staged policies can share this pipeline when useful. The addressed
policy can use identity phase progression, target-based classification, a chosen
ordering function and an explicit completion predicate. A staged policy can use
collection-aware progression/classification and its own completion predicate.
They are not required to share the same internal flow. Replacing only ordering
must leave the enclosing operation and response contracts unchanged; replacing
the complete assembly may change its declared capabilities coherently.

The selector must respect advertised bounded admission capacity. If a collection
cannot be admitted in full, its expected participant set remains explicit while
unassigned opportunities remain pending; enroll assignments incrementally.
Do not advance to collection completion after admitting only the first batch.

### 4.5 Waiting and candidate discovery

The runtime serializes authoritative writes and associates every decision with
a state revision. Pure policy evaluation performs no I/O or `await`. The proposed
storage owner commits asynchronously, so changes between snapshot, decision and
commit are expected and must be revalidated at the transaction boundary.

A rejection caused by a newer revision schedules another decision immediately,
subject to yielding between bounded decisions. Entering a wait and checking for
unseen changes share the serialized state boundary. Notification flags may
coalesce, but the scheduler rechecks the revision before sleeping; consuming a
notification alone is never evidence that all changes were processed.
Unchanged invalid plans are reported rather than retried in a busy loop.

Candidate windows carry a scan identity, continuation, fixed sequence upper
bound and exhaustion flag. A plan that cannot act on the current window advances
the continuation and schedules another bounded decision without requiring an
external message. Deferred requests remain pending when the cursor passes them.
The runtime fetches pages; policy functions never access storage themselves.

Scanning the unchanged exhausted set stops. Discovery bookkeeping alone does
not restart a scan or count as new collaborative work. Newly appended messages
remain visible as follow-up work beyond the scan's upper bound. Changes that
make earlier candidates eligible, such as a free member or a new phase, require
a revisit before waiting or completing. A finite scan must not restart solely
because more messages were appended; otherwise busy groups could starve later
pages. The runtime yields between pages so discovery does not monopolize I/O.
Required outcome pages obey the same continuation rule before ordinary completion.

Discovery positions are separate from display/read positions and work
settlement. Progressing a scan never acknowledges, assigns or completes work.
This guarantees discovery progress, not fairness for a policy that intentionally
keeps refusing to select an eligible member.

### 4.6 Two walkthroughs of the same runtime

These are interface checks, not selected defaults or implemented features.

Both examples use the proposed explicit-publication profile. Receipts follow
the transaction commit guarantee described in the companion operational
contracts. Runtime supplies invocation, request and sender identities.

**Addressed collaboration:**

1. The application opens invocation G1. A requests a review from B through the
   collaboration interface. Runtime commits the original and pending opportunity
   together and returns a receipt containing G1 and the request ID.
2. Classification retains B as the target. If B is busy, it proposes a deferral
   with a member-availability dependency. A later candidate page can still
   contain eligible work for C; discovery continues before waiting.
3. Once B is free, selection proposes B and runtime atomically reserves the
   opportunity. B starts. Another request for B is accepted into G1 and remains
   pending; B's current input does not change.
4. B explicitly posts a reply linked to the first request, then its model raises.
   The execution adapter closes the stream and records a failed assignment. The
   post remains public; no effect is replayed and the failure is not success.
5. Outcome progression observes the failure. A retry requires a new explicit
   attempt. The second request can proceed after B is actually idle. If the
   policy refuses another pending request, runtime records its terminal refusal
   and reason independently of the policy's private state.
6. With no dispatchable work, the policy may wait for user input. A configured
   completion predicate can instead propose closing G1 after all its work and
   discovery are accounted for. Input accepted before close must be considered;
   a request reaching G1 after the close boundary is explicitly rejected. The
   application may open G2 after G1 settles, without replaying G1's effects.

**Staged peer review:**

1. The application opens G3 with a request for A, B and C. The policy defines a
   collection with those three expected opportunities. Queue capacity allows
   three assignments while execution capacity allows two active members.
2. Selection proposes three assignments. Runtime commits their enrollment and
   the collecting state together; A and B launch, C stays queued in the same
   collection. A stale rejection would commit none of these changes and would
   cause re-evaluation instead of a wait on the rejected collection.
3. B publishes a partial result and then fails. Its accepted post and failure
   are retained. C can launch after B's cleanup releases the slot. Outcome
   progression consumes each assignment result once, including B's failure.
4. This example's configured rule waits for all three outcomes, then allows a
   review of the declared partial result. A different rule could require an
   explicit retry; failure alone never triggers effect replay. Once outcomes
   are complete, classification proposes a policy-originated review opportunity;
   selection assigns reviewer A with collection references. Runtime records the
   new opportunity, assignment and dependent phase transition together.
5. An extra request to B deferred until review remains pending and prevents
   silent invocation completion. For this example, after A's review the policy
   explicitly refuses that request as outside the completed stage, with a
   recorded reason; it does not erase the request or reroute it to A.
6. Completion sees the settled collection, review outcome, request refusal and
   exhausted discovery. Runtime revalidates, closes G3 admission and returns an
   outcome identifying the partial result. A later user request needs a new
   invocation. If cancellation occurs earlier, closing cancels unstarted work
   and settles active operations before returning; it does not report success.

The reviewing member gains no permanent authority over peers. In both examples,
switching the ordering function changes selection order without changing the
collection/termination contract or any tool schema.

The differing selections and phase decisions belong to policy. Message storage,
admission, Agent execution, cancellation, failure recording and resource cleanup
are identical in both walkthroughs.

### 4.7 Contrasting Agent-based collaboration traces

These traces check the extension boundary against the three archived ideas;
they introduce no concrete strategy, default, legacy tool name or compatibility
requirement. Each step must identify its input observations, whether inference
occurs, the accepted effects and the next wait/stop condition.

| Reference form | Activation and context | Continuation and termination |
| --- | --- | --- |
| Broad reaction, corresponding to the old `default` idea | One eligible member handles initial work. Its selected public output may create reaction opportunities for other members under declared eligibility rules. Each activated member receives identified triggers and background. | A local pass/yield or runtime receipt does not itself request another response. The strategy defines which new contributions justify further runs. Repeated fresh replies remain subject to the invocation ceiling and cannot be reported as successful completion merely because the ceiling stops them. |
| Explicit participation, corresponding to `on_demand` | Ordinary posts become available for declared context projection without automatically running peers. An explicit directed request selects its target; a broadcast request identifies its participant set. | A normal reply is recorded without an implicit follow-up run. Further participation needs the declared request/routing rule. No eligible run can mean waiting, not task completion. |
| Correlated feedback, corresponding to `broadcast_feedback` | A opens collection R with expected peers B and C. Their replies and execution outcomes retain R's request/opportunity identities. Another collection S remains independent. | R's predicate accounts for each expected response or explicit failure/refusal. It may then propose A's follow-up, even if unrelated S still has running work, subject to capacity. A's output triggers further work only under the declared continuation rule. |

For every trace, check a member already running when new input arrives, several
inputs coalesced before activation, a missing public reply, failure after a
committed post, and cancellation. A failed member does not disappear from a
collection. Retry creates a linked new attempt. No strategy needs an Agent
subclass, and no complete strategy is assumed to fit only a speaker-ordering hook.

## 5. Delivery and execution state

For the proposed public-chat reception profile, the
[reception and activation addendum](2026-09-26-group-reception-design.md)
specifies complete unread coverage, passive synthetic representation, context
application receipts, and closing/cancellation boundaries. It refines the
delivery choices below; it is not a statement of implemented runtime behavior.

Keep the following facts separate:

1. A message exists in the public record.
2. It is eligible for a member's context under the selected projection rules.
3. A projection has staged it, or a specific model request has included it.
4. A policy has proposed that the member act, and a run has reserved that work.
5. That run has ended with a recorded execution outcome.
6. The strategy's response or collection predicate has accepted the required
   evidence, or recorded that it remains missing, failed or explicitly waived.

None of these facts implies that an uninvoked model understood a message.
Merely being eligible for context does not guarantee later delivery. A strategy
declares whether information is staged without inference, projected on the next
activation, or causes a proposed activation. A new message never intrinsically
means that every member must run.

A display/read cursor is useful for pagination. It is not proof that work
completed, and it must not silently settle earlier unhandled messages.
Delivery state should be recorded for actual admitted projection/reception
operations or member-run inputs rather than eagerly creating a message-by-member
matrix for every public post. No-inference reception has its own operation
identity and does not require a fictitious execution assignment.

Execution outcomes distinguish completed, yielded, stopped, failed, cancelled,
timed out and limited; section 6.1 defines their mapping. A handled conversation
turn does not by itself prove the user's overall task is complete.

If no model turn is needed, record the scheduling disposition when useful for
diagnosis. A disposition must not masquerade as an actual model response or
model-initiated tool execution. A declared context adapter may use an explicitly
identified synthetic representation through supported Agent interfaces. Preserve
its runtime provenance separately from model-authored originals; do not publish
it as a member's judgment, count it as inference, or treat it as reply evidence.
No synthetic PASS format is required by this design. Compatibility with provider
history formats and later context summarization requires separate validation.
The no-inference guarantee comes from not entering the execution path.

Messages arriving while a member runs stay available for a later admission.
The current member input remains a stable snapshot. Failure after tool effects
must retain those effects and any already accepted group posts. A failed
assignment is not automatically replayed as if nothing happened. Retrying is
an explicit new attempt with a link to the prior outcome.

### 5.1 Opportunities and policy-independent settlement

An accepted response request owns one or more stable response opportunities.
An explicit multi-target request has a slot for each requested participant; a
request to select one suitable participant has one reservable slot. Allocate
these only for real requests, not for every public message/member pair.
All belong to the invocation identified in the acceptance receipt.

Policies can also propose work that no member explicitly requested: a review
stage, a reaction to an informational post, or a new retry attempt. Classification
produces a policy-originated candidate with message/collection/prior-attempt
references; selection chooses whether to admit it. Runtime creates the new
opportunity and assignment together with dependent policy state. No unselected
candidate is falsely recorded as accepted work. For incrementally enrolled
collections, policy state retains the expected participants not yet admitted.

A policy-originated opportunity records the invocation and policy origin, never
a manufactured member-authored response request. A retry links its prior attempt
and the original request when one exists. Its origin key stays stable across re-evaluation of
the same logical proposal, so runtime can reject duplicate admission; a deliberate
new attempt uses a new key. Such work cannot settle an explicit request for a
different member or overwrite any earlier opportunity. Recorded opportunities
use the same capacity, lifecycle and outcome rules regardless of their origin.

| Opportunity state | Meaning and permitted transition |
| --- | --- |
| Pending | No assignment owns it. Deferral retains this state with a reason and a dependency for reconsideration. |
| Assigned | An accepted queued or running assignment owns it. Mere candidate selection cannot produce this state. |
| Settled | Retains the assignment outcome, or an explicit refusal/cancellation before assignment. Settlement never implies success by itself. |

Policy proposes deferral/refusal; runtime validates and commits these as shared
records. Runtime also records cancellation and links assignment outcomes to the
opportunity. Refusal is terminal for that opportunity; deferral is not. Refusal
of one target does not settle another target's slot. An assigned opportunity
cannot be relabeled as refused: cancel/settle the real assignment instead.
Repeated identical deferral observations need not create a new revision or
unbounded duplicate log entries.

A failed or yielded assignment remains that outcome, not an automatically
fulfilled request. Policy determines what further collaboration is required.
Any retry is a newly identified opportunity/attempt linked to the prior outcome
and original request when one exists, admitted under the same limits. A reaction
to an informational post can instead retain its triggering-message references.
Never reopen or overwrite an old terminal outcome to hide the prior attempt.

Response satisfaction has a separate predicate and evidence set. For example,
an assembly requiring public review waits for accepted replies linked to its
request and expected participants, not merely assignments marked `completed`.
A different assembly may explicitly accept a selected private result. State the
rule and its version, evidence references, accepted failure/partial-result cases,
and missing-response disposition in collection state. A posted reply followed
by execution failure retains both facts; neither automatically cancels the other.
Missing evidence may lead to waiting, an explicit new attempt, refusal or a
declared partial result. Settled slots are not silently reopened.

The manual foundation's `finish` leaves this predicate to the application. A
future automatic driver must obtain the assembly's decision and revalidate its
evidence and work counts before closing. Execution settlement alone is never
an implicit assertion that a response contract was fulfilled.

On an allowed policy replacement, settled slots remain settled, pending slots
remain pending, and old policy-specific deferral conditions expire. Preserve
their recorded reasons for history; the new policy must evaluate pending work
using its own conditions. Private policy state is not the sole record of whether
a request was refused, assigned or settled.

## 6. Runtime and integration

Proposed control flow:

```text
Accept public message
  -> preserve original and any explicitly requested response opportunities
  -> build bounded observations under the selected assembly
  -> decide whether to project context, run, wait, obtain more input or finish
  -> atomically admit proposals, state, provenance and budget reservations
  -> prepare validated member input and launch within execution capacity
  -> consume the member Agent event stream
  -> apply the assembly's public-output rule and record execution outcomes
  -> release reservations, evaluate response evidence and reconsider eligibility
```

Use the existing `Agent.arun_events()` execution surface through the adapter in
section 6.1. Normal completion yields a terminal event; exceptional execution
raises to the iterator caller while publishing its terminal event only to the
observer bus. Both paths must be handled directly. The current observer bus
deliberately isolates callback failures and is not Group's control channel.

Group tools adapt the assembled collaboration commands/queries to the
runtime-owned interface; they neither bypass the decision driver's admission
path nor invoke another member recursively.
The recommended initial profile adds generic async-tool dispatch so publication
and queries can await storage without blocking the scheduler. Pure local yield
may remain synchronous. Runtime still owns all member execution.
Whether a post wakes peers immediately or after the publishing run finishes
can vary by policy once the runtime defines which committed events are eligible
to trigger decisions. The receipt follows transaction commit and runtime revision
publication; a policy cannot make an uncommitted message visible or undo an
already accepted public post. Cancelled receipt delivery does not undo acceptance.

Use one async scheduler as the initial proposed implementation. A synchronous
entrypoint, if required, should adapt that runtime instead of creating a second
scheduling algorithm. Same-member execution remains serial. Cross-member model
I/O can overlap subject to a configured capacity.

Current synchronous tools execute on the event-loop thread and may block other
members. The proposed execution profile instead uses native async I/O, explicitly
classified tracked workers, and fast nonblocking synchronous stages. Classification
covers permission/validation hooks, custom executors, result adapters and audit
sinks as well as handlers. Unsupported bindings fail during Group composition;
the companion operational contracts define their ordering and bounded audit path.
Worker cancellation remains cooperative;
a configured deadline does not prove that an arbitrary blocking operation has
stopped. The companion operational contracts define execution/context ownership
and prohibit premature member, workspace or resource reuse.

Resources need explicit ownership. An application-created member/client may be
owned; an injected member/client may be borrowed. Shared clients must not be
closed repeatedly or while another member still uses them. Group shutdown
stops new admission, cancels/settles owned runs, closes their event streams and
then closes owned resources. Do not mark a still-running operation as cleaned
up merely because a caller stopped waiting.

Member tool bindings and prompt composition happen through supported idle
composition surfaces. Avoid replacing existing tool registrations, mutating
private Agent fields or resetting member context after every turn. Tool-name
collisions should fail clearly. Membership changes during execution need a
separate contract; idle-only changes are the simpler initial recommendation.

### 6.1 Member execution adapter

The adapter owns one accepted assignment and its Agent iterator. It captures the
Agent run ID from `run_start` when available; a failed launch still has its Group
assignment ID even if no Agent run started. It consumes yielded events and
exceptional termination, explicitly closes the iterator and awaits the generic
execution settlement described below, then commits one terminal assignment
record through runtime state ownership.

| Observed path | Group assignment outcome |
| --- | --- |
| Yielded `run_end(completed)` | completed; not automatic Group completion |
| Yielded `run_end(max_turns)` | limited |
| Yielded `run_end(tool_stop)` | yielded for the bound Group yield tool; otherwise stopped, retaining the stopping tool's identity |
| Raised `AgentTimeoutError` | timed out |
| Raised cancellation or an explicitly requested early close | cancelled, with the originating lifecycle reason |
| Other execution exception or failure before `run_start` | failed, with error/launch information |
| Exhaustion without a terminal event or a known close reason | failed due to execution-protocol violation |

`stopped` makes no completion claim. Any richer ending-tool mapping needs an
explicit composition contract; the adapter must not infer success from tool text.
For `tool_stop`, track the ending tool from execution events/bindings, not a
private Agent field. An unidentifiable stopping tool is recorded as unknown.

The terminal record is committed once per assignment identity; repeated cleanup
notifications cannot rewrite it or release its reservation twice. A terminal
candidate is provisional until iterator cleanup has completed. If cleanup fails
or an underlying operation is still active, report that failure and retain
execution ownership; do not launch another run on that member or close its
client as if cleanup succeeded. Member failure normally becomes a policy-visible
outcome rather than escaping and terminating unrelated members. Invocation
cancellation is re-propagated after settlement; process-level interrupts must
not be swallowed as an ordinary recoverable member failure.

Already accepted posts and tool effects survive every path. This adapter neither
replays them nor relies on an observational callback to settle the assignment.
No Group-specific branches are required in the Agent execution loop. The generic
settlement capability below is a prerequisite for the cleanup guarantees, not
something an outer adapter can infer from today's event surface.

**Required Agent capability: observable execution settlement.** Current Agent
stream cleanup can log and suppress a provider-close error while preserving the
original cancellation/timeout (`react_loop.py`, `_close_async_stream`). Context
I/O uses `asyncio.to_thread`; cancellation of its await does not prove the worker
has finished. The Agent guard can already be released in either situation.
Consequently, closing `arun_events()` and seeing an inactive run guard are not
sufficient evidence for releasing all underlying resources.

Before claiming complete Group cleanup, add the generic execution handle defined
in the companion operational contracts. Concrete method names remain to be
selected, but the base capability must:

- Retain a per-run handle to owned asynchronous operations and context-I/O
  workers, independent of whether the event iterator or its caller is cancelled.
- Expose the primary run outcome and cleanup outcome separately, preserving
  original exceptions while making cleanup failure/incompletion observable.
- Allow awaiting actual completion of tracked operations; a cancelled awaiter
  must not erase the underlying operation or its completion tracking.
- Distinguish confirmed cleanup from still pending or failed/unknown cleanup.
  Only confirmed cleanup permits safe member/resource reuse under this contract.

The Group adapter consumes this public surface. Pending/failed cleanup leaves
the member unavailable, retains its resource ownership and blocks final scope
settlement, while unrelated members may still progress in an open invocation.
It may report the primary failure immediately without claiming cleanup completed.
This is a general single-Agent lifecycle improvement, not a Group dependency in
core. It does not promise to kill arbitrary threads, undo external effects or
provide crash recovery. Implementation must validate it before Group shutdown
acceptance; the existing offline terminal-event probe does not prove it.

## 7. History, context and storage

Keep the complete public message record separate from bounded memory caches and
member model inputs. Context selection limits what is sent now; it does not
delete historical originals. Each member can use the existing Agent compaction
for its private working history. Group-history retrieval must check group
membership and use bounded pages; an Agent's private archive access does not
automatically grant access to other members' private archives.

Candidate input needs an explicit budget. A large backlog can be admitted in
batches; unadmitted messages remain pending. An indivisible oversized request
must fail with a useful explanation or follow an agreed reference/ingestion
protocol, rather than being silently shortened.

Budget the rendered member input before invoking Agent. The current Agent pins
the newest User input during compaction; wrapping the whole Group backlog in
one User message is therefore not a substitute for bounded input construction.
Use selected content and stable references with paged retrieval for older
material. Candidate discovery and model input have separate budgets/cursors.

The recommended initial backend is local SQLite with one serialized write owner,
WAL and FULL synchronization. Original message, response slots, operation receipt
and revision commit together before acceptance is reported. Async Group tools
await this boundary through a bounded storage queue. The companion operational
contracts cover duplicate commands, uncertain commit delivery, quota/storage
failure and restart without automatic execution. No external database service
or general persistence framework is required.

Saving an idle Group should eventually capture membership/config references,
public records, assignments/outcomes, policy identity/configuration/state and
each member's session/archive references at one consistent boundary. A data
snapshot may preserve waiting assignments while no member executes; switching
policy has the stricter requirements described above. Restoring data must not
automatically start queued work or replay tool effects.
Automatic recovery of running work and exactly-once external effects are
separate features, not consequences of storing messages.

The companion operational contracts define a self-contained snapshot manifest,
admission/session freeze, reference validation and suspended restore. Snapshot
export remains a later feature rather than a prerequisite for core collaboration.

## 8. Waiting, completion and resource limits

Quiescence means there are no running members or currently dispatchable
assignments. It does not prove task completion. Pending work addressed to an
unavailable member must remain visible rather than being converted to success
or silently rerouted to the rest of the group.

Represent waiting, an explicit completion decision, budget exhaustion,
cancellation and failure separately. A member's claim of completion is an input
to the policy's configured completion rule; it is not universal authority to
stop all peers. The policy proposes completion with its reason and scope. The
runtime revalidates it against current work, so newly accepted input cannot be
silently ignored by a stale finish recommendation.

Pending requests and pending/running assignments require an explicit disposition
before an invocation can end. Draining or cancelling work follows the lifecycle
in section 8.1; a finish recommendation cannot relabel uncompleted work as
successful. Deadlines, user cancellation and resource ceilings are runtime
responsibilities independent of policy, subject to the cooperative execution
limits in section 6.

Runtime limits include active member count, admitted runs per invocation,
deadline, message/input size and pending-work capacity. Member model usage and
summary usage should be attributed and aggregated. Unknown provider usage
cannot become an exact global token/spend guarantee.

When an admission or storage limit is reached, apply explicit backpressure or
reject new work before acknowledging it. Do not discard accepted originals or
busy-poll a policy that keeps returning no eligible work.

Parallel file writes require an independent workspace rule. The proposed shared
workspace profile holds read or exclusive write leases for whole member runs,
including unsettled cleanup, and requires fresh observations and expected-base
checks for mutation. Separate writable roots/worktrees support parallel mutation
or commands without checked-edit support. The companion operational contracts
describe the tradeoffs; private sessions and scheduling alone do not isolate files.

### 8.1 Invocation scope and closing admission

A Group is a long-lived record and membership container. An invocation is one
explicitly opened execution scope with a stable ID; the initial runtime permits
only one nonterminal invocation per Group. The application opens an invocation
and submits input to its handle. Member commands inherit the assignment's
invocation ID. Acceptance receipts identify that scope; delayed commands never
silently attach themselves to whichever invocation happens to be current.

The lifecycle is `open -> closing -> terminal`. Waiting is an observable state
inside an open invocation, not automatic completion or creation of a new scope.
The application can submit input or explicitly cancel it. Policy replacement
at an allowed idle boundary preserves the invocation and its pending requests.

An ordinary completion recommendation is valid only after discovery/outcomes
are accounted for, no assignments remain queued/active, and no pending
opportunities remain after applying any terminal dispositions in the same
atomic plan. Deferral is not terminal settlement and cannot satisfy this check.
The policy's configured completion predicate supplies the task-level reason;
it identifies its response/collection evidence, not just run outcomes. An empty
queue alone is insufficient. A result that permits failed/refused
responses must expose those outcomes and identify any partial result.

Revalidation and `open -> closing` happen at the same serialized commit boundary
as command acceptance. An input accepted before that boundary belongs to the
scope and must affect revalidation; a new request arriving after it is refused
with a scope-closing/closed result before any acceptance receipt. This initial
contract does not silently buffer input for a future invocation. The application
may explicitly open another invocation after terminal settlement and resubmit
rejected input. Already accepted originals are never discarded.

Cancellation, deadline, budget stop or fatal policy/runtime failure may enter
closing while work remains. Runtime immediately stops new launches, cancels
unassigned opportunities/queued assignments with the closure reason, and
cancels or drains active operations according to the declared stop mode. Every
normal finish/cancellation plan specifies its stop outcome; runtime hard stops
do not depend on a working policy. Invocation settlement waits for actual
cleanup and records uncompleted work without claiming success.

After closing, external posts and all new response requests are rejected.
An already admitted member finishing cleanup may still record a plain post tied
to its assignment and original invocation. Its receipt explicitly indicates
that it creates no new dispatch work in the closing scope. Queries remain
available. A combined post-and-response-request command is refused as a whole;
do not silently downgrade it to an informational post. These rules preserve
in-flight results while preventing peers from creating endless new work during
shutdown. Posts/effects accepted before closing remain recorded unchanged.

On terminal settlement, return the scope outcome, reasons and request/assignment
references. Do not silently reopen failed work in a later invocation. Unresolved
cleanup keeps the invocation nonterminal and prevents resource reuse, even if
the application stops waiting. Automatic crash recovery is still out of scope.

### 8.2 Conversation bounds and feedback-loop control

An automatic decision driver requires both strategy-level continuation rules
and runtime-enforced invocation ceilings. Concurrent, queued and pending limits
only bound occupancy; a sequence of single-member runs can still continue forever.
Stable origin keys prevent duplicate admission of the same logical proposal,
not an endless chain of newly identified proposals.

Before enabling an automatic driver:

- Configure a finite cumulative admitted-run limit and invocation deadline.
  Count every newly admitted assignment, including reactions and retries, in
  the same transaction as its reservation. Rejected plans consume no admission;
  replay of the same accepted operation consumes no extra admission. Completed,
  cancelled and failed assignments do not refund the cumulative count.
- Revalidate remaining allowance against the full plan; reject an over-budget
  plan atomically. Already admitted work is not retroactively rejected when the
  counter reaches its ceiling. If further required work cannot be admitted,
  return an explicit limited outcome and apply the closing contract. Do not
  disguise it as normal completion or silently open a new invocation.
- Enforce the deadline independently of a functioning strategy. Fence new work
  and cooperatively stop/drain existing executions; deadline expiry does not
  prove a blocked worker has settled.
- Declare which accepted message/output categories can create new work and why.
  Bookkeeping, receipts, read positions and unchanged policy state are not new
  collaborative work. Waiting on the same exhausted observations does not cause
  repeated inference. Decision evaluation itself must remain bounded.
- Explain how multiple triggers for one busy member are retained or coalesced,
  with their identities preserved. A state notification is a reason to inspect
  eligibility, not an unconditional instruction to launch every member.

Usage aggregation remains useful, but unknown provider usage cannot establish an
exact token or monetary ceiling. Admission counts and deadlines supply enforceable
backstops without relying on a model to choose PASS or on estimated task quality.
The current manual foundation has occupancy limits, not these aggregate controls.

## 9. Suggested implementation boundary for later approval

The first implementation proposal should cover the new Group domain/runtime,
replaceable organization/scheduling contract, a small group-tool surface, application/CLI
composition, explicit stop outcomes, history access and failure cleanup.
Exercise the addressed and staged walkthroughs using the same runtime to verify
the boundary, without making either policy the architecture's hidden default.

No compatibility bridge to archived Group data or tools is required. UI,
distributed execution, recursive groups, automatic crash resume, a general task
DAG and plugin discovery are not implied by this initial proposal.

Suggested acceptance scenarios, to be implemented only after design approval:

- Replay the same normalized collaboration requests through two compatible
  decision rules while retaining command meanings and acceptance receipts.
- Assemble two supported collaboration forms with different tools/prompts or
  output rules without editing the Agent loop or runtime execution/storage.
  Reject a tool whose advertised behavior lacks compatible strategy support.
- Tools can publish/query using a fake collaboration interface with no policy
  present; policy decisions can be evaluated from records without tool handlers
  or a model. These are boundary tests, not alternate production execution paths.
- A request to a busy member retains its target and remains visible until its
  scheduling disposition is explicitly recorded; a status query cannot reserve
  that member or bypass run admission.
- Three peers exchange messages under addressed and staged policies without
  changes to the runtime's execution or storage mechanisms.
- A response collection larger than execution capacity retains queued work and
  does not finish before each expected assignment has an accounted-for outcome.
- Rejected/stale plans do not advance policy state or partially reserve work.
- Two interleaved discussions keep their replies associated with the correct
  original messages.
- Observe a public post without a response opportunity through bounded policy
  inputs. A content-dependent rule must not require hidden access to the store.
- Deliver identified background on later activation without a model call at
  message arrival; any synthetic representation retains runtime provenance.
- A private-only final answer cannot satisfy a public-reply predicate. A profile
  explicitly permitting selected final-result publication exercises its separate
  adapter without exposing reasoning or arbitrary tool output.
- Repeated fresh replies remain within the cumulative run/deadline ceilings;
  stopping for a limit is distinct from completing the requested collaboration.
- A message arriving during a member run is handled later without duplicate
  admission or loss.
- A member fails after a real tool effect; peers and a later user request can
  continue without replaying that effect.
- Group cancellation and limits settle owned operations and preserve history.
- Long discussions trigger bounded context handling while original public and
  private records remain accessible under their respective access boundaries.
- Quiescence, unavailable-member work, completion and budget exhaustion produce
  distinguishable results.
- Exercise normal end events, execution errors, typed timeouts, cancellation,
  explicit iterator closure and failed launches. Each assignment settles once
  after cleanup; accepted posts survive and a cleaned member can run again.
- Inject a provider-close failure during cancellation and cancel a run while a
  context-I/O worker is still active. The generic Agent settlement surface must
  expose incomplete cleanup and retain tracking until actual worker completion;
  Group must not reuse the member or close its resources prematurely.
- Replace only ordering/selection while retaining progress and completion
  functions. Conflicting run/refuse/finish outputs fail atomically and visibly.
- Admit a policy-originated review/reaction and an explicit retry without
  fabricating a member request. Re-evaluation preserves the same origin key and
  never duplicates the accepted assignment or overwrites its prior attempt.
- Refuse one slot of a multi-target request, switch policy at an allowed
  boundary, and verify other slots remain pending and the refusal stays settled.
- Complete a member between selection and waiting. The scheduler observes the
  newer revision and progresses without needing an additional user message.
- Fill the first candidate page with deferred requests and put eligible work on
  a later page. It is discovered without new input; an exhausted unchanged scan
  stops, and a phase/member change revisits deferred opportunities.
- Close an invocation while input races the commit. Pre-close input is accounted
  for; post-close input receives explicit rejection. A delayed command for an
  old scope must never attach to a newly opened scope.
- Cancel while a member is publishing. Accepted plain posts remain recorded;
  response requests after closing are rejected as whole commands and no new
  peer starts. Blocking operations retain ownership until actual cleanup.

## 10. Recommended profile and remaining decisions

| Decision | Current proposal / unresolved choice |
| --- | --- |
| Default scheduling | Intentionally undecided; keep it replaceable |
| Collaboration assembly | One entry declares compatible tools, prompts, context/output rules, decisions and capabilities; a shared vocabulary is reusable rather than mandatory |
| Public final answers | Private finals belong to the initial explicit-publication profile; alternate declared output adapters require their own acceptance evidence |
| Publication and durability | Transactional receipts and operation-key deduplication proposed in the operational contracts; numeric capacities remain configuration choices |
| Completion rules | Choose profile-specific evidence and partial-result criteria; scope closure is defined in section 8.1 |
| Policy schema details | Choose concrete field names, serialization/version format and numeric limits; composition and atomicity are defined in section 4 |
| Execution profile | Generic settlement handles and classified async/worker dispatch proposed; individual custom bindings need compatible declarations |
| Workspace mutation | Whole-run leases plus checked mutations proposed for shared workspaces; separate roots support parallel or unchecked commands |
| Persistence | Local SQLite and a bounded write owner proposed; schema/version details and operational limits need implementation planning |
| Initial membership | Idle-only changes with confirmed settlement proposed; live join/leave remains deferred |

The companion operational contracts provide the proposed mechanisms and their
acceptance gates. Remaining choices include concrete schemas, numeric limits,
custom tool declarations and profile-specific completion evidence. They do not
weaken runtime ownership of admission, effects and cleanup. Review the proposed first
profile before implementation planning; the default scheduling policy remains
deliberately undecided.

### 10.1 Review resolution map

The [original review](../reviews/2026-09-24-group-collaboration-review.md) retains
its original draft hash and findings. This revision proposes the following
resolutions without changing that historical review or claiming runtime fixes.

| Finding | Design resolution | Validation required during implementation |
| --- | --- | --- |
| R1: Exceptional terminal outcomes | Section 6.1 normalizes execution paths and requires generic observable Agent settlement | Failure-path tests plus provider-close failure and outstanding-worker tracking |
| R2: Function composition | Section 4.4 offers a composition pattern with explicit inputs/outputs and conflict rules; its internal pipeline is optional | Replace one function inside a compatible assembly without changing runtime or tools |
| R3: Request dispositions | Section 5.1 keeps shared opportunity records across policies | Refusal/deferral, multi-target and replacement tests |
| R4: Lost wakeups | Section 4.5 ties rejection/waiting to authoritative revisions | Selection/completion/wait interleavings |
| R5: Bounded discovery | Section 4.5 defines finite scans, continuation and revisit rules | Later-page progress without polling an unchanged set |
| R6: Completion scope | Section 8.1 defines invocation identity and atomic admission closure | Input races, late commands and cancellation settlement |

The [2026-09-26 feasibility review](../reviews/2026-09-26-group-feasibility-extensibility-review.md)
adds F1, full-pipeline execution classification. Operational contract sections
3.1–3.2 now define hook/executor compatibility, authorization-before-effect and
bounded ordered audit output, with acceptance scenarios in its section 8. These
are design refinements, not claims of implemented async behavior.

### 10.2 Semantics alignment corrections

The [September 26 alignment review](../reviews/2026-09-26-group-semantics-alignment-review.md)
identified the following corrections. They update the target contracts without
changing the documented behavior of the manual foundation.

| Finding | Corrected design boundary | Remaining implementation gate |
| --- | --- | --- |
| Fixed communication profile | Sections 2.2 and 3.1 assemble tools, prompts, context/output adapters and rules together | Replace hardcoded composition/input/output choices through supported interfaces |
| Insufficient policy observations and decisions | Section 4 defines public-message observations and the bounded driver protocol | Implement the richer records/driver before automatic strategies |
| Missing context-delivery boundary and categorical synthetic ban | Section 5 and operational section 7.1 separate delivery/activation and require truthful provenance | Implement/test the chosen adapter; no synthetic format is mandated |
| Settlement mistaken for response satisfaction | Section 5.1 and operational section 4.1 require profile-specific evidence | Add collection/completion validation without relabeling execution outcomes |
| Occupancy limits insufficient for feedback loops | Section 8.2 defines cumulative admission, deadline and continuation requirements | Enforce ceilings before enabling an automatic driver |

Section 4.7 supplies contrasting behavior traces and section 9 supplies acceptance
scenarios. No default policy, archive migration or Group-specific Agent subclass
is introduced by these corrections.

## 11. References and limits of borrowing

- [AutoGen Group Chat](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html)
  separates public group messages, requests to speak and termination. Borrow
  those responsibility boundaries. Its documented sequential manager example
  does not determine this project's concurrency or member authority.
- [Pi repository](https://github.com/earendil-works/pi) separates the Agent
  runtime from its coding application and provider API. Borrow the compositional
  boundary; no Pi dependency or extension format is proposed here.
- [Current MAS Agent runtime](../../agent-runtime.md) supplies the actual
  integration constraints described above. This proposal is not an audit or a
  claim that the proposed Group behavior is already implemented or tested.
