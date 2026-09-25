import hashlib
import json

import pytest

from autoapply import eligibility_repair as repair
from autoapply.jobs import eligibility
from autoapply.models import now


@pytest.fixture
def incident(db, config, monkeypatch):
    description = 'Undergraduate internship. Starting a graduate degree program in Fall 2027.'
    monkeypatch.setattr(repair, 'DESCRIPTION_SHA256', hashlib.sha256(description.encode()).hexdigest())
    db.execute('INSERT INTO jobs(id,identity_key,company,title,location,canonical_url,description,discovered_at,status,eligibility_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (5310,'fixture','LLNL','Undergraduate Intern','Livermore, CA',
                'https://jobs.smartrecruiters.com/LLNL/3743990015289136',description,now(),'INELIGIBLE',
                json.dumps(dict(eligible=False,reasons=repair.REASON.split('; ')))))
    db.execute("INSERT INTO applications(id,job_id,status,application_state,updated_at,failure_reason,stage,attempts) VALUES(5310,5310,'INELIGIBLE','INELIGIBLE',?,?,'checking',1)", (now(),repair.REASON))
    for kind, detail in [('discovered','fixture'),('CHECKING',''),('INELIGIBLE',repair.REASON),('hold',repair.REASON)]:
        db.event(5310,kind,detail)
    return db,config.profile


def test_repair_preserves_history_and_is_single_use(incident):
    db,profile=incident
    before=db.rows('SELECT * FROM events WHERE application_id=5310')
    result=repair.repair_5310(db,profile)
    assert result['corrected_parser_result']['eligible'] is True
    assert db.application(5310)['status']=='QUEUED'
    assert db.application(5310)['eligibility_override']==0
    assert db.rows('SELECT * FROM events WHERE application_id=5310')[:4]==before
    with pytest.raises(ValueError): repair.repair_5310(db,profile)
    db.transition(5310,'INELIGIBLE')
    with pytest.raises(ValueError): db.transition(5310,'QUEUED')


@pytest.mark.parametrize('field,value', [('submit_intent_at','intent'),('resume_used','resume.pdf'),
    ('submission_confirmation_seen',1),('status','SUBMITTED'),('failure_reason','real failure')])
def test_activity_or_different_state_rejected(incident,field,value):
    db,profile=incident
    db.execute(f'UPDATE applications SET {field}=? WHERE id=5310',(value,))
    with pytest.raises(ValueError): repair.repair_5310(db,profile)


@pytest.mark.parametrize('kind',['field_filled','upload','submit_click','submission_request','unknown'])
def test_unexpected_event_rejected(incident,kind):
    db,profile=incident
    db.event(5310,kind,'evidence')
    with pytest.raises(ValueError): repair.repair_5310(db,profile)


@pytest.mark.parametrize('app_id',[6401,6415,6416,1])
def test_other_ids_rejected(incident,app_id):
    db,profile=incident
    with pytest.raises(ValueError): repair.repair_5310(db,profile,app_id)


def test_real_graduation_failure_rejected(incident):
    db,profile=incident
    profile['education']['degree']='PhD'
    db.execute("UPDATE jobs SET title='PhD Intern' WHERE id=5310")
    profile['education']['degree']='Bachelor'
    with pytest.raises(ValueError): repair.repair_5310(db,profile)


def test_qualifications_heading_checks_skills(config):
    result=eligibility(dict(title='Undergraduate Intern',location='CA, United States',
        description='Qualifications\nAbility to apply computational science principles.'),config.profile)
    assert result.eligible is None
    assert any('computational science' in x for x in result.uncertainties)
