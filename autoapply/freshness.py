"""Posting evidence and the single, UTC rolling-age policy. Never infer from discovery."""
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

MAX_LISTING_AGE_DAYS = 30
log = logging.getLogger('autoapply')


def utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def window(days=MAX_LISTING_AGE_DAYS):
    if type(days) is not int or not 1 <= days <= MAX_LISTING_AGE_DAYS:
        raise ValueError('jobs.max_listing_age_days must be between 1 and 30')
    return timedelta(days=days)


def parse_posted(value, reference=None):
    """Naive/date-only source values mean midnight UTC; ambiguous months are unknown."""
    reference = utc(reference)
    if isinstance(value, (date, datetime)):
        return utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()
    if text in {'today', 'just posted'}:
        return reference
    if text == 'yesterday':
        return reference - timedelta(days=1)
    match = re.fullmatch(r'(\d+)\s*(d|days?|h|hours?|w|weeks?)(?: ago)?', text)
    if match:
        seconds = int(match[1]) * (3600 if match[2].startswith('h') else 604800 if match[2].startswith('w') else 86400)
        try:
            return reference - timedelta(seconds=seconds)
        except OverflowError:
            return None
    # A lower-bound age over the maximum is definitely stale, never a fresh boundary.
    if re.fullmatch(r'(?:over\s+30\s+days|30\+\s*days)(?: ago)?', text):
        return reference - timedelta(days=30, seconds=1)
    try:
        return utc(value)
    except (ValueError, TypeError, OverflowError):
        pass
    for fmt in ('%b %d, %Y', '%B %d, %Y', '%m/%d/%Y', '%b %d', '%B %d', '%m/%d'):
        try:
            parsed = datetime.strptime(text if '%Y' in fmt else f'{text} {reference.year}', fmt if '%Y' in fmt else fmt + ' %Y')
            parsed = utc(parsed)
            if '%Y' not in fmt and parsed > reference:
                parsed = parsed.replace(year=parsed.year - 1)
            return parsed
        except ValueError:
            pass
    return None


def freshness_state(posted, days=MAX_LISTING_AGE_DAYS, reference=None):
    duration, reference = window(days), utc(reference)
    value = parse_posted(posted, reference)
    if value is None or value > reference:
        return 'UNKNOWN_DATE'
    return 'FRESH' if value >= reference - duration else 'STALE'


@dataclass(frozen=True)
class PostingDate:
    posted_at: str | None = None
    source: str = ''
    confidence: str = 'unknown'
    original_posted_at: str | None = None
    reposted_at: str | None = None


def extract_posting_date(*, structured=None, api=None, ats=None, explicit=None,
                         html='', relative=None, reference=None, repost=None, genuine_repost=False):
    """Adapters supply only fields whose source contract means *posted*, never updated."""
    reference = utc(reference)
    candidates = []
    soup = BeautifulSoup(html, 'html.parser') if html else None
    if soup:
        def jobs(node):
            if isinstance(node, list):
                for item in node:
                    yield from jobs(item)
            elif isinstance(node, dict):
                types = node.get('@type', [])
                if types == 'JobPosting' or isinstance(types, list) and 'JobPosting' in types:
                    yield node
                yield from jobs(node.get('@graph', []))
        nodes = []
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                nodes.extend(jobs(json.loads(tag.get_text())))
            except (ValueError, TypeError):
                pass
        # An entire search page with many jobs is not evidence for a single card.
        if len(nodes) == 1 and 'datePosted' in nodes[0]:
            candidates.append((nodes[0]['datePosted'], 'json_ld.datePosted', 'high'))
    if structured is not None:
        candidates.insert(0, (structured, 'structured.datePosted', 'high'))
    candidates.extend((value, name, 'high') for value, name in [(api, 'api.posted_at'), (ats, 'ats.datePosted')] if value is not None)
    if explicit is None and soup:
        posted_text = re.search(r'\bposted\s+(?:on\s+)?(\d{4}-\d{2}-\d{2}|[A-Za-z]+\s+\d{1,2},\s+\d{4})\b', soup.get_text(' ', strip=True), re.I)
        if posted_text:
            explicit = posted_text[1]
    if explicit is not None:
        candidates.append((explicit, 'explicit.posted_on', 'medium'))
    if soup:
        tag = soup.select_one('[itemprop="datePosted"]')
        if tag is None:
            tag = next((t for t in soup.select('time[datetime]')
                        if not re.search(r'updated|modified', str(t), re.I)
                        and not re.search(r'\b(?:updated|modified)\b', t.parent.get_text(' ', strip=True), re.I)), None)
        if tag:
            candidates.append((tag.get('datetime') or tag.get('content') or tag.get_text(), 'html.datePosted', 'medium'))
        if relative is None:
            visible = soup.get_text(' ', strip=True)
            matches = re.finditer(r'(?:over\s+)?\d+\+?\s*(?:days?|hours?|weeks?|months?) ago|\byesterday\b|\btoday\b|\bjust posted\b', visible, re.I)
            for match in matches:
                prefix = visible[max(0, match.start()-40):match.start()]
                if re.search(r'\b(?:updated|modified|applied|viewed|saved|active|closed)\s*:?[ ]*(?:on\s*)?$', prefix, re.I):
                    continue
                if match[0].lower() in {'today', 'yesterday'} and not re.search(r'\bposted\s*$', prefix, re.I):
                    # A call to action such as "Apply today" is not posting evidence.
                    time_text = any(t.get_text(' ', strip=True).lower() == match[0].lower()
                                    and not re.search(r'updated|modified', str(t.parent), re.I)
                                    for t in soup.select('time'))
                    if not time_text:
                        continue
                relative = match[0]
                break
    if relative is not None:
        candidates.append((relative, 'relative.posted', 'medium'))
    original = None
    source, confidence = '', 'unknown'
    if candidates:
        value, source, confidence = candidates[0]
        original = parse_posted(value, reference)
        # Do not mask contradictory/malformed higher-priority evidence with a newer fallback.
        if original is None or original > reference:
            log.info('[FRESHNESS] Invalid or future posting date — REJECT')
            original, confidence = None, 'unknown'
    reposted = parse_posted(repost, reference) if genuine_repost else None
    if reposted and reposted <= reference and (not original or reposted >= original):
        return PostingDate(reposted.isoformat(), 'explicit.repost', 'high', original.isoformat() if original else None, reposted.isoformat())
    return PostingDate(original.isoformat() if original else None, source, confidence,
                       original.isoformat() if original else None)


def closed_status(text='', http_status=None, ats_status='', blocked=False):
    if blocked or http_status in {401, 403, 429} or http_status and http_status >= 500:
        return None
    if http_status in {404, 410} or str(ats_status).upper() == 'REMOVED':
        return 'REMOVED'
    if str(ats_status).upper() in {'CLOSED', 'FILLED', 'EXPIRED'}:
        return 'CLOSED'
    if re.search(r'\b(?:this |the )?(?:job|position|requisition|posting) (?:is |has been )?(?:no longer available|closed|filled|removed|expired)\b|\bno longer accepting applications\b|\bapplications closed\b|\bapplication window (?:is )?closed\b', text, re.I):
        return 'CLOSED'
    return None


def stale_boundary(listings, *, newest_first_guaranteed=False, days=30, reference=None):
    # Ranked/promoted/unknown dates can never prove a pagination boundary.
    return bool(newest_first_guaranteed and listings and all(
        freshness_state(item.posted_at, days, reference) == 'STALE' for item in listings))
