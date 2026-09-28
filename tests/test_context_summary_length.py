"""Conservative summary input allowances must be explicit and recoverable."""

from copy import deepcopy

import pytest

from core.agent_runtime.context_management.errors import CompactionError
from models import AI, User
from tests.runtime_fakes import execute
from tests.test_context_execution import ContextModel, make_agent, seed_old_context


class VerboseSummaryModel(ContextModel):
    def __init__(self, *, repair=True, draft='Preserved finding. ' * 30):
        super().__init__()
        self.repair = repair
        self.draft = draft

    def invoke(self, messages, **kwargs):
        self.requests.append((deepcopy(messages), deepcopy(kwargs)))
        if messages[0].message.startswith('CONTEXT_COMPACTION'):
            self.summary_calls += 1
            text = (self.draft if self.summary_calls == 1 or not self.repair
                    else 'Earlier findings preserved; continue the requested task.')
        else:
            self.main_calls += 1
            text = 'finished'
        return {'choices': [{'message': AI(text).to_dict(), 'finish_reason': 'stop'}]}


@pytest.mark.parametrize('mode', ['run', 'arun'])
@pytest.mark.parametrize('draft', ['Preserved finding. ' * 30, '\u03bb' * 150, '"\\\n' * 150],
                         ids=['ascii', 'unicode', 'json-escaping'])
async def test_completed_summary_above_planning_reserve_uses_actual_candidate_budget(tmp_path, mode, draft):
    model = VerboseSummaryModel(draft=draft)
    agent = make_agent(tmp_path, model, window=4096)
    agent.system_prompt = 'Keep these fixed instructions verbatim.'
    original = 'Original finding: ' + 'evidence ' * 110
    agent.session.add(User(original))
    agent.session.add(AI('Prior analysis: ' + 'finding ' * 110))
    agent.session.add(User('Continue with the original findings.'))
    if mode == 'run':
        agent.compact('Continue with the original findings.')
    else:
        await agent.acompact('Continue with the original findings.')
    assert await execute(agent, mode, 'Continue with the original findings.') == 'finished'
    assert model.summary_calls == 1 and model.main_calls == 1
    assert agent.runtime.context_manager.summary_calls == 1
    assert agent.session.checkpoint['summary'] == draft.strip()
    assert agent.session.checkpoint is not None
    assert any(message.message == original for message in agent.session.history)
    assert [m.message for m in model.requests[-1][0] if m.role == 'system'] == [
        'Keep these fixed instructions verbatim.']
    agent.session.validate_archives()


@pytest.mark.parametrize('mode', ['run', 'arun'])
@pytest.mark.parametrize('draft', ['finding ' * 120, '"' * 700], ids=['ascii', 'json-escaping'])
async def test_summary_that_exceeds_actual_candidate_budget_is_rewritten(tmp_path, mode, draft):
    model = VerboseSummaryModel(draft=draft)
    agent = make_agent(tmp_path, model, window=4096)
    agent.system_prompt = 'Fixed rules. ' * 150
    original = User('Original finding: ' + 'evidence ' * 110)
    agent.session.add(original)
    agent.session.add(AI('Prior analysis: ' + 'finding ' * 110))
    agent.session.add(User('Continue with original findings.'))
    if mode == 'run':
        agent.compact()
    else:
        await agent.acompact()
    assert model.summary_calls == 2 and model.main_calls == 0
    assert model.draft.strip() in model.requests[1][0][1].message
    assert original in agent.session.history
    agent.session.validate_archives()


async def test_failed_summary_rewrite_is_bounded_and_keeps_originals(tmp_path):
    model = VerboseSummaryModel(repair=False, draft='finding ' * 120)
    agent = make_agent(tmp_path, model, window=4096)
    agent.system_prompt = 'Fixed rules. ' * 150
    original = 'Original finding: ' + 'evidence ' * 110
    agent.session.add(User(original))
    agent.session.add(AI('Prior analysis: ' + 'finding ' * 110))
    agent.session.add(User('Continue with the original findings.'))
    with pytest.raises(CompactionError, match='Summary exceeds'):
        agent.compact('Continue with the original findings.')
    assert model.summary_calls == 2 and model.main_calls == 0
    assert agent.session.checkpoint is None
    assert any(message.message == original for message in agent.session.active)


def test_rewrite_input_budget_is_checked_before_another_model_call(tmp_path):
    model = VerboseSummaryModel(draft='x' * 4000)
    agent = make_agent(tmp_path, model, window=4096)
    original = User('Original findings: ' + 'evidence ' * 110)
    agent.session.add(original)
    agent.session.add(AI('Prior analysis: ' + 'finding ' * 110))
    agent.session.add(User('Continue'))
    with pytest.raises(CompactionError, match='rewrite input exceeds'):
        agent.compact()
    assert model.summary_calls == 1 and model.main_calls == 0
    assert agent.session.checkpoint is None and original in agent.session.active


def test_four_call_source_ceiling_also_counts_the_draft_rewrite(tmp_path):
    model = VerboseSummaryModel(draft='finding ' * 120)
    agent = make_agent(tmp_path, model, window=4096)
    agent.system_prompt = 'Fixed rules. ' * 150
    original = User('Original findings: ' + 'x' * 22000)
    agent.session.add(original)
    with pytest.raises(CompactionError, match='four calls'):
        agent.run('Continue')
    assert model.summary_calls == 4 and model.main_calls == 0
    assert model.draft.strip() in model.requests[1][0][1].message
    assert agent.session.checkpoint is None and original in agent.session.active


def test_multisegment_above_target_summaries_keep_exact_input_checks(tmp_path):
    from core.agent_runtime.context_management.budget import ModelBudget, TokenCounter
    model = VerboseSummaryModel(repair=False, draft='x' * 300)
    agent = make_agent(tmp_path, model, window=4096)
    seed_old_context(agent, turns=4)
    before = deepcopy(agent.session.history)
    agent.compact()
    assert 2 <= model.summary_calls <= 4
    assert agent.session.checkpoint['summary'] == model.draft
    assert all(message in agent.session.history for message in before)
    for messages, options in model.requests:
        assert TokenCounter().estimate(messages).tokens <= ModelBudget(4096).input_budget(options['max_tokens'])
    agent.session.validate_archives()
