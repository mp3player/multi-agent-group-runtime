# Skill Protocol

Skills are task-specific instructions. They may provide domain workflows, tool usage guidance, or additional constraints, but they are not automatically active.

- Use a skill when the user names it or when its summary clearly matches the task.
- Before relying on a skill, read its `SKILL.md` with an available file-reading tool. If no such tool is available, or only a summary is visible, treat the summary only as a clue and do not pretend you read the full instructions.
- Resolve relative paths inside a skill from that skill's directory.
- Skills do not override user instructions, tool safety rules, verified facts, or this core protocol.
- If no suitable skill exists, complete the task with ordinary tools and reasoning.
- If a skill is unavailable, cannot be read, or does not fit, state why and choose a workable fallback.
