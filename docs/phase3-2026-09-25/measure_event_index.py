"""Small offline SQLite index insertion check; no production storage or browser."""
import json
import sqlite3
import time
from statistics import median


def sample(indexed):
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,application_id INTEGER,kind TEXT,detail TEXT,created_at TEXT)')
    if indexed:
        conn.execute('CREATE INDEX events_application ON events(application_id)')
    rows = [(n % 1000,'synthetic','offline','2026-09-25T12:00:00+00:00') for n in range(20000)]
    start = time.perf_counter()
    conn.executemany('INSERT INTO events(application_id,kind,detail,created_at) VALUES (?,?,?,?)',rows)
    conn.commit()
    seconds = time.perf_counter()-start
    pages = conn.execute('PRAGMA page_count').fetchone()[0]
    page_size = conn.execute('PRAGMA page_size').fetchone()[0]
    plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN SELECT * FROM events WHERE application_id=? ORDER BY id',(1,))]
    conn.close()
    return dict(seconds=seconds,bytes=pages*page_size,plan=plan)


if __name__ == '__main__':
    result = {str(indexed):[sample(indexed) for _ in range(5)] for indexed in (False,True)}
    print(json.dumps(dict(method='Five synthetic in-memory inserts of 20,000 events over 1,000 applications; isolates index cost from history exporting.',
                         sqlite=sqlite3.sqlite_version,samples=result,
                         median_seconds={k:median(r['seconds'] for r in v) for k,v in result.items()}),indent=2))
