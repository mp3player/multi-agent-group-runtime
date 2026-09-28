# Agent long-running reliability audit and fixes

**Scope:** User requested a sweep of exception handling, context overflow and long-running correctness. Baseline is `23aa70c`, 321 passing tests. The user explicitly deferred choosing/designing context summarization and compaction; do not implement that policy in this round.

**Method:** Reproduce actual faults, write failing regressions, apply bounded fixes, independently review, and run the full suite on Python 3.10 and 3.14. Use the existing isolated worktree and verified delivery to the user directory. No live model calls or credential reads. No Group/Web work.

## Task A — Tool I/O and process lifecycle

Own `tools/builtin.py`, `tools/file_ops.py`, optional dedicated process/file helper modules, and `tests/test_long_running_tools.py`. Do not edit audit/runtime/config files owned by other tasks.

- [x] Reproduce/write failing tests for terminal descendants surviving timeout, large stdout+stderr allocation, giant requested/skipped/lookahead file lines, malformed/encoding-failing writes destroying existing files.
- [x] Drain terminal output with bounded retention; retain timeout/exit-code/stderr semantics and kill/reap owned process groups on timeout/interruption. This is local process cleanup, not a sandbox. Bound all cleanup waits and explicitly document platform limitations.
- [x] Read files incrementally, validate negative limits, and return truthful truncation hints plus a usable continuation for an oversized line. Avoid reading the entire skipped/lookahead line into memory. Bound directory listings and replacement input sizes with explicit diagnostics.
- [x] Validate text and encoding before writing; stage overwrite/replacement in a same-directory temporary file and atomically replace only after success. Preserve existing mode where possible. Append must validate before opening; document non-transactional OS write failures for append.
- [x] Focused tests and a report with RED/GREEN evidence, limits and compatibility notes. Parent runs integration/full-suite validation.

## Task B — Stream failure and deadline cleanup

Own `core/agent_runtime/react_loop.py`, `deadlines.py`, and new `tests/test_long_running_streams.py` only.

- [x] Reproduce async-generator cleanup hanging inside cancelled `__anext__`, including LLMClient with offline httpx transport; verify timeout returns and Agent can run again.
- [x] Extend cleanup grace to deadline-induced unwinding already inside model I/O. Preserve caller task/context, cancellation accounting, original errors, and no abandoned model task. Document the unavoidable limit of cancellation-suppressing/blocking custom code.
- [x] Preserve primary synchronous stream exceptions if `close()` also fails; surface cleanup-only failure normally. Test provider failure, explicit close and next-run reuse.
- [x] Run relevant timeout/execution tests and report RED/GREEN evidence. No speculative retry policy or context compaction in this task.

## Task C — Observability retention and recovery acceptance

Own `tools/audit.py`, `core/usage.py`, `core/llm.py` only for optional-monitor failure isolation, new observation/recovery tests, and documentation.

- [x] Reproduce audit Unicode/sink failures after successful effects, unbounded record retention, and malformed optional usage breaking valid responses.
- [x] Bound recent in-memory audit and usage records (default 1000, constructor-configurable); keep monotonic audit IDs and lifetime usage totals. Stream all audit events to configured sinks and expose sink degradation independently of tool success.
- [x] Ensure malformed usage or optional monitor callbacks cannot replace a valid model result. Do not fabricate missing token data.
- [x] Exercise repeated successful/failing turns, provider context-overflow errors, retry by a new request, busy-state release, and tool pairing using deterministic models/local HTTP fixtures. Verify no automatic replay of completed effects.
- [x] Report what is and is not covered: no automatic summary/compaction, no automatic provider retry policy, no crash resume/daemon promise, cooperative arbitrary synchronous tools, and finite model/tool execution limits.

## Integration

- [x] Review all changes and address concrete findings.
- [x] Full isolated tests on both Python versions; optional bounded soak fixture with explicit counts, no claim of real multi-day operation.
- [x] Update docs and verified delivery hashes; leave user `.env`, archive and editor state intact.

Final evidence: Python 3.10.18 full suite 387 passed / 1 cancellation-count-dependent
skip (11.34s); Python 3.14.0rc2 in the user project 388 passed (10.80s).
The finite mixed-mode acceptance ran 1000 tasks / 2000 offline HTTP requests,
including 90 provider failures after tool effects, without automatic replay.
Independent review found and rechecked fixes for normalized cancellation and
CRLF matching; no blocking findings remained. Delivery compared all 105 project
file hashes and the existing `.env` hash. See `docs/reliability-2026-09-23.md`.
