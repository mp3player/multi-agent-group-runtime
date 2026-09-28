# Group collaboration and system-prompt acceptance

Date: 2026-09-26

## Scope

Review and finite acceptance of persistent Group system guidance, explicit
on-demand scheduling, difficult peer collaboration, and real context compaction.
Tests use the current `.env` provider without modifying it or recording its
credentials. Agent decisions, peer reports, tool calls, and summaries in the
three new scenarios are real model outputs. The evidence tools expose immutable
documents only to their owning peers; they do not script collaboration.
The migration case deliberately sets a 16,000-unit runtime context window to
force repeated compaction. Business output is capped at 2,048 tokens, while
provider configuration and temperature otherwise come from `.env`. Reported
durations are observations, not performance benchmarks.

## Findings and changes

1. **Synchronous worker validation omitted system-extension callbacks.** An
   async `Session.replace_system` override could return an unawaited coroutine
   while inference continued without the intended Group system rules. Worker
   validation now checks system replacement and extension installation/removal
   before binding. Three regression cases reproduced the problem before the fix
   and verify rejection with clean rollback afterward.
2. **Provider output tokens did not ensure the summary fit its future input
   allowance.** The default input estimator counts serialized UTF-8 bytes.
   Completed summaries using fewer provider tokens could exceed the provisional
   reserve even when the full request fit. The reserve is now a planning target:
   an above-target draft must pass exact prospective candidate rendering,
   including tools, transforms, JSON escaping, hard budget, and sufficient
   progress. Subsequent source segments are still packed against the complete
   draft and exact input budget. A draft that cannot fit gets at most one
   shortening request with measured size feedback and a smaller target.
   This request shares the existing four-call ceiling, input-budget validation,
   deadline, and cancellation path. A still-invalid summary is rejected without
   publishing a partial checkpoint or discarding originals. Fourteen focused
   tests cover sync/async execution, Unicode/JSON escaping, safe above-target
   acceptance, multiple source segments, persistent failure, oversized rewrite
   input, and the shared call ceiling.
3. **Natural-language handoffs were sometimes mistaken for activation.** A
   member posted a proposal with an `@auditor` request but never called
   `group_request`; the runtime correctly became idle. The on-demand system
   instructions now explicitly distinguish mentions, recipient attribution,
   and reply links from scheduling. Scenario roles also specify actual handoff
   tool calls and phase boundaries. Runtime scheduling still does not infer
   work from prose or force a member to stop immediately after requesting peers.
4. **Summary paraphrases did not preserve exact protocol formatting.** A live
   migration result lost the significant trailing space in `"Bearer "`, although
   the complete original remained stored. Common Group guidance now tells
   members to retrieve originals for exact values and formatting. The migration
   task requires source retrieval, and its oracle verifies the entire original
   consumer contract in the synthesis request that produces the final artifact.
   The exact prefix assertion remains unchanged.
5. **Group retrieval pages ignored the receiving context budget.** In a later
   trial, a large history page was compacted again, and the member followed
   successive archives of retrieval results until its turn limit. Public
   originals remained intact; the runtime correctly returned `needs_input`.
   Group history/message tools now target one quarter of the current usable
   input budget, including output reservation, input limit, and extra reserve.
   They count a serialized tool envelope and independently enforce the character
   cap. History can shorten a first-item preview without skipping its descriptor;
   message chunks must advance unless already exhausted. Three regressions cover
   ASCII/escaped Unicode, fixed high-water pagination, exact reconstruction,
   reserve handling, and zero-progress rejection. Group guidance prefers direct
   public originals over archives of copies. The final whole-request check still
   governs aggregate tool batches; private archive retrieval has its own sizing.

The worker, compaction, and Group retrieval findings were implementation defects.
Missing document references, omitted report identifiers, an initially underspecified incident
fixture, and ambiguous phase instructions were task/evaluation contract issues;
they are not described as scheduler data loss. Early trials failed and were
retained as diagnostic evidence rather than counted as successful acceptance.
One repeat also found an auditor reading public history instead of its private
policy and approving an invalid plan. The test rejected this outcome. Evidence
owners now receive the explicit access path: their private document is available
only through `read_evidence`, which public history cannot replace. This is task
instruction clarification, not a claim that system prompts guarantee compliance.
Another repeat read all three documents but calculated `9*4 + 7*3` as 67, and
the auditor accepted the error. The oracle rejected that result as well. The
optimization scenario now supplies a bounded read-only integer-grid calculator
to planner and auditor. Members must supply its coefficients and constraints
from actual reports; it has no evidence access or preloaded answers. Successful,
relevant tool results must be visible in the model requests producing the
original/revised proposals and auditor approval, while the test independently
verifies the final optimum. Audit fields explicitly identify the proposal being
reviewed, keeping suggested replacement values separate. This
retains the distributed-evidence and rejection/revision problem without relying
on model-only arithmetic. An attempted built-in terminal binding was correctly
rejected by the current Group effect profile; that boundary was not weakened.
The combined rerun also caught migration output using Group message IDs as
contract references. Migration field descriptions now explicitly require the
original contracts' `ref` values and original-report retrieval. The assertions
still reject substituted message IDs.
After pagination was corrected, a repeat exposed model-side rediscovery loops
even though complete, correctly identified pages were visible. The staged
migration handoff now includes the two actual upstream report message IDs in
the protected synthesis assignment. It supplies locators only: members still
retrieve original contracts and derive every output field. This does not claim
reliable unaided rediscovery from compressed history. History/cursor correctness
is separately verified by deterministic reconstruction tests.

