"""Offline examples must start repeatably without reusing missing sessions."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_example(module, directory, *arguments):
    env = {**os.environ, 'PYTHONPATH': str(ROOT), 'TMPDIR': str(directory)}
    return subprocess.run([sys.executable, '-m', module, *arguments], cwd=directory,
                          env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('module,runs', [
    ('examples.group_on_demand', 4), ('examples.group_manual', 2),
])
def test_default_example_runs_use_independent_stores(tmp_path, module, runs):
    for _ in range(2):
        result = run_example(module, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
    stores = list(tmp_path.rglob('*.sqlite'))
    assert len(stores) == 2
    for store in stores:
        with sqlite3.connect(store) as db:
            assert db.execute('SELECT state,reason FROM invocations').fetchall() == [('terminal', 'completed')]
            assert db.execute('SELECT COUNT(*) FROM assignments').fetchone()[0] == runs


@pytest.mark.parametrize('module', ['examples.group_on_demand', 'examples.group_manual'])
def test_existing_example_store_is_rejected_without_mutation(tmp_path, module):
    store = tmp_path / 'retained.sqlite'
    first = run_example(module, tmp_path, '--store', str(store))
    assert first.returncode == 0, first.stdout + first.stderr
    before = store.read_bytes()
    second = run_example(module, tmp_path, '--store', str(store))
    assert second.returncode == 2, second.stdout + second.stderr
    assert '--store' in second.stderr and 'Traceback' not in second.stderr
    assert store.read_bytes() == before
    with sqlite3.connect(store) as db:
        assert db.execute('SELECT state,reason FROM invocations').fetchall() == [('terminal', 'completed')]
