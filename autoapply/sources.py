import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit

from bs4 import BeautifulSoup

from .jobs import canonical_url, normalize
from .models import Listing, now
from .freshness import extract_posting_date, closed_status, stale_boundary

log = logging.getLogger("autoapply")


def clean(value):
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<br\s*/?>", "; ", value, flags=re.I)
    return BeautifulSoup(value, "html.parser").get_text(" ", strip=True).strip(" *\n\r")


def links(value):
    return re.findall(r"\[[^\]]*\]\((https?://[^\s)]+)\)", value) + [a["href"] for a in BeautifulSoup(value, "html.parser").find_all("a", href=True)]


def context_key(source, query='', document='', section='', page=''):
    return json.dumps([source, query, document, section, page], separators=(',', ':'))


def record_contexts(db, observations):
    for key, fingerprint, members in observations:
        db.execute('INSERT OR REPLACE INTO discovery_contexts VALUES (?,?,?,?)',
                   (key, fingerprint, datetime.now().astimezone().date().isoformat(), json.dumps(members)))


def parse_repository(text, source, reference=None, max_results=None, *, document='',
                     cache=None, observations=None):
    """Parse header-driven HTML and Markdown tables without executing repo code."""
    reference = reference or datetime.now(timezone.utc)
    tables = []
    for table in BeautifulSoup(text, "html.parser").find_all("table"):
        tables.append([[str(cell) for cell in row.find_all(["th", "td"], recursive=False)] for row in table.find_all("tr")])
    current = []
    for line in text.splitlines() + [""]:
        if line.strip().startswith("|"):
            cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
            if not all(re.fullmatch(r"[\s:-]+", cell) for cell in cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    result, consumed = [], 0
    for table_number, table in enumerate(tables):
        if not table:
            continue
        headers = [normalize(clean(cell)) for cell in table[0]]
        def column(*names):
            return next((i for i, h in enumerate(headers) if any(name in h for name in names)), None)
        indices = {"company": column("company", "employer"), "title": column("role", "position", "title"),
                   "location": column("location"), "application": column("application", "apply", "link", "posting"),
                   "date": next((i for i,h in enumerate(headers) if h in {"date", "age", "posted", "date posted", "posted on", "posting date"}), None)}
        if any(indices[key] is None for key in ["company", "title", "location", "application"]):
            continue
        key = context_key(source, document=document, section=str(table_number))
        fingerprint = hashlib.sha256(json.dumps([table, max_results, consumed]).encode()).hexdigest()
        prior = (cache or {}).get(key)
        if (prior and prior['fingerprint'] == fingerprint
                and prior['checked_day'] == datetime.now().astimezone().date().isoformat()):
            members = json.loads(prior['members'])
            if observations is not None:
                observations.append((key, fingerprint, members))
            consumed += sum(len(v) if isinstance(v, list) else 1 for v in members.values())
            if max_results is not None and consumed >= max_results:
                return result
            continue
        members = {}
        if observations is not None:
            observations.append((key, fingerprint, members))
        last_company = ""
        for row in table[1:]:
            if len(row) < len(headers):
                continue
            get = lambda key: row[indices[key]] if indices[key] is not None else ""
            company = re.sub(r"[🔥🛂🇺🇸🎓]", "", clean(get("company"))).strip()
            if not company or company in {"↳", "↪", "→", "\"", "〃"}:
                company = last_company
            if not company:
                continue
            last_company = company
            title, location = clean(get("title")), clean(get("location"))
            urls = [u for u in links(get("application")) if u.startswith(("https://", "http://"))]
            urls.sort(key=lambda u: "simplify.jobs" in u)
            closed = "🔒" in get("application") or bool(re.search(r"\bclosed\b", clean(get("application")), re.I))
            if not urls and not closed:
                continue
            token = hashlib.sha256(f"{company}|{title}|{location}".encode()).hexdigest()[:24]
            url = urls[0] if urls else source + "?closed_listing=" + token
            try:
                identity = canonical_url(url)
            except ValueError:
                continue
            # Membership belongs to this document/table, even when another
            # table or source already contains the same canonical job URL.
            row_hash = hashlib.sha256(json.dumps(row).encode()).hexdigest()
            if row_hash in members.get(identity, []):
                continue
            members.setdefault(identity, []).append(row_hash)
            evidence = extract_posting_date(explicit=clean(get("date")), reference=reference)
            result.append(Listing(company, title, location, url, source, identity, evidence.posted_at,
                                  f"{clean(get('date'))}; source revision {reference.isoformat()}", closed=closed,
                                  posted_at_source='repository.posted', posted_at_confidence=evidence.confidence,
                                  original_posted_at=evidence.original_posted_at))
            consumed += 1
            if max_results is not None and consumed >= max_results:
                log.info('[PAGINATION] Repository result cap reached: %s', max_results)
                return result
    return result


class GitHubRepositorySource:
    def __init__(self, config, url):
        if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?", url):
            raise ValueError("GitHub source must be an HTTPS repository URL")
        self.max_results = config["discovery"]["max_results_per_query"]
        self.url = url.rstrip("/").removesuffix(".git")
        self.path = config.private / "sources" / (hashlib.sha256(self.url.encode()).hexdigest()[:16] + ".git")

    def git(self, *args, local=True):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        cmd = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
               "-c", "safe.directory=" + self.path.as_posix()]
        if local:
            cmd += ["--git-dir=" + str(self.path)]
        result = subprocess.run(cmd + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, env=env)
        if result.returncode:
            # Never expose credential helpers, tokens or arbitrary remote stderr in logs.
            raise RuntimeError("Git source operation failed: " + args[0])
        return result.stdout

    def update(self):
        remote = self.git("ls-remote", "--symref", self.url, "HEAD", local=False)
        match = re.search(r"ref: refs/heads/(.+)\s+HEAD", remote)
        if not match:
            raise RuntimeError("Cannot determine repository default branch")
        branch = match[1].strip()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.git("clone", "--bare", "--depth", "1", "--branch", branch, self.url, str(self.path), local=False)
        remote_head = re.search(r'^([0-9a-f]{40,64})\s+HEAD$', remote, re.M)
        try:
            local_revision = self.git('rev-parse', 'FETCH_HEAD').strip()
        except RuntimeError:
            local_revision = None
        if not remote_head or remote_head[1] != local_revision:
            self.git("fetch", "--depth", "1", "origin", "refs/heads/" + branch)
            local_revision = self.git("rev-parse", "FETCH_HEAD").strip()
        revision = local_revision
        reference = datetime.fromisoformat(self.git("show", "-s", "--format=%cI", revision).strip())
        return revision, branch, reference

    def discover(self, revision, reference):
        files = self.git("ls-tree", "--name-only", revision).splitlines()
        paths = [path for path in files if re.fullmatch(r"README(?:[-_]Off[-_]Season)?\.md", path, re.I)]
        paths.sort(key=lambda path: (path.lower() != 'readme.md', path))
        result = []
        self.documents_scanned = 0
        for path in paths:
            self.documents_scanned += 1
            # Each configured table document is a query; cap it independently so
            # an off-season archive cannot consume the main README's allowance.
            observed = []
            result.extend(parse_repository(self.git("show", revision + ":" + path), self.url, reference, self.max_results,
                          document=path, cache=getattr(self, 'cache', {}),
                          observations=observed))
            if not observed:
                raise RuntimeError('Repository document has no recognized listing tables')
            if hasattr(self, 'observations'):
                self.observations.extend(observed)
        if not result and not any(m for _, _, m in getattr(self, 'observations', [])):
            raise RuntimeError("No listing rows parsed; source may have changed its format")
        return result


