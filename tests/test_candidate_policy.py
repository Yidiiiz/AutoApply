import pytest

from autoapply.candidate_policy import evaluate
from autoapply.jobs import eligibility, phd_rejection
from autoapply.models import now


@pytest.mark.parametrize('title,description,reason', [
    ('PhD Research Intern', '', 'phd_title'),
    ('Ph.D. Software Intern', '', 'phd_title'),
    ('Machine Learning Intern (PHD)', '', 'phd_title'),
    ('Ph.D Software Intern', '', 'phd_title'),
    ('Ph D Intern', '', 'phd_title'),
    ('Bachelor or PhD Intern', 'Bachelor accepted', 'phd_title'),
    ('Software Intern', 'Currently pursuing a PhD in CS', 'phd_without_bachelor'),
    ('Software Intern', 'Preferred: Ph.D.', 'phd_without_bachelor'),
    ('Software Intern', "Pursuing a Bachelor's, Master's or PhD", ''),
    ('Software Intern', 'Pursuing bachelors or ph d', ''),
    ('graphd Intern', 'Use aphdx tools', ''),
    ('Software Intern', 'Undergraduate internship.', ''),
])
def test_phd_policy(config, title, description, reason):
    job = dict(id=1, title=title, description=description, location='New York, NY', posted_at=now())
    decision = evaluate(job, config.profile_snapshot())
    assert phd_rejection(title, description) == reason
    if reason:
        assert decision.status == 'REJECT' and decision.reason == reason and not decision.proceed
        check = eligibility(job, {})
        assert check.eligible is False and check.reasons == [reason]
    else:
        assert decision.proceed
        assert eligibility(job, config.profile).eligible is True


def test_phd_precedes_missing_evidence(config):
    job = dict(id=1, title='Intern', description='PhD in CS', location='', posted_at=None)
    assert evaluate(job, config.profile_snapshot()).reason == 'phd_without_bachelor'


@pytest.mark.parametrize('changes,reason', [
    ({'listing_status': 'CLOSED'}, 'confirmed_closed'),
    ({'posted_at': '2020-01-01'}, 'stale_listing'),
    ({'status': 'SUBMITTED'}, 'protected_application'),
    ({'status': 'ALREADY_APPLIED'}, 'protected_application'),
    ({'submit_intent_at': now()}, 'protected_application'),
])
def test_existing_boundaries_precede_phd(config, changes, reason):
    job = dict(id=1, title='PhD Intern', description='', location='', posted_at=None) | changes
    assert evaluate(job, config.profile_snapshot()).reason == reason
