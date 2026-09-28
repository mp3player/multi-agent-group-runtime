# Group hardening scope review

Date: 2026-09-28
Scope: Design effectiveness and implementation hazards; no production changes.

## Verdict

Proceed with bounded history batching and diagnostic improvements, with the
constraints added to the [scope](2026-09-28-group-hardening-scope.md). Evidence
supports fewer store round trips and clearer state observation. Improved model
request association remains a hypothesis to evaluate. This scope deliberately
does not repair omitted continuation decisions or incorrect business reasoning.

## Findings

### 1. Receipt feedback has no correction opportunity after yield

`core/agent_runtime/turn_machine.py:101` executes the model's tool batch before
checking terminal results at line 116. A successful `group_yield` ends the run;
there is no subsequent inference to inspect earlier tool results from that batch.
The existing receipt tests exercise this exact publication-plus-yield pattern.

Consequence: a better receipt cannot guarantee correction of `reply_to` mistakes.
The initial assignment is available before the model chooses tools and can still
be made clearer. Do not add forced correction turns or make yield conditional.
Measure receipt effectiveness separately for runs that actually read feedback.

This also cannot fix the missing Alice synthesis: the previous complex cases
had every recorded response obligation fulfilled while the business task lacked
a final artifact. A missing-obligation hint may correctly report zero and still
provide no reason to schedule Alice. Keep this limitation explicit.

### 2. Settled evidence cannot directly serve an in-progress receipt

`group/state.py:110` ends its evidence query with `a.state='settled'`. A tool is
bound to a running assignment. Reusing that query as the live feedback source
would therefore omit the current assignment even when a linked reply exists.

A temporary scripted runtime probe published a correctly linked reply, observed
the state through a read-only custom tool, then yielded in the same batch:

| Observation | Result |
| --- | ---: |
| Assignment state during observation | running |
| Current linked public messages | 1 |
| Settled evidence rows during execution | 0 |
| Provider requests for the whole execution | 1 |
| Settled evidence rows after execution | 1 |
| Unresolved replies after execution | 0 |

Use current-assignment publication facts for provisional feedback. Preserve final
settlement validation and the original execution identity requirements. Apply
missing-reply wording only when the selected profile requires public replies.

### 3. Post-commit enrichment must not create apparent publication failures

`group/tools.py:71` commits `_command` before returning the tool receipt. Adding
a diagnostic query after that commit introduces a new possible failure point.
If this exception becomes a generic tool error, the model may repeat a message
that was already stored; each new model tool call currently receives a new
operation key. Enlarged output can also be truncated by `tools/runtime.py:97`,
making the receipt unreadable despite successful publication.

Keep the accepted base receipt available even if optional diagnostics fail or do
not fit. Report unavailable diagnostics distinctly. Do not replay a committed
write or relax genuine store-failure handling. Bound diagnostic work and preserve
the existing maximum-audience receipt behavior. These are prospective hazards
in the proposed extension, not claims that it is already implemented and broken.

### 4. Batching targets measured overhead but is not an asymptotic solution

`group/member_context.py:69` pages historical sources, then performs a separate
store read for each source's application bindings. Validation traverses them
again. The earlier finite-run sampling found most non-idle samples in this
store/control bridge.

A read-only comparison on the retained real CLI database used all 76 sources,
Alice's application bindings and pages of 20. The per-source lookup path required
80 store-operation equivalents; grouping application reads by source page
required 8. The complete returned binding maps were identical (12 application
records in the retained current source index). Historical receipts outside that
index were not reclassified as missing context by this experiment.

This establishes equivalent lookup output for a frozen database and removes 72
store crossings in that traversal. It does not measure an end-to-end speedup,
execute a revised runtime, or prove correctness during compaction/concurrent
context application. It also leaves coverage-check and history-traversal costs.

The source high-water mark freezes message selection, not application receipts
or session coverage. Reusing stale receipt snapshots can produce wrong delivery
decisions. Session revision alone also cannot detect externally changed archive
files; existing archive metadata checks re-stat file fingerprints. Preserve those
checks and introduce cancellation checkpoints within covered-source scans.

### 5. CLI diagnostics and tests improve detection, not autonomous decisions

`cli/group.py:184` prints aggregate counters. An already settled execution can
have a missing linked response while `pending=0`. Showing request/member/evidence
details directly addresses the confusing CLI output without changing scheduling.
It does not make the model repair its association, because CLI output is not a
new model assignment. Keep diagnostic reads bounded and side-effect-free.

Regression tests protect free speech and explicit activation semantics. They
must be accompanied by a same-model repeated baseline comparison before claiming
that enhanced feedback reduces real-model mistakes. Do not substitute reduced
protocol errors for complete task acceptance.

## Validation performed

- Read-only source/application lookup comparison: identical binding maps,
  80 versus 8 operation equivalents. Evidence: `/tmp/group-hardening-query-review.json`.
- Temporary scripted runtime probe: live/settled evidence and same-batch yield
  observations above. Evidence: `/tmp/group-hardening-feedback-review.json`.
- Existing targeted regression: 18 passed in 0.62 seconds, exit 0:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q \
  tests/test_group_receipts.py \
  tests/test_group_profiles.py::test_wrapped_group_yield_stops_real_agent_after_one_model_invocation \
  tests/test_archive_cache_scaling.py
```

No live-provider A/B test was performed because the proposed feedback has not
been implemented. The temporary probe used a scripted provider and real Group
tools, execution and storage; it did not call the configured external model.
Only scope/review documents were changed in this review.
