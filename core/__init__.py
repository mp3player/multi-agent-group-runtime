"""Public single-agent APIs, loaded on explicit access."""

from importlib import import_module

_EXPORTS = {
    'Agent': ('core.agent', 'Agent'),
    'AgentTimeoutError': ('core.agent', 'AgentTimeoutError'),
    'LLMClient': ('core.llm', 'LLMClient'),
    'Session': ('core.session', 'Session'),
    'SystemBuilder': ('core.system_builder', 'SystemBuilder'),
    'SystemBuilderError': ('core.system_builder', 'SystemBuilderError'),
}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
