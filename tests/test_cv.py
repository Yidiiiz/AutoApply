"""Cover-letter checks use temporary facts and fake providers only."""

from datetime import date
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoapply.cv import (build_cover_letter, company_slug, cover_letter_request, profile_facts,
                          render_pdf, validate_letter_result)
from autoapply.codex_writer import CodexWritingProvider


def test_business_letter_layout_and_escaping():
    profile = {'identity': {'full_name': 'Alex <Student>'},
               'contact': {'city': 'Boston', 'state': 'MA', 'phone': '555-0123', 'email': 'alex@example.test'},
               'links': {'website': 'https://example.test/?a=1&b=2', 'linkedin': 'https://example.test/alex'}}
    html = build_cover_letter(profile, {'company': 'A & B'}, ['I built <script>tools</script>.',
                             'I can contribute.', 'Thank you.'], date(2026, 10, 11))
    assert 'font:12pt/1.15 "Times New Roman",Times,serif' in html
    assert 'p{margin:0 0 12pt;' in html
    assert '@page{size:Letter;margin:1in}' in html and 'color:#000' in html and 'text-align:left' in html
    assert '<h1>Alex &lt;Student&gt;</h1>' in html and 'border-bottom:1px solid #000' in html
    assert 'Boston, MA | 555-0123 | alex@example.test | example.test/?a=1&amp;b=2 | example.test/alex' in html
    assert '<p>October 11, 2026</p>' in html
    assert '<p>A &amp; B Hiring Team<br>A &amp; B</p>' in html
    assert '<p>Dear A &amp; B Hiring Team,</p>' in html
    assert '<p>I built &lt;script&gt;tools&lt;/script&gt;.</p>' in html and '<script>' not in html
    assert 'Sincerely,<br><br>Alex &lt;Student&gt;' in html
    assert '<h2>Education' not in html and 'Arial' not in html
    empty = build_cover_letter({}, {'company': 'Example'}, ['Body'])
    assert ' | ' not in empty
    assert company_slug('A & B / ../') == 'a-b'


@pytest.mark.parametrize('salutation', ['Dear Ms. Lee,', 'Dear Dr. Lee:', 'Dear ' + 'x' * 74 + ',',
                                      'Dear <Lee> & Team,'])
def test_named_salutation_changes_only_salutation(salutation):
    args = ({'identity': {'full_name': 'Alex Student'}}, {'company': 'Example'}, ['Body'], date(2026, 10, 11))
    from html import escape
    default = build_cover_letter(*args)
    assert build_cover_letter(*args, salutation=salutation) == default.replace(
        '<p>Dear Example Hiring Team,</p>', '<p>' + escape(salutation) + '</p>')


@pytest.mark.parametrize('salutation', [None, '', 5, True, [], {}, 'Hello Ms. Lee,', 'dear Ms. Lee,',
    'Dear Ms. Lee', 'Dear Ms. Lee.', 'Dear Ms.\nLee,', 'Dear Ms.\rLee:', 'Dear Ms.\u2028Lee,',
    'Dear Ms. Lee,\n', ' Dear Ms. Lee,', 'Dear ' + 'x' * 75 + ','])
def test_missing_or_invalid_salutation_falls_back(salutation):
    result = {'salutation': salutation, 'paragraphs': [' Body ']}
    assert validate_letter_result(result) == ('paragraphs', ['Body'])
    args = ({}, {'company': 'A &amp; B'}, ['Body'], date(2026, 10, 11))
    assert build_cover_letter(*args, salutation=salutation) == build_cover_letter(*args)


@pytest.mark.parametrize('state,expected', [('Maryland', 'MD'), ('District of Columbia', 'DC'),
                                           ('new york', 'NY'), ('MD', 'MD'), ('Ontario', 'Ontario')])
def test_contact_state_and_clean_links(state, expected):
    profile = {'contact': {'city': 'College Park', 'state': state},
               'links': {'website': 'https://www.example.test/', 'linkedin': 'http://www.linkedin.com/in/alex/'}}
    html = build_cover_letter(profile, {}, ['Body'])
    assert f'College Park, {expected} | example.test | linkedin.com/in/alex' in html
    assert 'white-space:nowrap' in html


