"""Tool permission metadata and allow/deny policy primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ToolSideEffect = Literal[
    "read_only",
    "memory_only",
    "propagating",
    "non_propagating",
    "workspace_mutating",
    "external_effect",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ToolPermission:
    """Stable permission metadata for one registered tool."""

    side_effect: ToolSideEffect = "unknown"
    scope: str = "default"
    requires_approval: bool = False
    description: str = ""

    @classmethod
    def from_value(cls, value: Any) -> "ToolPermission":
        """Normalize legacy permission metadata into ``ToolPermission``."""
        if isinstance(value, ToolPermission):
            return value
        if isinstance(value, str):
            return cls(side_effect=value)  # type: ignore[arg-type]
        if isinstance(value, dict):
            return cls(
                side_effect=value.get("side_effect", "unknown"),
                scope=value.get("scope", "default"),
                requires_approval=bool(value.get("requires_approval", False)),
                description=value.get("description", ""),
            )
        if value is None:
            return cls()
        return cls(description=str(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "side_effect": self.side_effect,
            "scope": self.scope,
            "requires_approval": self.requires_approval,
            "description": self.description,
        }

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.side_effect == other
        if isinstance(other, ToolPermission):
            return (
                self.side_effect,
                self.scope,
                self.requires_approval,
                self.description,
            ) == (
                other.side_effect,
                other.scope,
                other.requires_approval,
                other.description,
            )
        return False


@dataclass(frozen=True, slots=True)
class ToolPermissionDecision:
    """Decision returned by a tool permission policy."""

    allowed: bool = True
    requires_approval: bool = False
    reason: str = ""
    mode: str = "allow"


@dataclass(frozen=True, slots=True)
class ToolPermissionPolicy:
    """Permission policy for tool side-effect decisions."""

    dry_run: bool = False
    enforce: bool = False
    rules: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        *,
        dry_run: bool = False,
        enforce: bool = False,
        rules: str | dict[str, str] = "",
    ) -> "ToolPermissionPolicy":
        """Build a policy from simple side-effect rules."""
        if isinstance(rules, str):
            parsed = parse_permission_rules(rules)
        else:
            parsed = {
                str(key).strip(): normalize_permission_mode(value)
                for key, value in rules.items()
                if str(key).strip()
            }
        return cls(dry_run=dry_run, enforce=enforce, rules=parsed)

    def check(
        self,
        *,
        tool_name: str,
        permission: ToolPermission,
        caller: str = "",
        arguments: object = None,
    ) -> ToolPermissionDecision:
        mode = self.rules.get(permission.side_effect, "allow")
        if mode == "approval":
            return ToolPermissionDecision(
                allowed=not self.enforce,
                requires_approval=True,
                reason=(
                    f"{self._mode_label()} approval required for {tool_name} "
                    f"({permission.side_effect})"
                ),
                mode="approval",
            )
        if mode == "deny":
            return ToolPermissionDecision(
                allowed=not self.enforce,
                requires_approval=False,
                reason=(
                    f"{self._mode_label()} deny for {tool_name} "
                    f"({permission.side_effect})"
                ),
                mode="deny",
            )
        return ToolPermissionDecision(allowed=True, mode="allow")

    def _mode_label(self) -> str:
        if self.enforce:
            return "enforced"
        if self.dry_run:
            return "dry-run"
        return "configured"


def normalize_permission_mode(value: object) -> str:
    """Normalize one configured permission mode."""
    text = str(value).strip().lower()
    if text in {"approval", "approval_required", "approve"}:
        return "approval"
    if text in {"deny", "denied", "block"}:
        return "deny"
    return "allow"


def parse_permission_rules(value: str) -> dict[str, str]:
    """Parse ``side_effect=mode`` permission rules from env config."""
    rules: dict[str, str] = {}
    for part in value.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        side_effect, mode = part.split("=", 1)
        side_effect = side_effect.strip()
        if side_effect:
            rules[side_effect] = normalize_permission_mode(mode)
    return rules


__all__ = [
    "parse_permission_rules",
    "normalize_permission_mode",
    "ToolPermission",
    "ToolPermissionDecision",
    "ToolPermissionPolicy",
    "ToolSideEffect",
]
