"""Group dispatch policy package.

This package preserves the historical ``core.group_policy`` import path while
splitting policy protocols, pure rules, built-in policies, and registry code.
"""

from core.group_policy.policies import (
    BroadcastFeedbackGroupDispatchPolicy,
    DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy,
)
from core.group_policy.registry import (
    DispatchPolicyFactory,
    create_dispatch_policy,
    register_dispatch_policy,
    registered_dispatch_policies,
    unregister_dispatch_policy,
)
from core.group_policy.rules import (
    directed_auto_pass_messages,
    dispatchable_unread_messages,
    effective_mentions,
    first_responder_message_claimed,
    is_directed_message,
    is_directed_to_member,
    next_member,
    unread_messages,
)
from core.group_policy.types import GroupDispatchContext, GroupDispatchPolicy


__all__ = [
    "BroadcastFeedbackGroupDispatchPolicy",
    "DefaultGroupDispatchPolicy",
    "DispatchPolicyFactory",
    "GroupDispatchContext",
    "GroupDispatchPolicy",
    "OnDemandGroupDispatchPolicy",
    "create_dispatch_policy",
    "directed_auto_pass_messages",
    "dispatchable_unread_messages",
    "effective_mentions",
    "first_responder_message_claimed",
    "is_directed_message",
    "is_directed_to_member",
    "next_member",
    "register_dispatch_policy",
    "registered_dispatch_policies",
    "unread_messages",
    "unregister_dispatch_policy",
]
