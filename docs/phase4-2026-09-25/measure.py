"""Offline persistence counters. Only TemporaryDirectory storage is opened."""
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from autoapply import archive, history_statistics
from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import Listing, now
from autoapply.submission_probe import SubmissionProbe


def measured(db, action):
    counts = Counter(dict(db_commits=0, history_flushes=0, history_syncs=0,
                          bundle_exports=0, statistics_calculations=0,
                          db_writes=0, security_writes=0, file_rewrites=0))
    files = Counter()
    execute, flush, sync = db.execute, db.flush_history, db.history.sync
    write, stats = db.history._write, history_statistics.calculate_statistics
    replace, write_text = os.replace, Path.write_text
    def trace(sql):
        if sql.strip().upper() == 'COMMIT':
            counts['db_commits'] += 1
    def counted_execute(sql, args=(), **kwargs):
        mutation = sql.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}
        implicit = mutation and not db.conn.in_transaction
        if mutation:
            counts['db_writes'] += 1
            if sql.upper().startswith('UPDATE APPLICATIONS SET'):
                counts['security_writes'] += 1
        result = execute(sql, args, **kwargs)
        if implicit:
            counts['db_commits'] += 1  # SQLite autocommit has no COMMIT trace.
        return result
    def counted_flush(*args, **kwargs):
        counts['history_flushes'] += 1
        return flush(*args, **kwargs)
    def counted_sync(*args, **kwargs):
        counts['history_syncs'] += 1
        return sync(*args, **kwargs)
    def counted_write(*args, **kwargs):
        counts['bundle_exports'] += 1
        return write(*args, **kwargs)
    def counted_stats(*args, **kwargs):
        counts['statistics_calculations'] += 1
        return stats(*args, **kwargs)
    def rewritten(path):
        path = Path(path)
        if path.is_relative_to(db.history.root):
            counts['file_rewrites'] += 1
            files[str(path.relative_to(db.history.root))] += 1
    def counted_replace(source, target):
        result = replace(source, target)
        rewritten(target)
        return result
    def counted_text(path, *args, **kwargs):
        result = write_text(path, *args, **kwargs)
        rewritten(path)
        return result
    db.conn.set_trace_callback(trace)
    changes = db.conn.total_changes
    try:
        with ExitStack() as stack:
            for obj, name, replacement in [(db, 'execute', counted_execute),
                    (db, 'flush_history', counted_flush), (db.history, 'sync', counted_sync),
                    (db.history, '_write', counted_write),
                    (history_statistics, 'calculate_statistics', counted_stats),
                    (os, 'replace', counted_replace), (Path, 'write_text', counted_text)]:
                stack.enter_context(patch.object(obj, name, replacement))
            started = time.perf_counter()
            action()
            elapsed = time.perf_counter() - started
    finally:
        db.conn.set_trace_callback(None)
    return dict(counts, seconds=round(elapsed, 6), files=dict(files),
                sqlite_changes_including_triggers=db.conn.total_changes - changes)


def sample(workload):
    with tempfile.TemporaryDirectory(prefix='autoapply-phase4-') as folder:
        config = Config(folder, {'ai': {'enabled': False}, 'discord': {'enabled': False},
                               'gmail': {'enabled': False}, 'application': {'auto_submit': False}})
        (config.private / 'profile.yaml').write_text('identity:\n  first_name: Synthetic\n', encoding='utf-8')
        db = Database(config.private / 'synthetic.sqlite3', startup_maintenance=False)
        try:
            db.ingest(Listing('Synthetic', 'Software Intern', 'New York, NY',
                'https://jobs.lever.co/synthetic/one', 'synthetic', posted_at=now(),
                description='Synthetic description.'), config)
            fields = dict(security_state='NONE', ats_type='lever', current_url='https://example.test/form')
            db.update_security(1, **fields, diagnostics_at=now())
            def events():
                for n in range(10):
                    db.event(1, 'synthetic_observation', str(n))
            def batch():
                with db.transaction():
                    events()
            def security():
                for _ in range(10):
                    db.update_security(1, **fields, diagnostics_at=now())
            probe = SubmissionProbe(db, 1, None, None)
            def probe_events():
                for n in range(10):
                    probe.record('SYNTHETIC_OBSERVATION', {'sequence': n})
                    probe.save()
            actions = dict(separate_events=events, batched_events=batch,
                           unchanged_security=security, probe_observations=probe_events,
                           small_mutation=lambda: db.event(1, 'synthetic_observation', 'one'))
            result = measured(db, actions[workload])
            if workload == 'probe_observations':
                result['final_flush'] = measured(db, db.flush_history)
            return result
        finally:
            db.close()


if __name__ == '__main__':
    result = dict(python=platform.python_version(), sqlite=sqlite3.sqlite_version,
        workloads={name: sample(name) for name in ('separate_events', 'batched_events',
            'unchanged_security', 'small_mutation', 'probe_observations')})
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name('after.json')
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