async def scan_github(config, db, force=False, *, quick=False, stopped=lambda: False):
    import asyncio
    counts = {"sources": 0, "new": 0, "errors": 0, "pages_scanned": 0}
    for entry in config["github_sources"]:
        if stopped():
            counts['errors'] += 1
            break
        if not entry.get('enabled', True):
            continue
        url = entry["url"]
        previous = db.one("SELECT * FROM source_revisions WHERE source=?", (url,))
        if not force and not quick and previous and not previous['error'] and previous["checked_at"]:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["checked_at"])).total_seconds()
            if elapsed < config["polling"]["github_minutes"] * 60:
                continue
        counts["sources"] += 1
        try:
            source = GitHubRepositorySource(config, url)
            source.cache = {} if force else {r['context_key']: r for r in db.rows(
                "SELECT * FROM discovery_contexts WHERE json_extract(context_key,'$[0]')=?", (source.url,))}
            source.observations = []
            revision, branch, reference = await asyncio.to_thread(source.update)
            if stopped():
                raise InterruptedError('Discovery stopped')
            if force or not previous or previous['error'] or previous["revision"] != revision:
                listings = await asyncio.to_thread(source.discover, revision, reference)
                counts["pages_scanned"] += source.documents_scanned
                with db.ingest_batch():
                    for listing in listings:
                        if stopped():
                            raise InterruptedError('Discovery stopped')
                        _, created = db.ingest(listing, config)
                        counts["new"] += created
                        for metric,value in db.last_ingest_result.items():
                            counts[metric] = counts.get(metric,0) + value
                    record_contexts(db, source.observations)
            db.execute("INSERT OR REPLACE INTO source_revisions VALUES (?,?,?,?,NULL)", (url, revision, branch, now()))
        except Exception as exc:
            counts["errors"] += 1
            db.execute("""INSERT INTO source_revisions(source,checked_at,error) VALUES (?,?,?)
                ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,error=excluded.error""", (url, now(), type(exc).__name__))
            db.event(None, "source_error", f"{url}: {type(exc).__name__}")
            db.notify("source:" + url, {"message": f"Source scan failed: {url}. Inspect source configuration and network access."})
    return counts


