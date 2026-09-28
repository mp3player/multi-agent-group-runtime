# Configuration

`AgentAppConfig.from_env()` loads the project's `.env` without overwriting
existing process variables. `from_mapping()` parses an explicit mapping.
Explicit CLI overrides take precedence over parsed configuration; defaults
come from `core/defaults.py`.

Environment is read only by the application configuration entrypoint. Explicit
configuration and component constructors do not fall back to process variables.
In particular, `build_agent(AgentAppConfig())` needs an injected model or explicit
endpoint/key settings; it no longer silently borrows credentials from `.env`.
`LLMClient.from_env()` is the named convenience factory for direct model use.
`SystemBuilder` accepts `skills_dir=`; its default is the package-local directory.

| Variable | Default / purpose |
| --- | --- |
| `BaseURL`, `BaseKey`, `BaseModel` | Model endpoint, credential and model name |
| `MAS_LLM_TIMEOUT` | `120` seconds per provider request |
| `MAS_LLM_STREAM_USAGE` | `0`; opt in to `stream_options.include_usage` when the endpoint supports it |
| `MAS_LLM_TRUST_ENV` | `1`; HTTPX uses proxy and certificate environment settings. `0` disables that inheritance for model requests |
| `MAS_LLM_STREAM_COMPATIBILITY` | `standard`; optional `vllm_gemma4` workaround for the verified terminal SSE marker leak |
| `MAS_CONTEXT_WINDOW` | Required positive token capacity, unless an injected model supplies trusted `context_window` metadata |
| `MAS_CONTEXT_INPUT_LIMIT` | Optional positive provider input cap |
| `MAS_CONTEXT_ARCHIVE_DIR` | `~/.local/state/mas/context`; private archive storage supplied by the application |
| `MAS_CONTEXT_ARCHIVE_QUOTA_BYTES` | `268435456` (256 MiB), positive total archive quota |
| `MAS_DEFAULT_MAX_TOKENS` | `4096` |
| `MAS_DEFAULT_TEMPERATURE` | `0.7` |
| `MAS_DEFAULT_MAX_TURNS` | `20` model/tool turns |
| `MAS_ACTIVE_MESSAGE_LIMIT` | Accepted for migration; no longer trims active messages |
| `MAS_HISTORY_MESSAGE_LIMIT` | `1000` cached history messages; evicted originals are archived |
| `MAS_AGENT_RUN_TIMEOUT` | `0`, no overall deadline |
| `MAS_ENABLE_TOOLS` | `1`, enable workspace tools |
| `MAS_WORKSPACE_ROOTS` | Current user's home directory; `.` selects current directory |
| `MAS_READ_FILE_MAX_CHARS` | `12000` body characters per file read; hard cap 65536 |
| `MAS_ALLOW_UNSAFE_TERMINAL` | `0`; explicitly bypass obvious terminal path checks in trusted local use |
| `MAS_SKILLS_DIR` | Project-local `skills/`; legacy `SkillsDir` is also accepted |
| `MAS_PROMPTS_DIR` | Project-local `prompts/`, independent of process working directory |
| `MAS_PROMPT_ORDER_FILE` | Optional `order.txt` override, relative to the prompt directory unless absolute |
| `MAS_USAGE_ENABLED` | `1` |
| `MAS_LOG_LEVEL` | `INFO`; CLI console log level on stderr |
| `MAS_TOOL_PERMISSION_DRY_RUN` | `0`; legacy audit reason label, does not control enforcement |
| `MAS_TOOL_PERMISSION_ENFORCE` | `0`, enable to enforce deny/approval decisions |
| `MAS_TOOL_PERMISSION_RULES` | Empty, e.g. `external_effect=approval,workspace_mutating=deny` |
| `MAS_TOOL_AUDIT_JSONL` | Empty, optional audit output path |

