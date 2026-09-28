# Agent redundancy cleanup

Approved scope: resolve the redundancy review without changing the single-agent
product boundary, lossless context retention, or tool enforcement policy.

1. Remove discarded request-admission context copies and unused runtime hooks.
   Invoke parsing and ToolRuntime directly; retain public compatibility adapters.
2. Repair legacy message serialization roundtrips and share small encoding
   primitives. Preserve strict session/archive schemas and validation.
3. Consolidate prompt state and default loading; keep the SystemBuilder API.
4. Register workspace tools from one ordered callable declaration. Remove
   unimplemented, unregistered placeholders and unused imports.
5. Configure CLI logging explicitly and use shared observability presentation.
   Document deprecated options and leave embedding logging host-controlled.
6. Run focused regression tests, the complete suite, and a local-model CLI smoke
   using the existing .env without changing or exposing it. Review the final diff.

All source text remains English. Preserve preexisting workspace changes; no
commit, push, frontend, group runtime, new persistence format, or message trimming.

Compatibility policy: preserve actively used and explicitly exported entrypoints
where a small adapter suffices. Internal forwarding/state objects may be removed;
tests should cover behavior instead of requiring their previous layout. Prefer
AgentAppConfig, LLMClient.from_env, JsonSessionStore, and ToolRuntime for new code.
