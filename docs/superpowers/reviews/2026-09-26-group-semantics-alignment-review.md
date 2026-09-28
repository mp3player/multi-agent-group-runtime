# Group semantics alignment review

Date: 2026-09-26

Scope: review the current working-tree Group foundation and design documents
against the clarified Agent-based collaboration requirements. This is a review,
not approval of a new interface or an implementation plan. No production code
or existing design specification was changed during this review.

## Assessment

Retain the independent Agent base, peer composition, durable command admission,
atomic plan validation, explicit execution, and owned worker settlement. The
current manual profile does not automatically run members when messages arrive.
It therefore does not currently exhibit the automatic broadcast feedback loop
discussed with the user.

The foundation is intentionally restricted. Its passing tests do not establish
that complete collaboration strategies can be substituted. Before adding those
strategies, separate the current communication profile from execution invariants
and complete the observation, context, and completion contracts below.

No new failure of the documented manual profile was established. The findings
are important architecture corrections and prerequisites for autonomous policy
execution, rather than a claim that every deferred feature is a runtime bug.

## Findings

### 1. The communication profile is compiled into the runtime

Evidence: `group/runtime.py:43` and `:77` always install the same tools;
`group/tools.py:121` fixes their set; `group/dispatch.py:78` embeds their names and
the private-final-answer rule into every member input. The runtime discards
`ExecutionOutcome.result` when recording public execution outcomes at
`group/runtime.py:341`.

The design labels explicit publication as an initial profile, but its sections
2.1, 3.1, and acceptance examples lean toward a single shared tool vocabulary as
the main extension boundary. That is insufficient for a strategy with different
collaboration operations or a deliberately configured final-answer publication
adapter. Such a strategy currently needs changes outside its implementation.

Correction: make tools, prompt/context adaptation, output publication, policy
rules, and required capabilities coherently selectable at composition. Keep the
existing explicit-publication profile as a valid option. Keep truthful tool
semantics, authenticated identity, admission validation, and non-reentrant
execution as shared guarantees. No general plugin discovery platform is needed.

### 2. The pure policy boundary lacks the observations and decisions it needs

Evidence: `group/policy.py:8` prohibits runtime/store access;
`group/records.py:97` supplies opportunities and assignments but no public
message window or message bodies. `group/runtime.py:239` constructs that limited
snapshot. `DispatchPlan` at `group/records.py:128` has runs, terminal dispositions,
and opaque state, but no explicit discovery, waiting, or completion decision.

A newly posted message changes only the snapshot revision. A pure policy cannot
distinguish its sender, content, or reply relationship. A policy reacting to
ordinary discussion must obtain those facts outside the declared interface.
The design already describes richer observations and decisions; the foundation
has not implemented them.

Correction: define bounded, revision-aware message/outcome observations and the
driver's decision protocol before treating `SchedulingPolicy` as a complete
extension surface. Keep observations detached and effects committed by runtime.
A deterministic decision function can remain one useful policy component.

### 3. Context delivery and no-inference reception remain unspecified

Evidence: `group/dispatch.py:67` loads only messages attached to selected response
opportunities. The current proposal has no structured background-message
projection. A member can query public history after activation, and applications
can construct an instruction, but neither defines automatic context delivery.

Probe: post a background message, then explicitly run a member for a later
request. The model input contains the request and not the earlier background
message. This is consistent with the manual profile, but cannot be described as
members having received the discussion.

Correction: specify reception, input projection, inference activation, and
publication separately. Record input provenance and define safe delivery
boundaries for busy members. Keep the Agent's existing context management and
original-preservation guarantees.

The design at lines 551-553 categorically prohibits fabricated assistant/tool
records. Clarify the intended invariant: a runtime disposition must not be
represented as actual model reasoning or actual tool execution. An explicitly
identified projection adapter using supported interfaces is a separate design
choice. Do not mandate synthetic PASS, but do not prohibit every such encoding
before evaluating it. The user's example did not request its implementation.

