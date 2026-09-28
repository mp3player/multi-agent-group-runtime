# Group hardening scope

Date: 2026-09-28
Status: Reviewed proposal; no production changes implemented by this document.

## Objective and boundary

Improve faithful execution of explicit collaboration decisions while keeping
coordination decisions with the model and the selected strategy. The current
on-demand strategy continues to activate members only through accepted response
requests. A post, reply link, mention or receipt alone does not create work.

This scope follows the [endurance findings](2026-09-28-group-endurance-acceptance.md).
The failed default collaboration cases remain recorded failures of end-to-end
task acceptance. They do not establish that the execution engine dropped work,
and this scope does not promise to make them pass through automatic continuation.

## Expected effect after review

| Observed issue | Expected effect of this scope | Evidence limit |
| --- | --- | --- |
| Growing history-preparation latency | Batching directly reduces per-source store crossings | A read-only comparison reduced 80 lookups to 8 with identical records; production latency and concurrent preparation remain to be measured |
| Incorrect request association | Clearer initial input and provisional receipts may help the model correct its choice | No live-model before/after comparison yet; receipts cannot help an execution that has already yielded |
| Missing return to the initiator | No guaranteed correction; the model must still issue the return request | A review may satisfy every recorded obligation while the business synthesis remains unfinished |
| Unclear CLI failure/waiting state | More precise diagnosis for the user | Observation does not repair an association or continue the task |
| Incorrect business reasoning | No correction promised | Independent task oracles remain necessary |

See the [scope review](2026-09-28-group-hardening-scope-review.md) for evidence and
implementation constraints. Regression tests protect behavior; they do not by
themselves demonstrate improved model collaboration.

## Existing capabilities to reuse

- `CollaborationProfile` already provides fixed system instructions, configurable
  tools and an input envelope separating triggers from background sources.
- `ExecutionContext` already binds tools to the current member and assignment.
- The runtime retains original messages, request opportunities, assignment
  outcomes and execution-bound reply evidence.
- Reception does not require inference; historical sources remain recoverable
  after compaction. Preparation validates source coverage.
- The driver already distinguishes a quiet open discussion from explicit
  completion and exposes unresolved replies and blocked preparation.
- Run limits, deadlines, cancellation, explicit recovery and cleanup already
  exist. This work should extend those paths, not create another scheduler or
  independent task-state store.

## First implementation batch

### 1. History preparation performance

Primary locations: `group/member_context.py` and `core/session.py`.

Batch source-to-application reads rather than crossing the store/control boundary
for every historical source. Reuse immutable source descriptors within the frozen
assignment boundary. Reduce repeated coverage work within a stable validation
pass. Start with batching before introducing broader caching or a new index.

Any reuse must account for session changes, compaction, newly applied context and
archive integrity. Cached coverage must not hide changed or missing archive
files. Preserve bounded pages, complete originals, cancellation checkpoints and
the post-compaction validation barrier. Do not retain an unbounded full-history
copy merely to avoid queries. A frozen message high-water mark does not freeze
application receipts or session coverage. Re-read changed application state at
the appropriate preparation boundary rather than reusing a stale no-receipt
snapshot. Existing metadata caches already validate file fingerprints; increasing
cache size alone does not remove repeated ancestry walks.

Batching reduces store crossings but still traverses history. Do not claim
constant preparation cost or elimination of all scaling problems. Keep explicit
cancellation checks within bounded pages, including pages whose sources are all
already covered and therefore yield no new context units.

Acceptance: unchanged delivery and activation traces; retained corruption and
missing-source detection; lower store round trips and preparation growth at the
same history sizes. Measure preparation separately from provider latency. Prefer
operation counts over fragile wall-clock assertions in ordinary regression tests.

### 2. Current-request clarity and factual tool feedback

Primary locations: `group/profiles.py`, `group/tools.py` and existing runtime
evidence queries.

Reuse the protected trigger envelope and current execution binding to make the
assigned requests easy to identify. Keep fixed protocol instructions in the
system extension and changing request state in structured execution/tool data.
Avoid copying complete history into every receipt or adding more tools before
the existing input and receipts have been evaluated.

A publication receipt can report bounded facts about its effects: the committed
message, newly created response opportunities, whether its reply link matches
an assigned trigger, and which assigned triggers lack linked publication so far.
If lists do not fit, mark them partial and expose counts or an explicit read path.
Never silently truncate a supposedly complete diagnostic list.

These facts are provisional at publication time: a linked post is not proof of
successful execution settlement or a semantically correct answer. Reuse the
existing evidence contract; do not add a second authoritative completion flag.
Keep acceptance, execution and response evidence distinct.

