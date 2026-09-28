"""Public single-agent APIs, loaded on explicit access."""

from importlib import import_module

_EXPORTS = {
    'AgentRuntimeConfig': ('application.agent_config', 'AgentRuntimeConfig'),
    'LLMProviderConfig': ('application.agent_config', 'LLMProviderConfig'),
    'LoggingConfig': ('application.agent_config', 'LoggingConfig'),
    'PromptConfig': ('application.agent_config', 'PromptConfig'),
    'SkillsConfig': ('application.agent_config', 'SkillsConfig'),
    'ToolConfig': ('application.agent_config', 'ToolConfig'),
    'UsageConfig': ('application.agent_config', 'UsageConfig'),
    'build_agent': ('application.agent_builder', 'build_agent'),
    'AgentAppService': ('application.agent_service', 'AgentAppService'),
    'HistoryEntry': ('application.agent_service', 'HistoryEntry'),
    'AgentServiceOptions': ('application.options', 'AgentServiceOptions'),
    'AgentAppConfig': ('application.agent_config', 'AgentAppConfig'),
}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
