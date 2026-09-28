# Single-Agent focus

The active project is one local Agent application. Current responsibilities are
model requests, sequential tool execution, conversation state, prompt/skill
composition, resource ownership and a CLI.

Implemented structure:

- `AgentRuntime` owns execution dependencies and state.
- One turn machine defines sync/async and streaming/non-streaming behavior.
- `AgentAppConfig`, `build_agent` and `AgentAppService` compose the application.
- Versioned JSON files save and restore idle conversations without tool replay.
- Workspace tools expose permission decisions and audit records.
- Token budgets trigger context compaction while preserving replaced originals
  in private archives accessible through `read_context_archive`.
- Deadlines, cancellation, provider failures and tool failures release owned
  resources and allow subsequent tasks within the documented runtime limits.
- Bounded in-memory usage, audit and archive caches support continued process use.

The single-Agent milestone targets the local Python API and CLI on POSIX
systems. Its acceptance evidence includes automated regression checks and
separate live-model runs covering long tasks and repeated process use. See the
[closeout fixes](docs/agent-closeout-fixes-2026-09-24.md) and
[repeated acceptance report](docs/agent-repeated-acceptance-2026-09-24.md).

Future feature priorities remain undecided. Group collaboration will be
designed separately on top of the single-Agent foundation; no extension or
plugin mechanism has been selected. Parallel tools, richer interfaces and
persistent execution recovery also require separate design decisions.

The previous collaboration implementation and its roadmap live outside this
project; see [archive location](docs/archive.md).
