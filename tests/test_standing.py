import json

import pytest
import yaml

from autoapply.answers import AnswerResolver
from autoapply.jobs import eligibility
from autoapply.models import Question
from autoapply.standing import MEANINGS, SOURCE, resolve_standing


def assertions():
    return {"eligibility_assertions": [dict(id=key, meaning=value, source="USER_PROVIDED", scope="standing", answer=True)
                                      for key, value in MEANINGS.items()]}


@pytest.mark.parametrize("text", [
    *MEANINGS.values(),
    "Do you have strong communication skills?", "Excellent written and verbal communication skills",
    "Strong interpersonal skills", "Strong technical communication skills",
    "Strong analytical and problem-solving abilities", "Excellent problem-solving skills",
    "Strong attention to detail", "Ability to collaborate with cross-functional teams",
    "Ability to work effectively across departments", "Team-oriented and able to collaborate effectively",
    "Can work both independently and collaboratively", "Self-starter who takes initiative",
    "Ability to communicate effectively within a team", "GPA of at least 3.5", "3.5+ GPA",
    "Previous internship experience OR substantial project/lab/research experience",
    "At least one year of project, laboratory, or research experience", "Significant project-team experience",
    "Must have prior internship experience, or significant (>1yr) project team, laboratory, or research experience",
    "Strong interpersonal and technical communication skills (examples: leading a student project team, presenting research at conferences, etc.)",
    "Strong analytical and problem-solving skills with attention to detail",
    "Ability to work cross-departmentally with different groups and teams", "GPA of 3.5 or above",
])
def test_semantic_restatements(text):
    result = resolve_standing(text, assertions())
    assert result and result["source"] == SOURCE and result["assertion_ids"]


@pytest.mark.parametrize("text", [
    "Minimum GPA 3.7", "5+ years of professional technical writing experience",
    "3 years of full-time professional software engineering experience", "Managed a team of 20 engineers",
    "Expert-level MATLAB with 5 years of experience", "Do you lack strong communication skills?",
    "Strong communication skills in German", "Strong communication skills and US citizenship",
    "Strong analytical skills and Python proficiency", "Minimum GPA 3.5 and a security clearance",
    "Prior internship experience", "At least two years of laboratory experience",
    "Significant professional laboratory experience", "Strong communication skills or willingness to relocate",
    "Will you pass a background check?", "GPA", "What is your GPA?",
    "Explain your communication skills", "Strong communication skills (including fluency in French)",
    "Minimum GPA 3.5 on a 5 point scale", "Not able to work independently",
    "Excellent programming skills in TypeScript, Go and Python",
    "Excellent programming skills in Go",
    "Expert programming skills in Typescript, Go or Python",
    "Knowledge of automation testing methodologies, tools, and best practices with five years of professional experience",
    "Ability to solve problems creatively and communicate trade-offs effectively in German",
])
def test_no_new_or_stronger_claims(text):
    assert resolve_standing(text, assertions()) is None


def test_requires_explicit_source_and_all_components():
    profile = assertions()
    assert resolve_standing("Strong communication skills", {}) is None
    profile["eligibility_assertions"][1]["source"] = "AI_INFERRED"
    assert resolve_standing("Strong communication skills", profile) is None
    assert resolve_standing("Strong communication skills and attention to detail", profile) is None


def test_testing_requirements_need_their_own_confirmation(config):
    requirements = [MEANINGS[key] for key in (
        'programming_typescript_go_or_python', 'automation_testing', 'creative_problem_solving_tradeoffs')]
    job = {'title': 'Software Test Intern', 'location': 'San Francisco, CA',
           'description': 'Minimum qualifications\n' + '\n'.join(requirements)}
    prior = assertions()
    prior['eligibility_assertions'] = prior['eligibility_assertions'][:6]
    assert eligibility(job, config.profile | prior).eligible is None
    result = eligibility(job, config.profile | assertions())
    assert result.eligible is True and len(result.standing_matches) == 3


def test_eligibility_uses_assertions_but_keeps_new_threshold(config):
    profile = config.profile | assertions()
    job = {"title": "Software Intern", "location": "Los Angeles, CA",
           "description": "Minimum qualifications\nStrong communication skills\nGPA of 3.5 or above"}
    result = eligibility(job, profile)
    assert result.eligible is True and len(result.standing_matches) == 2
    job["description"] += "\nMinimum GPA 3.7"
    assert eligibility(job, profile).eligible is None


def test_form_source_and_no_numeric_invention(config, db, listing):
    (config.private / "profile.yaml").write_text(yaml.safe_dump(config.profile | assertions()))
    app_id, _ = db.ingest(listing, config)
    resolver = AnswerResolver(config, db)
    q = Question("communication", "Do you have strong communication skills?", "radio", True, ["Yes", "No"])
    answer = resolver.resolve(q, db.application(app_id))
    assert answer.value == "Yes" and answer.source == SOURCE and answer.evidence == ["communication"]
    event = db.one("SELECT * FROM events WHERE kind='standing_answer'")
    assert json.loads(event["detail"])["assertion_ids"] == ["communication"]
    assert resolver.resolve(Question("gpa", "GPA", "number", True), db.application(app_id)) is None
    assert resolver.resolve(Question("gpa", "Minimum GPA 3.5", "number", True), db.application(app_id)) is None


@pytest.mark.parametrize('heading',['Preferred Skills & Experience:', 'Preferred Skills and Experience:', 'Preferred Qualifications:'])
def test_preferred_experience_is_not_a_required_personal_claim(config,heading):
    job={'title':'Software Intern', 'location':'Los Angeles, CA',
         'description':'Basic Qualifications:\nEnrolled in a bachelor degree program\n'+heading+
         '\nExperience developing embedded software\nExperience with HMI and/or Grafana\n'
         'Ability to work in an environment with changing requirements\nExperience with hardware integration'}
    assert eligibility(job,config.profile).eligible is True
    job['description']+='\nAdditional Requirements:\nExperience developing embedded software'
    result=eligibility(job,config.profile)
    assert result.eligible is None
    assert any('embedded software' in u for u in result.uncertainties)
