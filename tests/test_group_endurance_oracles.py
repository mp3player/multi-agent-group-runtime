"""Keep numerical acceptance independent of language-model output."""

from copy import deepcopy

import pytest

from group_endurance_cases import artifacts, cases, expected, final_attempts, validate


@pytest.mark.parametrize('case', cases(), ids=lambda case: case.id)
@pytest.mark.parametrize('revision', [1, 2])
def test_endurance_oracles_accept_reference_and_reject_stale_identity(case, revision):
    value = expected(case, revision)
    validate(case, revision, value)
    wrong = {**value, 'revision': 3 - revision}
    with pytest.raises(AssertionError):
        validate(case, revision, wrong)


def test_rollout_eligibility_changes_with_capacity_not_just_price():
    case = cases()[0]
    assert (expected(case, 1)['plan'], expected(case, 1)['cost']) == ('delta', 11)
    assert (expected(case, 2)['plan'], expected(case, 2)['cost']) == ('cirrus', 8)
    with pytest.raises(AssertionError):
        validate(case, 1, {**expected(case, 2), 'revision': 1})


def test_timeline_offset_sign_tie_and_revision_are_observable():
    case = cases()[1]
    first, second = expected(case, 1), expected(case, 2)
    assert [row['id'] for row in first['events']] == ['E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E8']
    assert [row['id'] for row in second['events']] == ['E2', 'E1', 'E3', 'E4', 'E7', 'E5', 'E6', 'E8']
    assert [row['utc'] for row in second['events']] == [997, 1000, 1015, 1030, 1032, 1035, 1040, 1055]
    wrong = deepcopy(second)
    wrong['events'][1] = wrong['events'][0]
    with pytest.raises(AssertionError):
        validate(case, 2, wrong)


def test_schedule_oracle_allows_slack_but_rejects_conflicts_and_false_makespan():
    case = cases()[2]
    assert expected(case, 1)['makespan'] == 10
    assert expected(case, 2)['makespan'] == 9
    value = {**expected(case, 1), 'starts': dict(A=0, B=1, C=3, D=4, F=7, E=8)}
    validate(case, 1, value)
    for changes in ({'B': 2}, {'D': 6}, {'E': 7}, {'F': True}):
        with pytest.raises(AssertionError):
            validate(case, 1, {**value, 'starts': {**value['starts'], **changes}})
    with pytest.raises(AssertionError):
        validate(case, 1, {**value, 'makespan': 9})


def test_json_artifacts_do_not_treat_nested_fields_as_extra_final_answers():
    assert list(artifacts('Draft {bad}. Final {"kind":"x","starts":{"A":0}} done.')) == [
        {'kind': 'x', 'starts': {'A': 0}}]
    assert not list(artifacts('{"kind":"schedule_final","makespan":0,"makespan":10}'))


def test_malformed_final_attempt_is_not_hidden_by_a_later_valid_final():
    good = '{"kind":"rollout_final","plan":"delta","cost":11}'
    values, errors = final_attempts('{"kind":"rollout_final","cost":0,"cost":11} ' + good)
    assert values and errors
    values, errors = final_attempts('{"kind":"timeline_final","events":[]} ' + good)
    assert len(values) == 2 and not errors
