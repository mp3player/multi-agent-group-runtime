# Single-Agent closeout fixes: 2026-09-24

The HTTP completion-validation gap identified in the
[closeout review](agent-closeout-review-2026-09-24.md) is fixed. The local
single-Agent core, application service and CLI meet the agreed first-stage
functional acceptance criteria under the documented operational limits.

## Changes

- Both `LLMClient.invoke()` and `ainvoke()` now validate non-streaming HTTP
  completion metadata before returning a response. Invalid choice envelopes
  and missing, null, blank or non-string finish reasons raise `LLMError`.
  Each returned choice is checked. Known unsuccessful reasons such as `length`
  and `content_filter` are also rejected at this boundary.
- Reported usage is still recorded before completion validation. Structured
  provider-error classification retains its existing precedence.
- The shared Agent response parser and custom `ModelClient` contract remain
  unchanged. Complete custom-adapter responses may omit finish metadata.
- The roadmap, runtime guide, operation notes and README now describe current
  context compaction, iteration-limit feedback and acceptance evidence.
  Historical reliability findings are explicitly marked as historical.
- `.vscode/browse.vc.db` and its SQLite sidecars are ignored and removed only
  from the Git index. All four local files remain present. Editor configuration
  files remain tracked.

## Regression coverage

The new tests cover 32 cases:

- Twelve loopback HTTP integration cases combine synchronous/asynchronous
  non-streaming runs, missing/null/empty finish metadata, and text/tool replies.
  They assert no assistant commit or tool effect, no tool audit entry,
  preservation of the original user request, retained usage accounting,
  release of the run guard, and successful reuse of the same Agent.
- Two custom-adapter cases verify that complete responses without finish
  metadata still execute tools and finish normally.
- Eighteen direct-client cases cover malformed envelopes, blank or non-string
  reasons, unsuccessful completion and an invalid later choice in a response
  containing multiple choices.

Before implementation, the new cases produced **30 failures and 2 passes**;
all failures were missing expected `LLMError` exceptions. Two existing
usage-focused test families used empty choices as success fixtures. Those
fixtures were replaced with complete assistant responses; their original
request-option and usage assertions were retained.

## Fresh verification

| Check | Result |
| --- | --- |
| Focused provider and compatibility regression | 230 passed in 1.60 seconds |
| Full suite, Python 3.14 | 854 passed in 22.27 seconds |
| Full suite, Python 3.10 | 853 passed, 1 skipped in 25.54 seconds |
| Live CLI using the current `.env` | Passed in 5.210 seconds; exit code 0 |
| Whitespace/diff check | Passed |
| Generated editor databases | All four ignored, absent from index, still present locally |

An independent read-only review of this repair found no must-fix issues and
passed the 178 provider-boundary tests it ran. The shared unsuccessful-reason
validator is covered by existing tests; not every reason is repeated in the
new direct-client matrix.

The Python 3.10 skip is the existing test requiring `asyncio.Task.cancelling`,
which is unavailable on that version. The live CLI run used a temporary
workspace, archive and audit directory. It verified all five workspace tools,
saved-session recall, streaming output, usage/audit commands and normal exit.
It did not alter `.env` or user workspace files.

Full regression command:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin tests -q
```

Python 3.10 used the interpreter at
`/tmp/mas-agent-worktree-20260923/.venv/bin/python` against the current source
checkout with the same arguments and `PYTHONPATH=.`.

Local temporary evidence:

- `/tmp/mas-closeout-fix-red-20260924.log`
- `/tmp/mas-closeout-fix-tests-20260924.log`
- `/tmp/mas-closeout-fix-tests-py310-20260924.log`
- `/tmp/mas-closeout-fix-live-cli-20260924/cli-result.json`
- `/tmp/mas-closeout-fix-20260924.patch` (this repair's source, test and guide diff)

The earlier [repeated acceptance report](agent-repeated-acceptance-2026-09-24.md)
remains the evidence for long tasks and repeated process use. This repair did
not repeat that entire workload or introduce an hours-long/days-long soak.

## Milestone boundary

Group orchestration and UI remain separate future work. Existing limits include
cooperative interruption of synchronous code, finite archive quotas, explicit
snapshot save/load and no exactly-once effect recovery across process failure.
The accepted milestone does not claim unlimited unattended operation.

The workspace remains uncommitted, including earlier implementation changes.
Only the generated editor-file removals are staged by this repair. No commit,
merge, push or release was performed; creating a versioned baseline is a
separate delivery action.