def test_job_entities_are_decoded_once(tmp_path):
    job = {'company': 'A &amp; B', 'title': 'Platform &amp; Site Reliability Engineering Internship',
           'location': 'City &amp; State', 'description': 'Use &lt;tools&gt; &amp;amp; more'}
    request = cover_letter_request({}, job, tmp_path)
    assert request['job'] == {'company': 'A & B', 'title': 'Platform & Site Reliability Engineering Internship',
                              'location': 'City & State', 'description': 'Use <tools> &amp; more'}
    assert job['company'] == 'A &amp; B'
    html = build_cover_letter({}, job, ['Body'])
    assert '<p>Dear A &amp; B Hiring Team,</p>' in html
    assert '&amp;amp;' not in html
    nested = build_cover_letter({}, {'company': 'A &amp;amp; B'}, ['Body'])
    assert '<p>Dear A &amp;amp; B Hiring Team,</p>' in nested


def test_provider_request_professional_facts_and_bounded_samples(tmp_path):
    samples = tmp_path / 'samples'
    samples.mkdir()
    for i in range(4):
        (samples / f'{i}.txt').write_text('x' * 5000, encoding='utf-8')
    profile = {'identity': {'first_name': 'Alex', 'last_name': 'Student', 'ssn': 'SECRET'},
               'demographics': {'gender': 'SECRET'}, 'eligibility': 'SECRET', 'sponsorship': 'SECRET',
               'education': {'school': 'Example', 'minors': ['Math'], 'graduation_date': '2028-05', 'ssn': 'SECRET'},
               'education_history': [{'school': 'Earlier'}],
               'employment': [{'title': 'Tutor', 'description': 'Taught Python', 'eligibility': 'SECRET'}],
               'projects': [{'name': 'Tool', 'technologies': ['Python']}],
               'skills': ['Python'], 'skill_categories': {'Languages': ['Python']}, 'links': {'github': 'example.test'}}
    answers = [{'question': 'Why?', 'answer': 'The listed work.'}]
    request = cover_letter_request(profile, {'company': 'Example', 'title': 'Intern', 'description': 'Python',
                                           'location': 'Boston', 'private': 'SECRET'}, samples, answers)
    assert request['task'] == 'cover_letter' and request['answers'] == answers
    assert request['profile'] == profile_facts(profile)
    assert 'SECRET' not in json.dumps(request)
    assert request['profile']['education']['minors'] == ['Math']
    assert request['profile']['employment'][0]['description'] == 'Taught Python'
    assert request['profile']['projects'][0]['technologies'] == ['Python']
    assert len(request['style_samples']) == 3 and all(len(s) == 4000 for s in request['style_samples'])
    assert cover_letter_request({}, {}, tmp_path / 'missing')['style_samples'] == []


@pytest.mark.parametrize('value', [None, [], {}, {'answer': 'Resume'}, {'paragraphs': []},
    {'paragraphs': ['']}, {'paragraphs': [1]}, {'questions': []}, {'questions': ['q'] * 5},
    {'questions': [' ']}, {'questions': ['Q'], 'paragraphs': ['P']},
    {'questions': ['Q'], 'salutation': 'Dear Ms. Lee,'}, {'salutation': 'Dear Ms. Lee,'}])
def test_invalid_provider_output(value):
    with pytest.raises(ValueError, match='Cover letter provider returned invalid output'):
        validate_letter_result(value)


def test_valid_provider_output():
    assert validate_letter_result({'paragraphs': [' Body ']}) == ('paragraphs', ['Body'])
    assert validate_letter_result({'salutation': 'Dear Ms. Lee,', 'paragraphs': [' Body ']}) == ('paragraphs', ['Body'])
    assert validate_letter_result({'questions': [' Why? ']}) == ('questions', ['Why?'])


