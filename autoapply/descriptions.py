"""Fetch public job descriptions without a browser or applicant data."""
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup

from . import liveness
from .applications import description_text


DESCRIPTION_SELECTORS = ('#content, #job-description, .job-description, '
                         '.jv-job-detail-description, .posting-page .content, '
                         '[data-testid="job-description"], main')


def _text(value):
    return description_text(BeautifulSoup(html.unescape(value or ''), 'html.parser'))


def _json(url, timeout):
    status, _, body = liveness._get(url, timeout, 'application/json')
    return json.loads(body) if status == 200 else {}


def _public_description(parts, timeout):
    host = (parts.hostname or '').lower()
    path = parts.path.strip('/').split('/')
    query = parse_qs(parts.query)
    if host in {'boards.greenhouse.io', 'job-boards.greenhouse.io'}:
        match = re.match(r'^/([^/]+)/jobs/(\d+)', parts.path)
        board = match[1] if match else (query.get('for') or [''])[0]
        job_id = match[2] if match else (query.get('token') or query.get('gh_jid') or [''])[0]
        if board and job_id:
            data = _json(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}', timeout)
            return _text(data.get('content'))
    elif host == 'jobs.lever.co' and len(path) >= 2:
        data = _json(f'https://api.lever.co/v0/postings/{path[0]}/{path[1]}', timeout)
        chunks = [data.get('descriptionPlain') or _text(data.get('description'))]
        for item in data.get('lists') or []:
            chunks.extend([item.get('text', ''), _text(item.get('content'))])
        chunks.append(data.get('additionalPlain') or _text(data.get('additional')))
        return '\n'.join(chunk for chunk in chunks if chunk)
    elif host == 'jobs.ashbyhq.com' and len(path) >= 2:
        data = _json(f'https://api.ashbyhq.com/posting-api/job-board/{path[0]}?includeCompensation=false', timeout)
        job = next((job for job in data.get('jobs', []) if job.get('id') == path[1]), {})
        return job.get('descriptionPlain') or _text(job.get('descriptionHtml'))
    elif host == 'jobs.smartrecruiters.com' and len(path) >= 2:
        job_id = path[1].split('-')[0]
        data = _json(f'https://api.smartrecruiters.com/v1/companies/{path[0]}/postings/{job_id}', timeout)
        sections = (data.get('jobAd') or {}).get('sections') or {}
        return '\n'.join(_text(section.get('text')) for section in sections.values())
    elif host.endswith('.myworkdayjobs.com'):
        api = liveness._workday_api(parts)
        if api:
            return _text((_json(api, timeout).get('jobPostingInfo') or {}).get('jobDescription'))
    return ''


def _job_postings(data):
    if isinstance(data, list):
        for item in data:
            yield from _job_postings(item)
    elif isinstance(data, dict):
        types = data.get('@type', [])
        if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
            yield data
        yield from _job_postings(data.get('@graph', []))


def fetch_description(url, timeout=15) -> str:
    """Return 200 to 20,000 characters of plain text, or '' on failure."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return ''
        icims = parts.hostname.lower().endswith('.icims.com')
        if icims:
            query = parse_qs(parts.query, keep_blank_values=True)
            query['in_iframe'] = ['1']
            url = parts._replace(query=urlencode(query, doseq=True)).geturl()
        try:
            text = _public_description(parts, timeout)
        except Exception:
            text = ''
        if not text:
            status, _, body = liveness._get(url, timeout)
            if status != 200:
                return ''
            soup = BeautifulSoup(body, 'html.parser')
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    for posting in _job_postings(json.loads(script.string or script.get_text())):
                        text = _text(posting.get('description'))
                        if text:
                            break
                except (ValueError, TypeError, AttributeError):
                    continue
                if text:
                    break
            if not text:
                node = soup.select_one('.iCIMS_JobContent, .iCIMS_InfoMsg_Job') if icims else None
                if node is None:
                    node = soup.select_one(DESCRIPTION_SELECTORS)
                text = description_text(node if node is not None else soup)
        text = '\n'.join(line for raw in text.splitlines()
                         if (line := re.sub(r'\s+', ' ', raw).strip()))[:20000]
        return text if len(text) >= 200 else ''
    except Exception:
        return ''


def store(db, job_id, text):
    """Only replace a stored description with longer public text."""
    db.execute("UPDATE jobs SET description=? WHERE id=? AND length(coalesce(description, '')) < ?",
               (text, job_id, len(text)))
