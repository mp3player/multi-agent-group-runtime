"""核心运行时包。

- core.llm:            LLM 客户端
- core.session:        会话管理
- core.agent:          Agent（ReAct）
- core.group:          GroupChat（multi-agent 群聊）
- core.group_policy:   群聊调度策略抽象
- core.system_builder: System Prompt 模块化构建器
"""

from core.agent import Agent, AgentTimeoutError
from core.group import GroupChat, GroupChatError, GroupTimeoutError
from core.group_policy.policies import (
    BroadcastFeedbackGroupDispatchPolicy,
    DefaultGroupDispatchPolicy,
    OnDemandGroupDispatchPolicy,
)
from core.group_policy.registry import (
    create_dispatch_policy,
    registered_dispatch_policies,
    register_dispatch_policy,
    unregister_dispatch_policy,
)
from core.group_policy.types import GroupDispatchPolicy
from domain.group import GroupMember, GroupMessage, GroupTurn
from core.llm import LLMClient
from core.session import Session
from core.system_builder import SystemBuilder, SystemBuilderError

__all__ = [
    "LLMClient",
    "Agent",
    "AgentTimeoutError",
    "Session",
    "GroupChat",
    "GroupChatError",
    "GroupTimeoutError",
    "GroupDispatchPolicy",
    "DefaultGroupDispatchPolicy",
    "OnDemandGroupDispatchPolicy",
    "BroadcastFeedbackGroupDispatchPolicy",
    "create_dispatch_policy",
    "registered_dispatch_policies",
    "register_dispatch_policy",
    "unregister_dispatch_policy",
    "GroupMember",
    "GroupMessage",
    "GroupTurn",
    "SystemBuilder",
    "SystemBuilderError",
]
