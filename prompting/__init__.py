"""Public single-agent APIs, loaded on explicit access."""

from importlib import import_module

_EXPORTS = {
    'load_common_prompt_specs': ('prompting.loader', 'load_common_prompt_specs'),
    'resolve_prompt_spec_path': ('prompting.loader', 'resolve_prompt_spec_path'),
    'PromptRuntime': ('prompting.runtime', 'PromptRuntime'),
    'PromptRuntimeError': ('prompting.runtime', 'PromptRuntimeError'),
    'LoadedSkills': ('prompting.skills', 'LoadedSkills'),
    'load_skills_from_dir': ('prompting.skills', 'load_skills_from_dir'),
    'parse_skill_frontmatter': ('prompting.skills', 'parse_skill_frontmatter'),
    'render_skills': ('prompting.skills', 'render_skills'),
    'COMMON_PROMPT_MODULES': ('prompting.specs', 'COMMON_PROMPT_MODULES'),
    'PromptModuleSpec': ('prompting.specs', 'PromptModuleSpec'),
    'render_tool_registry': ('prompting.tool_renderer', 'render_tool_registry'),
}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value
