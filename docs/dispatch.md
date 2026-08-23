Group Dispatch
==============

MAS group chat is event-driven. A new propagating group message can wake member
agents, and every member reads only the group messages it has not already seen.
The scheduler decides who gets a real LLM turn and who only receives a synthetic
PASS record.

Design Goals
------------

- Avoid hard task locks.
- Let agents coordinate through conversation and group tools.
- Prevent obvious duplicate work when a task is meant for one member.
- Keep non-target members aware of directed work without spending LLM calls.
- Preserve enough events, stats, and audit data to debug long conversations.

Message Concepts
----------------

Every group message has a few scheduling-relevant fields:

- `sender`: the user, system, or member that produced the message.
- `propagate`: whether other members should treat it as unread group context.
- `dispatch_mode`: one of `normal`, `first_responder_only`, `broadcast`,
  or `feedback`.
- `mentions`: explicit target member names, usually produced by group tools
  such as `group_direct` or `group_handoff`.

Non-propagating messages are visible in the transcript but do not wake other
members. `group_pass` is non-propagating by design.

Unread Context
--------------

Each member tracks `last_read_message_id`. When a member runs, it receives a
prompt containing only unread propagating messages for that member, plus a small
recent-context window for orientation.

A member does not receive its own messages as unread work. This prevents an
agent from repeatedly waking itself because of its own response.

First Responder Messages
------------------------

User messages enter the group as `first_responder_only` by default. This means
the first eligible member can claim the initial response opportunity. Once one
member has read the message, other members will not automatically get the same
first-responder work unless later messages explicitly wake them.

This prevents simple tasks such as "create a file" from being executed by every
member at once, while still allowing the first member to broadcast, hand off, or
direct work when collaboration is useful.

Directed Dispatch
-----------------

Directed dispatch is triggered by explicit `mentions` on a group message, not by
parsing free-form `@name` text. Mention semantics should be created through
tools such as:

- `group_direct(to=[...], message=...)`
- `group_handoff(to=[...], summary=..., next=..., files=...)`

When a propagating message has valid mention targets:

- Target members can receive a real LLM turn.
- Non-target members do not run the model.
- Non-target members still record the unread message followed by a synthetic
  `group_pass` tool call in their private session.
- The synthetic PASS message is non-propagating and does not create a new
  scheduling wave.

This keeps member histories consistent without paying for unnecessary model
turns.

Synthetic PASS
--------------

Synthetic PASS is an internal runtime behavior. It mimics a real `group_pass`
tool call:

1. Build the member's unread prompt.
2. Add that prompt to the member's private session as a user message.
3. Add an assistant message with a `group_pass` tool call.
4. Execute the real `group_pass` handler.
5. Add the tool result message.
6. Advance the member's read cursor.
7. Reset the active context back to system prompt only.

Synthetic PASS does not count as a real LLM turn, but it is counted in group
stats and can appear in the transcript if PASS display is enabled.

Dispatch Policies
-----------------

MAS ships three policies:

### `default`

The default policy is closest to broadcast group chat:

- User first-responder messages are offered to one available member.
- Normal/broadcast/feedback messages can wake all eligible members.
- Directed messages wake only their targets; other members synthetic-pass them.

This policy maximizes awareness, but with many members it can produce more PASS
traffic and more discussion.

### `on_demand`

The on-demand policy reduces broad wakeups:

- The first user entry can wake a first responder.
- Explicit `broadcast` messages wake eligible members.
- Explicit directed messages wake only targets.
- Other unread messages are synthetic-passed when possible.

This policy lowers token usage and reduces accidental over-discussion, but it
depends more heavily on agents using group tools deliberately.

### `broadcast_feedback`

The broadcast-feedback policy extends `on_demand`:

- Explicit broadcasts wake eligible members.
- Member `group_report` / `group_done` feedback can route back to the most
  recent broadcast originator after other members finish.

This is useful for review flows:

1. A coordinator broadcasts a request.
2. Specialists report independently.
3. The coordinator is woken again to summarize and decide next steps.

Tool Effects and Scheduling
---------------------------

Group tools are categorized by scheduling side effect:

- read-only: inspect state, do not mutate memory, do not dispatch.
- memory-only: update shared memory, do not dispatch.
- propagating: add a group message that may trigger dispatch.
- non-propagating: add local/non-waking messages such as PASS.

Important propagating tools:

- `group_send`: general group message.
- `group_broadcast`: explicit broadcast for broad review or parallel input.
- `group_direct`: directed message to selected members.
- `group_handoff`: directed handoff with memory updates.
- `group_note_scope`: note current scope and broadcast it.
- `group_done`: record completed work and broadcast it.
- `group_report`: report findings, usually used in broadcast-feedback flows.
- `group_decision`: record a decision and broadcast it.

Memory-only tools such as `group_memory_update` and `group_memory_append` do not
trigger dispatch. Agents should send a separate propagating message when a
memory update needs other members' attention.

Async Dispatch
--------------

The async loop allows multiple member turns to be in flight. Before launching a
real turn, the runtime first applies synthetic PASS to eligible non-target
members. When a member emits a new propagating message, the loop wakes and
reevaluates dispatchability.

If a target member is already running, it is not interrupted. The message remains
unread for that member and is handled when the member becomes idle and the loop
reevaluates.

Stale Responses
---------------

A member may start from an older unread snapshot while other members continue
working. MAS guards direct stale responses:

- If the member did no real non-group tool work and its response is stale, the
  response can be converted to a non-propagating PASS.
- If the member did real tool work, the response is preserved because it may
  contain useful results even if the group moved on.

This avoids some late duplicate commentary without throwing away completed tool
work.

Known Risk: Token Usage
-----------------------

Group mode can consume tokens much faster than running one agent manually over
multiple turns. The exact cause is not fully proven yet. Current likely
contributors are:

- More member turns: even PASS-heavy flows still create scheduling checks and
  sometimes real LLM calls.
- Prompt duplication: every member has its own system prompt, tool list, skill
  list, and group prompt.
- Cache instability: multiple members may share one API key but send diverging
  prompt prefixes, which can reduce provider-side cache reuse. Some providers
  may also have cache-slot behavior that is opaque to MAS.
- Broadcast behavior: broad wakeups can multiply context reads when a task does
  not actually need every member.
- Tool/result context: file reads, terminal outputs, and long reports can make
  later member prompts larger.

This is a known operational risk, not a settled bug. The current mitigations are
`on_demand` / `broadcast_feedback`, directed dispatch, synthetic PASS, bounded
session history, `/usage`, `/groupstats`, `/debug`, and per-member model/API
configuration. More measurement is needed before assuming a single root cause.

Debugging Dispatch
------------------

Useful CLI commands:

```text
/groupstats       Show dispatch, PASS, synthetic PASS, stale response stats
/debug [N]        Show members, memory, stats, recent events, audit, and usage
/audit [N]        Show recent tool calls and permission decisions
/config           Show model, policy, audit, and event sink configuration
```

Useful environment settings:

```text
MAS_GROUP_DISPATCH_POLICY=default|on_demand|broadcast_feedback
MAS_MAX_DISPATCH_ROUNDS=100
MAS_GROUP_EVENTS_JSONL=logs/group_events.jsonl
MAS_TOOL_AUDIT_JSONL=logs/tool_audit.jsonl
```

Implementation Map
------------------

- `core/group_policy/`: policy protocol and policy implementations.
- `core/group_policy/rules.py`: pure helper rules for unread, directed, and
  first-responder decisions.
- `core/group_runtime/dispatch_loop.py`: sync and async dispatch loops.
- `core/group_runtime/synthetic_pass.py`: synthetic PASS service.
- `core/group_turns.py`: real member turn execution and stale response guard.
- `core/group_tool_specs.py`: group tool side-effect classification.
- `core/group_tools_runtime/`: group tool handlers.
- `domain/group_stats.py`: dispatch counters.
- `domain/group_events.py`: event log and optional JSONL sink.

When changing scheduling behavior, update `tests/test_group_concurrency.py` and
`tests/test_group_evaluation.py` first or in the same change. These tests encode
the important behavior boundaries.