Separate multiple workspace roots with the platform path separator (`:` on
Linux/macOS). When roots are explicitly configured, relative file paths and
terminal `cwd` resolve against the first root; use absolute paths for other
roots. Roots are resolved when assigned to a tool runtime. With no explicit
roots, relative paths use the process working directory captured at runtime
construction, and allowed file bounds retain the home-directory default.
The legacy `WorkspaceRoots` name is accepted if `MAS_WORKSPACE_ROOTS` is absent.
An explicitly empty `MAS_WORKSPACE_ROOTS` selects the default instead of the legacy value.
Roots, working directory, read-character limit and terminal path-check override
are captured in a `Workspace` when the tool runtime is constructed. Later changes
to process environment or cwd do not alter an existing Agent's settings.
An explicitly configured relative `MAS_PROMPTS_DIR` remains relative to the
process working directory. Terminal execution is a process capability; workspace file
bounds are not an operating-system sandbox.

`MAS_LLM_TRUST_ENV=0` applies to synchronous and asynchronous model requests,
including streaming. It does not modify process-wide proxy variables or other
tools' network settings. HTTPX also ignores `SSL_CERT_FILE`/`SSL_CERT_DIR` in this
mode; its normal certificate verification remains enabled.

The `vllm_gemma4` compatibility mode is an explicit workaround for a reproduced
Gemma 4 / vLLM response defect. It removes only an entire `delta.content` equal
to `<turn|>` in a frame that also reports `finish_reason=stop`, integer
`stop_reason=106`, a `google/gemma-4-` model name, a `vllm-` system fingerprint,
and no tool-call delta. Text containing that marker in ordinary frames is
preserved, as are reasoning, finish status and usage metadata. Non-streaming
responses are unchanged. No provider/model inference enables this mode
automatically; other profiles are rejected. A differently shaped server
response requires separate validation, rather than broader text stripping.

Both `SystemBuilder.load_default()` and application assembly use the same prompt
loader. An existing order file is authoritative (including an empty file);
missing modules named there are errors. Without an order file, available common
modules load in the sequence declared in `prompting/specs.py`. If none are present,
all Markdown files load in filename order. The bundled duplicate `order.txt` has
been removed; create one to override the sequence. `load_default_from_specs()`
remains an explicit strict operation requiring every common module.

`MAS_DEFAULT_MAX_TURNS` limits model/tool turns in a single run; summary requests
do not consume those turns. Keep it finite and raise it explicitly for long
tasks (for example, 150). Reaching the limit reports `run_end.status=max_turns`
and stores a final notice in all four execution modes; streaming also displays
the notice. Completed tool calls are not repeated automatically.

Set `MAS_CONTEXT_WINDOW` to the selected provider model's documented capacity.
An unknown capacity prevents new inference while keeping session inspection and
save operations available. Blank capacity/input-limit values mean unspecified;
zero, negative or non-integer capacities and quotas are rejected. There is no
model-name lookup or default window. The input allowance subtracts configured
output tokens and a safety reserve from the window, then applies the optional
input cap. Small windows may also require a lower `MAS_DEFAULT_MAX_TOKENS`.

The default counter estimates serialized UTF-8 messages, tool schemas and framing
conservatively; it is not an exact tokenizer. Embeddings can inject a model-specific
`token_counter` into `Agent`. Budgeted compaction retains the latest user request
and complete tool batches, archiving originals before publishing summaries or
previews. `MAS_ACTIVE_MESSAGE_LIMIT` no longer deletes context; `/context` includes
a migration notice. A positive history limit bounds the in-memory history cache,
with originals archived before its last copy can be removed; nonpositive values
leave that cache unbounded.

