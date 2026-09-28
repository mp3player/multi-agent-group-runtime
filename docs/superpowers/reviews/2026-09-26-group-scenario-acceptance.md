# Group scenario acceptance

Date: 2026-09-26

## Live local-model cases

The current project `.env` supplied the model endpoint, credentials, model name,
context capacity and generation options. Three synthetic tasks used real Agents,
Group tools, worker threads and SQLite. No model response was scripted in these
cases. Each member had an independent Agent, client, session and archive.

| Case | Observed behavior | Runs | Model calls | Elapsed | Result |
| --- | --- | --- | --- | --- | --- |
| Directed calculation | Alice replied with 323 for 17 * 19; Bob and Carol stayed idle | 1 | 1 | 1.83 s | Pass |
| Peer request | Alice calculated subtotal 57 and requested Bob; Bob calculated 20% tax and total 68.4; Carol stayed idle | 2 | 2 | 5.79 s | Pass |
| Broadcast | Alice published a queue risk and requested both peers; Bob and Carol each replied with a mitigation | 3 | 5 | 5.72 s | Pass |

Every case also verified that initial background messages activated nobody,
all admitted executions settled successfully, every response obligation had
linked public evidence, and quiet driving returned `waiting` with an open
invocation. A subsequent ordinary post caused no additional model call or
assignment. Explicit application completion then produced durable
`terminal/completed` state with zero outstanding runs or requests in all three
databases.

One model-behavior observation: Alice added a mitigation reply to its own
broadcast within its original execution, beyond the requested risk and peer
invitation. This did not create another assignment or response loop. The runtime
enforces explicit activation and budgets; the exact number and content of public
posts within an execution still depend on the model following the prompt.

The live harness used at most six turns per Agent execution, eight runs per
invocation, a 120-second invocation deadline, a 100-second Agent timeout and
an HTTP timeout capped at 90 seconds. This is scenario acceptance, not a soak
test or a guarantee of model compliance on arbitrary tasks.

Local artifacts:

- Harness: `/tmp/mas_live_group_cases.py`
- Public histories, assignments, checks and databases:
  `/tmp/mas-live-group-cases-969c87e754/`

## Deterministic failure and boundary cases

Ten selected cases passed in 1.12 seconds. They cover passive posts and directed
activation, peer requests and broadcast, explicit repair after private-only or
failed execution, historical-driver contention during timeout/settlement/close,
cancelled waiters during control-write retry, and cumulative-budget termination
without false completion. These cases script provider behavior or inject storage
pauses to reproduce specific conditions while exercising the actual runtime.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -v \
  tests/test_group_strategy.py::test_on_demand_only_requests_activate_and_public_replies_do_not_loop \
  tests/test_group_strategy.py::test_on_demand_drives_peer_requests_to_quiescence \
  tests/test_group_recovery.py::test_explicit_resolution_unblocks_completion_and_preserves_original_outcome \
  tests/test_group_control_capacity.py::test_historical_drivers_cannot_exhaust_lifecycle_capacity \
  tests/test_group_control_capacity.py::test_cancelled_waiter_preserves_cancellation_while_control_write_retries \
  tests/test_group_strategy.py::test_continuing_requests_hit_cumulative_budget_without_false_completion
```

The offline three-member example also passed: background caused zero runs,
directed and broadcast work produced Alice=1, Bob=2, Carol=1, and explicit
completion retained eight public messages with no outstanding work.

```sh
.venv/bin/python -m examples.group_on_demand --store /tmp/mas-on-demand-cases-20260926.sqlite
```

No production code changed during this acceptance run.

The subsequent [outgoing-receipt investigation](2026-09-26-group-outgoing-receipts.md)
clarifies the voluntary contribution observed here and verifies that free public
follow-ups remain passive.
