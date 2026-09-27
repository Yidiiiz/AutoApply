"""Listing lifecycle maintenance; application records are never deleted or rewritten."""
import json
import logging
import sqlite3
from datetime import timedelta
from statistics import mean, median

from .freshness import freshness_state, parse_posted, utc, window

log = logging.getLogger('autoapply')
LISTING_COLUMNS = {
    'posted_at_source': "TEXT DEFAULT ''", 'posted_at_confidence': "TEXT DEFAULT 'unknown'",
    'original_posted_at': 'TEXT', 'reposted_at': 'TEXT', 'source_updated_at': 'TEXT',
    'listing_status': "TEXT NOT NULL DEFAULT 'UNKNOWN'", 'freshness_state': "TEXT NOT NULL DEFAULT 'UNKNOWN_DATE'",
    'last_seen_at': 'TEXT', 'last_checked_at': 'TEXT', 'closed_at': 'TEXT', 'stale_at': 'TEXT',
    'culled_at': 'TEXT', 'listing_active': 'INTEGER NOT NULL DEFAULT 0',
}


class ListingStore:
    def initialize_listings(self, path):
        columns = {r['name'] for r in self.rows('PRAGMA table_info(jobs)')}
        first = 'listing_status' not in columns
        backup = path.parent / 'listing_freshness_backup.sqlite3'
        if first and self.one('SELECT count(*) n FROM jobs')['n'] and not backup.exists():
            target = sqlite3.connect(backup)
            try:
                self.conn.backup(target)
            finally:
                target.close()
        with self.transaction():
            for name, definition in LISTING_COLUMNS.items():
                if name not in columns:
                    self.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
        self.conn.executescript('''
            CREATE INDEX IF NOT EXISTS listing_active_idx ON jobs(listing_active, posted_at);
            CREATE TABLE IF NOT EXISTS listing_observations (
                identity_key TEXT PRIMARY KEY, canonical_url TEXT NOT NULL, source TEXT,
                discovered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                posted_at TEXT, freshness_state TEXT NOT NULL, listing_status TEXT NOT NULL,
                duplicates_skipped INTEGER NOT NULL DEFAULT 0, fresh_at_discovery INTEGER NOT NULL DEFAULT 0,
                age_at_discovery REAL, culled_at TEXT, job_id INTEGER,
                date_source TEXT, date_confidence TEXT);
            CREATE VIEW IF NOT EXISTS active_listings AS SELECT * FROM jobs
                WHERE listing_active=1 AND listing_status='ACTIVE';
            CREATE TABLE IF NOT EXISTS listing_aliases (
                source TEXT NOT NULL, source_job_id TEXT NOT NULL, identity_key TEXT NOT NULL,
                PRIMARY KEY(source,source_job_id));
        ''')
        scheduled = self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='listing_maintenance'")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS listing_maintenance (
                identity_key TEXT PRIMARY KEY, due_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS listing_maintenance_due ON listing_maintenance(due_at);
        """)
        # A compact derived work queue covers evidence edits from any connection.
        # Empty deadlines mean new/changed evidence; all real deadlines are UTC.
        for table in ('jobs', 'listing_observations'):
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                row = 'OLD' if operation == 'DELETE' else 'NEW'
                watched = ('posted_at,listing_status,listing_active,identity_key' if table == 'jobs'
                           else 'posted_at,listing_status,job_id,identity_key')
                event = 'UPDATE OF ' + watched if operation == 'UPDATE' else operation
                predicate = ('WHEN ' + ' OR '.join(f'NEW.{c} IS NOT OLD.{c}' for c in watched.split(','))
                             if operation == 'UPDATE' else '')
                self.conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS listing_work_{table}_{operation}
                    AFTER {event} ON {table} {predicate} BEGIN
                    INSERT INTO listing_maintenance VALUES ({row}.identity_key,'')
                    ON CONFLICT(identity_key) DO UPDATE SET due_at='';
                END;""")
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            self.conn.executescript(f"""CREATE TRIGGER IF NOT EXISTS listing_stats_{operation}
                AFTER {operation} ON listing_observations BEGIN
                INSERT INTO settings VALUES ('listing_statistics_dirty','true') ON CONFLICT(key) DO NOTHING;
            END;""")
        if not scheduled:
            self.conn.executescript("""
                INSERT OR IGNORE INTO listing_maintenance SELECT identity_key,'' FROM jobs;
                INSERT OR IGNORE INTO listing_maintenance SELECT identity_key,'' FROM listing_observations;
            """)
        return first or not self.setting('listing_migration_complete', False)

    def listing_days(self):
        return self.setting('listing_max_age_days', 30)

    def listing_decision(self, job, reference=None, *, days=None):
        fresh = freshness_state(job.get('posted_at'), self.listing_days() if days is None else days, reference)
        status = job.get('listing_status', 'UNKNOWN')
        if status not in {'CLOSED', 'REMOVED'}:
            status = 'STALE' if fresh == 'STALE' else 'UNKNOWN' if fresh == 'UNKNOWN_DATE' else 'ACTIVE'
        return fresh, status, fresh == 'FRESH' and status == 'ACTIVE'

    def observe_listing(self, key, url, source, posted, fresh, status, *, job_id=None,
                        date_source='', confidence='unknown', duplicate=False, discovered=None,
                        reference=None, culled_at=None, days=None):
        stamp = parse_posted(reference) or utc()
        discovered = discovered or stamp.isoformat()
        value = parse_posted(posted, stamp)
        discovery_time = parse_posted(discovered)
        age = (discovery_time - value).total_seconds() / 86400 if value and discovery_time else None
        self.execute('''INSERT INTO listing_observations
            (identity_key,canonical_url,source,discovered_at,last_seen_at,posted_at,freshness_state,
             listing_status,duplicates_skipped,fresh_at_discovery,age_at_discovery,culled_at,job_id,date_source,date_confidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(identity_key) DO UPDATE SET
            last_seen_at=excluded.last_seen_at,posted_at=excluded.posted_at,
            freshness_state=excluded.freshness_state,listing_status=excluded.listing_status,
            duplicates_skipped=listing_observations.duplicates_skipped+excluded.duplicates_skipped,
            culled_at=coalesce(listing_observations.culled_at,excluded.culled_at),
            job_id=coalesce(excluded.job_id,listing_observations.job_id),
            date_source=excluded.date_source,date_confidence=excluded.date_confidence''',
            (key,url,source,discovered,stamp.isoformat(),posted,fresh,status,int(duplicate),
             int(age is not None and 0 <= age <= (self.listing_days() if days is None else days) and status == 'ACTIVE'),
             age if age is not None and age >= 0 else None,culled_at,job_id,date_source,confidence))

    def maintain_listings(self, *, reference=None, migration=False):
        """Single durable schedule shared by startup, scan and ordinary claim.

        Five minutes bounds reporting lag; live application guards never use it.
        Evidence edits and age-window changes invalidate the deadline immediately.
        Controlled execution never runs ordinary global maintenance.
        """
        if not migration and self.setting('controlled_application_id') is not None:
            return {}
        reference = utc(reference)
        due = parse_posted(self.setting('listing_maintenance_due_at'))
        if (not migration and due and reference < due
                and not self.setting('listing_maintenance_dirty', False)
                and not self.one('SELECT identity_key FROM listing_maintenance WHERE due_at<=? LIMIT 1', (reference.isoformat(),))):
            return {}
        return self.cleanup_stale_listings(reference=reference, migration=migration)

    def cleanup_stale_listings(self, *, reference=None, migration=False):
        reference = utc(reference)
        stamp, days = reference.isoformat(), self.listing_days()
        full = migration or self.setting('listing_maintenance_days') != days
        with self.transaction():
            # Deadlines select work only; freshness.py still decides all eligibility.
            due_keys = [r['identity_key'] for r in self.rows(
                'SELECT identity_key FROM listing_maintenance WHERE due_at<=?', (stamp,))]
            rows = self.rows("""SELECT j.*,o.identity_key observed_key,o.posted_at observed_posted,
                o.freshness_state observed_fresh,o.listing_status observed_status,o.culled_at observed_culled,
                coalesce(j.last_seen_at,(SELECT max(last_seen) FROM job_sources WHERE job_id=j.id),j.discovered_at) source_seen,
                CASE WHEN a.status IN ('QUEUED','RETRY','CHECKING','APPLYING','READY','NEEDS_INPUT')
                    AND a.submit_intent_at IS NULL THEN 1 ELSE 0 END queued_count
                FROM """ + ('jobs j ' if full else 'listing_maintenance m JOIN jobs j ON j.identity_key=m.identity_key ')
                + "LEFT JOIN listing_observations o ON o.identity_key=j.identity_key "
                + "LEFT JOIN applications a ON a.job_id=j.id "
                + ('' if full else 'WHERE m.due_at<=?'), () if full else (stamp,))
            deadlines = {}
            report = dict(total_stored_listings=self.one('SELECT count(*) n FROM jobs')['n'], candidate_rows=len(rows), observation_updates=0, fresh=0, old=0, unknown_date=0,
                          confirmed_closed=0, confirmed_stale=0, culled_from_active_storage=0,
                          removed_from_processing_queue=0, applications_preserved=self.one('SELECT count(*) n FROM applications')['n'],
                          duplicates_merged=0, ambiguous_records_requiring_review=0, changed=0)
            for job in rows:
                posted = parse_posted(job['posted_at'], reference)
                source, confidence = job['posted_at_source'], job['posted_at_confidence']
                if migration:
                    # Recover exact relative evidence against its saved source revision, never today's poll.
                    evidence = job.get('date_evidence') or ''
                    if '; source revision ' in evidence:
                        raw, revision = evidence.split('; source revision ', 1)
                        try:
                            posted = parse_posted(raw, utc(revision))
                        except ValueError:
                            posted = None
                        source, confidence = 'repository.posted', 'medium' if posted else 'unknown'
                    else:
                        source, confidence = 'legacy.posted_at', 'medium' if posted else 'unknown'
                    if job['status'] == 'CLOSED':
                        job['listing_status'] = 'CLOSED'
                normalized = posted.isoformat() if posted else None
                fresh, status, active = self.listing_decision(dict(job, posted_at=normalized), reference, days=days)
                deadlines[job['identity_key']] = self._listing_deadline(normalized, fresh, days, reference)
                report[{'FRESH':'fresh','STALE':'old','UNKNOWN_DATE':'unknown_date'}[fresh]] += 1
                report['confirmed_closed'] += status in {'CLOSED','REMOVED'}
                report['confirmed_stale'] += status == 'STALE'
                report['ambiguous_records_requiring_review'] += fresh == 'UNKNOWN_DATE'
                cull = fresh == 'STALE' and status in {'CLOSED','REMOVED'}
                if cull and not job['culled_at']:
                    report['culled_from_active_storage'] += 1
                    log.info('[CLEANUP] Removed stale closed listing from active store job=%s; preserved associated application history', job['id'])
                if not active and (job['listing_active'] or migration):
                    report['removed_from_processing_queue'] += job['queued_count']
                fields = dict(posted_at=normalized, posted_at_source=source, posted_at_confidence=confidence,
                              original_posted_at=job['original_posted_at'] or normalized,
                              listing_status=status, freshness_state=fresh, listing_active=int(active),
                              stale_at=job['stale_at'] or (stamp if fresh == 'STALE' else None),
                              closed_at=job['closed_at'] or (stamp if status in {'CLOSED','REMOVED'} else None),
                              culled_at=job['culled_at'] or (stamp if cull else None),
                              last_seen_at=job['source_seen'])
                if any(job.get(k) != v for k,v in fields.items()) or migration:
                    self.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),job['id']))
                    report['changed'] += 1
                if not job['observed_key']:
                    self.observe_listing(job['identity_key'],job['canonical_url'],'legacy',normalized,fresh,status,
                        job_id=job['id'],date_source=source,confidence=confidence,discovered=job['discovered_at'],
                        reference=job['last_seen_at'] or job['discovered_at'],culled_at=fields['culled_at'],days=days)
                    report['observation_updates'] += 1
                elif (normalized,fresh,status,fields['culled_at']) != (
                        job['observed_posted'],job['observed_fresh'],job['observed_status'],job['observed_culled']):
                    self.execute('UPDATE listing_observations SET posted_at=?,freshness_state=?,listing_status=?,culled_at=? WHERE identity_key=?',
                                 (normalized,fresh,status,fields['culled_at'],job['identity_key']))
                    report['observation_updates'] += 1
            # Rejected observations never become applications. Unknown with no
            # posting evidence and already stale history cannot age into a change.
            items = self.rows('SELECT o.* FROM listing_observations o ' +
                ('' if full else 'JOIN listing_maintenance m ON m.identity_key=o.identity_key ') +
                'WHERE o.job_id IS NULL' + ('' if full else ' AND m.due_at<=?'), () if full else (stamp,))
            report['candidate_rows'] += len(items)
            for item in items:
                fresh, status, _ = self.listing_decision(item, reference, days=days)
                deadlines[item['identity_key']] = self._listing_deadline(item['posted_at'], fresh, days, reference)
                cull = fresh == 'STALE' and status in {'CLOSED','REMOVED'}
                values = (fresh,status,item['culled_at'] or (stamp if cull else None))
                if values != (item['freshness_state'],item['listing_status'],item['culled_at']):
                    self.execute('UPDATE listing_observations SET freshness_state=?,listing_status=?,culled_at=? WHERE identity_key=?',
                                 (*values,item['identity_key']))
                    report['observation_updates'] += 1
            # Apply after mutation triggers so our own updates do not requeue
            # the same work. Stale/unknown history has no time-based deadline.
            for key in set(due_keys) | deadlines.keys():
                deadline = deadlines.get(key)
                if deadline is None:
                    self.execute('DELETE FROM listing_maintenance WHERE identity_key=?', (key,))
                else:
                    self.execute("""INSERT INTO listing_maintenance VALUES (?,?)
                        ON CONFLICT(identity_key) DO UPDATE SET due_at=excluded.due_at
                        WHERE due_at IS NOT excluded.due_at""", (key, deadline))
            self.set_setting('listing_maintenance_days', days)
            self.set_setting('listing_maintenance_due_at', (reference + timedelta(minutes=5)).isoformat())
            self.execute("DELETE FROM settings WHERE key='listing_maintenance_dirty'")
            if migration:
                self.set_setting('listing_migration_complete', True)
            self.refresh_listing_statistics(reference)
        if migration:
            from .archive import atomic_json
            atomic_json(self.history.root.parent / 'listing_migration_report.json', report)
        return report

    @staticmethod
    def _listing_deadline(posted, fresh, days, reference):
        value = parse_posted(posted, reference)
        if value is None:
            return None
        if value > reference:
            return value.isoformat()
        if fresh == 'FRESH':
            # Inclusive cutoff: the first ineligible instant is one microsecond later.
            return (value + window(days) + timedelta(microseconds=1)).isoformat()
        return None

    def listing_statistics(self, reference=None):
        """Read the last persisted maintenance snapshot; never repair on display."""
        try:
            value = json.loads((self.history.root.parent / 'listing_statistics.json').read_text(encoding='utf-8'))
            if isinstance(value, dict) and 'discovered_total' in value:
                return value
        except (OSError, ValueError):
            pass
        return self._calculate_listing_statistics(reference)

    def refresh_listing_statistics(self, reference=None):
        from contextlib import nullcontext
        reference = utc(reference)
        path = self.history.root.parent / 'listing_statistics.json'
        valid = False
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            valid = isinstance(cached, dict) and 'discovered_total' in cached
        except (OSError, ValueError):
            pass
        if (valid and not self.setting('listing_statistics_dirty', False)
                and self.setting('listing_statistics_day') == reference.date().isoformat()):
            return cached
        with nullcontext() if self.conn.in_transaction else self.transaction():
            return self._refresh_listing_statistics(reference)

    def _refresh_listing_statistics(self, reference=None):
        from .archive import atomic_json
        stats = self._calculate_listing_statistics(reference)
        atomic_json(self.history.root.parent / 'listing_statistics.json', stats)
        self.set_setting('listing_statistics_day', utc(reference).date().isoformat())
        self.set_setting('listing_statistics_as_of', utc(reference).isoformat())
        self.execute("DELETE FROM settings WHERE key='listing_statistics_dirty'")
        # The existing synchronous history flush will include these statistics
        # when job changes already require an export. Preserve its lock protocol.
        if not self.one('SELECT application_id FROM history_dirty LIMIT 1'):
            with self.history.locked():
                self.history._refresh()
                self.history._statistics()
        return stats

    def _calculate_listing_statistics(self, reference=None):
        reference = utc(reference)
        items = self.rows('SELECT * FROM listing_observations')
        ages = [r['age_at_discovery'] for r in items if r['age_at_discovery'] is not None]
        today = reference.replace(hour=0,minute=0,second=0,microsecond=0)
        stats = dict(discovered_total=len(items),
            fresh_eligible=sum(r['freshness_state']=='FRESH' and r['listing_status']=='ACTIVE' for r in items),
            rejected_too_old=sum(r['freshness_state']=='STALE' for r in items),
            rejected_unknown_date=sum(r['freshness_state']=='UNKNOWN_DATE' for r in items),
            closed=sum(r['listing_status'] in {'CLOSED','REMOVED'} for r in items),
            stale=sum(r['listing_status']=='STALE' for r in items),
            culled_closed_stale=sum(bool(r['culled_at']) for r in items),
            duplicates_skipped=sum(r['duplicates_skipped'] for r in items),
            average_listing_age_at_discovery=mean(ages) if ages else None,
            median_listing_age_at_discovery=median(ages) if ages else None,age_unit='days')
        for name, boundary in [('today',today),('this_week',today-timedelta(days=today.weekday()))]:
            stats['fresh_discovered_'+name] = sum(bool(r['fresh_at_discovery'] and parse_posted(r['discovered_at']) and boundary <= parse_posted(r['discovered_at']) <= reference) for r in items)
        return stats

    def guard_listing(self, app_id, reference=None):
        """Final fail-closed guard. Preserve all prior submission/manual evidence."""
        app = self.application(app_id)
        job = self.one('SELECT * FROM jobs WHERE id=?',(app['job_id'],))
        fresh,status,active = self.listing_decision(job, reference)
        if active and job['listing_status'] == 'ACTIVE' and job['listing_active']:
            return True
        reason = 'LISTING_CLOSED_BEFORE_APPLICATION' if status in {'CLOSED','REMOVED'} else 'STALE_BEFORE_APPLICATION' if fresh=='STALE' else 'UNKNOWN_DATE_BEFORE_APPLICATION'
        from contextlib import nullcontext
        with nullcontext() if self.conn.in_transaction else self.transaction():
            self.execute('UPDATE jobs SET listing_active=0,listing_status=?,freshness_state=? WHERE id=?',(status,fresh,job['id']))
            self._update_observation_state(job, fresh, status, reference)
            if not app['submit_intent_at'] and not app['manual_action_required'] and app['status'] in {'QUEUED','RETRY','CHECKING','APPLYING','READY'}:
                self.transition(app_id, 'CLOSED' if status in {'CLOSED','REMOVED'} else 'INVALID', reason)
        if not self.conn.in_transaction:
            self.refresh_listing_statistics(reference)
        log.info('[FRESHNESS] %s application=%s',reason,app_id)
        return False

    def mark_listing_closed(self, job_id, status='CLOSED'):
        if status not in {'CLOSED','REMOVED'}:
            raise ValueError('Closure requires definitive CLOSED/REMOVED evidence')
        from contextlib import nullcontext
        with nullcontext() if self.conn.in_transaction else self.transaction():
            job = self.one('SELECT * FROM jobs WHERE id=?', (job_id,))
            self.execute('UPDATE jobs SET listing_status=?,listing_active=0,closed_at=coalesce(closed_at,?),last_checked_at=? WHERE id=?',
                         (status,utc().isoformat(),utc().isoformat(),job_id))
            fresh, _, _ = self.listing_decision(job)
            self._update_observation_state(job, fresh, status)
            self.refresh_listing_statistics()

    def _update_observation_state(self, job, fresh, status, reference=None):
        cull = job['culled_at'] or (utc(reference).isoformat() if fresh == 'STALE' and status in {'CLOSED','REMOVED'} else None)
        self.execute("""UPDATE listing_observations SET posted_at=?,freshness_state=?,listing_status=?,culled_at=?
            WHERE identity_key=? AND (posted_at IS NOT ? OR freshness_state IS NOT ? OR listing_status IS NOT ? OR culled_at IS NOT ?)""",
            (job['posted_at'],fresh,status,cull,job['identity_key'],job['posted_at'],fresh,status,cull))
