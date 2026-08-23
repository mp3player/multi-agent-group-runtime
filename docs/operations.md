Operations and Debugging
========================

Tool Permissions and Audit
--------------------------

Tools are registered with side-effect metadata, such as:

- `read_only`
- `workspace_mutating`
- `external_effect`
- `propagating`
- `non_propagating`
- `memory_only`

The default policy is allow-all. Dry-run policy records decisions without
blocking tool execution:

```bash
MAS_TOOL_PERMISSION_DRY_RUN=1 \
MAS_TOOL_PERMISSION_RULES=external_effect=approval,workspace_mutating=deny \
uv run python main.py --group
```

To enforce deny/approval rules:

```bash
MAS_TOOL_PERMISSION_ENFORCE=1 \
MAS_TOOL_PERMISSION_RULES=external_effect=approval,workspace_mutating=deny \
uv run python main.py --group
```

In enforce mode, matching `approval` or `deny` rules prevent the tool from
running and produce a permission-denied tool result. There is currently no
interactive approval UI.

Tool Audit JSONL
----------------

Enable append-only local tool audit logging:

```bash
MAS_TOOL_AUDIT_JSONL=logs/tool_audit.jsonl uv run python main.py --group
```

In group CLI mode, inspect recent audit records:

```text
/audit [N]
```

Group Events JSONL
------------------

Enable append-only group event logging:

```bash
MAS_GROUP_EVENTS_JSONL=logs/group_events.jsonl uv run python main.py --group
```

`/clear` only clears in-memory events; it does not delete JSONL files.

Debug Commands
--------------

Group CLI diagnostics:

```text
/config           Show model, dispatch, permission, audit, and event settings
/debug [N]        Show member status, memory summary, stats, events, audit, usage
/groupstats       Show dispatch, PASS, synthetic PASS, stale response stats
/usage            Show token/cache usage
/usage-clear      Clear in-memory usage records
```

Usage can be disabled with:

```text
MAS_USAGE_ENABLED=0
```

Token Usage Investigation
-------------------------

Group mode may spend tokens faster than expected. The root cause is not fully
confirmed; treat this as an operational risk to measure, not as a solved issue.

Recommended checks:

1. Use `/usage` to compare prompt, cached, cache-hit, cache-miss, completion,
   and reasoning tokens per member.
2. Use `/groupstats` to check real turns, PASS messages, synthetic PASS, stale
   responses, and dispatch-limit hits.
3. Use `/debug [N]` to inspect recent events and verify whether broad messages
   are waking more members than intended.
4. Test `MAS_GROUP_DISPATCH_POLICY=on_demand` or `broadcast_feedback` against
   the same task and compare usage.
5. Try per-member API keys or models if the provider appears to cache poorly
   when several members share one API key.
6. Watch tool outputs. Large file reads, terminal output, and long reports can
   inflate later prompts.

Do not assume provider cache behavior from MAS logs alone. If a provider exposes
cache-hit/cache-miss fields, use those fields together with MAS usage records.

Current Non-Goals
-----------------

- Interactive permission approval UI
- Web audit page
- Production Web productization
- Database-backed persistence and recovery
- Multi-user isolation or authentication
- Hosted SaaS operation
- Vector memory or automatic LLM summarization
