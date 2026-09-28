"""Frozen synthetic collaboration tasks and independent numerical acceptance."""

from dataclasses import dataclass
from itertools import permutations, product
import json
import re


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    source: dict
    revised: dict

    def data(self, revision):
        return self.source if revision == 1 else self.revised

    def schema(self):
        fields = {
            'rollout': '"plan":string,"cost":integer',
            'timeline': '"events":[{"id":string,"utc":integer}]',
            'schedule': '"starts":{"A":integer,"B":integer,"C":integer,"D":integer,"E":integer,"F":integer},"makespan":integer',
        }
        return '{"kind":"' + self.kind + '_final","case_id":"' + self.id + '","revision":integer,' + fields[self.kind] + '}'

    def prompt(self, revision):
        work = {
            'rollout': 'Choose the cheapest eligible rollout. All three constraints must hold: downtime <= maximum_downtime, rollback <= maximum_rollback, capacity >= minimum_capacity. Bob should calculate eligibility and costs; Carol should independently challenge the selection and constraints.',
            'timeline': 'Reconstruct the incident timeline. Local timestamp = UTC timestamp + server offset. Sort by UTC, then event ID for a tie. Bob should normalize timestamps; Carol should independently audit clock signs, ties, and event completeness. Chronology alone does not establish causality.',
            'schedule': 'Minimize the finishing time of all six nonpreemptive jobs. Start times are nonnegative integers. A machine executes at most one job at a time; every predecessor must finish before its successor starts. Bob should propose a concrete schedule; Carol should independently check dependencies, machine conflicts and whether a shorter feasible schedule exists.',
        }
        if revision == 1:
            change = 'Source data: ' + json.dumps(self.source, sort_keys=True)
        else:
            changed = {key: value for key, value in self.revised.items() if value != self.source.get(key)}
            change = ('Continue the same case using its previous source data. These replacement fields are authoritative; '
                      'all other fields remain unchanged: ' + json.dumps(changed, sort_keys=True))
        return (f'CASE {self.id} REVISION {revision}. {work[self.kind]} {change} '
                'Work through Bob and Carol rather than answering entirely on your own. '
                'The peers should bring their findings back so Alice can complete the task without further human scheduling. '
                'Alice should publish the final JSON only after both peer checks are available. '
                'Resolve conflicting findings before finalizing. On a revision, have both peers check the changed result again. '
                'Use this final artifact schema: ' + self.schema() + f' Set revision={revision}. '
                'Keep individual public messages concise and do not request acknowledgments after completion.')

    def recall(self, member):
        return (f'@{member} RECALL {self.id}. Using the earlier public discussion, reproduce the latest revision 2 '
                'final result for this case. Do not substitute a different case or the obsolete first revision. '
                'Do not ask another member to repeat the work. Return this JSON schema: ' + self.schema())


def cases():
    result = []
    for repetition in (1, 2):
        price_shift, time_shift = (repetition - 1) * 3, (repetition - 1) * 100
        plans = [dict(name=name, downtime=d, rollback=r, capacity=c, cost=p + price_shift)
                 for name, d, r, c, p in [('atlas', 6, 9, 120, 12), ('boreal', 9, 4, 120, 10),
                                         ('cirrus', 5, 5, 90, 8), ('delta', 7, 6, 120, 11)]]
        rollout = dict(plans=plans, maximum_downtime=8, maximum_rollback=6, minimum_capacity=100)
        result.append(Case(f'ROL-{repetition}', 'rollout', rollout, {**rollout, 'minimum_capacity': 90}))
        events = [dict(id=f'E{index}', server=server, local=local + time_shift)
                  for index, (server, local) in enumerate([
                      ('db', 1004), ('api', 1027), ('monitor', 1015), ('queue', 1022),
                      ('monitor', 1035), ('db', 1044), ('api', 1062), ('queue', 1047)], 1)]
        timeline = dict(offsets=dict(api=12, queue=-8, db=4, monitor=0), events=events)
        result.append(Case(f'INC-{repetition}', 'timeline', timeline,
                           {**timeline, 'offsets': {**timeline['offsets'], 'api': 30}}))
        jobs = {name: dict(machine=machine, duration=duration, after=after)
                for name, machine, duration, after in [
                    ('A', 'M1', 3, []), ('B', 'M2', 2, []), ('C', 'M1', 4, ['B']),
                    ('D', 'M2', 3, ['A']), ('F', 'M2', 1, ['C']), ('E', 'M1', 2, ['C', 'D', 'F'])]}
        result.append(Case(f'SCH-{repetition}', 'schedule', {'jobs': jobs},
                           {'jobs': {**jobs, 'C': {**jobs['C'], 'duration': 2}}}))
    return result


