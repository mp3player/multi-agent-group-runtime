# MAS — Agent and Group Foundation

A local Agent application with a CLI and Python API. The Agent owns its model,
conversation, tools and execution state. It supports OpenAI-compatible model
endpoints, workspace tools, streaming and explicit idle-session snapshots.

An independent [Group foundation](docs/group-runtime.md) and interactive Group CLI compose peer Agents
with durable public messages, explicit dispatch plans and execution settlement.
An optional `OnDemandStrategy` activates peers only for explicit requests; ordinary
posts and replies do not start another model run. Its worker binding supports declared
synchronous, read-only/memory-only pipelines; the standalone CLI stays available.

## Setup

Python 3.10+, uv and a POSIX system (Linux/macOS) are required for the local
application's archive and terminal facilities.

```sh
uv sync
# Only for a new installation without an existing .env:
cp .env.example .env
```

Set `BaseURL`, `BaseKey`, `BaseModel` and `MAS_CONTEXT_WINDOW` in `.env`.
Use the provider-documented context capacity for the selected model; there is
no guessed default. Set `MAS_WORKSPACE_ROOTS=.`
to limit file tools to the current project; the existing default is the user's
home directory. Skills are discovered under the project's `skills/` directory,
or the explicitly configured `MAS_SKILLS_DIR`.

## Run

Start the standalone Agent:

```sh
uv run python -m cli.agent
# Equivalent entrypoint:
uv run python main.py
# One task, then exit:
uv run python -m cli.agent --no-stream --prompt "List the workspace files"
```

`--max-turns N` and `--no-tools` override configuration when supplied.
`--no-stream` selects non-streaming output.

| Command | Action |
| --- | --- |
| `/save PATH`, `/load PATH` | Save or load an idle conversation |
| `/new`, `/clear` | Start a session with configured limits and current prompt |
| `/history` | Show history and active-context counts |
| `/context` | Inspect capacity, estimated token budget, compactions and archive space |
| `/compact [FOCUS]` | Summarize older context while idle, optionally emphasizing a focus |
| `/tools`, `/stream` | List tools or toggle streaming |
| `/usage`, `/usage-clear` | Inspect or clear recorded provider usage |
| `/audit [N]` | Inspect recent tool audit records |
| `/help`, `/exit`, `/quit` | Show help or exit |

Start a real-model Group conversation using the same `.env`:

```sh
uv run python -m cli.group
# One question, then exit:
uv run python -m cli.group --prompt "How many members are in this group?"
# Choose equal peers:
uv run python -m cli.group --members researcher reviewer
```

The default Group has three Agent members: `alice`, `bob`, and `carol`.
Ordinary text requests one available member; `@bob TEXT` requests Bob, and
`@all TEXT` requests every member. Members can request each other's help using
the existing collaboration tools. Public messages appear with their sender names.
Use `/members`, `/history`, `/status`, `/finish`, `/cancel`, and `/exit` to inspect
and control the discussion. `/post TEXT` adds background without activating a member.
The standalone `cli.agent` and `main.py` entrypoints still start one Agent.

This first Group CLI enables conversation and context archive tools. Workspace
mutation tools are outside the current Group execution profile. Every process
uses fresh private sessions and a fresh SQLite store; `--store` must name a new
path. See [Group CLI usage and limits](docs/group-runtime.md#interactive-group-cli).

## Python API

```python
import asyncio
from application import AgentAppConfig, AgentAppService

async def main():
    async with AgentAppService(AgentAppConfig.from_env()) as app:
        print(await app.arun("List the workspace files"))
        app.save_session("session.json")
        app.load_session("session.json")
        print(await app.arun("Summarize what you found"))

asyncio.run(main())
```

Long tasks use budgeted summaries and tool-result previews while preserving
replaced originals in private archives. The token estimate is conservative,
not exact model tokenization. `MAS_ACTIVE_MESSAGE_LIMIT` no longer discards old
messages. `read_context_archive` provides bounded historical pages even with
workspace tools disabled.

Snapshots preserve conversation data, tool links and archive references.
Keep the archive directory with the snapshot when moving or backing it up;
the default is `~/.local/state/mas/context`, with a 256 MiB quota. Loading uses
the current application prompt and never replays tools or running operations.
Provider streaming usage is collected when returned; enable
`MAS_LLM_STREAM_USAGE=1` only for endpoints supporting streamed usage requests.

Try the first Group strategy offline, using real Agents, tools and SQLite with
a deterministic provider:

```sh
uv run python -m examples.group_on_demand
```

Each run uses a fresh temporary SQLite store and prints its location for history
inspection. To select a location, pass `--store /path/to/new-group.sqlite` with
a path that does not exist. These examples create new Agent sessions; the SQLite
history alone does not restore prior private sessions, so existing stores are
rejected without modification.

Select `strategy=OnDemandStrategy()` in `GroupRuntime.create`, then call
`await group.drive(scope)` to run eligible requests. A `waiting` result means
the Group is quiet and still open; the application explicitly calls `finish`
after inspecting the results. See the [Group API](docs/group-runtime.md).

## Project structure

```text
application/   Configuration, construction and application lifecycle
cli/           Command-line interaction
core/          Agent API, runtime, model transport and sessions
group/         Peer collaboration, durable state and policy interfaces
models/        Messages and serialization types
prompting/     Prompt composition and skill discovery
prompts/       Single-agent prompt content
tools/         Workspace tools, permissions and audit
observability/ Usage and audit formatting
tests/         Single-Agent and Group foundation checks
examples/      Runnable Python API examples
docs/          Current architecture and operation notes
```

## Documentation

- [Runtime and extension boundaries](docs/agent-runtime.md)
- [Group foundation, limits and explicit execution](docs/group-runtime.md)
- [Configuration](docs/configuration.md)
- [Operations](docs/operations.md)
- [Module responsibilities](docs/modules.md)
- [Single-Agent closeout fixes and verification](docs/agent-closeout-fixes-2026-09-24.md)
- [Repeated long-task and process-use acceptance](docs/agent-repeated-acceptance-2026-09-24.md)
- [Refactor review and fixes](docs/review-2026-09-23.md)
- [Earlier test results and reproduction](docs/testing-2026-09-23.md)
- [Historical reliability audit](docs/reliability-2026-09-23.md)
- [Archived collaboration implementation](docs/archive.md)

The suite covers local HTTP/CLI integration, response and configuration
boundaries, resource cleanup, and 1000 mixed successful/failing runs through
the offline model transport. The automated suite does not use a live model
endpoint. Separate live-model and CLI acceptance runs are documented in the
[repeated acceptance report](docs/agent-repeated-acceptance-2026-09-24.md).
