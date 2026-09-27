"""Local lifecycle counters; no production data or browser access."""
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from autoapply.config import Config
from autoapply.database import Database
from autoapply.models import Listing, now
from autoapply.retry import RetryPolicy


def measure(db, operation):
    counts = Counter()
    execute, decide, sync = db.execute, RetryPolicy.decide, db.history.sync
    def sql(statement, args=()):
        if statement.startswith('UPDATE applications SET'):
            counts['application_updates'] += 1
        return execute(statement, args)
    def retry(*args, **kwargs):
        counts['retry_decisions'] += 1
        return decide(*args, **kwargs)
    def history(*args, **kwargs):
        counts['history_syncs'] += 1
        return sync(*args, **kwargs)
    with patch.object(db, 'execute', sql), patch.object(RetryPolicy, 'decide', retry), patch.object(db.history, 'sync', history):
        operation()
    return dict(counts)


with tempfile.TemporaryDirectory(prefix='phase7-measure-') as root:
    config = Config(root, {'ai': {'enabled': False}, 'discord': {'enabled': False}})
    db = Database(config.private / 'measurement.sqlite3')
    try:
        db.ingest(Listing('Synthetic', 'Software Intern', 'New York, NY',
                         'https://jobs.lever.co/synthetic/one', 'fixture', posted_at=now()), config)
        results = {'claim': measure(db, lambda: db.claim(1))}
        if hasattr(db, 'lifecycle'):
            results['begin_attempt'] = measure(db, lambda: db.lifecycle.begin_attempt(1, 3))
        results['failure'] = measure(db, lambda: db.fail(1, 'synthetic timeout', 3))
        db.execute('UPDATE applications SET retry_at=NULL WHERE id=1')
        db.claim(1)
        if hasattr(db, 'lifecycle'): db.lifecycle.begin_attempt(1, 3)
        results['recovery'] = measure(db, db.recover)
    finally:
        db.close()
Path(sys.argv[1]).write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
