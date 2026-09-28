"""Scoped system extensions compose without changing the Agent's base role."""

import pytest

from core.agent import Agent
from core.session import Session
from core.system_builder import SystemBuilder
from models import AI, System
from tests.runtime_fakes import ScriptedModel


def system_text(agent):
    return '\n\n'.join(message.message for message in agent.session.active if message.role == 'system')


def test_extensions_survive_base_rebuild_and_are_independently_removable():
    builder = SystemBuilder()
    builder.add_module('role', 'Base role')
    agent = Agent(ScriptedModel(AI('done')), system_builder=builder)
    first = agent.add_system_prompt_extension('Collaboration rules')
    second = agent.add_system_prompt_extension('Workspace rules')
    builder.add_module('role', 'Updated role')
    agent.rebuild_system_prompt()
    assert system_text(agent) == 'Updated role\n\nCollaboration rules\n\nWorkspace rules'
    agent.remove_system_prompt_extension(first)
    assert system_text(agent) == 'Updated role\n\nWorkspace rules'
    agent.remove_system_prompt_extension(second)
    assert system_text(agent) == 'Updated role'
    assert agent.system_prompt == 'Updated role'


def test_extension_follows_session_replacement_without_contaminating_old_session():
    first, second = Session(), Session()
    first.add(System('First inherited role'))
    second.add(System('Second inherited role'))
    agent = Agent(ScriptedModel(), session=first)
    extension = agent.add_system_prompt_extension('Temporary rules')
    agent.set_session(second)
    assert [message.message for message in first.active] == ['First inherited role']
    assert system_text(agent) == 'Second inherited role\n\nTemporary rules'
    agent.remove_system_prompt_extension(extension)
    agent.session.clear_active()
    agent.reset_active_to_system()
    assert system_text(agent) == 'Second inherited role'


def test_invalid_extension_does_not_change_prompt():
    agent = Agent(ScriptedModel(), system_prompt='Base role')
    for invalid in (None, '', '   ', 42):
        with pytest.raises((TypeError, ValueError)):
            agent.add_system_prompt_extension(invalid)
        assert system_text(agent) == 'Base role'


def test_removed_extension_is_not_inherited_by_a_new_agent_using_same_session():
    first = Agent(ScriptedModel())
    extension = first.add_system_prompt_extension('Temporary rules')
    first.remove_system_prompt_extension(extension)
    second = Agent(ScriptedModel(AI('done')), session=first.session)
    second.reset_active_to_system()
    assert second.run('Independent task') == 'done'
    assert system_text(second) == ''


def test_attaching_cleared_session_does_not_reactivate_historical_system_rules():
    session = Session()
    session.add(System('Historical rules'))
    session.clear_active()
    agent = Agent(ScriptedModel(), session=session)
    extension = agent.add_system_prompt_extension('Current rules')
    assert system_text(agent) == 'Current rules'
    agent.remove_system_prompt_extension(extension)
    assert system_text(agent) == ''


def test_first_extension_preserves_system_rules_added_to_idle_session():
    agent = Agent(ScriptedModel())
    agent.session.add(System('Role installed before binding'))
    extension = agent.add_system_prompt_extension('Group rules')
    assert system_text(agent) == 'Role installed before binding\n\nGroup rules'
    agent.remove_system_prompt_extension(extension)
    assert system_text(agent) == 'Role installed before binding'
