# Single-Agent closeout review: 2026-09-24

> Follow-up: the HTTP validation issue and documentation corrections below have
> been addressed. Generated editor databases have been removed from the Git
> index while retaining local files. See [fixes and verification](agent-closeout-fixes-2026-09-24.md).
> The original assessment is retained as a record of the findings before repair.

## Verdict

The implemented local single-Agent core, application service and CLI are close
to a stable first-stage baseline. No further architectural rewrite is justified
by this review. Before declaring the stage complete, fix one narrow HTTP
response-validation gap and bring the current documentation up to date.

Group orchestration and UI remain outside this milestone. The accepted
stability target is repeated functional coverage, long tasks and continued
process use; it does not require an hours-long or days-long soak.

This review changed no runtime code, configuration or existing tests. No commit,
merge, release or external publication was performed.

## Required correction: non-stream HTTP completion validation

**Priority: P2.** `LLMClient.invoke()` and `LLMClient.ainvoke()` decode HTTP 200
JSON and reject explicit error envelopes, but do not require a nonempty terminal
`finish_reason`. The shared response parser accepts absent, null and empty
reasons. Consequently, the built-in HTTP adapter can pass an unconfirmed
completion to the runtime, which commits its assistant message and executes
its tool calls.

This does not prove that every such response is truncated. It means the
completion signal is missing and the built-in HTTP boundary still treats the
response as successful. Existing SSE handling rejects an equivalent missing
finish signal.

Reproduction used a loopback HTTP server, the real `LLMClient` and a real Agent:

- `run` and `arun`;
- missing, null and empty-string `finish_reason`;
- plain text and a tool call with valid arguments.

All 12 cases completed successfully under the current implementation. All six
tool cases executed an in-memory effect and wrote an audit record. The probe
did not call a live model or modify user files. Results are recorded in the
temporary local artifact
`/tmp/mas-closeout-missing-finish-20260924.json`.

The response shape was:

```json
{
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "partial answer",
      "tool_calls": [{
        "id": "effect-1",
        "type": "function",
        "function": {"name": "record_effect", "arguments": "{}"}
      }]
    }
  }]
}
```

The suggested correction belongs in the built-in HTTP adapter, before it
returns a successful response. Preserve explicit provider-error classification
and existing usage-accounting semantics. Do not blindly require this metadata
in the shared runtime parser: custom `ModelClient` adapters and existing
compatibility tests intentionally return complete messages without it.

Regression acceptance should cover both non-streaming modes and all three
invalid finish values. Assert rejection before assistant commit or tool effects,
retention of the original user input, release of the run guard, and successful
reuse of the same Agent after a subsequent valid response. Preserve successful
custom adapters that omit the field.

## Verification evidence

Fresh full-suite verification against the current workspace:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin tests -q
```

**822 passed in 21.97 seconds.** The log is
`/tmp/mas-single-agent-closeout-tests-20260924.log`. The new HTTP edge case above
is not covered by that passing suite.

The earlier [repeated acceptance report](agent-repeated-acceptance-2026-09-24.md)
records eight live-model mixed cycles, repeated CLI use, context compaction
with original-message recovery, 1,625 injected failures with subsequent
same-Agent recovery, and a separate 1,000-task mixed run. Those runs were not
repeated in this review. The subsequent
[compatibility-fix report](agent-compatibility-fixes-2026-09-24.md) covers the
latest cleanup corrections and a live CLI smoke test.

These results support the tested continuous-use scenarios. They do not imply
unbounded resource capacity, automatic crash recovery or exactly-once external
effects across process failure.

## Documentation and delivery follow-up

- `ROADMAP.md` still lists context compaction as future work and says tests
  were not run. Both statements describe an older stage.
- `docs/agent-runtime.md` still says streaming has no iteration-limit fallback;
  current code and tests cover it in all four execution modes.
- `docs/operations.md` points to an older report as the current test evidence.
  Link the current acceptance reports and distinguish offline regression tests
  from separate live-model checks.
- The workspace contains substantial uncommitted and untracked implementation,
  test and documentation changes. A reviewed, versioned baseline is still
  needed for handoff. Tracked `.vscode` browsing databases should be excluded
  from that baseline through a deliberate repository cleanup.

Package metadata, CI and distribution polish can be scheduled separately if a
public release is desired; they are not new functional requirements for this
local single-Agent milestone. Existing limits such as cooperative cancellation
of synchronous code, finite archive quotas and explicit snapshot save/load
remain documented operational boundaries.
