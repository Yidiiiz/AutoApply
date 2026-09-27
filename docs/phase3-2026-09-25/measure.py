"""Phase 3 compatible offline maintenance measurements; all database/profile/history writes use TemporaryDirectory.

Run from the repository: .venv/Scripts/python.exe docs/phase3-2026-09-25/measure.py
No browser, provider, production Config, or production Database is constructed.
"""
import json
import platform
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import now


def measured(db, action):
    counts = Counter()
    execute, sync = db.execute, db.history.sync
    refresh = db._refresh_listing_statistics
    def counted_refresh(*args, **kwargs):
        counts['statistics_refreshes'] += 1
        return refresh(*args, **kwargs)
    def counted_execute(sql, args=()):
        counts[sql.strip().split()[0].upper()] += 1
        if sql.lstrip().upper().startswith(('UPDATE LISTING_OBSERVATIONS', 'INSERT INTO LISTING_OBSERVATIONS')):
            counts['observation_writes'] += 1
        return execute(sql, args)
    def counted_sync(*args):
        counts['history_sync_calls'] += 1
        return sync(*args)
    changes = db.conn.total_changes
    with patch.object(db, 'execute', counted_execute), patch.object(db.history, 'sync', counted_sync), patch.object(db, '_refresh_listing_statistics', counted_refresh):
        started = time.perf_counter()
        action()
        elapsed = time.perf_counter() - started
    return {'seconds': round(elapsed, 6), 'database_execute_calls': dict(counts),
            'sqlite_total_changes_including_triggers': db.conn.total_changes - changes}


def sample(size):
    with tempfile.TemporaryDirectory(prefix='autoapply-offline-audit-') as folder:
        config = Config(folder, {'ai': {'enabled': False}, 'discord': {'enabled': False}, 'gmail': {'enabled': False}})
        (config.private / 'profile.yaml').write_text('identity:\n  first_name: Synthetic\n', encoding='utf-8')
        db = Database(config.private / 'audit.sqlite3', startup_maintenance=False)
        try:
            stamp = now()
            with db.transaction():
                for n in range(1, size + 1):
                    db.execute('''INSERT INTO jobs(id,identity_key,company,title,location,canonical_url,ats,
                        posted_at,discovered_at,status,listing_status,freshness_state,listing_active,last_seen_at,
                        original_posted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (n, f'lever:audit:{n}', 'Synthetic', 'Software Intern', 'New York, NY',
                         f'https://jobs.lever.co/audit/{n}', 'lever', stamp, stamp, 'QUEUED', 'ACTIVE', 'FRESH', 1, stamp, stamp))
                    db.execute("INSERT INTO applications(id,job_id,status,updated_at) VALUES (?,?,'QUEUED',?)", (n,n,stamp))
                    db.execute('''INSERT INTO job_sources(job_id,source_name,source_url,source_job_id,first_seen,last_seen)
                        VALUES (?,'synthetic',?,?,?,?)''', (n,f'https://jobs.lever.co/audit/{n}',str(n),stamp,stamp))
                    db.observe_listing(f'lever:audit:{n}',f'https://jobs.lever.co/audit/{n}','synthetic',stamp,
                                       'FRESH','ACTIVE',job_id=n,reference=stamp)
            with db.transaction():
                for n in range(1, size + 1):
                    db.event(n, 'synthetic', 'offline')
            # Warm-up canonical metadata, then measure an unchanged cleanup.
            db.cleanup_stale_listings()
            results = {'listings': size}
            results['unchanged_cleanup'] = measured(db, db.cleanup_stale_listings)
            queries = {
                'identity_or_url': ("SELECT * FROM jobs WHERE identity_key=? OR canonical_url=?", ('lever:audit:1','https://jobs.lever.co/audit/1')),
                'events_by_app': ('SELECT * FROM events WHERE application_id=? ORDER BY id', (1,)),
                'sources_by_job': ('SELECT * FROM job_sources WHERE job_id=?', (1,)),
                'written_reuse': ('SELECT * FROM written_responses WHERE question=? AND company=? AND job_title=? AND verified=1 ORDER BY id DESC', ('Why?','Synthetic','Software Intern')),
                'known_answer': ('SELECT * FROM known_answers WHERE normalized_question=? AND scope=? AND verified=1', ('first name','global')),
                'daily_count': ('SELECT count(*) FROM applications WHERE substr(submit_intent_at,1,10)=?', (stamp[:10],)),
                'queue': ("""SELECT a.id FROM applications a JOIN jobs j ON j.id=a.job_id
                    WHERE j.listing_active=1 AND j.listing_status='ACTIVE' AND a.status IN ('QUEUED','RETRY')
                    AND a.retry_allowed=1 AND a.submit_intent_at IS NULL AND (a.retry_at IS NULL OR a.retry_at<=?)
                    AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key='duplicate_submission_guard:' || a.id
                        AND s.value NOT IN ('false','null','0')) ORDER BY j.priority DESC,j.discovered_at,a.id LIMIT 1""", (stamp,)),
            }
            results['plans_before'] = {name:[r['detail'] for r in db.rows('EXPLAIN QUERY PLAN '+sql,args)] for name,(sql,args) in queries.items()}
            return results
        finally:
            db.close()


if __name__ == '__main__':
    print(json.dumps({'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                      'method': 'Synthetic temporary data; timings are single samples, not production throughput.',
                      'samples': [sample(100), sample(1000)]}, indent=2))
