"""Persistent group member configuration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMBERS_FILE = ROOT / "config" / "group_members.json"


@dataclass(frozen=True)
class MemberConfig:
    name: str
    description: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""


def default_members_file() -> Path:
    configured = os.environ.get("MAS_GROUP_MEMBERS_FILE", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_MEMBERS_FILE


def load_member_configs(path: Path | None = None) -> list[MemberConfig]:
    """Load member configs from JSON. Missing files mean no default members."""
    config_path = path or default_members_file()
    if not config_path.exists():
        return []
    data = json.loads(config_path.read_text(encoding="utf-8"))
    members = data.get("members") if isinstance(data, dict) else data
    if not isinstance(members, list):
        raise ValueError(f"成员配置格式错误: {config_path}")

    configs: list[MemberConfig] = []
    seen: set[str] = set()
    for item in members:
        if isinstance(item, str):
            name = item.strip()
            description = ""
            base_url = ""
            api_key = ""
            model = ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
            base_url = str(
                item.get("base_url", item.get("url", ""))
            ).strip()
            api_key = str(
                item.get("api_key", item.get("key", ""))
            ).strip()
            model = str(item.get("model", "")).strip()
        else:
            raise ValueError(f"成员配置项格式错误: {item!r}")
        if not name:
            raise ValueError(f"成员配置包含空 name: {config_path}")
        if name in seen:
            raise ValueError(f"成员配置包含重复 name: {name}")
        seen.add(name)
        configs.append(MemberConfig(
            name=name,
            description=description,
            base_url=base_url,
            api_key=api_key,
            model=model,
        ))
    return configs


def save_member_configs(
    members: list[MemberConfig],
    path: Path | None = None,
) -> None:
    config_path = path or default_members_file()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "members": [
            _member_config_to_dict(member)
            for member in members
        ],
    }
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _member_config_to_dict(member: MemberConfig) -> dict[str, str]:
    item = {
        "name": member.name,
        "description": member.description,
    }
    if member.base_url:
        item["base_url"] = member.base_url
    if member.api_key:
        item["api_key"] = member.api_key
    if member.model:
        item["model"] = member.model
    return item


def merge_member_configs(
    base: list[MemberConfig],
    extra_names: list[str],
) -> list[MemberConfig]:
    """Append CLI-provided member names without losing configured descriptions."""
    merged = list(base)
    seen = {member.name for member in merged}
    for name in extra_names:
        clean = name.strip()
        if clean and clean not in seen:
            merged.append(MemberConfig(name=clean))
            seen.add(clean)
    return merged
