# On-demand Group endurance and collaboration acceptance

Date: 2026-09-28

## Conclusion

The execution mechanisms passed the finite lifecycle, delivery, cancellation,
concurrency and cleanup checks. Complex collaboration succeeds when the workflow
explicitly defines return requests or a later synthesis assignment. The default
CLI does **not** yet reliably complete a natural multi-step task without human
intervention: all twelve tested task phases stopped after peer review without
the requested Alice final artifact. Short conversational handoffs also reproduced
the previously observed response-link mismatch.

Long-lived sessions remained operational, but preparation cost increased markedly
with retained history. These results support the current mechanisms, not an
unqualified claim of autonomous collaboration or bounded long-term latency.

No production implementation, scheduling policy, system prompt or `.env` was
changed. This turn added opt-in acceptance tests, numerical oracles and evidence.
The accepted first-idle-member policy remains intact.

## Coverage and results

| Track | Workload | Result |
| --- | --- | --- |
| Default real CLI | Three task families, two repetitions, two revisions per case, one persistent process | 12/12 phases delivered substantive peer work; 0/12 produced the required Alice final artifact |
| Delayed direct queries | Two queries about earlier revision-2 results after all six cases | 2/2 correct without requesting peer assistance |
| Natural name handoffs | Eight user messages over two discussions in one process | All twelve member runs settled; four peer replies linked the wrong request |
| Explicit workflow controls | Three real-model tasks using independent private evidence | 3/3 passed, including revision, review, synthesis and original retrieval after compression |
| Deterministic endurance | Forty discussions using the same three Agents and sessions | Passed: 176 assignments, 172 business provider executions, 20 compactions |
| Smaller-context profiling | Eighteen normal discussions at a 32K context budget | Passed: 90 executions, 46 compactions; preparation slowdown reproduced |
| Full default regression | Offline suite; opt-in live/endurance tests remain disabled | 1,175 passed, 14 skipped, exit 0, 46.15 seconds |

Across the live CLI, natural-dialogue replay and explicit controls, **66 member
executions and 125 real provider requests** were observed. Requests include
context summaries and multiple model turns within a member execution. The live
model endpoint and credentials came from the existing `.env`.

## Default CLI: meaningful peer work without final continuation

The frozen tasks are in `tests/group_endurance_cases.py`:

1. Select the cheapest rollout satisfying downtime, rollback and capacity
   constraints; change capacity requirements and obtain fresh independent review.
2. Normalize eight incident timestamps from four server clocks, including a
   negative offset and a tie; correct one clock offset and rebuild the order.
3. Minimize the makespan of six nonpreemptive jobs with dependencies and two
   machines; change one duration and repeat independent feasibility/optimality
   checking.

Each case assigned Bob calculation/planning and Carol independent review, with
Alice responsible for the final JSON after both checks. Task prompts required the
peers to bring findings back without further human scheduling. They did not add
system instructions, fabricate tool calls, or repair response evidence. The
second repetition changed rollout prices and incident timestamps. The numerical
oracle independently enumerates all machine orders and accepts valid optimal
schedules containing slack.

One CLI process ran for **520.55 seconds**, retaining its member sessions across
seven discussions. It executed 38 member assignments and 57 HTTP requests, stored
76 public messages, and exited 0. No pending/active work remained after shutdown;
passive notes generated no response opportunities and the store lock was released.
Five context-budget archives were produced. Peak observed RSS was 50,524 KiB
(about 49.3 MiB), with five threads and thirteen descriptors. These samples do not
establish an asymptotic memory bound.

Every task phase followed the same pattern:

```text
user -> Alice -> Bob -> Carol
                     calculation + request
                              review (passive)
```

There were **zero peer requests back to Alice**, no later Alice assignment in
the phase, and no Alice final artifact. All linked response obligations were
satisfied, so `waiting` was consistent with the current protocol. The script's
subsequent `/finish` closed a quiet discussion; its success was never used as
evidence that the business task was complete. Acceptance correctly failed.

Independent inspection confirmed that all twelve Bob calculations were correct
and all twelve Carol reviews were substantive. Original cases, current revisions
and Bob's reports were within Carol's captured input boundaries and had matching
historical context-application receipts. All members ultimately received through
public message 76. This supports successful data delivery followed by missing
continuation, rather than a lost-message explanation.

There were also model reasoning defects: both scheduling revision-2 reviews
called a seven-unit chain the critical path despite an eight-unit dependency
chain. Their optimality explanations did not rule out alternative machine orders.
The actual schedules and objective values were correct by exhaustive enumeration.
Thus a correct result did not imply a fully correct independent proof.

The two delayed queries returned correct revised rollout and timeline results
without peer assistance, including after context compression. Earlier discussions
had no Alice final artifact, so this demonstrates reconstruction from retained
peer findings, not retrieval of a previously published Alice final answer.

The original raw result is retained. Offline audit revision 2 separates protocol,
numerical correctness, peer participation, data availability and independent
recall. It also detects malformed or duplicate-field final attempts instead of
silently selecting a later valid JSON object. No task prompts were changed after
the live run began. Peer publication order alone is not treated as proof of
independent reasoning; substantive reviews were inspected separately.

## Short-dialogue response linkage

A second real CLI process repeated ordinary greetings and the inputs `bob?` and
`carol ?` across two discussions. The expected first-idle-member policy selected
Alice, who requested the named peer. Both peers answered in both repetitions.

