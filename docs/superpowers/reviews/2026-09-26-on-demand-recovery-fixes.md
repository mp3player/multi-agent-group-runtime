# On-demand recovery fixes

Date: 2026-09-26

## Scope and behavior

Two reproduced review findings are fixed without changing standalone Agent code.

### Temporary queue saturation

Previously, a temporary command/store queue rejection reached the driver's fatal
error handler and terminated accepted pending work. `QueueCapacityError` now
identifies rejection before admission separately from invalid plans and durable
invocation limits. The driver reobserves after a bounded 10–100 ms backoff; the
original deadline remains in force. Initial deadline and final lifecycle reads
use reserved control capacity. Cancellation of a caller still leaves the owned
driver running; actual runtime close owns cleanup.

Permanent oversized plans fail once, and deadline exhaustion closes as timeout.
This retry path is for scheduler operations, not automatic replay of model tool
effects. Application/member command capacity rejection remains an explicit result
for that caller to handle.

### Explicit replacement response evidence

Previously, a missing/failed original reply permanently blocked normal completion,
even after a later successful execution published a replacement. Applications can
now call `resolve_response(scope, opportunity_id, reply_id, key=...)` after arranging
an explicit repair execution. Admission validates the original settled obligation,
same invocation, same original member, reply to the original message, and a later
successfully settled execution. External, unrelated, running and failed evidence
is rejected without a partial update.

The resolution link and idempotent receipt commit together. Original messages,
outcomes, errors and cumulative admission counts are unchanged. Scheduling views
expose original evidence plus `resolved_by` and `resolution_reply_id`. A failed
run with several obligations remains unresolved until all are satisfied. Resolution
does not run a model, publish a message or finish an invocation automatically.

Schema versions 1 and 2 upgrade additively to version 3. Completed stores retain
messages, outcomes, limits and receipts; unfinished-store crash recovery remains
unsupported.

## Verification

The new regression file is `tests/test_group_recovery.py`. Its initial run produced
nine expected failures for the two missing behaviors. Final coverage contains
14 passing cases, including real SQLite queue contention, bounded retry/deadline,
cancelled waiters, permanent plan rejection, successful/private-failed recovery,
partial repair, invalid evidence, running replacement execution, durable receipt
replay and schema upgrade.

Independent read-only review found no new P1/P2 issue in the implemented fixes.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.14: 984 passed in 25.28s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/mas-agent-worktree-20260923/.venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.10: 983 passed, 1 skipped in 28.84s

.venv/bin/python -m examples.group_on_demand --store /tmp/mas-on-demand-recovery-20260926.sqlite
# Zero runs for background, exactly four runs for directed/broadcast work,
# waiting result followed by explicit finish; no reply feedback loop.
```

The skip is the existing Python 3.10 cancellation-count test. Scoped compilation,
whitespace checks and English-only code checks passed. No live model was needed
for these storage/scheduling recovery regressions.

Usage is documented in [the Group guide](../../group-runtime.md).
