# Group hardening implementation and verification

Date: 2026-09-28
Scope: [Reviewed hardening proposal](2026-09-28-group-hardening-scope.md).

## Delivered behavior

- Historical source/application lookup is batched per bounded page in one store
  snapshot. Each preparation and validation pass reads fresh bindings. Covered
  sources now check cancellation even when they yield no context. No archive
  coverage cache or original-message deletion was introduced.
- Publication tool receipts optionally expose current-assignment publication
  facts: exact reply-target matches, linked/unlinked trigger counts, bounded IDs
  with a completeness marker, profile requirements and an observation revision.
  These facts are provisional and do not satisfy settled response evidence by
  themselves. Tools still accept legal unrelated replies and terminate on yield.
- Optional feedback cannot turn a committed publication into a reported rejection.
  Unavailable/oversized feedback falls back to the accepted base receipt; SQLite
  failures still poison the store and stop subsequent admission. A current-
  assignment lookup index avoids scanning every historical opportunity for feedback.
- Fixed system guidance identifies `trigger_messages[].message_id` as the current
  reply target and distinguishes the trigger's own earlier `reply_to` link.
- CLI `/status` provides a bounded, escaped, attributed observation of requests,
  assignments, public evidence, missing replies, errors and explicit resolutions.
  It reads one store snapshot and does not start inference or mutate work.
- Full regression exposed a stdin/SIGINT race: cancellation could occur during
  `os.read`, after the future's initial done check. The callback now handles an
  already-cancelled future without an InvalidStateError log. Normal I/O errors
  still propagate; deterministic real-pipe tests cover both result/error races.

Explicit wakeup rules, free public follow-ups, execution-bound completion evidence
and model-owned return decisions are unchanged. No new model-facing tool or
automatic repair/synthesis run was added. `.env` was not changed.

## Validation

| Check | Result |
| --- | --- |
| Full default offline regression after all production changes | 1,206 passed, 14 skipped; exit 0; 40.09 seconds |
| Finite same-Agent endurance | 40 discussions, 176 assignments, 172 business provider executions; passed in 209.18 seconds |
| Endurance failure/cancellation paths | Four provider failures and four cancellations; subsequent discussions recovered and owned resources released |
| Endurance compaction | 23 committed compactions across three retained sessions |
| Independent scoped review | No must-fix findings; 36 direct tests passed |
| Independent batching/evidence checks | 158 source/member rows across 16 pages matched; 144 evidence-value combinations matched authoritative satisfaction rules |
| Compatibility | Changed Python sources parsed using the Python 3.10 grammar |

The fourteen default skips are opt-in live/endurance tests; the finite endurance
test was separately enabled. Its JUnit export emitted a `record_property`/xunit2
compatibility warning; the metrics were retained and parsed. Use
`-o junit_family=legacy` when exporting that test in future runs.

Red/green evidence included absent feedback fields, post-commit observation
failure, oversized optional feedback, excessive per-source crossings, cancellation
through fully covered history, absent status details and the stdin callback race.
Existing regressions additionally protect alternate profiles, manual/no-trigger
assignments, multi-trigger replies, corrupted/missing archives and explicit repair.

## Performance evidence and limits

Paired real-worker measurements with 159 passive sources plus one current task,
page size twenty and no compaction reduced preparation iterator store crossings
from **334 to 16**. Median iterator time across three repeats fell from **48.8 ms
to 9.8 ms**. Full execution medians were about 510 ms and 483 ms; other history
sizes did not consistently improve total execution time. These measurements
support batching, not a universal end-to-end speedup claim.

The forty-discussion run took 209.18 seconds versus the earlier 243.78-second
baseline. This is an observational comparison, not a controlled performance SLA:
the new feedback/system guidance increased compactions from twenty to twenty-three,
and some regression work ran concurrently. The first and last normal discussions
took 0.183 and 11.479 seconds, with a 12.307-second maximum normal discussion.
Retained-history traversal and repeated archive coverage remain material costs.

Feedback effectiveness on actual model choices has not been A/B tested in this
implementation turn. Same-batch publication/yield still ends without another
inference to inspect feedback. Prior complex CLI tasks that omitted final return
requests remain failures of task acceptance; the new implementation does not
reinterpret them as success. CLI diagnosis also does not repair those decisions.

## Reproduction and evidence

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q

MAS_RUN_GROUP_ENDURANCE_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q \
  tests/test_group_endurance_boundaries.py --durations=1
```

- [Structured results and paired measurements](2026-09-28-group-hardening-results.json)
- Full regression: `/tmp/mas-hardening-regression-final.log`
- Endurance: `/tmp/mas-hardening-endurance.log` and `/tmp/mas-hardening-endurance.xml`
- Independent review: `/tmp/mas-hardening-review.md`
- Before-change source snapshot: `/tmp/mas-hardening-before-20260928`

No commit, reset, stash, checkout or deployment was performed. Existing unrelated
workspace edits were retained.
