"""Dispatch policy registry."""

from __future__ import annotations

from collections.abc import Callable

from core.group_policy.policies import (
    BroadcastFeedbackGroupDispatchPolicy,
    DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy,
)
from core.group_policy.types import GroupDispatchPolicy


DispatchPolicyFactory = Callable[[], GroupDispatchPolicy]

_POLICY_REGISTRY: dict[str, DispatchPolicyFactory] = {
    DefaultGroupDispatchPolicy.name: DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy.name: OnDemandGroupDispatchPolicy,
    BroadcastFeedbackGroupDispatchPolicy.name: BroadcastFeedbackGroupDispatchPolicy,
}


def register_dispatch_policy(
    name: str,
    policy_factory: DispatchPolicyFactory,
) -> None:
    """Register a dispatch policy factory by name."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("dispatch policy name cannot be empty")
    _POLICY_REGISTRY[normalized] = policy_factory


def unregister_dispatch_policy(name: str) -> None:
    """Remove a registered dispatch policy by name."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("dispatch policy name cannot be empty")
    if normalized == DefaultGroupDispatchPolicy.name:
        raise ValueError("cannot unregister default dispatch policy")
    _POLICY_REGISTRY.pop(normalized, None)


def create_dispatch_policy(name: str = "default") -> GroupDispatchPolicy:
    """Create a dispatch policy by registered name."""
    normalized = name.strip() or "default"
    try:
        factory = _POLICY_REGISTRY[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_POLICY_REGISTRY))
        raise ValueError(
            f"unknown dispatch policy: {normalized}; available: {available}"
        ) from exc
    return factory()


def registered_dispatch_policies() -> list[str]:
    """Return registered dispatch policy names."""
    return sorted(_POLICY_REGISTRY)


__all__ = [
    "DispatchPolicyFactory",
    "create_dispatch_policy",
    "register_dispatch_policy",
    "registered_dispatch_policies",
    "unregister_dispatch_policy",
]
