from dataclasses import replace
import pytest
from autoapply.engine import Engine
from autoapply.applications import GenericApplicationAdapter
from autoapply.requirements import extract_requirements
from autoapply.jobs import eligibility
from autoapply.requirements import administrative


@pytest.mark.parametrize('heading', ['Desired Qualifications:', 'Desired Requirements'])
def test_desired_qualifications_are_optional_but_required_schedule_survives(heading):
    text = 'Qualifications:\nExperience in embedded development\n'+heading+'\nExperience with NASA core flight software\nThis is an in-person internship; Monday - Friday, 8:00 AM to 5:00 PM in Golden, Colorado.'
    rows = extract_requirements(text)
    assert [(r.text,r.preferred) for r in rows] == [
        ('Experience in embedded development',False),
        ('Experience with NASA core flight software',True),
        ('This is an in-person internship; Monday - Friday, 8:00 AM to 5:00 PM in Golden, Colorado.',False)]


@pytest.mark.parametrize('heading', ['The Must-Haves', 'Must-Haves:'])
def test_rippling_must_haves_do_not_disappear_from_eligibility(heading, listing):
    description=heading+'\nExperience in data engineering\nFamiliarity with Docker and Kubernetes\nBenefits\nWe offer support.'
    check=eligibility(replace(listing,description=description).__dict__,{})
    assert check.eligible is None
    assert any('data engineering' in s for s in check.uncertainties)
    assert any('Docker and Kubernetes' in s for s in check.uncertainties)
    assert not any('We offer' in s for s in check.uncertainties)


@pytest.mark.parametrize('extra', ['', ' You must have two years of aerospace experience.'])
def test_complete_employer_brand_paragraph_is_administrative_without_hiding_added_requirements(extra):
    text='Over the years, we’ve discovered that the most effective and successful associates at apexanalytix are people who have a specific combination of values, skills, and behaviors that we call “The apex Way”. Read more about The apex Way - https://www.apexanalytix.com/careers/'
    assert administrative(text+extra) is (not extra)
    rows=extract_requirements('The Must-Haves\n'+text+extra)
    assert bool(rows) == bool(extra)


@pytest.mark.parametrize('role', ['button','link'])
async def test_duplicate_apply_controls_do_not_turn_locale_search_into_applicant_input(config,db,role):
    engine=Engine(config,db,fill_only=True)
    try:
        page=await engine.browser.new_page()
        control='<button type="button">Apply now</button>' if role=='button' else '<a href="#application">Apply now</a>'
        await page.set_content('<main><h1>Software Engineering Intern</h1>'+control+control+
            '<footer><input role="combobox" aria-label="Search" value="United States (English)"></footer></main>'+
            '<script>window.clicks=0;document.querySelectorAll("button,a").forEach(e=>e.onclick=()=>window.clicks++)</script>')
        adapter=GenericApplicationAdapter(page)
        with pytest.raises(RuntimeError,match='Multiple application entry controls') as failure:
            await adapter.begin()
        assert failure.value.category=='APPLICATION_ENTRY_MODE_UNRESOLVED'
        assert await page.evaluate('window.clicks')==0
        assert not db.rows('SELECT * FROM questions')
    finally:
        await engine.close()