The current archive implementation requires POSIX `flock`, `O_NOFOLLOW` and
`O_DIRECTORY`; the standard application therefore requires Linux/macOS with
these filesystem capabilities, including when workspace tools are disabled.
Archive initialization reads and syncs the canonical ancestor directories of
its root. Those directories must allow opening and directory `fsync`; failures
abort initialization. Publication syncs both the session directory and archive
root before a checkpoint can refer to the attachment.
Archive directories are private (0700); immutable published attachments are
0400. The quota includes retained files and pending writes. No successful
archive is automatically deleted. Quota exhaustion fails required compaction
without discarding context. The archive root is independent of workspace file
roots. `read_context_archive` is a reserved read-only tool with pages capped at
12000 content characters; it remains available with `MAS_ENABLE_TOOLS=0`.
The archive-space display is an estimate during concurrent writes; a temporary
file disappearing during that scan is normal and does not fail `/context`.
Full ancestry checks retain a bounded metadata cache (4096 records / 4 MiB of
accounted metadata). A scan that exceeds capacity validates uncached files
without evicting reusable metadata; ordinary loads still use LRU replacement.
Every referenced file is checked. Validation and quota scans still grow with
archive count; this cache does not provide constant-time validation.

Positive overall deadlines remain cooperative for synchronous tools. An
`approval` policy result is blocked when enforced; this CLI does not implement
an interactive approval exchange. A tool's `requires_approval=True` metadata
also requests approval; a category `allow` rule does not override it, and
`deny` takes precedence. Without enforcement, these decisions are recorded
without blocking execution.
Auditing runs in both modes. The legacy `dry_run` setting only labels
non-enforced decisions; it never disables `MAS_TOOL_PERMISSION_ENFORCE`.

`AgentOptions` is immutable and validates turn/token limits and finite numeric
settings. The CLI applies `LoggingConfig.level` before constructing the service,
logs to stderr, and neither creates a log directory nor replaces existing handlers.
Embedding `AgentAppService` does not configure logging; the host owns its handlers.

Direct file/terminal calls also use explicit settings. Replace ambient environment
configuration with a workspace scope:

```python
from tools import read_file
from tools.workspace import Workspace, workspace_context

workspace = Workspace(roots=(".",), read_file_max_chars=12000)
with workspace_context(workspace):
    text = read_file("README.md")
```

For registered tools, pass `workspace=workspace` to `ToolRuntime` and inject that
runtime with `Agent(model, tool_runtime=runtime)`. A simultaneously supplied
`registry` must be the same registry. `agent.tool_executor.runtime` remains
available for existing callers, but application assembly now uses constructor
injection. Registry replacement retains workspace, permission policy and audit sink.
The compatibility executor and `agent.runtime.tool_runtime` address the same
runtime, including after replacement. Runtime replacement is allowed only while
idle. When changing the tool set, use `agent.set_registry(...)` to synchronize
the registry and generated system prompt together.

Built-in tools enforce finite resource budgets independently of model context:

| Boundary | Limit |
| --- | --- |
| Terminal capture | 80000 retained bytes per pipe, while continuing to drain both |
| Terminal returned output | 20000 content characters plus status markers |
| File read | 1000 lines and 65536 body characters plus continuation hint |
| File page lookup | 64 Mi decoded characters scanned per call, in 8192-character chunks |
| Whole-file write / replace | 8 MiB UTF-8 input and resulting content; append limits each supplied payload |
| Directory listing | 1000 entries / 65536 characters; larger listings fail explicitly |
| Tool result retained by runtime | 66560 characters including truncation marker; invalid UTF-8 text escaped |
| Audit / usage recent records | 1000 each; lifetime usage totals remain independent |

`read_file(max_chars=0)` selects the workspace setting; a workspace value of
zero still uses the hard cap. Negative per-call values fail instead of disabling
the limit. `char_offset` supports continuing within a long line.
For embedded use, `ToolRuntime(max_result_chars=...)` accepts integers at least
64; `ToolAuditLog(max_records=...)` and `UsageMonitor(max_records=...)` accept
nonnegative integers. Zero keeps no recent records but still writes audit sinks
and accumulates usage totals. Usage totals grow with distinct labels; the
standard application uses a stable label per model instance. These constructor
options do not add new environment variables.

The active application has no member configuration file. Historical settings
and examples are preserved in the [separate archive](archive.md).