def optimal_schedule(jobs):
    """Enumerate machine orders, then compute earliest starts in each acyclic DAG."""
    groups = [[name for name, job in jobs.items() if job['machine'] == machine]
              for machine in sorted({job['machine'] for job in jobs.values()})]
    candidates = []
    for orders in product(*(permutations(group) for group in groups)):
        deps = {name: set(job['after']) for name, job in jobs.items()}
        for order in orders:
            for before, after in zip(order, order[1:]):
                deps[after].add(before)
        ends, starts = {}, {}
        while len(ends) < len(jobs):
            ready = [name for name in jobs if name not in ends and deps[name] <= ends.keys()]
            if not ready:
                break
            for name in ready:
                starts[name] = max((ends[dep] for dep in deps[name]), default=0)
                ends[name] = starts[name] + jobs[name]['duration']
        if len(ends) == len(jobs):
            candidates.append((max(ends.values()), starts))
    return min(candidates, key=lambda item: item[0])


def expected(case, revision):
    data = case.data(revision)
    base = dict(kind=case.kind + '_final', case_id=case.id, revision=revision)
    if case.kind == 'rollout':
        eligible = [plan for plan in data['plans'] if plan['downtime'] <= data['maximum_downtime']
                    and plan['rollback'] <= data['maximum_rollback'] and plan['capacity'] >= data['minimum_capacity']]
        best = min(eligible, key=lambda plan: plan['cost'])
        return {**base, 'plan': best['name'], 'cost': best['cost']}
    if case.kind == 'timeline':
        events = sorted([dict(id=e['id'], utc=e['local'] - data['offsets'][e['server']])
                         for e in data['events']], key=lambda event: (event['utc'], event['id']))
        return {**base, 'events': events}
    makespan, starts = optimal_schedule(data['jobs'])
    return {**base, 'starts': starts, 'makespan': makespan}


def validate(case, revision, value):
    """Validate typed output and scheduling feasibility, accepting valid slack."""
    oracle = expected(case, revision)
    for field in ('kind', 'case_id', 'revision'):
        assert type(value[field]) is type(oracle[field]) and value[field] == oracle[field], field
    if case.kind != 'schedule':
        for field in oracle.keys() - {'kind', 'case_id', 'revision'}:
            assert json.dumps(value[field], sort_keys=True) == json.dumps(oracle[field], sort_keys=True), field
        return
    jobs, starts = case.data(revision)['jobs'], value['starts']
    assert set(starts) == set(jobs), 'job identities'
    assert all(type(start) is int and start >= 0 for start in starts.values()), 'start times'
    ends = {name: starts[name] + job['duration'] for name, job in jobs.items()}
    for name, job in jobs.items():
        assert all(ends[dep] <= starts[name] for dep in job['after']), f'{name} precedences'
        for other, peer in jobs.items():
            if name != other and job['machine'] == peer['machine']:
                assert ends[name] <= starts[other] or ends[other] <= starts[name], 'machine overlap'
    assert type(value['makespan']) is int and value['makespan'] == max(ends.values()) == oracle['makespan'], 'optimal makespan'


def artifacts(content):
    """Extract JSON objects embedded in a public message, without grading prose."""
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field')
            value[key] = item
        return value
    cursor = 0
    decoder = json.JSONDecoder(object_pairs_hook=unique_fields)
    while cursor < len(content):
        start = content.find('{', cursor)
        if start < 0:
            return
        try:
            value, end = decoder.raw_decode(content[start:])
        except ValueError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            yield value
        cursor = start + end


def final_attempts(content):
    """Retain identifiable malformed final attempts instead of cherry-picking."""
    values = [value for value in artifacts(content)
              if isinstance(value.get('kind'), str) and value['kind'].endswith('_final')]
    markers = re.findall(r'"kind"\s*:\s*"[^"\n]+_final"', content)
    errors = ['Malformed or duplicate-field final JSON'] if len(markers) > len(values) else []
    return values, errors
