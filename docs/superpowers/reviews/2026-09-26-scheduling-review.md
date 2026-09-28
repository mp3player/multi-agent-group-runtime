# Scheduling review and test results

Date: 2026-09-26. Scope: working-tree Group scheduling after reception
implementation, followed by the authorized correction of the P1 finding below.
The initial review did not edit runtime code; the follow-up changes the revision
read in `GroupRuntime.wait_for_change` and adds standard regression tests.
The current Group package is already untracked relative to HEAD, so review
inspected actual working files, not an empty commit diff.

## Verdict

The separation between durable reception, strategy decisions, dispatch admission
and Agent execution is sound for the supported local peer profile. Normal
OnDemand behavior and a test-only alternate activation rule passed. The verified high-priority service-lifecycle defect has been fixed: idle
revision reads now use the existing reserved, retrying control-read path.
The original service deadline still bounds that wait, and real persistence
failures still propagate. Independent scoped review found no remaining issue
in this correction. The original finding and evidence below are retained for
traceability; no architectural replacement was needed.

## Original P1 finding: idle service exits on transient read-queue pressure (fixed)

Pre-fix locations:

- `group/service.py:26`: the service waits for a revision outside the driver's
  retry handler and catches only `asyncio.TimeoutError`.
- `group/runtime.py:380`: `wait_for_change` obtains the current revision through
  `store.read` using normal admission capacity.
- `group/store.py:104`: normal queue saturation rejects with `QueueCapacityError`.

When the normal queue is full just as an idle service checks the revision, the
exception escapes `GroupService._serve`. The service task terminates and its
live-owner cache entry is removed. The invocation remains open, and neither later
input nor the invocation deadline is handled automatically by that failed owner.
A caller awaiting `service.wait()` sees the exception; an application that starts
and retains a background service without immediately waiting can miss it.

A deterministic reproduction used `queue_jobs=1` and an invocation timeout of
0.3 seconds. It paused immediately before the service's revision read, occupied
the normal lane with one real accepted SQLite read, then released the service.
This does not inject an artificial QueueCapacityError: the actual store's queue
admission raises it. The ordinary read subsequently completes. A separate
observation 0.4 seconds later reports:

```text
service error: QueueCapacityError Group store queue is full; operation was not accepted
after deadline: open reason: None live services: 0
```

The minimal queue setting makes the interleaving deterministic; the same rejection
path applies whenever the configured normal lane fills. Existing driver retries
cover the active loop but not this service idle-wait path.

Suggested correction: use the reserved/retrying lifecycle revision-read path for
owned waits, or explicitly handle transient queue rejection inside the service
with bounded backoff and the original absolute deadline. Preserve the distinction
between a rejected-before-admission queue operation and a real persistence
failure. Add the failing probe below to the standard lifecycle suite when fixed.

## Test evidence

| Test set | Result | What it establishes |
| --- | --- | --- |
| Existing Group suite | 159 passed, 3 skipped in 5.65 s | Manual/OnDemand dispatch, pending requests, replay keys, full reception, source barriers, blocked retry, limits, cancellation and closure. |
| Explicit `.env` live tests | 3 passed in 25.32 s | Real directed/broadcast continuation and delayed collaborative integration; the normal workflow still works. |
| New review probes | 2 passed, 1 failed in 3.28 s | Two extended functional scenarios pass; the service-pressure requirement fails reproducibly. |
| Independent lifecycle review run | 36 passed in 3.67 s | Additional scoped re-execution of lifecycle, reception, control-capacity and boundary suites; these overlap the Group suite above. |

The new successful probes are:

1. Four members, `max_active=2`, `max_queued=2`, small reception/discovery pages,
   six rounds of passive posts plus directed all-peer and untargeted requests.
   Exactly 30 assignments and 30 provider calls, independent satisfied response
   slots, no activation from passive posts, complete final reception.
2. A test-only tool-free strategy reacts to user posts using stable origin keys,
   complete discovery acknowledgment and durable deferred IDs in policy state.
   Seven sources produce exactly seven runs, without explicit response slots,
   lost busy-member work or reactions to its own published outputs. This shows
   that the extension boundary can change activation behavior, not merely order.

The failing probe asserts the desired behavior: a temporarily saturated idle
service must remain owned and eventually report the original deadline's timeout.
The pre-fix implementation raised QueueCapacityError instead; the retained
probe now passes after the correction. The probe remains
outside the default `tests/` discovery path as a historical review artifact.
Its service-pressure requirement is now also covered by the standard suite in
`tests/test_group_service_backpressure.py`; no expected-failure marker is used.

## Reproduce

The [review probes](scheduling-2026-09-26/test_scheduler_probes.py) are retained
with this report. From the repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -p pytest_asyncio.plugin -q tests/test_group*.py

MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s \
  tests/test_group_live_reception.py tests/test_group_live_continuation.py

PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -p pytest_asyncio.plugin --asyncio-mode=auto -q -s \
  docs/superpowers/reviews/scheduling-2026-09-26/test_scheduler_probes.py
```

The final command now exits 0 after the fix (3 probes pass). Before the fix it
exited 1 with exactly the service-pressure probe failing. The live tests read the existing
`.env`; no credentials are included in this report or the new probe source.

## Design observations and scope limits

- Reception remains separate from task activation. Synthetic history neither
  invokes business inference nor satisfies response obligations.
- A queued assignment has a stable input boundary. Busy and blocked members keep
  their own response work; normal OnDemand posts do not create an output loop.
- Strategy substitution already supports distinct tools, prompts, activation
  causes, stable origin keys and deferred state. Only OnDemand is a production
  strategy; the alternate policy above is a test fixture.
- Untargeted OnDemand requests use stable membership order. It does not promise
  round-robin fairness or load balancing; that is a documented policy choice,
  not a newly discovered correctness bug.
- Time-window collection, policy switching and richer response collections need
  additional explicit contracts. Passing the alternate-policy fixture is not
  evidence that every future organization can use the existing interface unchanged.
- A suspected settlement/capacity wakeup race reproduced only when adding an
  extra scheduling pause. Production reachability was not established, so it is
  not counted as a confirmed finding.
- These are finite functional/concurrency checks. They do not prove arbitrary
  plugin safety, forced termination of blocking Python threads or crash recovery.


## Follow-up correction and verification

Production change: `wait_for_change` calls `_store_control(sql.revision)` instead
of ordinary `store.read`. It still captures the notification event before the
read, so a commit during admission retries cannot be missed. The existing helper
uses the reserved read lane and bounded backoff for `QueueCapacityError` only.
The service's existing `asyncio.wait_for` retains its absolute invocation deadline;
no new deadline, retry policy or scheduling strategy was introduced.

Six standard regression cases cover normal/control read pressure, handling new
work after congestion, expiry while control reads remain unavailable, closing
while reads are pending, and a real SQLite failure. Tests use actual store queue
admission; application-side queries/commands still handle their documented
ordinary capacity rejections. Independent verification of the new service tests
and existing control-capacity/driver-lifecycle tests passed: **17 passed**.
The unchanged review probes plus the six new tests passed: **9 passed**.

Final full-suite verification passed: **1063 passed, 3 skipped in 34.85s**.
The three opt-in configured-provider tests were run separately against the
existing local-model configuration: **3 passed in 27.04s**. The collaboration
scenario completed four assignments with four provider calls, confirmed delayed
original-source visibility, and completed final reception.
