"""Frozen distributed-evidence tasks and evaluator-only semantic oracles.

Expected artifacts never enter model prompts, tools, or public messages.
These tasks were specified before the first held-out provider run.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HeldoutCase:
    name: str
    evidence: dict
    task: str
    expected: dict


def access_case(nonce):
    evidence = {
        'identity': {'ref': f'IDENTITY-{nonce}', 'users': [
            {'id': 'alex', 'active': True, 'groups': ['eng'], 'mfa': True},
            {'id': 'bea', 'active': True, 'groups': ['ops'], 'mfa': False},
            {'id': 'cal', 'active': False, 'groups': ['ops'], 'mfa': True},
            {'id': 'dia', 'active': True, 'groups': ['eng', 'ops'], 'mfa': True}]},
        'inventory': {'ref': f'INVENTORY-{nonce}', 'requests': [
            {'id': 'Q1', 'user': 'alex', 'resource': 'docs'},
            {'id': 'Q2', 'user': 'alex', 'resource': 'production'},
            {'id': 'Q3', 'user': 'bea', 'resource': 'production'},
            {'id': 'Q4', 'user': 'cal', 'resource': 'production'},
            {'id': 'Q5', 'user': 'dia', 'resource': 'production'},
            {'id': 'Q6', 'user': 'dia', 'resource': 'docs'}]},
        'policy': {'ref': f'POLICY-{nonce}', 'resources': {
            'docs': {'required_group': 'eng', 'require_mfa': False},
            'production': {'required_group': 'ops', 'require_mfa': True}},
            'explicit_denies': [{'user': 'dia', 'resource': 'production'}],
            'rules': 'All users must be active. Required group membership and required MFA '
                     'must both hold. An explicit deny overrides every grant.',
            'reason_precedence': ['explicit_deny', 'inactive', 'missing_group', 'mfa_required', 'allowed']},
    }
    expected = {'kind': 'access_review', 'decisions': [
        {'request': 'Q1', 'allowed': True, 'reason': 'allowed'},
        {'request': 'Q2', 'allowed': False, 'reason': 'missing_group'},
        {'request': 'Q3', 'allowed': False, 'reason': 'mfa_required'},
        {'request': 'Q4', 'allowed': False, 'reason': 'inactive'},
        {'request': 'Q5', 'allowed': False, 'reason': 'explicit_deny'},
        {'request': 'Q6', 'allowed': True, 'reason': 'allowed'}],
        'evidence_refs': [doc['ref'] for doc in evidence.values()]}
    task = ('Perform access recertification using identity, inventory, and policy evidence. '
            'Evaluate every request and select the first applicable reason under policy precedence. '
            'Publish JSON {"kind":"access_review","decisions":[{"request":string,"allowed":boolean,'
            '"reason":string}],"evidence_refs":[strings]}. Include every request exactly once and '
            'all three original document ref values, not Group message IDs.')
    return HeldoutCase('access', evidence, task, expected)


def delivery_case(nonce):
    evidence = {
        'orders': {'ref': f'ORDERS-{nonce}', 'orders': [
            {'id': 'O1', 'priority': 1, 'zone': 'east', 'units': 4, 'deadline_days': 2, 'cold': True},
            {'id': 'O2', 'priority': 2, 'zone': 'west', 'units': 3, 'deadline_days': 1, 'cold': False},
            {'id': 'O3', 'priority': 3, 'zone': 'east', 'units': 3, 'deadline_days': 3, 'cold': False},
            {'id': 'O4', 'priority': 4, 'zone': 'east', 'units': 2, 'deadline_days': 1, 'cold': False},
            {'id': 'O5', 'priority': 5, 'zone': 'west', 'units': 1, 'deadline_days': 3, 'cold': True},
            {'id': 'O6', 'priority': 6, 'zone': 'west', 'units': 1, 'deadline_days': 3, 'cold': False}]},
        'warehouse': {'ref': f'STOCK-{nonce}', 'available_units': 8,
            'allocation': 'Process orders in ascending numeric priority. Never split an order. '
                          'First find a qualifying carrier. If none exists, mark no_carrier and consume '
                          'no stock. Otherwise if stock is insufficient mark out_of_stock and consume '
                          'none. Otherwise ship the whole order and deduct its units.'},
        'carriers': {'ref': f'CARRIERS-{nonce}', 'services': [
            {'id': 'cold_east', 'zones': ['east'], 'days': 2, 'cold': True, 'fee': 8},
            {'id': 'ground_east', 'zones': ['east'], 'days': 2, 'cold': False, 'fee': 4},
            {'id': 'fast_east', 'zones': ['east'], 'days': 1, 'cold': False, 'fee': 9},
            {'id': 'ground_west', 'zones': ['west'], 'days': 2, 'cold': False, 'fee': 5}],
            'selection': 'A carrier must cover the zone, arrive within the deadline (inclusive), '
                         'and support cold transport when requested. Choose the lowest fee among '
                         'qualifying services. Each fee is charged once per shipped order, not per unit.'},
    }
    expected = {'kind': 'delivery_plan', 'orders': [
        {'order': 'O1', 'status': 'shipped', 'carrier': 'cold_east'},
        {'order': 'O2', 'status': 'no_carrier', 'carrier': None},
        {'order': 'O3', 'status': 'shipped', 'carrier': 'ground_east'},
        {'order': 'O4', 'status': 'out_of_stock', 'carrier': None},
        {'order': 'O5', 'status': 'no_carrier', 'carrier': None},
        {'order': 'O6', 'status': 'shipped', 'carrier': 'ground_west'}],
        'remaining_units': 0, 'total_shipping_fee': 17,
        'evidence_refs': [doc['ref'] for doc in evidence.values()]}
    task = ('Produce a whole-order delivery plan using orders, warehouse, and carriers evidence. '
            'Follow allocation order and carrier qualification exactly. Publish JSON '
            '{"kind":"delivery_plan","orders":[{"order":string,"status":string,"carrier":string_or_null}],'
            '"remaining_units":integer,"total_shipping_fee":integer,"evidence_refs":[strings]}. '
            'Include every order exactly once; unshipped orders have null carrier. Use original '
            'document ref values for all three evidence sources.')
    return HeldoutCase('delivery', evidence, task, expected)


def release_case(nonce):
    evidence = {
        'components': {'ref': f'GRAPH-{nonce}', 'requires': {
            'api1': ['core1'], 'api2': ['core2', 'adapter2'],
            'adapter2': ['storage2'], 'core2': ['storage2'], 'core1': ['storage1'],
            'monitor1': ['core1'], 'storage1': [], 'storage2': []}},
        'compatibility': {'ref': f'COMPAT-{nonce}', 'conflicts': [
            {'id': 'core_generation', 'when_all_present': ['core1', 'core2']},
            {'id': 'storage_generation', 'when_all_present': ['storage1', 'storage2']},
            {'id': 'adapter_storage', 'when_all_present': ['adapter2', 'storage1']}],
            'rule': 'A release is deployable only when its root components plus all transitive '
                    'prerequisites contain none of these conflict pairs. Components outside the '
                    'selected closure do not count.'},
        'decisions': {'ref': f'DECISIONS-{nonce}', 'releases': ['blue', 'green'], 'records': [
            {'release': 'blue', 'revision': 1, 'approved': True, 'roots': ['api2', 'monitor1']},
            {'release': 'green', 'revision': 1, 'approved': True, 'roots': ['api1', 'monitor1']},
            {'release': 'green', 'revision': 2, 'approved': True, 'roots': ['api2']},
            {'release': 'green', 'revision': 99, 'approved': False, 'roots': ['api2', 'monitor1']}],
            'rule': 'For each requested release choose the highest approved numeric revision. '
                    'Unapproved records never supersede approved records.'},
    }
    expected = {'kind': 'release_review', 'releases': [
        {'release': 'blue', 'revision': 1, 'roots': ['api2', 'monitor1'],
         'closure': ['api2', 'monitor1', 'core2', 'adapter2', 'storage2', 'core1', 'storage1'],
         'conflicts': ['core_generation', 'storage_generation', 'adapter_storage'], 'deployable': False},
        {'release': 'green', 'revision': 2, 'roots': ['api2'],
         'closure': ['api2', 'core2', 'adapter2', 'storage2'], 'conflicts': [], 'deployable': True}],
        'evidence_refs': [doc['ref'] for doc in evidence.values()]}
    task = ('Review each requested release using components, compatibility, and decisions evidence. '
            'Select its effective approved roots, compute the full transitive closure including roots, '
            'and evaluate all compatibility rules within that closure. Publish JSON '
            '{"kind":"release_review","releases":[{"release":string,"revision":integer,"roots":[strings],'
            '"closure":[strings],"conflicts":[rule_ids],"deployable":boolean}],"evidence_refs":[strings]}. '
            'Return each release once and each set entry once, with all original evidence ref values.')
    return HeldoutCase('release', evidence, task, expected)


BUILDERS = {'access': access_case, 'delivery': delivery_case, 'release': release_case}


def validate_artifact(case, actual):
    """Compare semantic values without grading prose or imposing list order."""
    def compare(value, expected, path):
        assert type(value) is type(expected), (path, value, expected)
        if isinstance(expected, dict):
            # Additional explanatory fields do not invalidate a correct artifact.
            assert expected.keys() <= value.keys(), (path, value, expected)
            for key, child in expected.items():
                compare(value[key], child, f'{path}.{key}')
        elif isinstance(expected, list):
            assert len(value) == len(expected), (path, value, expected)
            if expected and isinstance(expected[0], dict):
                identity = next(key for key in ('request', 'order', 'release') if key in expected[0])
                assert all(isinstance(item, dict) and identity in item for item in value), (path, value)
                indexed = {item[identity]: item for item in value}
                assert len(indexed) == len(value), (path, 'duplicate identity', value)
                assert set(indexed) == {item[identity] for item in expected}, (path, value, expected)
                for item in expected:
                    compare(indexed[item[identity]], item, f'{path}[{item[identity]}]')
            else:
                assert sorted(value) == sorted(expected), (path, value, expected)
        else:
            assert value == expected, (path, value, expected)
    compare(actual, case.expected, case.name)