A legitimate post replying to another message remains accepted. Report any
unfulfilled assigned obligation as neutral information, not an error requiring
every post to answer the current trigger. Invalid identities, recipients or
lifecycle operations continue to receive precise errors before admission.
Feedback must not rewrite reply targets, retry a committed operation, create
response requests or make `group_yield` refuse to end the execution.

The current `state.reply_evidence` query deliberately returns settled assignments
only. Live receipts must read execution-bound publication facts for the current
assignment without treating an empty settled-evidence result as missing work.
Share the identity-matching rules where practical, but keep provisional
observation separate from final outcome validation. For profiles without a public
reply requirement, describe publications without inventing a missing-reply
obligation. A manual assignment can legitimately have no trigger messages.

Keep optional diagnostics from converting an already committed publication into
an apparent tool failure: a diagnostic read failure or oversized diagnostic
payload must preserve the successful base receipt with diagnostics omitted or
marked unavailable. Genuine runtime/store failures still follow the existing
failure policy; they must not be disguised as a rejected publication. Do not
retry the committed write to obtain better feedback. Bound additional reads to
the current assignment, not all historical discussions.

If a model emits publication and `group_yield` in one response, the runtime ends
without another model turn to consume that receipt. Reaching a turn limit or
cancellation has the same limitation. Improve pre-call request clarity and
evaluate receipt feedback only where the model actually receives another turn;
do not force a corrective turn or prevent yield to manufacture a success.

Acceptance: free follow-up posts remain valid; malformed commands have no
partial effects; multi-trigger assignments are represented correctly; receipts
do not become new assignments; partial or subsequently failed runs cannot be
reported as successfully settled responses.

### 3. CLI and diagnostic visibility

Primary locations: `cli/group.py`, existing scheduling views and observation APIs.

Extend `/status` beyond aggregate counters with bounded, attributed details:
request identity, responsible member, execution state, missing linked evidence
and blocked/error information. An unsettled response can exist when pending
queue count is zero; show these separately.

When a member published a message linked elsewhere, describe that observable
fact rather than claiming the answer was wrong. Keep `waiting` as execution
quiescence and explicit `/finish` as application/user acceptance. Preserve the
existing distinction instead of adding an automatic task-completion classifier.

Acceptance: status observation is read-only, bounded and causes no inference,
activation or resolution; displayed details agree with runtime evidence. Large
histories and unusual member/message text remain manageable.

### 4. Regression and capability evidence

Extend existing tests alongside the above changes. Deterministic contract tests
cover legal free speech, exact request activation, broadcast, multiple triggers,
incorrect and unrelated reply links, compaction, failures and cancellation.
Include another profile or manual scheduling path so on-demand requirements do
not silently become universal runtime behavior.

Add cases for publication followed by same-batch yield, live versus settled
evidence, successful publication followed by unavailable diagnostics, large
receipts, stale application snapshots after compaction and cancellation during
fully covered history scans. These are acceptance requirements for the proposed
changes, not claims that the unimplemented feedback path has already passed.

Keep real-model capability tests separate. Measure task completion, protocol
errors, intervention count and model calls. Preserve the complex default-CLI
cases as a baseline; do not redefine a missing final artifact as success. A model
comparison is useful later, but is not required to fix the measured performance
issue or to validate deterministic execution contracts.

For feedback effectiveness, compare unchanged versus enhanced feedback on the
same model/configuration and repeated tasks. Track whether feedback reached a
subsequent inference, association mistakes, correction rate and extra calls.
Keep failed final-artifact acceptance visible even if protocol compliance improves.

## Deferred protocol options

The distinction between conversational `reply_to` and explicit fulfillment of an
assigned request deserves a separate design if clearer input and feedback remain
insufficient. It affects tools, persistent evidence, recovery and compatibility.
It must preserve execution-bound proof and support multiple assigned requests;
simply accepting any related reply would weaken the contract.

A read-only current-assignment/status tool may be useful if models cannot obtain
the necessary facts from the existing envelope and receipts. Its exposure should
remain selectable through the communication profile. Neither this tool nor a new
fulfillment operation is included in the first batch.

Automatic return-to-initiator, mandatory synthesis, acknowledgements that wake
peers, automatic repair runs and model-based completion judges are policy choices.
They may belong to future explicitly selected strategies, but are not reliability
patches to the default runtime. The current model's failure to request a return
does not authorize the runtime to invent one.

## Order and review criterion

Implement and measure history batching first. Then add bounded tool feedback and
CLI diagnostics with their contract tests. Rerun the relevant finite endurance
and real-model baselines; report execution improvements separately from changes
in collaboration success.

For the same explicit commands, a hardening change should preserve committed
messages, response opportunities and scheduling effects. Better feedback may
help a model choose different later commands; the runtime itself must not
manufacture those decisions. Changes to the response-evidence contract require
their own explicit design rather than being hidden in a performance patch.