### 4. Execution settlement is not response fulfillment

Evidence: `group/runtime.py:329` settles an opportunity with its assignment's
execution outcome; `:357` permits explicit completion once all opportunities and
assignments are settled. Existing runtime tests deliberately allow this when the
Agent produced only a private final answer.

Probe: an Agent returns a private answer without posting a public reply. The
opportunity becomes `settled/completed`; public member replies remain zero; an
explicit application `finish` succeeds. This does not contradict the current
manual contract, where the application is responsible for deciding completion.

Correction: before automatic completion, require profile-specific response or
collection evidence in addition to settled executions. Reuse the existing
message IDs, `reply_to`, run origins, and opportunity identities. Do not restore
the archive's latest-broadcast heuristic. A no-reply profile may deliberately
accept a private outcome; a public-review profile must not silently do so.

### 5. Capacity bounds do not bound an autonomous conversation

Evidence: `group/records.py:12` bounds active, queued, and pending work, but not
the cumulative number of admitted runs in an invocation or an invocation
deadline. Unique `origin_key` values prevent replay of one proposal; they do not
stop an endless chain of new proposals.

The target design already requires these ceilings at lines 806-813. The manual
foundation has no policy loop, so this is a prerequisite for adding one rather
than evidence of a current automatic loop.

Correction: pair strategy-specific stopping/continuation conditions with
runtime-enforced aggregate admission limits. Distinguish waiting, completion,
cancellation, and limit exhaustion. A zero-pending snapshot or a sequence of
fresh message IDs cannot alone establish useful progress or successful completion.

## Preserved boundaries

- Core imports no Group implementation; Group composes peer Agents.
- Commands durably record facts without implicitly running another member.
- Explicit targets and reply IDs remain meaningful across scheduling decisions.
- Plans validate against revisions and reserve work atomically.
- Busy workers retain ownership until their actual execution settles.
- Public originals remain stored; context representation is not permission to
  discard them.
- Fixed membership, the synchronous worker profile, shared-write restrictions,
  and deferred crash recovery are documented scope choices, not new findings.

## Verification

The existing focused suite passed: **38 tests** across Group runtime,
integration, boundaries, storage, and generic Agent worker tests.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin tests/test_group_runtime.py tests/test_group_integration.py tests/test_group_boundaries.py tests/test_group_store.py tests/test_agent_worker.py -q
```

A separate deterministic probe used a temporary SQLite database and a scripted
provider. It confirmed the snapshot, input-projection, and private-only completion
observations above. No live model request or private configuration was used.
An independent read-only review checked the design-document alignment.

## Recommended next boundary

Revise the communication-profile and policy-extension contracts first. Then
walk through the archived default, on-demand, and broadcast-feedback scenarios
using explicit events, model inputs, outputs, and termination evidence. Include
interleaved requests, busy members, failed runs, and repeated feedback.

Use those walkthroughs to validate the interface before implementing concrete
policies. Preserve the current execution and storage guarantees throughout.

## Design follow-up

The user subsequently authorized correcting the design. The architecture now
defines a cohesive strategy assembly (section 2.2), richer driver observations
and decisions (section 4), contrasting archived-idea traces (section 4.7),
context provenance and response satisfaction (section 5), and cumulative
conversation bounds (section 8.2). Section 10.2 maps the findings to those changes.
The operational companion and current foundation guide distinguish these target
contracts from the unchanged runtime profile. The completed foundation plan is
annotated accordingly.

This follow-up closes the documented design corrections, not their runtime
implementation. The observations and test results above describe the original
review and have not been rewritten as evidence of newly implemented features.

An independent review of the corrected documents found no important remaining
contradiction. Its minor finding was corrected: delivery records can identify
an admitted no-inference reception operation, not only an execution assignment.
Local link, code-fence and whitespace checks passed for the five updated
documents. No runtime tests were rerun for these documentation-only changes.
