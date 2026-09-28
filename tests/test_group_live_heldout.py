"""Frozen unseen tasks: one external request, real peer-controlled handoffs.

Run all six cases once before interpreting results. Do not tune the protocol or
task prompts to failed results; corrections require a separately labelled run.
"""

import json
import os
from uuid import uuid4

import pytest

from group_heldout_cases import BUILDERS, validate_artifact
from test_group_live_collaboration import (
    artifacts, assert_quiet, collaboration_case, public_messages,
)


pytestmark = pytest.mark.skipif(os.environ.get('MAS_RUN_LIVE_GROUP_TESTS') != '1',
    reason='Set MAS_RUN_LIVE_GROUP_TESTS=1 to use the configured provider')


COORDINATOR = (
    'Coordinate the assigned task using the private evidence owners listed in the task. '
    'You have no private evidence document. Request the necessary owners to read and report their '
    'evidence, then yield while they work. They will explicitly request your next turn after reporting. '
    'Use the shared reports to solve the task; do not invent missing evidence. If some requested '
    'reports are still pending, reply with a brief progress update and yield; do not repeatedly '
    'request the same pending work. Retrieve public originals when needed. Publish the final JSON '
    'artifact only after all source reports are available and you have checked the result. '
    'If later report notifications arrive after a correct final artifact, link a brief reply to '
    'those triggers and yield without restarting collection. Do not request acknowledgments.'
)

EVIDENCE_OWNER = (
    'You own one private evidence document. When asked for evidence, read it and publish the '
    'complete original JSON including its ref and every field in a linked public reply. '
    'Then call group_request for coordinator to integrate your report with the other requested '
    'evidence and finish the original task when enough information is available. Yield after '
    'this handoff. Do not fabricate other owners\' evidence or request acknowledgments.'
)


def contains_document(content, document):
    """Find the complete typed authoritative JSON, optionally wrapped in prose."""
    if isinstance(content, dict):
        if document.keys() <= content.keys():
            candidate = {key: content[key] for key in document}
            if json.dumps(candidate, sort_keys=True) == json.dumps(document, sort_keys=True):
                return True
        return any(contains_document(value, document) for value in content.values())
    if isinstance(content, list):
        return any(contains_document(value, document) for value in content)
    if isinstance(content, str):
        for index, char in enumerate(content):
            if char == '{':
                try:
                    value, _ = json.JSONDecoder().raw_decode(content[index:])
                except ValueError:
                    continue
                if contains_document(value, document):
                    return True
    return False


def assert_report_visible(trace, publication, report):
    """Match actual public source identity in the input that produced the final artifact."""
    def contains_source(value):
        if isinstance(value, dict):
            if (value.get('id', value.get('message_id')) == report.id
                    and value.get('content') == report.content):
                return True
            return any(contains_source(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_source(child) for child in value)
        if isinstance(value, str):
            # Assignment envelopes have a text prefix; tool results are JSON.
            for index, char in enumerate(value):
                if char == '{':
                    try:
                        decoded, _ = json.JSONDecoder().raw_decode(value[index:])
                    except ValueError:
                        continue
                    if contains_source(decoded):
                        return True
        return False

    for record in trace:
        if record['summary'] or record['assignment'] != publication.run_id:
            continue
        calls = [call for choice in record.get('response', {}).get('choices', [])
                 for call in (choice.get('message', {}).get('tool_calls') or [])]
        for call in calls:
            function = call['function']
            args = function['arguments']
            args = json.loads(args) if isinstance(args, str) else args
            if function['name'] == 'group_post' and args.get('content') == publication.content:
                assert any(contains_source(message['content']) for message in record['messages']
                           if message['role'] in ('user', 'tool')), (
                    'Full attributable public report absent from final model input', report.id)
                return
    pytest.fail('Final artifact was not produced by a traced real group_post call')


@pytest.mark.parametrize('name', BUILDERS)
@pytest.mark.parametrize('repetition', [1, 2])
async def test_frozen_peer_handoffs_on_unseen_task(tmp_path, name, repetition):
    task = BUILDERS[name](f'{repetition}-{uuid4().hex[:8]}')
    roles = {'coordinator': COORDINATOR, **{owner: EVIDENCE_OWNER for owner in task.evidence}}
    async with collaboration_case(tmp_path, roles, task.evidence) as case:
        case.summary.update(task=name, repetition=repetition, protocol='heldout-v1', external_requests=1)
        (tmp_path / 'task-contract.json').write_text(json.dumps({
            'task': task.task, 'evidence': task.evidence, 'expected': task.expected,
            'roles': roles,
        }, indent=2), encoding='utf-8')
        await case.group.request(case.scope,
            task.task + f' Evidence owners: {", ".join(task.evidence)}. '
            'Complete the collaboration through member-issued handoffs; there will be no '
            'separate application synthesis request.', key='start', recipients=('coordinator',))
        await assert_quiet(case, min_runs=5)
        assert set(case.reads) == set(task.evidence), case.reads
        messages = await public_messages(case)
        publications = artifacts(messages, task.expected['kind'], sender='coordinator')
        assert publications, 'No final artifact despite an idle runtime'
        case.summary['final_artifacts'] = [value for _, value in publications]
        for publication, actual in publications:
            validate_artifact(task, actual)
            for owner, document in task.evidence.items():
                reports = [message for message in messages if message.sender == owner
                           and contains_document(message.content, document)
                           and message.sequence < publication.sequence and message.reply_to]
                assert reports, ('No prior attributed linked evidence report', owner)
                for report in reports:
                    try:
                        assert_report_visible(case.models['coordinator'].trace, publication, report)
                    except AssertionError:
                        continue
                    break
                else:
                    pytest.fail(f'No complete {owner} report visible in the final publication input')
