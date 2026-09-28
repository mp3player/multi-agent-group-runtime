# Group foundation review and verification

Date: 2026-09-26

Scope: The policy-free foundation in
[the implementation plan](../plans/2026-09-26-group-foundation.md), with the
restricted binding described in [Group runtime](../../group-runtime.md).
Existing uncommitted single-Agent work was preserved. No commit, migration,
live-model request or release was performed.

## Review findings resolved

| Finding | Resolution and regression |
| --- | --- |
| A late stop could affect the next Agent run | Stop control belongs to one submission; stale handles cannot stop successors |
| A stop during `run_start` still admitted input | Recheck stop before advancing past the start event |
| Known async tool hooks escaped the synchronous binding checks | Check concrete pipeline hooks, configured audit sink and archive methods before ownership |
| Public synchronous error iteration changed | Capture terminal state internally while preserving the original public exception behavior |
| Cancelling an old scope could stop current work | Select and wait only for assignment IDs belonging to the requested scope |
| History JSON could lose continuation data to generic truncation | Return bounded complete preview envelopes and expose original content through chunked reads |
| Repeated launch/cancel requests could create unbounded owned tasks | Coalesce requests by scope and reject new controls at capacity |
| A failed read could leave revision waiters asleep | Signal store failure independently of mutation notifications |
| Partial tool installation could leave registry mutations behind | Roll back installed names on failure and keep Agent ownership acquisition atomic with installation |
| Invalid history filters could poison the store | Validate query shape and state filters before SQL execution |
| Tool defaults could exceed smaller configured limits | Use the configured page size and cap content chunks while retaining continuation |

Independent review and scoped re-review found no remaining actionable issues in
these changes. The final small changes also received a scoped review. Tests
reproduced the new boundary defects before their fixes.

## Verification

Commands use `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and `-p pytest_asyncio.plugin`.
This prevents unrelated system ROS pytest entrypoints from importing unavailable
PyYAML before this project's tests can collect.

| Verification | Result |
| --- | --- |
| Baseline single-Agent suite, Python 3.14 | 854 passed |
| Full suite, Python 3.14 | 892 passed in 24.35 seconds |
| Final focused Agent/Group regression, Python 3.14 | 95 passed |
| Full suite, Python 3.10 | 891 passed, 1 skipped in 25.57 seconds |
| Offline explicit-plan example, including reopening its database | Passed |
| Compilation and scoped whitespace checks | Passed |

The Python 3.10 skip is the existing test requiring `asyncio.Task.cancelling`.
An initial full 3.10 run exposed a timing-dependent test precondition: the
explicit-close branch's 10 ms run timeout could expire in archive I/O before
opening the stream it intended to close. Its isolated rerun passed. The test now
uses only the independent cleanup deadline for explicit closure; the separate
run-timeout branch retains its original deadline. Production timeout behavior
was not relaxed. Full 3.10 and focused 3.14 verification passed after this change.

Group tests include a real two-Agent tool exchange, parallel blocked providers,
blocked audit persistence with responsive Group storage, 25 completed invocation
cycles, preservation after reopen, private final answers, scoped cancellation,
atomic rejection, durable command idempotency, queue pressure, settlement and
pagination. Providers are deterministic test boundaries; the Agent/tool/store
paths are real. This is not a live-model or hours-long load test.

## Remaining scope boundaries

No concrete scheduling policy, Group UI/CLI, native asynchronous member adapter,
shared writable-workspace protection, dynamic membership, automatic crash
reconciliation, portable Group snapshot or global storage quota is implemented.
An indefinitely blocked synchronous operation retains its lease. These are
explicit profile limits, not functionality claimed by the passing tests.
