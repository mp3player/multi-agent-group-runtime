# Interactive Group CLI acceptance

Date: 2026-09-28

## Implementation

The previous interactive entrypoint created a standalone Agent. It could not
exercise Group membership or collaboration, even though separate Group API tests
already existed. `python -m cli.group` now provides a real-model Group entrypoint
using the current `.env`. `cli.agent` and `main.py` remain independent.

`application.group_chat.GroupChat` owns three configurable peer Agents, separate
sessions and registries, the existing on-demand runtime, and model-client cleanup.
Identity and collaboration guidance are persistent system extensions. The CLI
supports ordinary requests, explicit recipients, broadcasts, passive background,
membership/history/status queries, explicit completion and cancellation. It shows
committed public messages with sender names and follows peer-created requests.
It does not add a scheduling strategy or business-result validator.

Each launch retains a fresh public SQLite database and refuses existing paths.
Private sessions persist across discussions within the process; the public
database alone does not restore them after exit. The current execution profile
enables conversation and context archive tools, excluding workspace mutations.
See [usage and limits](../../group-runtime.md#interactive-group-cli).

## Review corrections

Regression tests first reproduced and then verified fixes for display failure
leaving an owned driver active, fatal errors returning to the input loop, and
failed cancellation returning to an already closed chat. Error paths now settle
execution or close the owned application before propagating failure.

Real subprocess tests reproduced an idle Ctrl+C hang caused by synchronous
`input()`. Terminal input now waits through the event loop and removes its reader
on cancellation. Coverage includes active HTTP cancellation, idle/partial input,
pipe and file EOF, Unicode without a final newline, and `/dev/null` EOF.
Independent review found no remaining issue in the final input/lifecycle changes.

## Verification

Focused CLI and process suite: **28 passed**. It uses deterministic providers
through both the actual Agent/Group runtime and a local HTTP server. It verifies
routing, passive reception, peer handoff, system/tool binding, pagination,
provider failure, scope rollover, resource ownership and process exit behavior.

Final offline regression: **1,158 passed, 12 skipped**, exit 0, in 44.35 seconds:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p pytest_asyncio.plugin -q
```

The skipped tests require explicit live-provider switches. Logs are at
`/tmp/mas-group-cli-focused.log` and `/tmp/mas-group-cli-full-final.log`.

Separate real-model checks used the user's existing `.env` without modifying it:

- One-shot membership question: returned three members, alice, bob and carol;
  one settled member run and exit 0.
- Piped interactive conversation: greeting, membership query, Bob requesting
  Carol's independent confirmation, and a three-member broadcast; seven settled
  runs, explicit completion and exit 0.
- Actual terminal interaction after the input changes: membership query,
  Bob-to-Carol handoff and broadcast; six settled runs, explicit completion and
  exit 0. No active or pending work remained.

The one-shot command closes its quiet discussion on exit; its stored cancellation
reason is cleanup, not a claim of business completion. Both interactive runs
used `/finish` and their stored reason is `completed`. Evidence is retained in
`/tmp/mas-group-cli-live-results.json`, with the database paths and public replies.
These checks validate the new entrypoint; they do not reclassify earlier model
reasoning failures as resolved.
