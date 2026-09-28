# Manual test handoff

Date: 2026-09-28

## Scope and implementation finding

The user deferred model reasoning failures and requested implementation readiness
for personal testing. No business-result validation feature, model prompt tuning,
or scheduling changes were introduced.

Both offline Group examples failed on a second process run with the same SQLite
store. The examples created fresh Agent sessions while persisted source receipts
still referred to previous sessions. The runtime correctly blocked preparation
because current context coverage could not be verified. The on-demand example
then raised an assertion; the manual example failed normal completion.

Both CLI entrypoints now choose a fresh temporary SQLite store by default. An
explicit `--store` must name a new path; existing paths and symlinks are rejected
with an explanatory usage error before runtime creation. Old databases remain
untouched. Runtime session-coverage checks remain unchanged. README and Group
documentation explain the distinction between retained history and private
session restoration.

Four real-subprocess regressions failed before the fix and passed afterward.
They cover two consecutive default launches and rejection of an existing explicit
store without database changes for both examples. The focused suite, including
the existing reception boundary checks, passed all 24 tests. Independent review
found no material issues in the bounded correction.

Final full offline regression: **1,130 passed, 12 skipped**, exit 0, in 38.99
seconds. The skipped tests require the explicit live-provider switch. Logs are
retained at `/tmp/mas-example-entrypoints-red-20260928.log`,
`/tmp/mas-example-entrypoints-green-20260928.log`, and
`/tmp/mas-manual-handoff-final-20260928.log`.

## Manual entrypoints

From the project directory, start the standalone Agent with the current `.env`:

```bash
.venv/bin/python -m cli.agent --no-stream
```

Configuration loading, `/context`, `/tools`, and `/exit` were smoke-tested without
provider calls. To exercise automatic peer scheduling with the offline provider:

```bash
.venv/bin/python -m examples.group_on_demand
```

Manual dispatch is also available via `.venv/bin/python -m examples.group_manual`. Both
examples completed successfully after the fix and print their retained database
paths. They are deterministic implementation demonstrations, not live model
conversations. At this handoff, Group exposed a Python API without an interactive
Group CLI. A subsequent change added `.venv/bin/python -m cli.group` for real-model
interactive conversations; see [current Group CLI usage](../../group-runtime.md#interactive-group-cli).

For an existing live-provider Group scenario using `.env`:

```bash
MAS_RUN_LIVE_GROUP_TESTS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest -p pytest_asyncio.plugin -q -s \
  tests/test_group_live_reception.py
```

This handoff did not rerun the live semantic evaluations. Their previously
recorded model-output failures remain separate from implementation regression
checks and have not been relabeled as passing.
