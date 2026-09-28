# Outgoing receipts and free-discussion verification

Date: 2026-09-26

## Finding and accepted behavior

The extra requester contribution in the earlier broadcast case belonged to its
original assignment. Tracing a continuation after a nonterminal request tool
batch showed ordinary assistant/tool messages, with no injected user instruction
and no new requester assignment. The model sometimes answered its own outgoing
request or restated a peer response within that same execution.

Free public contributions are intentional. The accepted requirement is to keep
them passive and prevent accidental activation or unbounded response loops.
There is no one-post restriction or mandatory handoff after sending a request.
Explicit new requests remain subject to the invocation run budget and deadline.

## Changes

Model-facing publication receipts now identify `kind`, `sender`, and `recipients`
in addition to their existing fields. `outgoing_post_receipt` distinguishes a
passive publication from `outgoing_request_receipt`. Metadata describes the
committed outgoing message, not an assignment to the calling member. An empty
request audience still delegates selection to policy; broadcasts name the other
members. Python Receipt values, stored receipts, schema and scheduling are unchanged.

On-demand guidance distinguishes assigned triggers, background, outgoing requests
and acceptance receipts. It explicitly allows relevant voluntary follow-ups and
asks members to yield when done. These instructions improve communication; they
are not a hard guarantee of model compliance.

Tests identify actual requests through opportunity records. A directed ordinary
post can contain recipients without creating response obligations, so recipient
presence is not used as a request classifier.

## Verification

- Five deterministic receipt cases verify passive posts, directed and untargeted
  requests, broadcasts, committed identities/audiences, and complete JSON for 32
  maximally escaped member identifiers at the minimum admitted result budget.
- Two deterministic continuation cases force the requester to reply to its own
  broadcast, with and without a directed recipient. The contribution remains in
  the original run, creates no opportunity, and leaves exactly three assignments.
  A subsequent passive post also causes no new model calls.
- Two opt-in live-provider cases exercise requester continuation after sending
  directed or broadcast peer work. Setup turns and peer replies are scripted;
  the requester then uses the current `.env` model. Both passed in 3.03 seconds,
  with linked replies, expected assignments, `group_yield` settlement and no new
  calls after a passive follow-up. Extra public contributions are permitted.
- Existing cumulative-budget, deadline, queue-contention and close tests passed.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.14: 998 passed, 2 skipped in 26.02s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/mas-agent-worktree-20260923/.venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.10: 997 passed, 3 skipped in 29.53s

MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q tests/test_group_live_continuation.py
# 2 passed; actual verification traces retained under:
# /tmp/mas-free-discussion-live-final-audience/
```

The two default skips are the opt-in live tests. Python 3.10 also skips the
existing cancellation-count test. Independent review confirmed the receipt
metadata and both passive-post test variants. Scoped compilation, whitespace
and English-only checks passed.

See the [Group guide](../../group-runtime.md) for receipt and activation semantics.

## Follow-up review and fresh verification

A fresh independent review compared `group/tools.py` and `group/strategies.py`
with their pre-change snapshots, inspected the related runtime boundaries and
tests, and found no actionable correctness or compatibility issue. No production
code changed during this review. Focused offline receipt and strategy tests
passed: 24 cases in 0.96 seconds.

Fresh full-suite results:

- Python 3.14: 998 passed, 2 skipped in 26.91 seconds.
- Python 3.10: 997 passed, 3 skipped in 30.75 seconds.
- Opt-in requester continuation tests: 2 passed in 2.88 seconds. Setup turns and
  peer replies remain scripted; requester continuations used the configured
  provider. Traces: `/tmp/mas-review-live-continuation-20260926/`.

Three additional end-to-end cases used the current configured local provider for
every participating member. All passed:

| Case | Admitted member runs | Model calls (Alice, Bob, Carol) | Result |
| --- | --- | --- | --- |
| Direct arithmetic | 1 | 1, 0, 0 | Correct result: 323 |
| Directed tax calculation | 2 | 1, 1, 0 | Correct total: 68.4 |
| Broadcast risk discussion | 3 | 3, 1, 1 | All assigned public replies present |

Each case checked passive background delivery, expected member assignments,
successful execution settlement, linked public replies, no additional calls
after a passive follow-up, and explicit application completion. Multiple Alice
model calls in the broadcast case belonged to one assignment; no extra requester
assignment was admitted. Saved messages, assignments, SQLite stores and results
are under `/tmp/mas-live-group-cases-e396229f50/`.

These runs validate the exercised activation and lifecycle boundaries, not
universal model compliance or hour/day-scale uptime. The full offline suite also
covers cumulative request limits, deadlines, cancellation, and control-queue
contention.