## Difficult scenarios and independent checks

| Scenario | Why collaboration is necessary | Acceptance |
| --- | --- | --- |
| Production optimization with independent audit | Profit, resource capacity, and private audit policy belong to different peers | Planner proposes `(A=4,B=4,profit=64)`; auditor rejects under `B<=3`; planner revises to independently enumerated optimum `(5,2,59)`; auditor approves that exact plan before final publication; all three document refs retained |
| Concurrent duplicate-charge investigation | Worker timeline, ledger entries, and deployment configuration are separate private sources | Reporter provider calls overlap; synthesis identifies both stale-epoch commit and same-epoch retry, epochs 7/8, excess charge 74, fencing and idempotency, exact job ID and source refs |
| API migration after repeated compaction | Producer and consumer contracts are private, with an original rollout requirement and later authorized change | At least two real compactions; system guidance remains present exactly once; original contract retrieval precedes publication; seconds `1700000000`, original milliseconds `1700000000123`, `Authorization`, exact `"Bearer "`, original canary 7 and current canary 3 |

The optimization scenario uses model-generated peer handoffs. The incident and
migration scenarios use a model broadcast followed by an application-submitted
synthesis request after collection; they do not claim an automatic join policy.
The migration handoff carries upstream report locators, not contract contents or
expected answers. Those reports still originate from real peer evidence reads.
All cases check linked replies, settled successful assignments, bounded runs,
and no extra inference after a passive post. Business assertions run before
explicit Group completion. Each temporary `case-result.json` records
`validation_passed` separately from scheduler status.

An earlier migration trial exercised 33 committed compactions, but its final
prefix was wrong, so that trial failed. The repeatable test uses 16 passive
observations rather than 64 to exercise repeated compaction at lower latency.
Neither source retention nor semantic acceptance criteria were relaxed.

## Reproduction

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q

MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s \
  tests/test_group_live_collaboration.py

MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s \
  tests/test_group_live_continuation.py tests/test_group_live_reception.py
```

Explicit plugin loading avoids the host's unrelated ROS pytest plugin.
Detailed model/source traces remain in pytest temporary directories.

## Final verification

- Full offline suite: **1,098 passed, 6 opt-in live cases skipped**, 35.56 seconds.
- Final live optimizer, incident, and existing continuation/reception run:
  **5 passed**, 92.91 seconds.
- Final targeted migration rerun, with explicit upstream source locators:
  **1 passed**, 278.72 seconds. These two runs cover all six live tests on the
  final production implementation; this is not a claim that the earlier
  combined run passed. Failed trials are described above.
- Independent review after the summary correction: **158 scoped offline tests
  passed**. After the retrieval correction: **89 scoped offline tests passed**.
  No confirmed implementation finding remains from these scoped reviews.
- Calculator evidence checks were also tested negatively: substituting failed
  tool calls or incorrect computed results caused rejection, as intended.

| Latest difficult scenario | Member executions | Provider calls, including summaries | Committed compactions | Semantic validation / terminal reason |
| --- | ---: | ---: | ---: | --- |
| Optimization and independent audit | 7 | 14 | 0 | Passed / completed |
| Parallel incident investigation | 5 | 10 | 0 | Passed / completed |
| Migration with source handoff | 4 | 29 | 18 | Passed / completed |

The three cases total 16 member executions and 53 provider calls. Every latest
`case-result.json` was checked for `validation_passed: true` and a terminal
`completed` invocation. Passive-post quiescence and fixed system-rule checks
passed in every case. No source messages were discarded to obtain these results.

This is finite acceptance of the tested profile and provider configuration.
It does not prove arbitrary model compliance, lossless natural-language summaries,
automatic crash recovery, or unattended success on every future task. Runtime
`waiting` means no eligible work remains; it is not a business correctness verdict.
