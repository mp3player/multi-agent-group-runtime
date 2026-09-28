# Frozen Group held-out acceptance

Date: 2026-09-28

## Conclusion

The current on-demand collaboration foundation executed all six new trials and
returned to an idle state without lost source evidence, failed member runs, or
unbounded activation. Four trials met their business acceptance criteria; both
delivery-allocation trials failed. This supports a usable execution foundation,
not reliable completion of arbitrary tasks by the configured model.

The strongest demonstrated gap is unchecked business reasoning. A secondary
efficiency gap is redundant requests for already-pending work. Neither finding
justifies changing passive-publication semantics or hardcoding task-specific
business rules into the scheduler.

## Frozen protocol

The plan, three new task definitions, one generic collaboration protocol, and
semantic oracles were fixed before any provider call. Each task ran twice with
new document identifiers. Production Group prompts and scheduling code were
unchanged. New task roles were fixed for this evaluation; this does not mean the
task-specific prompts from the previous acceptance were reused.

Each invocation received one external request. The coordinator requested three
independent evidence owners, which read private documents, published complete
original JSON, and explicitly requested the coordinator's next turn. The test
application never supplied an intermediate synthesis request, fabricated peer
reply, or corrective instruction. Expected answers existed only in evaluator
data, never in member tools or prompts. A coordinator was a task role played by
an equal Group member, not a separate supervisor/subagent runtime.

The existing live fixture retained its limits: context window 131,072, output
cap 2,048, temperature 0.7, ten turns per member run, sixteen runs per invocation,
and a 420-second invocation deadline. Provider and other settings came from
`.env`, which was not modified. Ninety-nine source/configuration/test files were
fingerprinted before the live run and verified unchanged afterward.

No failed case was retuned or selectively rerun. The two observations per task
use the same problem structure with different source identifiers; these are
three task templates, not six independently designed problem families or a
statistical estimate of production reliability.

## Results

| Task | Repetition | Member runs | Provider calls | Business result |
| --- | ---: | ---: | ---: | --- |
| Access recertification | 1 | 7 | 12 | Pass |
| Access recertification | 2 | 7 | 11 | Pass |
| Delivery allocation | 1 | 7 | 11 | Fail |
| Delivery allocation | 2 | 7 | 10 | Fail |
| Release dependency analysis | 1 | 11 | 18 | Pass, redundant requests |
| Release dependency analysis | 2 | 7 | 11 | Pass |
| Total | 6 trials | 46 | 73 | 4 pass, 2 fail |

All six satisfied the execution checks before semantic validation: no missing
linked replies, no failed member assignments, persistent system guidance in
every business request, and no extra inference after a subsequent passive post.
No provider exceptions were recorded. Complete authoritative owner reports were
also verified in the actual model input that generated each final artifact,
including both rejected delivery outputs. Every original document was read by
its owning member. None of these trials triggered context compaction.

Successful cases were explicitly finished only after semantic validation. The
two failed cases were closed with terminal reason `cancelled` by test cleanup;
they were never recorded as successfully completed business tasks. Their member
runs had already settled normally.

## Failure analysis

### Delivery allocation: reasoning and output-contract failures

The warehouse has eight units. Priority order and carrier restrictions require
shipping O1 (four units, fee eight), skipping O2 (no qualifying carrier), shipping
O3 (three units, fee four), skipping O4 (two units needed but only one remains),
skipping O5 (no qualifying carrier), and shipping O6 (one unit, fee five).
The correct remaining stock is zero and total shipping fee is seventeen.

Trial 1 correctly identified O4 as out of stock but still attached a carrier,
contrary to the explicit output contract. It incorrectly rejected O6 despite
one remaining unit and one required unit, returning stock one and fee twelve.

Trial 2 shipped O4 even though stock was insufficient, then rejected O6. Its
claimed shipped quantities sum to nine against initial stock eight, while the
artifact still claims one unit remains. Its shipping fee was twenty-one.

Both failures occurred with all three complete source documents in the final
provider input. No compaction, source truncation, missing private read, failed
tool execution, or application handoff omission explains them. The evidence
supports a model reasoning/instruction-adherence failure. It does not establish
which internal model process caused that failure, nor that a particular prompt
or stronger model would reliably solve it.

