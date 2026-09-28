"""Offline integrity checks for the frozen held-out evaluation."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from group_heldout_cases import BUILDERS, validate_artifact
from test_group_live_heldout import assert_report_visible, contains_document


@pytest.mark.parametrize('name', BUILDERS)
def test_oracle_accepts_correct_unordered_artifact(name):
    case = BUILDERS[name]('offline')
    actual = deepcopy(case.expected)
    for value in actual.values():
        if isinstance(value, list):
            value.reverse()
    actual['explanation'] = 'Optional explanation is not graded.'
    validate_artifact(case, actual)


@pytest.mark.parametrize('name', BUILDERS)
@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'wrong_reference'])
def test_oracle_rejects_incomplete_or_fabricated_evidence(name, mutation):
    case = BUILDERS[name]('offline')
    actual = deepcopy(case.expected)
    if mutation == 'missing':
        actual.pop('evidence_refs')
    elif mutation == 'duplicate':
        actual['evidence_refs'][1] = actual['evidence_refs'][0]
    else:
        actual['evidence_refs'][0] = 'invented-document'
    with pytest.raises(AssertionError):
        validate_artifact(case, actual)


@pytest.mark.parametrize('name', BUILDERS)
def test_oracle_rejects_plausible_but_wrong_decision(name):
    case = BUILDERS[name]('offline')
    actual = deepcopy(case.expected)
    if name == 'access':
        actual['decisions'][4].update(allowed=True, reason='allowed')
    elif name == 'delivery':
        actual['orders'][5].update(status='out_of_stock', carrier=None)
    else:
        actual['releases'][1].update(revision=99, deployable=False)
    with pytest.raises(AssertionError):
        validate_artifact(case, actual)


@pytest.mark.parametrize('name,key', [('access', 'decisions'), ('delivery', 'orders'), ('release', 'releases')])
def test_oracle_rejects_duplicate_business_rows(name, key):
    case = BUILDERS[name]('offline')
    actual = deepcopy(case.expected)
    actual[key][1] = deepcopy(actual[key][0])
    with pytest.raises(AssertionError):
        validate_artifact(case, actual)


@pytest.mark.parametrize('source', ['assignment', 'retrieval', 'truncated', 'wrong_id', 'assistant'])
def test_visibility_requires_an_attributed_full_source_in_actual_model_input(source):
    report = SimpleNamespace(id='source-1', content='Original contract: {"prefix": "Bearer "}')
    publication = SimpleNamespace(run_id='run-1', content='{"kind":"final"}')
    envelope = {'id': report.id, 'content': report.content}
    role = 'user'
    if source == 'retrieval':
        envelope['message_id'] = envelope.pop('id')
        role = 'tool'
    elif source == 'truncated':
        envelope['content'] = report.content[:12]
    elif source == 'wrong_id':
        envelope['id'] = 'other-source'
    elif source == 'assistant':
        role = 'assistant'
    content = 'Current Group assignment:\n' + json.dumps({'background_messages': [envelope]})
    trace = [{'summary': False, 'assignment': 'run-1',
              'messages': [{'role': role, 'content': content}],
              'response': {'choices': [{'message': {'tool_calls': [{'function': {
                  'name': 'group_post', 'arguments': json.dumps({'content': publication.content})}}]}}]}}]
    if source in ('assignment', 'retrieval'):
        assert_report_visible(trace, publication, report)
    else:
        with pytest.raises(AssertionError):
            assert_report_visible(trace, publication, report)


@pytest.mark.parametrize('source,expected', [
    ('Document DOC-1 is ready.', False),
    ('{"ref":"DOC-1"}', False),
    ('Report: {"ref":"DOC-1","enabled":true}', True),
    ('{"wrapper":{"ref":"DOC-1","enabled":true},"analysis":"ready"}', True),
    ('{"ref":"DOC-1","enabled":1}', False),
])
def test_complete_report_validation_rejects_reference_only_and_changed_types(source, expected):
    assert contains_document(source, {'ref': 'DOC-1', 'enabled': True}) is expected
