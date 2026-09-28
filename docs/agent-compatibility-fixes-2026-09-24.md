# Compatibility fixes after the slimming review

The review found three regressions outside the default CLI flow. These fixes
preserve the slimmer prompt structure and restore the behavior of retained hooks.

## Runtime ownership and execution

Previously, assigning a new `agent.tool_executor.runtime` updated only the
compatibility adapter; actual execution kept the previous runtime. A configured
deny policy could therefore appear active while tools still ran.

`AgentRuntime` now holds the single runtime reference. Its bound compatibility
executor forwards reads and replacements to that owner. Updates through either
entrypoint are visible through the other, and execution uses the replacement's
permission policy, workspace, caller, and audit settings. Runtime replacement is
guarded against active runs. Archive retrieval is bound before a replacement
registry is published. Use `Agent.set_registry()` when changing tools so the
generated system prompt is refreshed as well.

Non-streaming responses again pass through the public response parser. Custom
normalization affects both returned and committed messages; validation errors
abort before an assistant message is committed. The tool execution and stop
hooks likewise participate in real execution. The adapters remain lazy and
the bound executor stores no independent runtime reference. Streaming response
assembly retains its existing separate accumulator.

## Prompt rendering

Private rendering helpers now forward to the public virtual methods instead of
aliasing fixed base implementations. Subclass and instance overrides of
`render_tools()` and `render_skills()` appear in the built prompt, while private
helper overrides remain effective. Default sections and ordering are unchanged.

## Regression coverage

- All four run modes: runtime replacement through either view honors the new
  deny policy, prevents effects, and records the replacement caller in audit.
- Both non-streaming modes: custom parsing changes committed output, and custom
  validation failures are propagated with the run released.
- Runtime replacement during a run is rejected through both entrypoints.
- All four run modes: custom tool validation prevents effects and leaves pending
  tool requests balanced on error.
- Final prompt output preserves public subclass/instance overrides, default
  sections, and private helper overrides.

Before implementation, all 18 runtime regressions failed as expected; two prompt
regressions failed and one private-hook control already passed. The combined
focused suite passed 134 tests after the fixes.

## Final verification

| Check | Result |
| --- | --- |
| Full suite, Python 3.14.0rc2 | 822 passed in 22.16 seconds |
| Full suite, Python 3.10 | 821 passed, 1 skipped in 25.79 seconds |
| Original runtime and prompt review probes | Exact match with pre-cleanup results |
| Current `.env` local-model CLI check | Passed in 4.751 seconds; all five workspace tools succeeded |
| Independent read-only review | No serious findings |
| `git diff --check` | Passed |

The Python 3.10 skip is the existing cancellation-count test requiring
`asyncio.Task.cancelling`. The CLI check includes save/load, conversation
continuity, streaming, context/compaction commands, usage/audit, reset, and exit.
This is a functional regression check, not an hour/day uptime claim.

Project source and tests remain English. Existing uncommitted work was preserved;
the model configuration in `.env` was not modified. No commit or push was made.

Local evidence:

- `/tmp/mas-compat-runtime-red-20260924.log`
- `/tmp/mas-compat-fixes-tests-20260924.log`
- `/tmp/mas-compat-fixes-tests-py310-20260924.log`
- `/tmp/mas-compat-fixed-runtime-probe-20260924.json`
- `/tmp/mas-compat-fixed-prompt-probe-20260924.json`
- `/tmp/mas-compat-fixes-live-cli-20260924/cli-result.json`
