# Operation notes

Start from the project root with `uv run python -m cli.agent` or
`uv run python main.py`. `--prompt TEXT` performs one task and exits.

The CLI uses one asyncio loop for its lifetime, closes nested streams, and
closes application-owned model connections when exiting. An injected model is
borrowed by default; pass `owns_model=True` when the service should close it.
Do not reuse an asynchronous application across separate event loops.
Concurrent `aclose()` callers await the same cleanup task. Cancelling a caller
does not cancel that task; keep the event loop alive and await `aclose()` again
to observe completion. Cleanup errors leave the service in `close_failed`,
blocking new runs while allowing another cleanup attempt. Whether a failed
model can recover on retry depends on that model's cleanup implementation.

Use `/history` to inspect conversation counts and recent messages, `/tools` to
inspect registered tools, and `/audit [N]` to inspect tool decisions/results.
`/usage` reports provider usage received in streaming and non-streaming requests.
`MAS_LLM_STREAM_USAGE=1` requests streamed usage only for compatible endpoints;
unsupported providers may reject that option. `MAS_TOOL_AUDIT_JSONL` enables a file sink.
Audit and usage keep the latest 1000 records in memory by default. Usage totals
remain cumulative until cleared. Missing usage is not recorded as zero usage.
Audit files receive every event and require external disk rotation/retention.
Sink failures are exposed through `ToolAuditLog.sink_error_count` and
`last_sink_error`; optional usage failures through the corresponding
`LLMClient.usage_error_count` and `last_usage_error`. These ordinary monitoring
errors do not replace a completed tool effect or valid model reply.

Use `/save PATH` while idle, and `/load PATH` to restore a saved conversation.
Loading validates schema and tool links before replacing the current session.
It keeps the application's current system prompt, and never executes tools.
`/new` and `/clear` reset the conversation to configured limits and prompt.
Snapshots use version 2 and reference separate archive attachments; loading a
version 1 snapshot preserves its available data but cannot recover messages
discarded by older releases. Keep the archive directory with snapshots during
backup or migration. Missing attachments allow inspection and saving, but block
the next model run. Restore the attachments to the configured archive root
before continuing. New sessions retain the configured archive store, with a
fresh session identity.

Use `/context` to inspect capacity, the conservative token estimate, budget,
latest compaction, summary usage and archive bytes. Unknown usage is reported
as unknown, not measured zero. Configure `MAS_CONTEXT_WINDOW` before inference;
the old `MAS_ACTIVE_MESSAGE_LIMIT` setting no longer drops messages. `/compact`
requests idle compaction; `/compact preserve file names and remaining tests`
adds an optional focus. Both manual and automatic compaction use bounded
non-streaming summary calls in the existing event loop. Progress events go to
stderr and historical summary text is kept out of the answer stream.

If an execution is interrupted, completed tool results remain recorded.
Unfinished calls receive interruption records. Inspect external state before
retrying an operation whose result is unknown; session files do not provide
exactly-once effect recovery. Synchronous tools can block the calling thread
and cannot be forcibly stopped by an async deadline.

Provider errors, malformed stream events and streams ending before a
`finish_reason` raise `LLMError`. The built-in HTTP client also rejects
non-streaming replies with missing, null, blank or non-string finish reasons.
A token limit or content filter also fails the run before committing an
assistant message or executing its tools. Streaming
text already displayed may be incomplete. Check the error instead of treating
that text as a successful result.

Builtin operation failures are typed `ToolFailure` strings, so audit records
mark failed writes, missing files, command timeouts and nonzero exits as errors.
They cannot trigger successful terminal-tool completion.

Terminal execution currently requires POSIX (Linux/macOS). It drains both
output pipes with bounded buffers and cleans up its process group on timeout
or interruption. Children that start a separate session are outside that group;
this is not a sandbox. File reads now provide `line_offset` and `char_offset`
continuation hints for long lines. The latter counts Unicode characters within
the selected line. Keep the file unchanged between pages.

Whole-file writes and replacements validate UTF-8 before mutation and stage
an atomic replacement. Existing permission bits are preserved; new whole files
are private (0600 on POSIX). Inode identity, hard links, ownership, ACLs and
extended metadata are not preserved by this operation. Append validates first
but is not transactional if the OS fails midway. Replacement uses universal
newlines, as before, matching the text shown by `read_file`.

The runtime checks complete request budgets before every model request and
compacts older context when needed. Replaced originals remain available through
the reserved `read_context_archive` tool, scoped to the current session and
paginated with `offset`, `limit` and `next_offset`. It does not execute old calls.
Compaction is limited to four summary requests per transaction. A recognized
provider context overflow can trigger one recovery attempt before visible
streamed output; ordinary provider errors are not automatically retried.
An oversized protected user request or fixed prompt/tool overhead fails with
an actionable error rather than silently shortening the request.

Failed, stale or cancelled summary candidates never replace the working view.
If required compaction exhausts the archive quota or cannot produce a smaller
valid request, inference stops and existing context remains available. Increase
the configured quota or restore space without deleting referenced archives.
The default quota is 256 MiB and successful archives have no automatic cleanup.
See [runtime boundaries](agent-runtime.md) for the current operational limits.

The [closeout fixes](agent-closeout-fixes-2026-09-24.md) record the latest
regression results. The [repeated acceptance report](agent-repeated-acceptance-2026-09-24.md)
records separate live-model and CLI runs, long tasks, fault recovery and resource
checks. Automated regression tests use local fixtures; live acceptance is a
separate workflow using the configured endpoint. Neither requires an hours-long
or days-long soak for this milestone.


## Group reception and automatic operation

See [Group runtime](group-runtime.md) for the complete Python API. `post` stores
public information; `request` also creates durable response work. `receive(scope)`
records all accepted sources through a fixed boundary without inference. Query
`reception(scope, member)` for received/pending source counts. These counts do
not mean the member has run or understood the information.

Call `start_service(scope)` to retain an owned automatic scheduler during idle
periods, or use `drive(scope)` for a single drain. A service requires an explicitly
selected strategy. It shares the driver with concurrent callers and keeps the
invocation deadline. Cancel the scope or close the runtime to stop it; cancelling
a waiting client does not release a busy worker.

Preparation happens on the member's serial worker before business inference.
Inspect `blocked_assignment_ids` and assignment errors when driving returns
`needs_input`. Resolve the preparation cause and explicitly `retry_preparation`
with the same assignment ID, or cancel. Protected sources are never shortened to
fit; byte-budget changes require a newly configured runtime. A failed execution
that already started inference is not automatically replayed.

Normal `finish(scope, revision=...)` seals input, drains a finite reception
boundary and then records completion. Handle `StalePlanError` by rereading and
reconsidering; never force an obsolete revision. Always await `close` before
stopping the event loop. The store rejects unfinished-process reopening and
nonempty legacy schemas; this feature does not add automatic crash recovery.
