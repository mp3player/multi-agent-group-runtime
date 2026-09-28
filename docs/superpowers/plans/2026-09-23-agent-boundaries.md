# Agent context and configuration boundaries

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve the current task during context pruning and keep constructed Agents independent of ambient configuration changes.

**Architecture:** Retain the shared turn machine. Prune active context at user-turn boundaries with a soft message limit; keep audit history separately bounded. Resolve environment at application startup, inject a complete tool runtime, and use explicit immutable workspace settings during execution.

**Tech Stack:** Python >=3.10, existing httpx/dotenv, pytest/pytest-asyncio. No new dependencies.

**Spec:** `docs/modules.md`, items 1, 2 and the tool injection part of item 4, approved by the user's request to implement the fixes.

## Global Constraints

- Work in `/tmp/mas-agent-worktree-20260923`; deliver verified files to `/home/coder/project/mas` without overwriting user changes.
- Group and Web remain archived. Keep existing model modes, serial tool execution, permissions, audits, snapshots and resource ownership.
- Do not read real credentials or call paid/live models. Disable dotenv and proxies for tests.
- This round does not implement prompt-wrapper consolidation, provider response redesign, streaming usage, or bounded tool-output capture.
- Test command prefix: `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy PYTHONPATH= PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 BaseURL=http://127.0.0.1:1/v1 BaseKey=agent-test-only BaseModel=fixture .venv/bin/python -m pytest -p pytest_asyncio.plugin`.

## Task 1: Preserve complete user turns

**Files:** `core/session.py`, `core/agent_runtime/runtime.py` (only `build_invoke_messages`), new `tests/test_context_pruning.py`; existing pruning tests only if intentional old-contract assertions need adjustment.

**Interfaces:** Keep `Session.prune_active(max_messages, keep_system=True)` signature. This task changes its behavior to a soft bound; `prune_history` stays a hard independent bound. No other runtime changes.

- [x] Add failing tests for a current user with two tool calls/results, consecutive tool rounds, older complete user turns, system/no-system, disabled limits, no-user transcripts and cancellation pairing. Use literal expected messages and real Session, plus ScriptedModel integration for the next request.

```python
session = Session()
session.add_many([System('rules'), User('task'), AI('working')])
session.prune_active(1)
assert [m.message for m in session.active] == ['rules', 'task', 'working']
```

- [x] Run the new tests against old code and record meaningful failures.
- [x] Partition non-system messages at user-role boundaries. Retain the newest complete user turn even when it exceeds the limit; include older complete turns only if the whole resulting suffix fits. Without any user message, retain the complete transcript rather than destroy a tool batch. Preserve existing system retention behavior. `keep_system=False` removes special protection, not arbitrary messages within a retained user turn.
- [x] Append the new user message before pruning in `build_invoke_messages`, so completed older turns can be evicted when a new request starts.
- [x] Run focused session/runtime tests. Update old tests only where they encode the intentionally replaced hard active-count contract. Do not weaken pairing, history or interruption assertions.
- [x] Commit only task-owned files; write a report with RED/GREEN commands, exact results and concerns.

## Task 2: Freeze configuration and inject tools

**Files:** `application/agent_config.py`, `agent_builder.py`; `core/llm.py`, `core/llm_runtime/config.py`, `core/agent.py`, `core/agent_runtime/runtime.py`, `tool_executor.py`; `prompting/runtime.py`, `core/system_builder.py`; new `tools/workspace.py`, `tools/runtime.py`, `file_ops.py`, `builtin.py`; configuration tests and affected documentation.

**Interfaces:** Add explicit workspace settings (roots, cwd, read-character default, terminal path-check override), and an optional preconfigured `ToolRuntime` injection to Agent/AgentRuntime. Preserve `agent.tool_executor.runtime` compatibility for now; registry access forwards to one ToolRuntime owner. `LLMClient` construction is explicit; expose `LLMClient.from_env()` for deliberate environment convenience. CLI continues to use `AgentAppConfig.from_env()`.

- [x] Write failing tests proving explicit empty model values do not inherit environment, explicit constructors do not load dotenv, prompt defaults ignore ambient skill paths, and existing Agents ignore subsequent changes to roots/read limits/terminal override.

```python
monkeypatch.setenv('BaseURL', 'http://127.0.0.1:1/v1')
monkeypatch.setenv('BaseKey', 'synthetic')
with pytest.raises(LLMError):
    build_agent(AgentAppConfig.from_mapping({}))
```

- [x] Observe failures before implementation. Use real temporary files and tool execution for isolation assertions; use stub models only to avoid remote requests.
- [x] Resolve environment through explicit entry points. LLM constructors use provided values only, with missing endpoint/key rejected. `from_env` uses application config lazily. Prompt defaults become package-local; application passes configured skills directory explicitly.
- [x] Move workspace context out of concrete file tools. Normalize roots and cwd once on runtime construction; default allowed roots are the user's home, default cwd is construction cwd. Explicit roots use their first root as cwd. Capture read limit and terminal override in the context, not process environment. Keep an explicit context manager for direct tool invocation and document migration from ambient direct-tool configuration.
- [x] Build registry, policy, audit sink and ToolRuntime before constructing Agent. Validate supplied registry agrees with injected runtime; retain guarded registry replacement, policy and audit behavior.
- [x] Run focused tests then full suite, review changes and update configuration/runtime/module docs to distinguish fixed items from deferred work. Verify Python 3.10 and user Python 3.14 after delivery.
- [x] Commit owned changes, verify source/root hashes and leave `.env`, archive and editor state intact.

## Verification and review

- [x] Task 1 spec/quality review before accepting its implementation.
- [x] Final independent code review of the complete changes; address verified findings.
- [x] Full test results and delivered file hashes recorded in the completion report.
