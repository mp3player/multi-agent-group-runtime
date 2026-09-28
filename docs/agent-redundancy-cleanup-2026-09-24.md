# Agent redundancy cleanup — September 24, 2026

Follow-up review found three customization regressions. The
[compatibility fixes](agent-compatibility-fixes-2026-09-24.md) restore public hook
dispatch and make the retained runtime adapter a view of a single owner.

This cleanup targets the single-agent implementation after the English source
update. It preserves the existing lossless context archive, shared turn machine,
sync/async execution drivers, lifecycle guards, and tool policy boundaries.

## Changes

- Task admission appends the user message without constructing an unused detached
  active-context view. Request preparation still isolates provider input from
  session originals. Unused runtime active-view/pruning hooks were removed.
- `AgentRuntime` owns `ToolRuntime` directly. Lazily created executor/parser
  adapters participate in execution so their public customizations remain effective.
  The bound executor forwards runtime access to the owner instead of retaining a
  separate writable reference.
- The legacy JSON message serializer now preserves tool outcome flags and Chunk
  usage/model metadata. AI/Chunk conversion is shared, as is tool-call encoding
  with the session codec. Strict snapshot/archive schemas and checksums are unchanged.
- One `SystemBuilder` owns prompt state and composition. Core re-exports it;
  redundant runtime/store/state forwarding was removed. Application and direct
  construction use the same default loader. The duplicated bundled `order.txt`
  was removed; an optional user-created order file still overrides defaults.
- Workspace registration uses one ordered callable-and-permission declaration.
  Dynamic imports, three unimplemented placeholder tools, and unused imports are gone.
- CLI startup honors `MAS_LOG_LEVEL`, writing console logs to stderr without
  creating files or replacing existing handlers. Embedded service construction
  does not configure logging. CLI usage/audit commands use observability views;
  audit serialization belongs to the record itself.

## Compatibility and migration

| Previous surface | Supported path |
| --- | --- |
| `core.system_builder.SystemBuilder` | Same import and primary methods; implementation lives in `prompting.runtime` |
| `PromptRuntime`, `PromptRuntimeError`, `.create()` | Aliases/factory retained |
| `PromptEnvironment`, `PromptModuleStore`, `PromptState`; nested `.runtime/.store/.state` | Removed; use the builder's `modules`, `order`, `skills`, and public methods |
| `WorkspaceToolSpec(name, effect, module, handler)` | Use `WorkspaceToolSpec(decorated_callable, effect)`; name/handler descriptor properties are derived |
| Mutation of `WORKSPACE_TOOL_SPECS` to affect binding | Unsupported; register custom tools explicitly or provide `handlers=` |
| `agent.tool_executor.runtime` | Retained as a read/write view of the owned runtime; replacement requires an idle Agent |
| `AgentResponseParser`, `AgentToolExecutor` | Lazily created execution adapters; their custom parsing/execution hooks remain effective |
| `JsonMessageSerde` files | Legacy `message` keys and text arguments remain readable; missing new fields use prior defaults |
| Session persistence | Continue using `JsonSessionStore`, which validates its versioned format |
| Audit presentation inputs | Serialized dictionaries or records exposing `to_dict()`; native records remain supported |
| `AgentServiceOptions/from_options`, LLM `get_config/load_env` | Retained compatibility entrypoints; prefer `AgentAppConfig` and `LLMClient.from_env` |

`active_message_limit` remains an accepted migration setting and never discards
messages. Permission `dry_run` remains a legacy reason label; `enforce` controls
blocking, and audit recording occurs in either mode. These retained compatibility
surfaces are deliberate, not additional active execution layers.
Public registry formatting/query helpers and `PromptModuleSpec` metadata also
remain for compatibility; removing an unused public method is a separate API
retirement decision.

The default prompt policy is explicit: an existing order file is authoritative;
otherwise load available common modules in their declared sequence, falling back
to sorted Markdown files only when no common modules exist. The strict
`load_default_from_specs()` API still requires the complete common bundle.

## Initial cleanup validation

New regressions first reproduced discarded context work, legacy metadata loss,
default-loader divergence, logger import filesystem effects, and ignored CLI log
levels. Existing composition tests now assert behavior rather than requiring
the removed forwarding structure.

| Check | Result |
| --- | --- |
| Full suite, Python 3.14.0rc2 | 801 passed in 20.37 seconds |
| Full suite, Python 3.10 | 800 passed, 1 skipped in 25.64 seconds |
| CLI against the current `.env` local model | Passed in 5.275 seconds; no failed tool calls |
| Independent read-only review | No critical or important findings |
| Python source language scan | No CJK text remaining |
| `git diff --check` | Passed |

The Python 3.10 skip is the existing cancellation-count test, which requires
`asyncio.Task.cancelling`. The CLI check covered session save/load, remembered
conversation state, all five workspace tools, file contents, history/context
commands, the compaction command, usage/audit, stream switching, reset, and exit.
This short CLI check does not independently prove hour/day uptime or forced
compaction under pressure; the regression suite covers those context boundaries.

The reviewer compared the before/after bundled prompt text, complete provider tool
schemas, tool order, and permissions: all matched. Against the pre-cleanup working
snapshot, 26 production Python files changed with a net reduction of 427 lines.
Existing uncommitted work was preserved, and `.env` was not changed.

Full-suite command:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin tests -q
```

Local verification artifacts:

- `/tmp/mas-redundancy-tests-20260924.log`
- `/tmp/mas-redundancy-tests-py310-20260924.log`
- `/tmp/mas-redundancy-live-cli-20260924/cli-result.json`
- `/tmp/mas-redundancy-change-inventory-20260924.json`