@pytest.mark.parametrize('question', ['Can you send the job description?', 'Share the POSTING', 'Please paste it'])
def test_posting_questions_are_filtered(question):
    assert validate_letter_result({'questions': [question, 'When can you start?']}) == ('questions', ['When can you start?'])
    assert validate_letter_result({'questions': [question], 'paragraphs': ['Body']}) == ('paragraphs', ['Body'])
    with pytest.raises(ValueError, match='retry'):
        validate_letter_result({'questions': [question]})


@pytest.mark.parametrize('task,expected', [('cover_letter', '250-350 words'),
                                         ('verify_grounding', 'independent factual auditor'),
                                         ('answer', 'fact_ids')])
async def test_codex_prompt_branches_without_cli(monkeypatch, task, expected):
    monkeypatch.setattr('autoapply.codex_writer.shutil.which', lambda name: 'fixture-codex')
    provider = CodexWritingProvider({'name': 'fixture', 'billing': 'included', 'executable': 'unused'}, None, None)
    prompts = []
    async def ready(folder):
        pass
    async def run(args, folder, prompt=None):
        from pathlib import Path
        prompts.append(prompt)
        Path(args[args.index('-o') + 1]).write_text('{"paragraphs": ["Fixture"]}', encoding='utf-8')
    monkeypatch.setattr(provider, 'ensure_ready', ready)
    monkeypatch.setattr(provider, 'run', run)
    assert await provider.generate_response({'task': task}) == {'paragraphs': ['Fixture']}
    assert expected in prompts[0]
    if task == 'cover_letter':
        assert 'Never ask the user for the job description, posting text, company name or role' in prompts[0]
        assert 'personal facts the applicant alone knows' in prompts[0]
        assert '1-4 short questions' in prompts[0] and 'style only' in prompts[0]
        assert 'applicant speaking naturally' in prompts[0]
        assert 'never mention the profile, supplied/provided facts, data, samples or these instructions' in prompts[0]
        assert 'I have not yet worked with X, but ...' in prompts[0]
        assert 'only for something the posting clearly asks for' in prompts[0]
        assert 'at most one gap sentence' in prompts[0]
        assert 'do not ask skipped questions again' in prompts[0]
        assert 'write the letter without that information' in prompts[0]
        assert 'optional "salutation" string' in prompts[0]
        assert 'ONLY if the job description names that person as the contact for this role' in prompts[0]
        assert 'otherwise omit salutation' in prompts[0]
        assert 'single line starting with "Dear " and ending with "," or ":"' in prompts[0]
        assert 'at most 80 characters' in prompts[0]


def test_pdf_boundary_is_local_headless_and_one_page_options(monkeypatch, tmp_path):
    page = Mock()
    page.evaluate.return_value = 1720
    browser = Mock()
    browser.new_page.return_value = page
    chromium = Mock()
    chromium.launch.return_value = browser
    context = Mock()
    context.__enter__ = Mock(return_value=SimpleNamespace(chromium=chromium))
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: context)
    render_pdf('<html>Local facts</html>', tmp_path / 'cv.pdf')
    chromium.launch.assert_called_once_with(headless=True)
    page.route.assert_called_once()
    route = Mock()
    page.route.call_args.args[1](route)
    route.abort.assert_called_once()
    fit_script, height_script = [call.args[0] for call in page.evaluate.call_args_list]
    assert "document.querySelector('header p')" in fit_script
    assert 'contact.scrollWidth > contact.clientWidth' in fit_script
    assert "contact.style.fontSize = size + 'px'" in fit_script
    assert height_script == 'document.body.scrollHeight'
    assert page.pdf.call_args.kwargs['scale'] == 0.5
    assert page.pdf.call_args.kwargs['format'] == 'Letter'
    assert page.pdf.call_args.kwargs['margin'] == dict.fromkeys(('top', 'bottom', 'left', 'right'), '1in')
    browser.close.assert_called_once()


