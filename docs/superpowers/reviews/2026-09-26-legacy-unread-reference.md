# Legacy unread delivery reference

Date: 2026-09-26

## Source and scope

The old implementation is preserved in
`/home/coder/project/mas-group-archive-20260923/`. Its removal from the active
tree is documented by commit `9758574`; the preceding revision and earlier
commit `6fd1600` also retain the implementation in Git history.

This investigation inspected the frozen code, ran seven focused offline tests,
and reproduced one delivery limitation. No production code or frozen source
was modified. No live model calls were made.

## How it worked

Each `GroupMember` held `last_read_message_id`. The unread query selected retained
messages whose ID exceeded that cursor, whose `propagate` flag was true, and
whose sender was not the member itself. This was per-member state, rather than
a shared recent-message window.

The policy interface exposed separate methods for `unread_messages`,
`dispatchable_messages`, `synthetic_pass_messages` and `next_member`. The three
built-in policies were `default`, `on_demand` and `broadcast_feedback`.
Although these methods separated some responsibilities, unread delivery and
activation were still influenced by policy-specific filtering and PASS behavior.

For a real turn, the runtime captured the selected unread messages, rendered
their full content into the member's input, and optionally included recent
context without duplicates. The model did not need to initiate a history query
to receive that assigned unread batch. After the Agent call returned normally,
the runtime advanced the cursor to the batch's maximum ID. An exception before
that update retained the unread position. Messages arriving after the snapshot
remained available for a later turn.

For eligible non-speaking members, the runtime used synthetic PASS:

1. Append the unread prompt to the private session.
2. Append an assistant `group_pass` tool call and execute its handler.
3. Append the tool result and advance the cursor without calling the model.
4. Reset the active context to the system prompt.

Code references in the frozen archive:

- `domain/group.py:51`: member state and read cursor.
- `core/group_policy/rules.py:28`: unread selection.
- `core/group_policy/policies.py`: the three strategies and PASS selection.
- `core/group_runtime/context_builder.py:20`: full unread-message rendering.
- `core/group_runtime/dispatch_loop.py`: selection, snapshots and execution.
- `core/group_turns.py:12`: real turns and cursor advancement.
- `core/group_turns.py:77`: synthetic PASS and active-context reset.
- `core/agent.py:246`: reset preserves audit history but clears active input.

## Verification and limitation

The frozen integration tests were run with the active Python environment but
the archive as the working directory and import root. Bytecode and pytest cache
writing were disabled. Directed dispatch across three policies in sync/async
mode, plus timeout/unread retention, passed: **7 passed, 8 deselected in 0.11s**.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/coder/project/mas/.venv/bin/python -m pytest \
  -p pytest_asyncio.plugin -p no:cacheprovider -q \
  tests/test_group_runtime_integration.py \
  -k 'directed_work_only_runs_target_and_synthetic_pass_has_no_execution_events or group_timeout_releases_real_member_and_retains_unread_work' \
  --basetemp=/tmp/mas-legacy-unread-check-20260926
```

A separate deterministic probe used the real archived Group and Agent loop with
scripted provider responses. A source message directed to A was synthetic-passed
for B. B's cursor advanced and its private history retained the source, although
B had made no model call. After ten ordinary updates and a directed request to
B, B's first real model input no longer contained that original requirement.
The archive's default recent-context window was eight messages.

Result: `/tmp/mas-legacy-unread-probe.json`. This establishes that the old
"read" flag was not a guarantee that the original would be present in a later
model invocation. Synthetic PASS plus active-context clearing created a gap
between recorded history and actual model input.

The archived message store also had an in-memory pruning limit. That mechanism
does not meet the current requirement to preserve originals and should not be
adopted as the new retention model.

## What to carry forward

The useful foundation is per-member delivery progress and automatic collection
of pending messages. Message publication, delivery into a member's context, and
activation of a model run must remain distinct operations.

When a strategy activates a member, the framework can prepare a bounded snapshot
of that member's pending messages, distinguish response-triggering requests
from passive context, and retain later arrivals for subsequent delivery. The
delivery record must describe what was actually supplied, while task completion
and public-response obligations remain separate state. Exact acknowledgement,
failure and retry rules still need design; a cursor alone does not solve them.

In particular, restoring unread delivery should not restore automatic activation
on every public message, synthetic model decisions, or destructive history
pruning. Large pending batches need explicit budget handling without advancing
past undelivered messages. Sparse selection and concurrent arrivals must not be
hidden by simply taking the maximum selected message ID.

This mechanism addresses the missing delivery foundation identified in the
recent collaborative test. Requirement-specific references and semantic
acceptance remain useful additional layers, but they should not substitute for
reliable ordinary member delivery.
