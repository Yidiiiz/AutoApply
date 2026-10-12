"""Offline resume migration helpers. Never construct a browser or replay a form."""
import copy
import json

from .models import FINAL
from .profile_snapshot import ProfileSnapshot


def refresh_profile(profile, facts, sections, sha256):
    """Replace reviewed resume evidence while retaining explicitly sourced user facts.

    facts and sections must be reviewed against the supplied PDF by the operator;
    this function deliberately does not infer qualifications from extracted text.
    """
    result = copy.deepcopy(profile)
    sources = result.setdefault('fact_sources', {})
    verified = result.setdefault('verified_facts', {})
    removed, preserved = [], []
    for key, source in list(sources.items()):
        if source.get('source') == 'USER_PROVIDED':
            preserved.append(key)
        elif source.get('sha256') and source.get('file'):
            verified.pop(key, None)
            sources.pop(key)
            removed.append(key)
            if key.startswith('qualifications.'):
                result.get('qualifications', {}).pop(key.split('.', 1)[1], None)
    provenance = dict(file='resumes/resume.pdf', sha256=sha256, page=1,
                      basis='User-supplied resume; manually checked against rendered page',
                      source='RESUME_DERIVED')
    conflicts = []
    for key, value in facts.items():
        if key in preserved or key in verified:
            if verified.get(key) != value:
                conflicts.append(key)
            continue
        if key.startswith('qualifications.'):
            name = key.split('.', 1)[1]
            existing = result.setdefault('qualifications', {})
            if name in existing and existing[name] != value:
                conflicts.append(key)
                continue
            existing[name] = value
        else:
            verified[key] = value
        sources[key] = dict(provenance)
    for key, value in sections.items():
        if sources.get(key, {}).get('source') == 'USER_PROVIDED':
            if result.get(key) != value:
                conflicts.append(key)
            continue
        result[key] = copy.deepcopy(value)
        sources[key] = dict(provenance)
    result['resume_revision'] = dict(file='resumes/resume.pdf', sha256=sha256)
    ProfileSnapshot.create(result)
    return result, dict(removed=removed, refreshed=sorted(facts), sections=sorted(sections), conflicts=conflicts)


def invalidate_current_state(conn, old_sha256, new_sha256, checkpoints):
    """Invalidate current settings only; application and attachment history stays exact.

    Caller owns the SQLite transaction and atomic checkpoint-file writes. Old
    hashes remain evidence of what happened, never relabeled as the new file.
    """
    from .fill_batch import EXCLUDED
    settings = {r['key']: json.loads(r['value']) for r in conn.execute('SELECT key,value FROM settings')}
    affected = []
    updated = {}
    for row in conn.execute('SELECT * FROM applications').fetchall():
        app = dict(row)
        app_id = app['id']
        protected = (app['status'] in FINAL or app['submit_intent_at'] or app['submitted_at']
                     or app['submission_confirmation_seen'] or app_id in EXCLUDED
                     or app['security_state'] not in {'NONE', 'PASSIVE_PROTECTION_DETECTED'}
                     or settings.get('controlled_application_id') == app_id
                     or settings.get(f'duplicate_submission_guard:{app_id}')
                     or settings.get(f'automation_retired:{app_id}'))
        if protected:
            continue
        key = f'fill_checkpoint:{app_id}'
        upload_key = f'upload_result:{app_id}'
        if key not in settings and upload_key not in settings and not app['resume_sha256']:
            continue
        affected.append(app_id)
        def save(name, value):
            conn.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (name, json.dumps(value)))
        invalid = dict(readiness='RESUME_REVALIDATION_REQUIRED', reconstruction_verified=False,
                       validated_at=None, required_resume_sha256=new_sha256,
                       reason='Canonical resume replaced; fresh upload and revalidation required')
        if key in settings or app_id in checkpoints:
            save(key, {**settings.get(key, {}), **invalid})
        if app_id in checkpoints:
            updated[app_id] = {**checkpoints[app_id], **invalid, 'upload_ready': False}
        if upload_key in settings:
            # The prior receipt is evidence, retained verbatim but no longer active.
            save(f'resume_migration_history:{old_sha256}:upload_result:{app_id}', settings[upload_key])
            save(upload_key, dict(ready=False, category='UPLOAD_NOT_STARTED',
                                 required_resume_sha256=new_sha256, reason=invalid['reason']))
    return affected, updated
