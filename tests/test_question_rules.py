import pytest
from autoapply.question_rules import experience_key, remember
from autoapply.answers import AnswerResolver
from autoapply.control import Controller
from autoapply.models import Question


@pytest.mark.parametrize('label', [
    'Are you willing to work four days per week in our San Francisco office?',
    'Are you willing to work in our San Francisco office?',
    'Are you willing to work four days per week in any location?',
    'Are you willing to work in our office?',
])
def test_specific_office_or_schedule_is_not_any_location(label):
    from autoapply.concepts import canonical_key
    assert canonical_key(label) != 'application_preferences.willing_to_work_any_location'


def test_generic_any_location_and_existing_office_attendance():
    from autoapply.concepts import canonical_key
    assert canonical_key('Are you willing to work in any location?') == 'application_preferences.willing_to_work_any_location'
    assert canonical_key('Are you willing to work in our San Francisco office five days per week?') == 'application_preferences.office_five_days'


def test_any_location_preference_does_not_answer_specific_office(config, db):
    path = config.private / 'profile.yaml'
    path.write_text(path.read_text(encoding='utf-8') + '\napplication_preferences: {willing_to_work_any_location: true}\n', encoding='utf-8')
    resolver = AnswerResolver(config, db)
    app = dict(id=0, company='Fixture')
    specific = Question('specific', 'Are you willing to work four days per week in our San Francisco office?', 'radio', True, ['Yes', 'No'])
    generic = Question('generic', 'Are you willing to work in any location?', 'radio', True, ['Yes', 'No'])
    assert resolver.resolve(specific, app) is None
    assert resolver.resolve(generic, app).value == 'Yes'


@pytest.mark.parametrize('label,key',[
    ('Do you have experience with Git?', 'git'),
    ('Experience using version control tools such as Git','version_control'),
    ('Experience in embedded development','embedded'),
    ('Experience using Continuous Integration/Continuous Deployment (CI/CD) pipeline','cicd'),
    ('Can you confirm this requirement? Verify qualification: Experience with SQL and database management systems.','sql_database'),
    ('Experience with Python','python')])
def test_exact_grammars(label,key):
    assert experience_key(label)==key

@pytest.mark.parametrize('label',[
    'Do you NOT have experience with Git?', 'Do you have 3 years experience with Git?',
    'Experience with Git and Python', 'Experience with Git in a production setting',
    'Certified in Git', 'Fueled by a passion for space exploration',
    'Strong programming skills in Python, with experience in data engineering, data science, or a related field.',
    'Minimum Junior status', 'Salary - $20/hour', 'Experience without Git',
    'Experience with Git or equivalent tools', 'Will you teach Git?'])
def test_ambiguity_abstains(label):
    assert experience_key(label) is None

def test_controller_reuses_same_skill_across_apps_and_invalidates_profile(config,db,listing):
    from dataclasses import replace
    db.ingest(listing,config)
    db.lifecycle.record_hold(1,'Question missing','INPUT_REQUIRED',manual_resume_allowed=0)
    q=Question('git','Do you have experience with Git?','radio',True,['Yes','No'])
    row=db.question(1,q)
    before=db.application(1)
    Controller(config,db).answer(row['id'],'No')
    assert db.application(1)==before # Learning never bypasses this hold.
    db.ingest(replace(listing,company='Second',url='https://jobs.lever.co/second/other'),config)
    new=Question('git2','Experience using Git version control','select',True,['No','Yes'])
    resolver=AnswerResolver(config,db)
    answer=resolver.resolve(new,db.application(2))
    assert answer and answer.value=='No' and answer.source=='verified_keyword_memory'
    assert resolver.resolve(Question('duration','3 years experience with Git?','radio',True,['Yes','No']),db.application(2)) is None
    config.private.joinpath('profile.yaml').write_text('identity: {first_name: Changed}',encoding='utf-8')
    assert resolver.resolve(new,db.application(2)) is None

def test_exact_options_and_require_user_do_not_learn(config,db):
    q=Question('git','Experience with Git','radio',True,['Yes','No'],policy='REQUIRE_USER')
    assert not remember(db,config,q,'Yes',1)
    q.policy=''; q.options=['Yes','No','Sometimes']
    assert not remember(db,config,q,'Yes',1)

@pytest.mark.parametrize('first,second',[
    ('Experience with SQL','Experience with SQL and database management systems'),
    ('Experience with Git','Experience using version control tools such as Git'),
    ('Experience in embedded development','Experience in embedded software development')])
def test_broader_or_combined_claims_do_not_transfer(config,db,listing,first,second):
    from dataclasses import replace
    db.ingest(listing,config)
    q=Question('skill',first,'radio',True,['Yes','No'])
    assert remember(db,config,q,'Yes',1)
    resolver=AnswerResolver(config,db)
    assert resolver.resolve(Question('other',second,'radio',True,['Yes','No']),db.application(1)) is None

@pytest.mark.parametrize('mapping',['REJECTED_FIELD','citizenship.us_citizen','contact.first_name','documents.resume'])
def test_keyword_memory_never_overrides_other_field_metadata(config,db,mapping):
    from types import SimpleNamespace
    from autoapply.question_rules import resolve,PREFIX,VERSION
    q=Question('git','Experience with Git','radio',True,['Yes','No'],semantic_key=mapping)
    revision=config.profile_snapshot().revision
    settings={PREFIX+'git':dict(value='Yes',source='USER_PROVIDED',question_id=1,rule_version=VERSION,profile_revision=revision)}
    descriptor=SimpleNamespace(semantic_key=mapping,policy='AUTOFILL_IF_EXACT')
    assert resolve(settings,q,descriptor,revision) is None
    assert remember(db,config,q,'Yes',1) is False
