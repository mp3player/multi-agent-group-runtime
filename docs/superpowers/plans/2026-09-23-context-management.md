# Context Management Implementation Plan

> **For agentic workers:** Use test-driven-development for each task. Independent storage, budget and transport work uses dispatching-parallel-agents with disjoint file ownership; integration and reviews are sequential.

**Goal:** Continue long single-Agent tasks through budgeted summarization while preserving replaced messages in readable archives; never discard messages to fit a context window.

**Architecture:** One shared turn policy prepares each exact model request, optionally yields non-streaming summary requests, validates detached candidates and atomically publishes the working view. Immutable archives and versioned session snapshots preserve originals. Application composition supplies capacity, archive paths and lifecycle ownership.

**Tech Stack:** Python >=3.10, existing httpx/python-dotenv, stdlib filesystem/JSON, pytest/pytest-asyncio. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-23-context-management-design.md`

## Global Constraints

- No message dropping, legacy pruning fallback, automatic clear, or tool replay.
- Unknown model capacity blocks inference with actionable configuration errors.
- Summary calls have their own complete-input/output budget and at most four calls per compaction.
- Preserve latest user request and complete assistant/tool batches; archive before preview/summary replacement.
- Failed/cancelled/stale candidates never publish; completed tool effects remain recorded.
- Python >=3.10; core must not read environment; no new runtime dependencies.
- Do not edit `.env`, editor data, Group archive, or unrelated user changes.
- Worktree `/tmp/mas-agent-worktree-20260923`; deliver to `/home/coder/project/mas` only after baseline hash checks.

## Validation command

`env PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin ...`

The explicit plugin selection prevents unrelated ROS plugins from entering the project test process. No live paid provider calls are necessary for deterministic acceptance.

## Task 1: Session identity, immutable archives, v2 snapshots

Files: `core/session.py`, `core/session_store.py`, new `core/context_archive.py`, optional `core/session_codec.py`; storage/session tests.

Interfaces consumed by integration:

```python
@dataclass(frozen=True)
class SessionEntry:
    entry_id: str
    message: Message

class Session:
    session_id: str
    revision: int
    checkpoint: dict | None
    overrides: dict[str, dict]  # preview, archive_id
    archive_store: FileContextArchive | None
    def context_entries(self) -> list[SessionEntry]: ...  # detached active originals
    def working_messages(self) -> list[Message]: ...  # summary + preview projection
    def archive_entries(self, entries: list[SessionEntry], *, reason: str) -> str: ...
    def commit_context(self, *, expected_revision: int, retained_ids: list[str],
                       summary: str | None, archive_id: str,
                       overrides: dict[str, dict]) -> dict: ...
    def maintain_history(self) -> None: ...  # only at safe boundary
    def validate_archives(self) -> None: ...
    def replace_system(self, text: str | None) -> None: ...
    def reset_to_system(self) -> None: ...  # explicit user operation

class FileContextArchive:
    def __init__(self, root: str | Path, *, quota_bytes: int = 268435456): ...
    def read(self, session_id: str, archive_id: str, *, offset: int = 0,
             limit: int = 12000) -> dict: ...
```

Checkpoint contains `id`, `summary`, `archive_id`, `parent_id`, newly `covered_ids`, `retained_ids`. Archive payload stores exact entry IDs, full encoded messages and parent references. Preview-only commit passes `summary=None` to retain previous summary. Override keys must refer to retained tool results. Snapshots are strictly validated v2, with v1 migration preserving ambiguity as distinct imported IDs. Loading never executes tools. Session can exist without store but cannot evict its last raw copy or compact without one.

- [x] Write/run red tests: failed archive write preserves active/history; stale revision cannot commit; originals survive repeated compaction and snapshot roundtrip; missing attachment blocks validation; cache eviction preserves last copy; v1 duplicate messages remain distinct when correspondence ambiguous.
- [x] Implement archive storage with opaque IDs, private permissions, quota (including temporaries), atomic writes and bounded reads; no successful archive automatic GC.
- [x] Implement controlled identity/revision mutations, safe working projection, cache maintenance and strict snapshot migration.
- [x] Run storage tests and inspect the exact diff before integration.

## Task 2: Complete-request budgets and bounded summary inputs

Files: new `core/agent_runtime/context_management/budget.py`, `errors.py`, minimal package `__init__.py`, `tests/test_context_budget.py`.

Interfaces:

```python
@dataclass(frozen=True)
class ModelBudget:
    context_window: int | None = None
    input_limit: int | None = None
    safety_ratio: float = 0.10
    minimum_reserve: int = 512
    def input_budget(self, output_tokens: int, *, extra_reserve: int = 0) -> int: ...

@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str

class TokenCounter:
    def estimate(self, messages: list[Message], tools: list[dict] | None = None) -> TokenEstimate: ...
    def text_tokens(self, text: str) -> int: ...
    def truncate(self, text: str, tokens: int) -> str: ...
