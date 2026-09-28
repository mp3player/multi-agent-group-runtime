# Live multi-stage peer collaboration acceptance

Date: 2026-09-26

## Task and setup

Four equal peers reviewed a synthetic queue-sizing proposal, audited one
another's work, and revised the decision after a budget change. All member
decisions used the current project `.env` provider. No model responses were
scripted, and the expected numerical answers were not supplied to the members.
No production code changed.

The task supplied a handoff protocol, rather than asking members to invent an
organization. The existing `OnDemandStrategy` selected explicit requests:

- Initial review: Alice (integration) -> Bob (capacity) -> Carol (reliability)
  -> Dave (independent audit) -> Alice (decision).
- Budget revision: Dave -> Bob -> Carol -> Alice.

Only the initial task and the later budget change came from the application.
The seven intermediate handoffs came from real member `group_request` calls.
This exercises dependent collaboration and repeated member activation, not
parallel fan-out. Members retain their sessions across the two phases.

Background projection was limited to two messages, requiring retrieval for
older source material. Tools, worker execution, SQLite persistence and reply
evidence were the real project implementations. Limits were 16 admitted runs,
12 model turns per run, 150 seconds per run and 480 seconds per invocation.

## Independent acceptance oracle

Normal arrivals are 40 jobs/s. A 60-second peak has 70 jobs/s while one worker is
unavailable. Every worker processes 12 jobs/s. All workers return after the peak;
normal arrivals continue. All jobs must be accepted, and the accumulated backlog
must drain within 120 seconds. Workers cost 55 credits each, plus 45 fixed credits.

| Candidate | Normal capacity | Outage capacity | Peak backlog | Drain time | Cost |
| --- | --- | --- | --- | --- | --- |
| Four workers | 48 jobs/s | 36 jobs/s | 2,040 jobs | 255 seconds | 265 |
| Five workers | 60 jobs/s | 48 jobs/s | 1,320 jobs | 66 seconds | 320 |

The initial 320-credit budget permits five workers. Reducing the budget to 290
makes the requirements infeasible; the minimum feasible budget remains 320,
requiring 30 more credits. Throttling peak admission to 52 jobs/s would reduce
the internal backlog to 960 and drain time to 120 seconds, but leave 1,080 offered
jobs unaccepted or deferred outside the measured queue. That violates the brief.

## First run

The two phases completed in 114.10 seconds with nine member assignments and 20
provider calls (Alice 5, Bob 3, Carol 4, Dave 8). All assignments settled through
`group_yield`, all response obligations had linked public replies, and both final
JSON decisions matched the numerical oracle. Final citations referred to actual
peer messages; the revised decision used the new Bob and Carol analyses.

Passive messages addressed to all four members after each phase caused no new
model calls or assignments. Recorded tool calls were nine posts, seven requests,
nine yields, three history queries and five full-message queries.

The initial probe nevertheless exited with failure because its retrieval check
incorrectly required at least four history calls. The count was not a functional
requirement: three members had retrieved valid history pages, and four distinct
original messages had been read completely. The corrected check compares tool
results with stored message IDs and content. Rechecking the saved transcript
passed; empty and failed retrieval transcripts correctly failed. The first run
was closed through cancellation cleanup because this probe failure prevented
the explicit completion branch. Its original report was retained unchanged.

First-run artifacts: `/tmp/mas-complex-collaboration-eeaae3b794/`.

## Repeat run and final evidence check

With the same task, prompts and provider settings, the second run took 127.98
seconds: nine assignments and 15 provider calls (Alice 5, Bob 3, Carol 4, Dave 3).
Both numerical decisions, actual peer citations, revised evidence, linked reply
obligations, clean `group_yield` settlement and passive-post checks passed again.

This time members used 14 `group_message` calls and no `group_history` calls:
they already had the IDs needed for direct retrieval. The intermediate probe
still incorrectly required a successful history listing, so it also exited with
failure and closed through cancellation cleanup. This was another overly narrow
coverage condition in the temporary probe, not a failed retrieval in the project.

The final retrieval criterion checks successful complete reads of both the
original user brief and actual peer content, comparing IDs and text with stored
originals. Listing tools and invocation counts are observations, not mandatory
steps. Offline re-evaluation of **both saved runs** passed this criterion. Empty
transcripts, tool-error responses and missing source evidence failed it. No third
live run was performed, and neither original `summary.json` was overwritten.
Each artifact directory contains `acceptance-recheck.json` documenting the
corrected evaluation and the actual terminal state.

Second-run artifacts: `/tmp/mas-complex-collaboration-07e8a8f47e/`.

Across the two live runs, all 18 assignments came from explicit requests and
settled successfully, using 35 real provider calls. Both drivers naturally
returned `waiting` before application cleanup. Explicit `finish` was **not**
exercised in these two runs because the probe assertions blocked that branch;
both stores were confirmed terminal with reason `cancelled` after cleanup.
This report does not treat those stores as completed business invocations.

## Content-quality finding

Follow-up: the [provider-input diagnosis](2026-09-26-group-context-diagnosis.md)
established that Carol and Dave saw only the 512-character brief preview in the
first run, but read the complete 1,803-character brief in the repeat. The initial
description below records the output difference; that difference should not be
attributed solely to model variation under equivalent input. Controlled replays
also exposed a separate gap in checking coverage of the original requirements.

Numerical correctness and runtime settlement do not establish complete task
fulfilment. The first source brief explicitly required acknowledgement timing,
the crash window after a side effect commits but before acknowledgement, and
the limits of the deterministic model under variable latency or outages.

The reliability analysis and independent audit omitted those details. They
mentioned at-least-once delivery and idempotency keys, but did not explain the
commit/acknowledgement boundary or how deduplication must protect side effects.
The final decision retained these omissions. The audit confirmed the existing
controls without identifying missing requirements.

This is a content acceptance gap, not evidence of a scheduler or receipt bug.
The current reply-evidence contract proves that the assigned run published a
linked response; it does not judge whether every task requirement was met.
An explicit requirement checklist at the task review boundary would make these
omissions visible. No new global scheduling restriction was introduced.

The repeat run's reliability analysis did cover the crash-before-acknowledgement
window, durable admission, idempotent side effects, visibility timeouts,
monitoring and variable latency. The task prompts were unchanged. This improves
the second answer but demonstrates variable review completeness rather than a
verified fix. The final JSON remained a terse summary referring to the analyses.

The supported conclusion is that this prescribed multi-stage collaboration
flow and its numerical revision worked in both samples. Complete engineering
review quality was not consistent across them. This task did not test autonomous
organization selection, simultaneous peer fan-out, context-limit compression,
or hour/day-scale uptime.

For reproduction, the current temporary probe is
`/tmp/mas_complex_collaboration.py`; copies and readable transcripts are retained
with the artifacts. Only the probe's acceptance checks changed between attempts.