All four peer replies used the **original user message** as `reply_to`, while the
peer's response obligation referred to **Alice's new request**. The runtime
therefore retained four unsatisfied obligations, correctly displaying
`needs_input`. All executions still settled and explicit cancellation left no
queued/running work. This is a model/tool-protocol usability failure; weakening
the evidence check to accept any vaguely related reply would hide it.

## Explicit workflow controls

The three existing `test_group_live_collaboration.py` scenarios all passed in
250.96 seconds, totaling sixteen member executions and fifty-three real provider
requests:

- An optimizer obtains private capacity evidence, submits a proposal to an
  independent policy owner, revises a rejected plan and obtains approval:
  seven executions and fifteen requests.
- An incident investigation combines private evidence owned by separate peers:
  five executions and twelve requests.
- A migration synthesis retrieves exact producer/consumer originals after real
  model compression, retains an authorized revision, significant whitespace and
  archived timestamp values: four executions and twenty-six requests, including
  fifteen compactions.

These controls use explicit role-level return instructions, and the migration
control supplies a separate application synthesis request. They also provide
read-only evidence/calculation fixture tools. They establish that the mechanisms
can support the work; they are not evidence that the unchanged CLI independently
invents and completes those workflows.

## Persistent-session boundaries and scaling

The deterministic forty-discussion test took 243.78 seconds. It covered 32 normal
handoff/broadcast/handback chains, four provider failures, and four cancellations
while two workers were occupied, another assignment queued and a request pending.
The same Agents, sessions and registries were retained throughout.

Checks verified exact activations, no inference from passive text/mentions,
idempotent request receipts, no implicit replay of failed work, recovery in the
next discussion, maximum concurrency two and per-member concurrency one. Queues,
owned driver/control tasks, worker threads, store jobs and the database lock were
released; bound Agent instances became reusable after close. Fixed 105-byte fake
summaries exercised twenty real compaction commits and archival coverage, not
summary semantics.

Normal-discussion time grew from **0.251 seconds to 15.803 seconds**, with a
17.493-second maximum. All successful runs stayed inside the configured
30-second invocation deadline. Initial ten-second outer test guards expired;
those were test guard failures and were not reported as runtime deadline failures.

A separate bounded 32K-context run completed eighteen normal discussions in
63.85 seconds, with ninety executions and forty-six compactions. Discussion time
grew from 0.182 to 9.672 seconds. Python-level worker sampling found 131 of 153
non-idle samples in `MemberContext._read`, waiting on source-by-source store/control
round trips. `MemberContext.__iter__` traverses complete history, and validation
traverses it again. Archived per-source coverage checks also repeatedly walk
archive ancestry.

| Archives | Oldest-batch check, cold metadata cache | Warm metadata cache | Metadata lookups |
| ---: | ---: | ---: | ---: |
| 4 | 6.07 ms | 0.197 ms | 12 |
| 10 | 14.97 ms | 0.361 ms | 30 |
| 16 | 30.67 ms | 0.577 ms | 48 |

The OS page cache was not flushed. Archive validation contributes to increasing
work, but source/store round trips dominated this sample; blaming all growth on
archive I/O would overstate the evidence. An initial profiler process crashed
during a `faulthandler` periodic dump under Python 3.14.0rc2. A replacement sampler
completed successfully; that instrumentation failure is not classified as a
reproduced Group correctness defect.

## Follow-up priorities

1. Make return/continuation intent explicit in the collaboration protocol so a
   requested review can lead back to synthesis without relying on the model to
   remember an additional wakeup. Preserve passive-publication semantics and
   finite budgets rather than waking every member on every reply.
2. Separate the identity of the assigned response obligation from the conversational
   message being quoted/replied to. Preserve execution-bound evidence and surface
   incorrect associations clearly; do not silently mark unrelated work complete.
3. Batch historical source/application reads and reduce repeated coverage scans
   within a stable preparation boundary. Preserve fresh archive-integrity checks,
   complete source coverage, cancellation and compaction invalidation.

These are findings and design directions, not changes implemented in this turn.
Further model prompt tuning is not counted as a code fix. No claim is made about
hour/day uptime, unrestricted autonomous task completion or unbounded history.

## Reproduction and retained evidence

```sh
# Current default CLI acceptance is expected to expose the missing final continuation.
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s tests/test_group_live_endurance_cli.py

# Actual private-evidence and compression controls.
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s tests/test_group_live_collaboration.py

# Finite deterministic long-session regression, disabled in ordinary test runs.
MAS_RUN_GROUP_ENDURANCE_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/test_group_endurance_boundaries.py
```

The complex CLI run used `--max-turns 12 --max-runs 24 --timeout 240` to bound the
experiment. Model endpoint, model identity, temperature, output-token configuration
and normal context capacity came from `.env`. The explicit compression control
used a 16K context budget. These are finite acceptance conditions, not load SLAs.

- [Structured results](2026-09-28-group-endurance-results.json)
- CLI script, contracts, database, raw log and audited results:
  `/tmp/mas-group-endurance-cli-20260928-v1/test_continuous_complex_collab0/`
- Natural dialogue: `/tmp/mas-group-natural-20260928-v1.sqlite` and adjacent `.log`
- Private-evidence controls and provider traces:
  `/tmp/mas-group-endurance-controls-20260928-v1/`
- Deterministic metrics: `/tmp/group-endurance-boundaries-evidence.json`
- Bounded profiling: `/tmp/group-endurance-32k-profile.json`
- Full offline regression: `/tmp/mas-group-endurance-regression.log`
