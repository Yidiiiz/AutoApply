from types import SimpleNamespace

from autoapply.archive import ApplicationHistory, protected_record
from autoapply.conflicts import ConflictIndex, identity_match


def test_snapshot_matches_original_linear_conflicts(tmp_path):
    history = ApplicationHistory(tmp_path / 'history')
    records = [
        dict(application_id='unknown', application_state='UNKNOWN', company='Legacy', job_title='Intern'),
        dict(application_id='manual', application_state='MANUAL_REQUIRED', company='Legacy', title='Intern'),
        dict(application_id='exact', application_state='SUBMITTED', canonical_url='https://boards.greenhouse.io/a/jobs/42'),
        dict(application_id='intent', application_state='FAILED', submit_intent_at='intent', canonical_url='https://jobs.lever.co/a/intent'),
        dict(application_id='confirmed', application_state='FAILED', submission_confirmation_seen=1, canonical_url='https://jobs.ashbyhq.com/a/confirmed'),
        dict(application_id='7', application_state='UNKNOWN', canonical_url='https://jobs.lever.co/a/excluded'),
        dict(application_id='ordinary', application_state='DISCOVERED', canonical_url='https://jobs.lever.co/a/ordinary'),
    ]
    with history.locked():
        history._invalidate_readers()
        for record in records:
            history._write(record)
        history._statistics()
    db_rows = [dict(id=7, company='Other', title='Intern', canonical_url='https://jobs.lever.co/a/db')]
    db = SimpleNamespace(rows=lambda sql: db_rows, history=ApplicationHistory(history.root))
    index = ConflictIndex(db)
    baseline = [r for r in ApplicationHistory(history.root).list_applications() if protected_record(r)]
    apps = [dict(id=100, company='Legacy', title='Intern', canonical_url='https://jobs.lever.co/a/new'),
            dict(id=101, canonical_url='https://job-boards.greenhouse.io/b/jobs/42'),
            dict(id=102, canonical_url='https://jobs.lever.co/a/intent/apply'),
            dict(id=103, canonical_url='https://jobs.ashbyhq.com/a/confirmed/application'),
            dict(id=104, canonical_url='https://jobs.lever.co/a/excluded'),
            dict(id=105, canonical_url='https://jobs.lever.co/a/ordinary'),
            dict(id=106, canonical_url='https://jobs.lever.co/a/db')]
    for app in apps:
        expected = None
        for other in db_rows:
            if other['id'] != app['id'] and (match := identity_match(app, other)):
                expected = dict(other, identity_match=match)
                break
        if expected is None:
            for other in baseline:
                if other['application_id'] in {str(app['id']), '7'}:
                    continue
                if match := identity_match(app, other):
                    expected = dict(other, id=other.get('id', other['application_id']),
                                    status=other.get('status', other['application_state']), identity_match=match)
                    break
        assert index.conflict(app) == expected
    assert index.conflict(apps[0])['identity_match'] == 'ambiguous_legacy_identity'
    assert index.conflict(apps[4]) is index.conflict(apps[5]) is None
