# On-demand strategy implementation and verification

Date: 2026-09-26

## Scope

The archived on-demand behavior was reimplemented on the peer Group foundation.
No archived runtime was copied or imported. Selection remains explicit with
`GroupRuntime.create(..., strategy=OnDemandStrategy())`; manual dispatch remains
supported. The standalone Agent implementation was not changed for this slice.

The implementation includes communication profiles, optional broadcast, a bounded
detached scheduling view, a pure request-selection rule and an owned driver.
Profiles select tools, instructions, input rendering, optional final publication
and required public reply evidence. Public posts become stored context without
activating every member. No synthetic assistant PASS/history mutation is used.

New invocations persist cumulative run ceilings and deadlines. Successful,
failed and cancelled reservations retain their admission count. Deadline and
driver-error cleanup retain worker ownership until actual execution returns.
Quiet invocations remain open: application acceptance and `finish` are explicit.

## Independent review and fixes

The independent reviewer reproduced three concrete defects during implementation:

1. **Driver errors lost deadline ownership.** A strategy exception after launch
   previously returned while a worker remained active. Ordinary strategy/render/
   plan failures now close the scope with `error`, wait for actual settlement and
   re-raise. A blocked-provider regression observed the failure before the fix.
2. **Custom tools received an implicit safe classification.** Arbitrary profile
   callables were registered as memory-only. Custom tools now require an explicit
   `CollaborationTool` declaration with supported permission metadata. Unknown,
   unsupported and asynchronous declarations fail composition. Metadata is
   preserved; this remains a trusted declaration, not a sandbox.
3. **Wrapped yield lost its terminal behavior.** Exact `ToolFunction` wrappers
   now retain known built-in metadata. A real Agent regression confirms one
   invocation and `tool_stop`. Arbitrary wrappers/subclasses cannot inherit safe
   effect metadata merely through `__wrapped__`.

Focused tests also caught installation rollback deleting a preexisting tool,
wrapped-tool alias divergence, and a default-background/page-limit compatibility
regression. Those cases now preserve prior registrations and honor configured
limits. Re-review found no remaining substantive issue in the corrected paths.

## Automated verification

Final runs used stable implementation files:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# 970 passed in 25.01s (Python 3.14)

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /tmp/mas-agent-worktree-20260923/.venv/bin/python -m pytest -p pytest_asyncio.plugin -q
# 969 passed, 1 skipped in 27.86s (Python 3.10)
```

The Python 3.10 skip is the preexisting test requiring `asyncio.Task.cancelling`.
Plugin autoload is disabled to exclude unrelated system ROS plugins.
Scoped compilation and whitespace checks passed. New Group code, examples and
tests contain no Chinese text.

Coverage includes directed and untargeted requests, explicit broadcast, passive
posts, no reply feedback loop, exact-run response evidence, missing/private
replies, member errors, cumulative limits, busy-member retention, pending work
beyond one page, deadline/close ownership, cancelled driver waiters, strategy
errors, bounded context projection, final-publication failures, explicit custom
tool effects and completed-store schema upgrades. The existing standalone suite
also passed.

## Executed examples

```sh
.venv/bin/python -m examples.group_on_demand --store /tmp/mas-on-demand-20260926.sqlite
```

The offline example exercised real Agents, tools, SQLite and the driver:

- Background only: zero member executions.
- Alice requests Bob and broadcasts to Bob/Carol: Alice once, Bob twice, Carol
  once; exactly four executions, all required replies recorded.
- Driver returns `waiting`; application explicitly finishes the invocation.

A bounded live test also used the current project `.env` endpoint configuration
without exposing credentials. Workspace tools were absent; inputs were synthetic
arithmetic requests. Each Agent was limited to five model turns and each scope
to eight runs with a 180-second deadline.

| Scenario | Member executions | Outcomes | Missing replies | Driver result |
| --- | --- | --- | --- | --- |
| Directed request | Alice once | `tool_stop` | 0 | `waiting` |
| Alice broadcasts to peers | Alice, Bob, Carol once each | Three `tool_stop` | 0 | `waiting` |

Both scopes then passed explicit `finish`. Local reproduction script and summary
are at `/tmp/mas_on_demand_live_smoke.py` and
`/tmp/mas-on-demand-live-report.json`; these temporary artifacts are not required
to run the repository example. The live test is a smoke test, not a guarantee of
every model's future tool adherence.

## Remaining boundaries

- This is one request-driven strategy, not the default strategy, broadcast-
  feedback collections, task-success inference or dynamic strategy switching.
- Missing replies do not silently retry. Public reply evidence verifies the
  source execution and relation, not answer correctness. Applications inspect
  results and decide how to continue.
- Profiles are trusted synchronous extensions. General capability negotiation,
  arbitrary output adapters and complete event-discovery streams remain future
  work. A recent-message window is not an exhaustive event feed.
- Native asynchronous member pipelines, shared mutable workspaces, distributed
  execution and unfinished-store crash recovery are not added here. Synchronous
  blocked operations cannot be forcibly killed; cleanup keeps ownership.
- The Group store retains originals and grows over time. This slice adds no
  history deletion or global storage quota.

Current usage and limits are documented in [the Group guide](../../group-runtime.md).
