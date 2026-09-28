"""Opt-in sustained real-model collaboration through the actual Group CLI.

All cases run in one process with unchanged production prompts and tools. The
script specifies business collaboration, but never fabricates replies, invokes
peer tools on the model's behalf, or silently repairs response evidence.
"""

from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from group import state
from group.scheduling import ResponseEvidence
from group_endurance_cases import cases, expected, final_attempts, validate


pytestmark = pytest.mark.skipif(os.environ.get('MAS_RUN_LIVE_GROUP_TESTS') != '1',
    reason='Set MAS_RUN_LIVE_GROUP_TESTS=1 to use the configured provider')


def process_sample(pid):
    try:
        status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
        return dict(rss_kib=int(status.get('VmRSS', '0 kB').split()[0]),
                    threads=int(status.get('Threads', '0')), fds=len(list(Path(f'/proc/{pid}/fd').iterdir())))
    except (FileNotFoundError, ProcessLookupError):
        return None


def phase_results(db, tasks, messages):
    by_id = {case.id: case for case in tasks}
    external = [message for message in messages if message['sender'] == 'user'
                and (message['content'].startswith('CASE ') or message['content'].startswith('RECALL '))]
    assignments = {row['id']: dict(row) for row in db.execute('SELECT * FROM assignments')}
    opportunities = [dict(row) for row in db.execute('SELECT * FROM opportunities')]
    results = []
    for index, origin in enumerate(external):
        recall = origin['content'].startswith('RECALL ')
        case_id = origin['content'].split()[1].rstrip('.')
        revision = 2 if recall else int(origin['content'].split()[3].rstrip('.'))
        case = by_id[case_id]
        end = external[index + 1]['sequence'] if index + 1 < len(external) else float('inf')
        segment = [m for m in messages if origin['sequence'] <= m['sequence'] < end]
        sources = {message['id'] for message in segment}
        relevant = [o for o in opportunities if o['message_id'] in sources]
        runs = [assignments[identity] for identity in sorted({o['assignment_id'] for o in relevant if o['assignment_id']})]
        evidence = [ResponseEvidence(*row) for row in state.reply_evidence(db, origin['invocation_id'])
                    if row[0] in {o['id'] for o in relevant}]
        missing = [asdict(item) for item in evidence if not item.satisfied]
        member = json.loads(origin['recipients'])[0] if recall else 'alice'
        finals, errors = [], []
        for message in segment:
            if message['sender'] == member:
                values, parse_errors = final_attempts(message['content'])
                finals.extend((message, value) for value in values)
                errors.extend(f'{message["id"]}: {error}' for error in parse_errors)
        if not finals:
            errors.append('No final artifact')
        for _, value in finals:
            try:
                validate(case, revision, value)
            except (AssertionError, KeyError, TypeError, AttributeError, ValueError) as error:
                errors.append(f'{type(error).__name__}: {error}')
        peer_order = recall or (bool(finals) and all(
            any(m['sender'] == peer and m['sequence'] < final['sequence'] for m in segment)
            for final, _ in finals for peer in ('bob', 'carol')))
        participants = sorted({run['member_id'] for run in runs})
        independent_recall = recall and participants == [member] and len(runs) == 1 and len(relevant) == 1
        availability = []
        for final, _ in finals:
            manifest = db.execute('SELECT high_water FROM assignment_inputs WHERE assignment_id=?',
                                  (final['run_id'],)).fetchone()
            availability.append(dict(final_id=final['id'], high_water=manifest[0] if manifest else None,
                prior_peer_messages=[dict(message_id=m['id'], sender=m['sender'],
                    captured_at_admission=bool(manifest and m['sequence'] <= manifest[0]))
                    for m in segment if m['sender'] in ('bob', 'carol') and m['sequence'] < final['sequence']]))
        result = dict(case_id=case_id, revision=revision, recall=recall, expected=expected(case, revision),
                      artifacts=[value for _, value in finals], semantic_errors=errors,
                      participants=participants, member_runs=len(runs),
                      public_messages=len(segment), missing_responses=missing,
                      unassigned_opportunities=[o['id'] for o in relevant if not o['assignment_id']],
                      all_runs_settled=all(run['state'] == 'settled' for run in runs),
                      run_errors=[run['error'] or run['outcome'] for run in runs
                                  if run['error'] or run['outcome'] not in ('completed', 'tool_stop')],
                      final_after_peer_messages=bool(peer_order), data_availability=availability,
                      peer_publications=[dict(id=m['id'], sender=m['sender'], content=m['content'])
                                         for m in segment if m['sender'] in ('bob', 'carol')],
                      independent_recall=independent_recall if recall else None)
        result['semantic_pass'] = not errors
        result['protocol_pass'] = (not missing and not result['run_errors']
                                   and len(evidence) == len(relevant) and result['all_runs_settled'])
        result['participation_pass'] = bool(independent_recall if recall else
                                           peer_order and participants == ['alice', 'bob', 'carol'])
        results.append(result)
    return results


