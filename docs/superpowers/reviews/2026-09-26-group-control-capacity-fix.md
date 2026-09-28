# Group lifecycle capacity fix

Date: 2026-09-26

## Reproduced failure

Concurrent drivers reading completed invocations could occupy every reserved
control slot while SQLite was slow. An active invocation's timeout cancellation
then failed before admission, leaving its worker running. The same contention
could reject outcome persistence, incorrectly fail the runtime with a healthy
store, or prevent close from cancelling nonterminal invocations.

## Changes

Store admission now separates normal operations, reserved control writes, and
one reserved control read. All accepted operations still execute FIFO on the
same SQLite worker. A slow transaction is awaited rather than bypassed.

Owned lifecycle writes, driver lifecycle reads, and close-time scope reads share
bounded backoff for `QueueCapacityError` rejection before admission. Cancellation
of a caller does not abandon its owned write. Accepted transactions are not
replayed, and database failures still stop admission. Normal command rejection,
invocation deadlines, run limits, and response resolution semantics are unchanged.

## Verification

Seven new regression cases cover historical-driver contention during timeout,
settlement and close; cancellation and settlement write admission retries;
cancelled waiters during retry; and independent bounded store lanes, including
a cancelled read whose transaction still owns its slot. These cases fail against
the pre-fix implementation and pass with the fix. The contention tests use real
Agents, workers and SQLite with event-controlled pauses.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.14: 991 passed in 25.45s

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/mas-agent-worktree-20260923/.venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# Python 3.10: 990 passed, 1 skipped in 28.92s
```

The skip is the existing Python 3.10 cancellation-count test. Independent
read-only review found no concrete issue and passed the ten targeted storage
and lifecycle tests. Scoped compilation, whitespace and English-only checks
passed. No live model call or schema change was needed.

The admission behavior is documented in [the Group guide](../../group-runtime.md).