@pytest.mark.browser
def test_long_letter_pdf_fits_one_page_offline(monkeypatch, tmp_path):
    import re
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    executable = next((p for p in (Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
                                  Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if p.is_file()), None)
    if executable is None:
        pytest.skip('System Chromium is unavailable')
    profile = {'identity': {'full_name': 'Alex Student'},
               'contact': {'city': 'College Park', 'state': 'Maryland', 'phone': '+1 301-555-0123',
                           'email': 'alex.long.contact@example.test'},
               'links': {'website': 'https://www.example.test/student/',
                         'linkedin': 'https://www.linkedin.com/in/alex-long-student-name/'}}
    paragraphs = [' '.join(['Technical collaboration and software development support reliable systems.'] * 16)] * 3
    html = build_cover_letter(profile, {'company': 'Example'}, paragraphs)
    with sync_playwright() as playwright:
        measurements = []
        def launch(**options):
            browser = playwright.chromium.launch(executable_path=str(executable), **options)
            new_page = browser.new_page
            def measured_page(**options):
                page = new_page(**options)
                pdf = page.pdf
                def measured_pdf(**options):
                    measurements.append(page.evaluate('''() => {
                        const p = document.querySelector('header p');
                        return {width: p.clientWidth, scroll: p.scrollWidth,
                            font: parseFloat(getComputedStyle(p).fontSize),
                            lineHeight: getComputedStyle(document.body).lineHeight};
                    }'''))
                    return pdf(**options)
                monkeypatch.setattr(page, 'pdf', measured_pdf)
                return page
            monkeypatch.setattr(browser, 'new_page', measured_page)
            return browser
        boundary = Mock()
        boundary.__enter__ = Mock(return_value=SimpleNamespace(chromium=SimpleNamespace(
            launch=launch)))
        boundary.__exit__ = Mock(return_value=False)
        monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: boundary)
        target = tmp_path / 'letter.pdf'
        render_pdf(html, target)
        pdf = target.read_bytes()
        assert pdf.startswith(b'%PDF-')
        assert len(re.findall(rb'/Type\s*/Page\b', pdf)) == 1
        assert measurements[0]['scroll'] <= measurements[0]['width']
        assert measurements[0]['font'] < 10 * 96 / 72
        assert measurements[0]['lineHeight'] == '18.4px'


async def test_t057_writing_policy_and_grounded_provider_packet(tmp_path, monkeypatch):
    from pathlib import Path
    profile = dict(identity=dict(first_name='Synthetic'), writing_preferences=dict(exclude_attune_metrics=True,
                   attune_not_applicant_coded=True, cover_letter_excluded_projects=['Attune'],
                   cover_letter_style='Natural, direct, specific to employer and role', secret='DO NOT SHARE'),
                   projects=[dict(name='Attune',description='Reviewed results',contribution='Did not implement this project',bullets=['999 tests']),
                             dict(name='Verified tool',description='Reduced runtime by 25%',contribution='Implemented the parser')])
    job = dict(company='Synthetic Orbit',title='Software Intern',canonical_url='https://example.test/official-job',
               description='Build customer mission scheduling tools and operate satellite constellations.')
    request = cover_letter_request(profile,job,tmp_path)
    assert request['profile']['projects'] == [profile['projects'][1]]
    assert profile_facts(profile)['projects'][0]['contribution'] == 'Did not implement this project'
    assert 'secret' not in request['profile']['writing_preferences']
    assert request['evidence_packet']['source_url'] == job['canonical_url']
    monkeypatch.setattr('autoapply.codex_writer.shutil.which',lambda _: 'fixture')
    provider = CodexWritingProvider(dict(name='fixture',billing='included'),None,None)
    async def ready(folder): pass
    prompts = []
    async def run(args,folder,prompt=None):
        prompts.append(prompt)
        Path(args[args.index('-o')+1]).write_text('{"paragraphs":["Grounded fixture body."]}',encoding='utf-8')
    monkeypatch.setattr(provider,'ensure_ready',ready)
    monkeypatch.setattr(provider,'run',run)
    await provider.generate_response(request)
    assert all(v in prompts[0] for v in ['250-350','two relevant verified examples','never imply the applicant coded or implemented Attune','source URL','25%'])
    assert '999 tests' not in prompts[0]
