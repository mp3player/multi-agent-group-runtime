Configuration
=============

Environment
-----------

The required `.env` values are:

```text
BaseURL=your OpenAI-compatible endpoint
BaseKey=your API key
BaseModel=your model
```

These are defaults. Group members can override `base_url`, `api_key`, and
`model` individually in `config/group_members.json` or with `/addmember` flags.

Optional settings:

```text
# Skill directory. Defaults to /home/coder/skill.
MAS_SKILLS_DIR=/home/coder/skill

# Workspace roots for file and terminal tools.
# Use ":" to separate multiple roots on Linux/macOS.
MAS_WORKSPACE_ROOTS=/home/coder

# ReAct/session limits.
MAS_DEFAULT_MAX_TOKENS=4096
MAS_DEFAULT_MAX_TURNS=20
MAS_ACTIVE_MESSAGE_LIMIT=80
MAS_HISTORY_MESSAGE_LIMIT=1000

# Request and whole-run timeouts. Set run timeouts to 0 to disable.
MAS_LLM_TIMEOUT=120
MAS_AGENT_RUN_TIMEOUT=0
MAS_GROUP_RUN_TIMEOUT=0

# Group dispatch.
MAS_MAX_DISPATCH_ROUNDS=100
MAS_GROUP_DISPATCH_POLICY=default
MAS_GROUP_MEMBERS_FILE=/home/coder/project/mas/config/group_members.json

# File tool output limit. Use read_file(max_chars=0) to read this value.
MAS_READ_FILE_MAX_CHARS=12000
```

Group Member Config
-------------------

Default path:

```text
config/group_members.json
```

Example:

```json
{
  "members": [
    {
      "name": "Leader",
      "description": "Coordinates the group.",
      "base_url": "https://provider-a.example/v1",
      "api_key": "provider-a-key",
      "model": "model-a"
    },
    {
      "name": "Dev",
      "description": "Implements changes.",
      "model": "model-b"
    }
  ]
}
```

`base_url` may also be written as `url`; `api_key` may also be written as
`key`. Empty fields inherit the default `.env` model connection.

You can override the path:

```bash
MAS_GROUP_MEMBERS_FILE=/path/to/group_members.json uv run python main.py --group
uv run python web_server.py --member-config /path/to/group_members.json
```

Skills
------

MAS loads skill summaries from `MAS_SKILLS_DIR`, defaulting to:

```text
/home/coder/skill
```

If the directory is missing, agents still run, but the system prompt will not
include available skill summaries.

Dispatch Policies
-----------------

Available group dispatch policies:

- `default`: broadcasts unread group messages.
- `on_demand`: wakes members for the first user entry, explicit broadcasts, and
  explicit directed messages; other members record synthetic PASS.
- `broadcast_feedback`: extends `on_demand` with feedback to the broadcast
  originator when members report or finish work.

Example:

```bash
MAS_GROUP_DISPATCH_POLICY=on_demand uv run python main.py --group --show-pass
```
