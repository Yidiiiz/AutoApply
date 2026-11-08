"""Read-only HTTP probe that a listing is still open before a batch selects it.

OPEN and UNKNOWN keep the candidate (the browser still re-checks); REMOVED and
CLOSED retire it without opening an application tab or consuming an attempt.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .freshness import closed_status

log = logging.getLogger('autoapply')
AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


def _get(url, timeout, accept='text/html,application/json'):
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': accept})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.geturl(), response.read(600_000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(200_000).decode('utf-8', 'replace')
        except Exception:
            body = ''
        return exc.code, url, body


def _workday_api(parts):
    # https://tenant.wd5.myworkdayjobs.com/[locale/]site/job/<location>/<slug>
    path = [p for p in parts.path.split('/') if p]
    if 'job' not in path or path.index('job') < 1:
        return None
    i = path.index('job')
    site = path[i-1]
    tenant = parts.netloc.split('.')[0]
    return f'https://{parts.netloc}/wday/cxs/{tenant}/{site}/' + '/'.join(path[i:])


def _workday(parts, timeout):
    api = _workday_api(parts)
    if not api:
        return None
    status, _, body = _get(api, timeout, 'application/json')
    if status in {404, 410}:
        return 'REMOVED'
    # Removed requisitions answer 403 S22 "permission denied"; live ones answer 200.
    if status == 403 and re.search(r'"errorCode"\s*:\s*"S22"', body):
        return 'REMOVED'
    if status == 200:
        try:
            info = json.loads(body).get('jobPostingInfo') or {}
        except ValueError:
            return None
        if not info:
            return 'REMOVED'
        return 'CLOSED' if info.get('canApply') is False else 'OPEN'
    return None


def _workable(parts, timeout):
    match = re.match(r'^/([^/]+)/j/([^/]+)', parts.path)
    if not match:
        return None
    status, _, _ = _get(f'https://apply.workable.com/api/v2/accounts/{match[1]}/jobs/{match[2]}', timeout, 'application/json')
    return 'REMOVED' if status in {404, 410} else 'OPEN' if status == 200 else None


def listing_state(url, timeout=15):
    """Return OPEN, CLOSED, REMOVED or UNKNOWN. Never raises for network errors."""
    try:
        parts = urlsplit(url)
        host = parts.netloc.lower()
        state = None
        if host.endswith('myworkdayjobs.com'):
            state = _workday(parts, timeout)
        elif host == 'apply.workable.com':
            state = _workable(parts, timeout)
        if state:
            return state
        status, final, body = _get(url, timeout)
        if status in {404, 410}:
            return 'REMOVED'
        if 'greenhouse.io' in host and re.search(r'[?&]error=true', final):
            return 'CLOSED'
        text = re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', body))
        closed = closed_status(re.sub(r'\s+', ' ', text))
        if closed:
            return closed
        return 'OPEN' if status == 200 else 'UNKNOWN'
    except Exception as exc:
        log.info('[LIVENESS] Probe failed for %s: %s', url.split('?')[0], type(exc).__name__)
        return 'UNKNOWN'