def recency_url(name, url, days):
    # Existing LinkedIn search parameter; choose only documented UI window sizes.
    # Handshake has no verified portable date parameter, so leave saved URLs intact.
    if name != 'linkedin':
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    seconds = (30 if days >= 30 else 7 if days >= 7 else 1) * 86400
    existing = re.fullmatch(r'r(\d+)', query.get('f_TPR', ''))
    if existing and int(existing[1]) > 0:
        seconds = min(seconds, int(existing[1]))
    query['f_TPR'] = 'r'+str(seconds)
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))


class BrowserJobSource:
    """Bounded visible cards. No current source guarantees ordering (including promotions)."""
    newest_first_guaranteed = False

    def __init__(self, name, config, browser, db):
        self.name, self.config, self.browser, self.db = name, config, browser, db
        self.pages_scanned = 0
        self.stop_reason = 'no next page'
        self.errors = 0
        self.observations = []

    async def discover(self, *, full=False, stopped=lambda: False):
        from .browser import page_condition
        result = []
        settings = self.config[self.name]
        if not settings['enabled']:
            return result
        page = await self.browser.new_page()
        limits = self.config['discovery']
        try:
            for search_url in settings['search_urls']:
                url = recency_url(self.name, search_url, self.config['jobs']['max_listing_age_days'])
                visited, scanned, seen = set(), 0, set()
                for page_number in range(limits['max_pages_per_query']):
                    if stopped():
                        raise InterruptedError('Discovery stopped')
                    if url in visited:
                        self.errors += 1
                        self.stop_reason = 'pagination cycle'
                        break
                    visited.add(url)
                    condition, evidence = await self.browser.navigate(page,url)
                    if not condition:
                        condition,evidence = await page_condition(page)
                    self.pages_scanned += 1
                    if condition:
                        self.errors += 1
                        self.db.notify('source-auth:'+self.name, {'message': f'{self.name}: {condition}: {evidence}. Restore the source session or inspect its layout.'})
                        self.stop_reason = 'source unavailable'
                        break
                    pattern = r'/jobs/view/\d+' if self.name == 'linkedin' else r'/(?:stu/)?jobs/\d+'
                    soup = BeautifulSoup(await page.content(),'html.parser')
                    batch, page_members = [], {}
                    cards = soup.find_all('a', href=re.compile(pattern))
                    if not cards and not re.search(r'no (?:matching )?(?:jobs|results|postings)(?: found)?|0 jobs found',
                                                   soup.get_text(' ', strip=True), re.I):
                        self.errors += 1
                        self.stop_reason = 'unrecognized source layout'
                        break
                    for link in cards:
                        if scanned >= limits['max_results_per_query']:
                            break
                        card = link.find_parent('li') or link.find_parent('article') or link.parent
                        title = link.get_text(' ',strip=True)
                        if not title:
                            continue
                        job_url = urljoin(page.url,link['href'])
                        identity = canonical_url(job_url)
                        page_members[identity] = hashlib.sha256(str(card).encode()).hexdigest()
                        if identity in seen:
                            continue
                        seen.add(identity)
                        scanned += 1
                        text = card.get_text(' ',strip=True)
                        age = re.search(r'(?:over\s+)?\d+\+?\s*(?:days?|hours?|weeks?|months?) ago|\byesterday\b|\btoday\b|\bjust posted\b',text,re.I)
                        posting = extract_posting_date(html=str(card))
                        company = card.select_one('.base-search-card__subtitle, .artdeco-entity-lockup__subtitle, [data-company-name]')
                        location = card.select_one('.job-search-card__location, .artdeco-entity-lockup__caption, [data-job-location]')
                        batch.append(Listing(company.get_text(' ',strip=True) if company else 'Unknown employer',
                            title,location.get_text(' ',strip=True) if location else '',job_url,self.name,identity,
                            posting.posted_at,age[0] if age else '',closed=bool(closed_status(text)),
                            posted_at_source=posting.source,posted_at_confidence=posting.confidence,
                            original_posted_at=posting.original_posted_at))
                    result.extend(batch)
                    # Neither adapter proves ordering. Known/pinned/old rows
                    # never stop traversal, in either quick or daily mode.
                    key = context_key(self.name, search_url, section='cards', page=url)
                    self.observations.append((key, hashlib.sha256(str(soup).encode()).hexdigest(), page_members))
                    if (not full and self.newest_first_guaranteed
                            and stale_boundary(batch, newest_first_guaranteed=True,
                                               days=self.config['jobs']['max_listing_age_days'])):
                        self.stop_reason = 'stale boundary'
                        break
                    if scanned >= limits['max_results_per_query']:
                        self.stop_reason = 'result cap'
                        break
                    # Only follow a next link actually supplied by this page, on the same host.
                    next_link = soup.select_one('a[rel="next"][href], a[aria-label="Next"][href]')
                    if not next_link or next_link.get('aria-disabled') == 'true':
                        self.stop_reason = 'no next page'
                        break
                    next_url = urljoin(page.url,next_link['href'])
                    next_parts = urlsplit(next_url)
                    if (next_parts.scheme != 'https' or next_parts.username or next_parts.password
                            or next_parts.netloc != urlsplit(search_url).netloc):
                        self.errors += 1
                        self.stop_reason = 'invalid next-page host'
                        break
                    url = recency_url(self.name,next_url,self.config['jobs']['max_listing_age_days'])
                else:
                    self.stop_reason = 'page cap'
                log.info('[PAGINATION] %s: %s; results=%s',self.name,self.stop_reason,scanned)
        finally:
            await page.close()
        return result
