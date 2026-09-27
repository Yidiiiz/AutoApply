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


def submission_conflict(db, app_id):
    app = db.application(app_id)
    if db.automation_retired(app_id) or app['submit_intent_at'] or app['submission_confirmation_seen']:
        return app
    # One scan of the protected set, with identical policy for imported history.
    # No speculative identity index: legacy incomplete records still need review.
    rows = db.rows("""SELECT a.*,j.company,j.title,j.ats,j.canonical_url FROM applications a
        JOIN jobs j ON j.id=a.job_id WHERE a.id!=? AND
        (a.submit_intent_at IS NOT NULL OR a.submission_confirmation_seen=1 OR
         a.status IN ('SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REVIEW'))""", (app_id,))
    seen = {str(app_id)}
    for other in rows:
        seen.add(str(other['id']))
        match = identity_match(app, other)
        if match:
            return dict(other, identity_match=match)
    for other in db.history.list_applications():
        if other['application_id'] in seen:
            continue
        if not (other.get('submit_intent_at') or other.get('submission_confirmation_seen') or
                other['application_state'] in {'SUBMITTED','ALREADY_APPLIED','SUBMITTING','MANUAL_REQUIRED','UNKNOWN'}):
            continue
        match = identity_match(app, other)
        if match:
            return dict(other, id=other.get('id', other['application_id']), status=other.get('status', other['application_state']), identity_match=match)
    return None