The evaluator rejected the outputs exactly as specified before the run.
Acceptance was not weakened, and production code was not changed to encode
these particular orders or answers.

### Release analysis: redundant pending work

The first release trial produced the correct approved revision, transitive
closures, and conflict sets, including ignoring a higher but unapproved
revision. However, after the first report notification, the coordinator sent
another request to two evidence owners already asked to report. They repeated
their reports and handoffs. This produced eleven member runs instead of the
seven seen in the second trial.

The extra work came from a distinct accepted `group_request`, not duplicate
execution of the same scheduling opportunity. The policy is intentionally
on-demand and honors explicit requests. The generic role instructions discouraged
re-requesting pending work, but that descriptive instruction was not sufficient
in this trial. A future structured pending-work/collection barrier could improve
this behavior; blindly deduplicating message text would also suppress legitimate
follow-up reviews and is not an appropriate general repair.

## Evaluation integrity and changes

Only evaluation files and this report were added. Production code, `.env`, and
existing fixtures were preserved. Twenty-eight offline checks exercise the
oracle and source-visibility checks, including incorrect business results,
missing/duplicate references, duplicate result rows, source identity, complete
source content, and typed original document values.

An independent reviewer recalculated all three expected results and checked for
task ambiguity and answer leakage before live execution. That review caught an
evaluation gap: a reference-only report could be mistaken for complete evidence,
and selecting only the first candidate could reject a later complete report.
The checks were corrected and verified before the first provider call. The final
review found no remaining material pre-run evaluation issue.

A separate post-run trace review confirmed both incorrect delivery artifacts
matched raw provider output and contained the full task and source evidence in
their generating requests. It also confirmed the release rerun work came from
new model-issued requests with distinct opportunities, rather than repeated
runtime execution. No framework defect was identified by that review.

Files:

- `tests/group_heldout_cases.py`: frozen task data and evaluator-only oracles.
- `tests/test_group_heldout_oracles.py`: offline evaluation-integrity checks.
- `tests/test_group_live_heldout.py`: the six opt-in real-provider trials.
- [Machine-readable results](2026-09-28-group-heldout-results.json): all outcomes,
  actual and expected artifacts, counters, and source-presence diagnostics.

## Verification

- Full offline suite: **1,126 passed, 12 skipped**, exit 0, 34.64 seconds.
- Frozen live suite: **4 passed, 2 failed**, exit 1, 140.49 seconds.
- The 12 offline skips are opt-in provider cases, including these six.
- Source/configuration/fixture fingerprints: **99 unchanged** after all trials.
- No live retries, prompt repairs, or runtime modifications were used to obtain
  these six acceptance outcomes. Subsequent attribution controls are separate.

Run the offline suite with:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
```

Run the complete live evaluation with:

```bash
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s --tb=short \
  tests/test_group_live_heldout.py
```

The exact initial logs, SQLite histories, provider input/output traces, and
fingerprint manifest are retained locally under
`/tmp/mas-heldout-20260928-_4l715k7/`. This temporary directory is not a durable
repository artifact; the results JSON preserves the essential evidence without
credentials. Later runs are repetitions of a known benchmark, not fresh held-out
acceptance, and model outputs may differ.

## Recommended next boundary

Keep this implementation as the current execution baseline. Prioritize a
pluggable task-result contract with deterministic computation and validation
where the domain supports it. Validation should belong to the application/task
extension and explicitly govern whether to accept an artifact, request bounded
correction, or return an unresolved result. Waiting and member completion should
retain their existing execution meaning.

Separately consider an optional collection/join mechanism or structured pending
work state for strategies that need it. Neither extension was implemented in
this evaluation, and it does not demonstrate workspace
write collaboration, automatic crash recovery, long-duration process health, or
new compaction behavior. The preceding acceptance covers different aspects of
the baseline and should not be conflated with these results.

## Subsequent attribution

At the user's request, a later [failure-attribution investigation](2026-09-28-group-failure-attribution.md)
replayed both failed requests through raw HTTP and tested the same evidence in
two short, standalone requests without Group history or tools. All four returned
incorrect artifacts. The exact-request outputs matched their original failures.
This provides additional evidence for model-side reasoning failures; it does
not change the frozen six-trial acceptance result above.