```

Count provider-visible `to_dict_list(messages)`, tool schemas, arguments, UTF-8 text and framing. Default is a conservative byte-based estimate (explicitly not exact tokenization), not characters/4; optional injected counters can override it. Exceptions: `ContextManagementError`, `ContextCapacityUnknown`, `InputTooLarge`, `CompactionError`. Pure budget has no session/model I/O dependencies.

- [x] Write/run red tests: unknown capacity rejects; output+reserve deducted; tools and non-ASCII count; reasoning not retransmitted; invalid/zero budgets reject; truncation preserves UTF-8 text and respects budget.
- [x] Implement strict validated budget/counter and typed errors.
- [x] Run budget tests with explicit literal expectations and review.

## Task 3: Provider overflow and four-mode usage

Files: `core/llm.py`, `core/llm_runtime/{errors,sse,__init__}.py`, `models/message.py`; provider/usage tests.

Interfaces:

```python
class ContextWindowExceeded(LLMError):
    pass
# Chunk gains optional usage: dict | None, response_model: str | None.
# LLMClient gains stream_usage: bool = False.
```

Use known structured provider overflow codes in HTTP and SSE. Generic 400/413, `length`, auth/filter/rate/server errors do not become overflow. Usage-only streaming frames are retained and final usage recorded exactly once, including on late validation errors where known; no global mutable last_usage. Nonstream responses keep their existing dictionary shape. stream_options only sent when stream_usage explicitly enabled.

- [x] Write/run red tests for typed HTTP/SSE overflow, negative classifications, duplicate/missing/malformed usage, supported opt-in, billing on validation failures, and effective model identity.
- [x] Implement transport normalization and streaming usage metadata.
- [x] Update tests that encoded absent streaming usage; run focused transport tests and review.

## Task 4: Shared context preparation and transactional compaction

Files: new context_management `policy.py`, `manager.py`; `runtime.py`, `turn_machine.py`, `react_loop.py`, `ports.py`, `events.py`, `options.py`, `core/agent.py`; new `tests/test_context_execution.py`.

Exact flow: prepare transformed detached projection + schemas → budget check → archive-backed preview if needed → plan complete old units → yield CompactionRequest → bounded summary candidates → validate no tools/nonempty/finished/size/reduction → archive originals → check owner/revision/deadline → commit → recount and send identical request snapshot.

`AgentOptions` adds `context_window: int | None`, `context_input_limit: int | None`; injected model `context_window` metadata may supply capacity. No default guessed capacity. Agent constructor accepts an archive store (or preconfigured Session) and injected token counter. Parent owns these runtime integration contracts.

- [x] Red four-mode tests require repeated compaction in one user task, preserved original goal/archive, valid tool pairs, no tool replay, cancellation rollback, unknown capacity stop, soft failure handling and no infinite compression.
- [x] Implement pure complete-unit planning retaining latest user anchor; final request uses transformed snapshot counted once. Summary is historical assistant content, never promoted to system authority.
- [x] Bound each summary input/output, segmentation and final merge to four calls; no partial checkpoint commit.
- [x] Add one typed overflow recovery through generator.throw; distinguish main vs summary; never silently retry after visible streamed output.
- [x] Add controlled manual compact/acompact using same run owner/deadline; events and context status.
- [x] Run focused integration tests and fix behavior before broad regression.

## Task 5: Application, CLI, migration and documentation

Files: `application/{agent_config,agent_builder,agent_service}.py`, `cli/agent.py`, `.env.example`, README and configuration/runtime/operations docs; application/CLI tests and shared test fakes.

- [x] Red tests for explicit `MAS_CONTEXT_WINDOW`, optional `MAS_CONTEXT_INPUT_LIMIT`, private `MAS_CONTEXT_ARCHIVE_DIR`, positive `MAS_CONTEXT_ARCHIVE_QUOTA_BYTES`, `MAS_LLM_STREAM_USAGE`; environment remains application-owned.
- [x] Compose archive store using an application state-directory default; bind read_context_archive as a read-only bounded tool even without workspace tools. Reserve its name against accidental registry replacement.
- [x] Wire /context and asynchronous /compact, compact events on stderr, save/load/new lifecycle and missing archives; do not nest asyncio.run.
- [x] Migrate tests to explicitly declare fake model capacities, and replace old pruning expectations with preservation expectations. Do not add production test-only bypasses.
- [x] Document required capacity and old config migration without reading/changing `.env`; describe approximation, archive quota and provider support limitations.

## Task 6: Review, acceptance and delivery

- [x] Run whole Python 3.10 suite and Python 3.14 suite in clean plugin environments, plus deterministic long-task traces across multiple compactions and all modes.
- [x] Record below-threshold network overhead and bounded-resource behavior using deterministic fakes; no claims of live-model quality/cost savings without measurement.
- [x] Independent whole-change code review; fix critical/important findings with regression tests.
- [x] Verify diff and baseline hashes, commit only task changes in isolated worktree, copy changed/new files into user workspace without overwriting drift; preserve `.env` and editor data.
- [x] Report implemented behavior, actual checks and material limitations.

## Progress

- Tasks 1–5 implemented and integrated. Python 3.10: 670 passed, 1 cancellation-count capability skip; Python 3.14: 671 passed.
- Thousand-run offline trace passed across four modes with repeated compaction and injected provider faults.
- Independent review findings fixed: cancellable archive I/O with detached prepare/publish, whole-unit summary segmentation, and incomplete usage reporting. Both independent re-reviews passed. All 56 changed/new files delivered after baseline hash verification; user workspace full suite: 671 passed. Existing .env, editor data, and Group archive preserved.
