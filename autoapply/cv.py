"""Cover-letter inputs and standard business-letter rendering."""

from datetime import date
from html import escape, unescape
from pathlib import Path
import re


US_STATES = dict(zip(
    ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|'
     'Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|'
     'Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|'
     'New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|'
     'South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|'
     'Wisconsin|Wyoming|District of Columbia').lower().split('|'),
    'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM '
    'NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split()))


def text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ''


def profile_name(profile):
    identity = profile.get('identity') or {}
    return text(identity.get('full_name')) or ' '.join(filter(None, (
        text(identity.get('first_name')), text(identity.get('last_name')))))


def company_slug(company):
    return re.sub(r'[^a-z0-9]+', '-', text(company).lower()).strip('-')[:80] or 'company'


def cover_letter_request(profile, job, samples_folder, answers=None):
    """Allowlist professional facts; samples supply style, never applicant facts."""
    fields = {
        'education': ('school', 'degree', 'major', 'minors', 'minor', 'graduation_date',
                      'start_date', 'gpa', 'currently_enrolled', 'current', 'coursework', 'relevant_coursework'),
        'employment': ('company', 'title', 'location', 'start_date', 'end_date', 'current', 'description', 'bullets'),
        'projects': ('name', 'role', 'company', 'url', 'start_date', 'end_date', 'technologies', 'description', 'bullets'),
        'links': ('website', 'personal_website', 'linkedin', 'github', 'portfolio', 'other'),
    }
    facts = {'name': profile_name(profile)}
    for section, keys in fields.items():
        value = profile.get(section)
        select = lambda item: {k: item[k] for k in keys if k in item}
        if isinstance(value, dict):
            facts[section] = select(value)
        elif isinstance(value, list):
            facts[section] = [select(item) for item in value if isinstance(item, dict)]
    facts['education_history'] = [{k: item[k] for k in fields['education'] if k in item}
                                  for item in profile.get('education_history', []) if isinstance(item, dict)]
    for section in ('skills', 'skill_categories'):
        if profile.get(section):
            facts[section] = profile[section]
    folder = Path(samples_folder)
    samples = [path.read_text(encoding='utf-8')[:4000] for path in sorted(folder.glob('*.txt'))[:3]]
    return dict(task='cover_letter', job={k: unescape(text(job.get(k))) for k in ('company', 'title', 'location', 'description')},
                profile=facts, style_samples=samples, answers=answers or [],
                skipped_questions=[item['question'] for item in answers or [] if not item['answer']])


def validate_letter_result(value):
    if isinstance(value, dict) and isinstance(value.get('questions'), list):
        questions = value['questions']
        if questions and all(isinstance(item, str) for item in questions):
            filtered = [item for item in questions if not any(
                keyword in item.lower() for keyword in ('job description', 'posting', 'paste'))]
            if len(filtered) != len(questions):
                value = dict(value)
                if filtered:
                    value['questions'] = filtered
                elif 'paragraphs' in value:
                    del value['questions']
                else:
                    raise ValueError('Cover letter provider returned invalid output; please retry generation')
    if isinstance(value, dict) and set(value) in ({'paragraphs'}, {'salutation', 'paragraphs'}, {'questions'}):
        key = 'paragraphs' if 'paragraphs' in value else 'questions'
        items = value[key]
        if (isinstance(items, list) and items and (key != 'questions' or len(items) <= 4)
                and all(isinstance(item, str) and item.strip() for item in items)):
            return key, [item.strip() for item in items]
    raise ValueError('Cover letter provider returned invalid output; expected paragraphs or 1-4 short questions; please retry generation')


def build_cover_letter(profile, job, paragraphs, today=None, salutation=None):
    """Return escaped, print-ready HTML with a fixed layout and optional salutation."""
    name, company = profile_name(profile), unescape(text(job.get('company')))
    if not (isinstance(salutation, str) and len(salutation) <= 80 and len(salutation.splitlines()) == 1
            and salutation.startswith('Dear ') and salutation.endswith((',', ':'))):
        salutation = 'Dear ' + company + ' Hiring Team,'
    contact, links = profile.get('contact') or {}, profile.get('links') or {}
    state = text(contact.get('state'))
    clean_link = lambda value: re.sub(r'^(?:https?://)?(?:www\.)?', '', text(value), flags=re.I).rstrip('/')
    header = [', '.join(filter(None, (text(contact.get('city')), US_STATES.get(state.lower(), state)))),
              text(contact.get('phone')), text(contact.get('email')),
              clean_link(links.get('personal_website') or links.get('website')), clean_link(links.get('linkedin'))]
    today = today or date.today()
    written_date = today.strftime('%B') + ' ' + str(today.day) + ', ' + str(today.year)
    return ''.join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>',
        escape(name + ' - Cover Letter - ' + company), '</title><style>',
        '@page{size:Letter;margin:1in}*{box-sizing:border-box}'
        'body{font:12pt/1.15 "Times New Roman",Times,serif;color:#000;margin:0;text-align:left}'
        'header{border-bottom:1px solid #000;padding-bottom:10pt;margin-bottom:12pt}'
        'h1{font-size:18pt;font-weight:normal;margin:0 0 6pt}'
        'header p{font-size:10pt;margin:0;white-space:nowrap}p{margin:0 0 12pt;overflow-wrap:anywhere}'
        '.closing{margin-bottom:0}</style></head><body><header><h1>', escape(name), '</h1><p>',
        escape(' | '.join(dict.fromkeys(filter(None, header)))), '</p></header><p>', escape(written_date),
        '</p><p>', escape(company + ' Hiring Team'), '<br>', escape(company), '</p><p>',
        escape(salutation), '</p>',
        ''.join('<p>' + escape(paragraph) + '</p>' for paragraph in paragraphs),
        '<p class="closing">Sincerely,<br><br>', escape(name), '</p></body></html>'])


def render_pdf(html, path):
    """Small replaceable boundary around the existing local Chromium dependency."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 624, 'height': 864})
            page.route('**/*', lambda route: route.abort())
            page.set_content(html, wait_until='domcontentloaded', timeout=15000)
            page.evaluate('''() => {
                const contact = document.querySelector('header p');
                if (!contact) return;
                let size = parseFloat(getComputedStyle(contact).fontSize);
                while (contact.scrollWidth > contact.clientWidth && size > 1) {
                    size -= 0.5;
                    contact.style.fontSize = size + 'px';
                }
            }''')
            height = page.evaluate('document.body.scrollHeight')
            page.pdf(path=str(path), format='Letter', margin={k: '1in' for k in ('top', 'bottom', 'left', 'right')},
                     scale=min(1, max(0.1, 860 / max(height, 1))), print_background=True)
        finally:
            browser.close()
