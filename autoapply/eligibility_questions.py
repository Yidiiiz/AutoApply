"""Application-scoped confirmations using the existing question/answer store."""
import hashlib
import json

from .jobs import eligibility
from .models import Question


def unresolved_questions(check, app):
    # Bind confirmation to the complete listing: edits cannot reuse a Yes for
    # an earlier program duration or an added/strengthened qualification.
    context = [app.get(key, '') for key in ('title', 'location', 'description')]
    for uncertainty in check.uncertainties:
        digest = hashlib.sha256(json.dumps([context, uncertainty], ensure_ascii=False).encode()).hexdigest()
        yield Question('eligibility:' + digest, 'Can you confirm this requirement? ' + uncertainty,
                       'radio', True, ['Yes', 'No'], scope=f"application:{app['id']}:eligibility:{digest}",
                       semantic_key='eligibility_confirmation', policy='REQUIRE_USER')


def assess_eligibility(app, snapshot, db):
    """Only explicit current user confirmations can resolve an unknown clause."""
    check = eligibility(app, snapshot.facts)
    remaining = []
    for uncertainty, q in zip(check.uncertainties, unresolved_questions(check, app)):
        row = db.one("SELECT * FROM questions WHERE application_id=? AND field_key=? AND status='ANSWERED'",
                     (app['id'], q.key))
        from .field_mapping import describe
        provenance = json.loads(row.get('answer_provenance') or '{}') if row else {}
        confirmed = (row and row['answer_source'] == 'user_confirmed' and provenance.get('verified') is True
                     and row['question_signature'] == describe(q, app).signature
                     and provenance.get('provenance', {}).get('canonical_profile_revision') == snapshot.revision)
        value = json.loads(row['answer']) if confirmed else None
        if value is None:
            # Factual requirements (work authorization, enrollment, citizenship)
            # are answered from the profile; skill/willingness ones are Yes.
            from .profile_rules import confirm_requirement
            from .affirmative import affirmative, ENABLED
            fact = confirm_requirement(uncertainty, snapshot.facts) if ENABLED else None
            if fact is not None:
                value = 'Yes' if fact else 'No'
            elif affirmative(q.label):
                value = 'Yes'
        if value == 'Yes':
            check.reasons.append('User confirmed: ' + uncertainty)
        elif value == 'No':
            check.eligible = False
            check.reasons.append('User confirmed unmet requirement: ' + uncertainty)
        else:
            remaining.append(uncertainty)
    check.uncertainties = remaining
    if check.eligible is not False:
        check.eligible = None if remaining else True
    return check


def request_unresolved(db, app, check):
    """Refresh only parser-owned questions; keep legacy/form questions intact."""
    questions = list(unresolved_questions(check, app))
    active = {q.key for q in questions}
    with db.transaction():
        for row in db.rows("SELECT id,field_key FROM questions WHERE application_id=? AND status='PENDING'",
                           (app['id'],)):
            if row['field_key'].startswith('eligibility:') and row['field_key'] not in active:
                db.execute("UPDATE questions SET status='SKIPPED',reason=? WHERE id=?",
                           ('No longer unresolved in the current eligibility assessment', row['id']))
        for uncertainty, q in zip(check.uncertainties, questions):
            row = db.question(app['id'], q, uncertainty)
            if row['status'] != 'PENDING':
                # A profile revision can invalidate a confirmation without
                # changing the question wording. Never silently replay it.
                db.execute("UPDATE questions SET status='PENDING',answer=NULL,answer_source=NULL,"
                           "confidence=NULL,answer_provenance=NULL,reason=? WHERE id=?",
                           (uncertainty, row['id']))
    return questions