def test_continuous_complex_collaboration_via_real_cli(tmp_path):
    tasks = cases()
    commands = ['/members']
    for case in tasks:
        for revision in (1, 2):
            commands.extend([f'/post PASSIVE_NOTE {case.id} revision {revision}: retain this marker without requesting a reply.',
                             case.prompt(revision), '/status'])
        # Completion refusal remains visible; cancellation isolates the next
        # case without erasing sessions, publications or unresolved evidence.
        commands.extend(['/finish', '/cancel'])
    commands.extend([tasks[0].recall('carol'), tasks[1].recall('bob'), '/status', '/finish', '/cancel', '/exit'])
    script = '\n'.join(commands) + '\n'
    (tmp_path / 'commands.txt').write_text(script, encoding='utf-8')
    (tmp_path / 'contracts.json').write_text(json.dumps([asdict(case) for case in tasks], indent=2))
    db_path, log_path = tmp_path / 'group.sqlite', tmp_path / 'cli.log'
    arguments = [sys.executable, '-m', 'cli.group', '--store', str(db_path),
                 '--max-turns', '12', '--max-runs', '24', '--timeout', '240']
    started = time.monotonic()
    samples, timed_out = [], False
    with (tmp_path / 'commands.txt').open('r', encoding='utf-8') as stdin, log_path.open('w') as stdout:
        process = subprocess.Popen(arguments, stdin=stdin, stdout=stdout, stderr=subprocess.STDOUT,
                                   cwd=Path(__file__).resolve().parents[1], env=os.environ.copy())
        try:
            while process.poll() is None:
                sample = process_sample(process.pid)
                if sample:
                    samples.append(sample)
                if time.monotonic() - started > 1200:
                    timed_out = True
                    process.send_signal(signal.SIGINT)
                    process.wait(timeout=150)
                    break
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    duration = time.monotonic() - started
    log = log_path.read_text()
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        messages = [dict(row) for row in db.execute('SELECT * FROM messages ORDER BY sequence')]
        invocations = [dict(row) for row in db.execute('SELECT * FROM invocations')]
        phases = phase_results(db, tasks, messages)
        unsettled = db.execute("SELECT COUNT(*) FROM assignments WHERE state!='settled'").fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM opportunities WHERE state='pending'").fetchone()[0]
        assignments = db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0]
        passive_opportunities = db.execute("SELECT COUNT(*) FROM opportunities o JOIN messages m ON o.message_id=m.id WHERE m.content LIKE 'PASSIVE_NOTE %'").fetchone()[0]
    with db_path.with_suffix('.sqlite.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    metrics = {key: dict(first=samples[0][key], last=samples[-1][key], peak=max(s[key] for s in samples))
               for key in ('rss_kib', 'threads', 'fds')} if samples else {}
    structural = (process.returncode == 0 and not timed_out and not unsettled and not pending
                  and not passive_opportunities and len(phases) == 14
                  and all(scope['state'] == 'terminal' for scope in invocations)
                  and sum(m['sender'] == 'user' for m in messages) == 26
                  and 'Traceback' not in log and 'Task was destroyed' not in log)
    summary = dict(duration_seconds=duration, returncode=process.returncode, timed_out=timed_out,
                   provider_requests=log.count('HTTP Request: POST'), member_runs=assignments,
                   public_messages=len(messages), process_metrics=metrics, invocations=invocations,
                   structural_pass=structural, pending_after_close=pending, unsettled_after_close=unsettled,
                   passive_opportunities=passive_opportunities, phases=phases)
    (tmp_path / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in summary.items() if key not in ('phases', 'invocations')}))
    for phase in phases:
        print(json.dumps({key: phase[key] for key in ('case_id', 'revision', 'recall', 'semantic_pass',
                                                    'protocol_pass', 'participation_pass', 'member_runs')}))
    assert structural, f'Process/runtime failure; inspect {tmp_path}'
    assert all(p['semantic_pass'] and p['protocol_pass'] and p['participation_pass'] for p in phases), (
        f'Collaboration acceptance failed; full unmodified evidence: {tmp_path / "result.json"}')
