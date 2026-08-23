Functional Modules
==================

This document maps the current MAS codebase into functional modules. It is
intended for contributors who need to find the right boundary before changing
runtime behavior.

Entrypoints
-----------

- `main.py`: CLI adapter. It owns argument parsing, terminal printing, command
  routing, and streaming display.
- `web_server.py`: local Web adapter. It owns HTTP endpoints, SSE events,
  request locking, logging, and static file serving.
- `web/`: browser UI assets for the local group chat shell.

Entrypoints should stay thin. Shared construction and runtime operations belong
in the application layer.

Application Layer
-----------------

- `application/config.py`: typed runtime configuration loaded from environment
  values and entrypoint options.
- `application/builders.py`: composition functions that build agents, groups,
  LLM clients, prompt builders, tools, audit sinks, and dispatch policies.
- `application/agent_service.py`: single-agent service API used by the CLI.
- `application/group_service.py`: group service API used by CLI and Web.
- `application/member_config_service.py`: member config loading, merging, and
  `/addmember` parsing.
- `application/options.py`: entrypoint option dataclasses.

The application layer may depend on core runtime and observability. Core
runtime must not depend on application code.

Single-Agent Runtime
--------------------

- `core/agent.py`: public `Agent` facade.
- `core/agent_runtime/`: ReAct loop, response parsing, tool execution adapter,
  run timeout state, and narrow runtime adapter.
- `core/session.py`: active/history message storage and pruning.
- `core/llm.py`: public LLM client facade.
- `core/llm_runtime/`: request payload construction, provider config, SSE
  parsing, usage extraction, and transport helpers.

The group runtime treats each member as a normal `Agent`; it should not depend
on private agent runtime implementation details.

Group Runtime
-------------

- `core/group.py`: public `GroupChat` facade.
- `domain/group.py`: group member, group message, and turn domain types.
- `domain/group_memory.py`: shared group memory sections.
- `domain/group_events.py`: in-memory and optional JSONL event log.
- `domain/group_stats.py`: dispatch and message counters.
- `core/group_runtime/`: message store, member store, context builder, dispatch
  loop, run state, member lifecycle, message/memory side-effect services, and
  synthetic PASS service.
- `core/group_policy/`: dispatch policy protocol, default policy, on-demand
  policy, broadcast-feedback policy, registry, and rule helpers.
- `core/group_turns.py`: member turn execution and stale response handling.

The group design intentionally avoids hard task locks. Coordination is expressed
through group tools, memory, directed messages, broadcasts, reports, and PASS.
See [Group Dispatch](dispatch.md) for the full scheduling model.

Group Tools
-----------

- `core/group_tool_specs.py`: group tool schema declarations.
- `core/group_tools.py`: attach/detach and registration order.
- `core/group_tools_runtime/`: group tool handler implementation.

Important tool categories:

- read-only: `group_status`, `group_memory_get`
- memory-only: `group_memory_update`, `group_memory_append`,
  `group_memory_clear`
- propagating: `group_send`, `group_broadcast`, `group_direct`,
  `group_handoff`, `group_note_scope`, `group_done`, `group_report`,
  `group_decision`
- non-propagating: `group_pass`

Prompting
---------

- `core/system_builder.py`: public system prompt builder facade.
- `prompting/runtime.py`: prompt module store, dynamic prompt state, and final
  prompt assembly.
- `prompting/specs.py`: prompt module registry and drift checks.
- `prompting/skills.py`: `SKILL.md` discovery and frontmatter parsing.
- `prompting/tool_renderer.py`: tool list prompt rendering.
- `prompts/`: current prompt source files.

Group-only prompt modules are injected by group member lifecycle code and should
not appear in ordinary single-agent prompts.

Workspace Tools
---------------

- `tools/workspace_specs.py`: workspace tool specs and side-effect metadata.
- `tools/workspace_binding.py`: default workspace tool registration order.
- `tools/workspace_handlers.py`: binding entrypoint for existing handlers.
- `tools/file_ops.py`: file read/write/replace/list tools.
- `tools/builtin.py`: terminal command tool.
- `tools/registry.py`: tool registration, schema export, lookup, and direct
  invocation.
- `tools/runtime.py`: permission decision, audit recording, and tool result
  message construction.
- `tools/permissions.py`: permission metadata and policy.
- `tools/audit.py`: in-memory and optional JSONL audit log.

Tool permissions can be enforced, but the default mode is allow-all.

Observability
-------------

- `observability/views.py`: read-only views and CLI formatting for messages,
  members, usage, audit records, config reports, and debug snapshots.
- `core/usage.py`: LLM usage record model and aggregation.
- `logs/`: local runtime logs and optional JSONL outputs. Logs are ignored by
  git.

Observability must stay read-only. It should not trigger dispatch, mutate
memory, or call tools.

Tests
-----

- `tests/test_agent_architecture.py`: single-agent runtime boundaries.
- `tests/test_group_architecture.py`: group runtime boundaries and tool
  semantics.
- `tests/test_group_concurrency.py`: dispatch, async, synthetic PASS, and stale
  response behavior.
- `tests/test_group_evaluation.py`: higher-level group collaboration scenarios.
- `tests/test_target_architecture.py`: package boundaries, app services,
  prompt/tool architecture, and public compatibility checks.
- `tests/test_tool_safety.py`: workspace and tool safety behavior.
- `tests/test_llm_runtime.py`: LLM payload, stream, and usage behavior.
- `tests/test_session.py`: session pruning behavior.
- `tests/test_timeouts.py`: agent and group timeout behavior.

Contribution Rules
------------------

- Prefer changing the narrowest module that owns the behavior.
- Keep CLI/Web adapters thin.
- Do not make core runtime import application, Web, CLI, or observability.
- Do not change prompt text, tool schema, or dispatch semantics in a structural
  refactor unless the change is explicitly requested.
- If a tool output string changes, update tests deliberately; those strings are
  part of current behavior.
