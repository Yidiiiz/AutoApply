"""One exact identity / incomplete legacy review policy for preflight and submit."""
from .jobs import ats_identity, canonical_url, job_identity, normalize


def identity_match(app, other):
    left, right = app.get('canonical_url') or app.get('url'), other.get('canonical_url') or other.get('url')
    try:
        if canonical_url(left) == canonical_url(right) or job_identity(left) == job_identity(right):
            return 'exact_protected_duplicate'
        complete = bool(ats_identity(left)[1] and ats_identity(right)[1])
    except (ValueError, AttributeError, TypeError):
        complete = False
    if not complete and normalize(app.get('company', '')) == normalize(other.get('company', '')) and normalize(app.get('title', '')) == normalize(other.get('title', other.get('job_title', ''))):
        return 'ambiguous_legacy_identity'
    return None


def _url(record):
    return record.get('canonical_url') or record.get('url')


def _keys(record):
    """Index keys covering every way identity_match can return a match."""
    keys = [('ct', normalize(record.get('company', '')), normalize(record.get('title', record.get('job_title', ''))))]
    url = _url(record)
    try:
        keys.append(('url', canonical_url(url)))
    except (ValueError, AttributeError, TypeError):
        pass
    try:
        keys.append(('job', job_identity(url)))
    except (ValueError, AttributeError, TypeError):
        pass
    return keys


class ConflictIndex:
    """One scan of the protected set, shared by every candidate in one selection pass.

    The index only narrows which records are compared; identity_match still
    decides, in the original order, so the policy is unchanged.
    """
    def __init__(self, db):
        rows = db.rows("""SELECT a.*,j.company,j.title,j.ats,j.canonical_url FROM applications a
            JOIN jobs j ON j.id=a.job_id WHERE
            (a.submit_intent_at IS NOT NULL OR a.submission_confirmation_seen=1 OR
             a.status IN ('SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REVIEW'))""")
        self.records = [('db', dict(r)) for r in rows]
        self.db_ids = {str(r['id']) for r in rows}
        self.history = [other for other in db.history.list_applications()
                        if other.get('submit_intent_at') or other.get('submission_confirmation_seen') or
                        other['application_state'] in {'SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REQUIRED','UNKNOWN'}]
        self.records += [('history', r) for r in self.history]
        self.index = {}
        for position, (_, record) in enumerate(self.records):
            for key in _keys(record):
                self.index.setdefault(key, []).append(position)

    def conflict(self, app):
        app_id = app['id']
        positions = sorted({p for key in _keys(app) for p in self.index.get(key, ())})
        for position in positions:
            kind, other = self.records[position]
            if kind == 'db':
                if other['id'] == app_id:
                    continue
                match = identity_match(app, other)
                if match:
                    return dict(other, identity_match=match)
        for position in positions:
            kind, other = self.records[position]
            if kind != 'history' or other['application_id'] == str(app_id) or other['application_id'] in self.db_ids:
                continue
            match = identity_match(app, other)
            if match:
                return dict(other, id=other.get('id', other['application_id']), status=other.get('status', other['application_state']), identity_match=match)
        return None


def submission_conflict(db, app_id):
    app = db.application(app_id)
    if db.automation_retired(app_id) or app['submit_intent_at'] or app['submission_confirmation_seen']:
        return app
    shared = getattr(db, '_conflict_index', None)
    return (shared or ConflictIndex(db)).conflict(app)
