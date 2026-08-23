MAS
===

MAS is a lightweight multi-agent runtime. It supports a single-agent ReAct CLI
and a group-chat collaboration mode where multiple agents coordinate through
shared group tools, memory, and dispatch policies.

Requirements
------------

- Python 3.10+
- uv

Install
-------

```bash
uv sync
cp .env.example .env
```

Edit `.env`:

```text
BaseURL=your OpenAI-compatible endpoint
BaseKey=your API key
BaseModel=your model
```

Run
---

Single-agent CLI:

```bash
uv run python main.py
```

Group mode:

```bash
uv run python main.py --group --show-pass
```

Web UI:

```bash
uv run python web_server.py
```

Then open:

```text
http://127.0.0.1:8000
```

Group Members
-------------

Create a local member config:

```bash
mkdir -p config
cp config/group_members.example.json config/group_members.json
```

`config/group_members.json` is ignored by git. Each member may override the
default model connection:

```json
{
  "members": [
    {
      "name": "Leader",
      "description": "Coordinates the group.",
      "base_url": "https://provider.example/v1",
      "api_key": "provider-key",
      "model": "model-name"
    }
  ]
}
```

Empty `base_url`, `api_key`, or `model` fields inherit `BaseURL`, `BaseKey`,
and `BaseModel` from `.env`.

Group CLI Commands
------------------

```text
/members          Show members, models, and endpoint URLs
/addmember NAME [DESCRIPTION] [--url URL] [--key KEY] [--model MODEL]
/tools            Show member tools
/usage            Show token/cache usage
/audit [N]        Show recent tool audit records
/config           Show runtime configuration
/debug [N]        Show group debug snapshot
/groupstats       Show dispatch statistics
/clear            Clear group transcript/memory/events
/new              Rebuild the group and member agents
/exit             Exit
```

More Documentation
------------------

- [Configuration](docs/configuration.md)
- [Operations and Debugging](docs/operations.md)
- [Functional Modules](docs/modules.md)
- [Group Dispatch](docs/dispatch.md)
- [Target Architecture](ROADMAP.md)

Notes
-----

- `.env`, `config/group_members.json`, logs, and local generated notes are ignored.
- Do not publish real API keys in member configs.
- The Web UI is a thin local testing shell, not a production multi-user product.
