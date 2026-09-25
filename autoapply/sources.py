import hashlib
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


def parse_repository(text, source, reference=None, max_results=None):
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
    result, seen = [], set()
    for table in tables:
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
            if identity in seen:
                continue
            seen.add(identity)
            evidence = extract_posting_date(explicit=clean(get("date")), reference=reference)
            result.append(Listing(company, title, location, url, source, identity, evidence.posted_at,
                                  f"{clean(get('date'))}; source revision {reference.isoformat()}", closed=closed,
                                  posted_at_source='repository.posted', posted_at_confidence=evidence.confidence,
                                  original_posted_at=evidence.original_posted_at))
            if max_results is not None and len(result) >= max_results:
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
        self.git("fetch", "--depth", "1", "origin", "refs/heads/" + branch)
        revision = self.git("rev-parse", "FETCH_HEAD").strip()
        reference = datetime.fromisoformat(self.git("show", "-s", "--format=%cI", revision).strip())
        return revision, branch, reference

    def discover(self, revision, reference):
        files = self.git("ls-tree", "--name-only", revision).splitlines()
        paths = [path for path in files if re.fullmatch(r"README(?:[-_]Off[-_]Season)?\.md", path, re.I)]
        paths.sort(key=lambda path: (path.lower() != 'readme.md', path))
        result = []
        for path in paths:
            # Each configured table document is a query; cap it independently so
            # an off-season archive cannot consume the main README's allowance.
            result.extend(parse_repository(self.git("show", revision + ":" + path), self.url, reference, self.max_results))
        if not result:
            raise RuntimeError("No listing rows parsed; source may have changed its format")
        return result


async def scan_github(config, db, force=False):
    import asyncio
    counts = {"sources": 0, "new": 0, "errors": 0, "pages_scanned": 0}
    for entry in config["github_sources"]:
        url = entry["url"]
        previous = db.one("SELECT * FROM source_revisions WHERE source=?", (url,))
        if not force and previous and previous["checked_at"]:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["checked_at"])).total_seconds()
            if elapsed < config["polling"]["github_minutes"] * 60:
                continue
        counts["sources"] += 1
        try:
            source = GitHubRepositorySource(config, url)
            revision, branch, reference = await asyncio.to_thread(source.update)
            if force or not previous or previous["revision"] != revision:
                listings = await asyncio.to_thread(source.discover, revision, reference)
                counts["pages_scanned"] += 1
                with db.ingest_batch():
                    for listing in listings:
                        _, created = db.ingest(listing, config)
                        counts["new"] += created
                        for metric,value in db.last_ingest_result.items():
                            counts[metric] = counts.get(metric,0) + value
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

    async def discover(self):
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
                    if url in visited:
                        self.stop_reason = 'pagination cycle'
                        break
                    visited.add(url)
                    condition, evidence = await self.browser.navigate(page,url)
                    if not condition:
                        condition,evidence = await page_condition(page)
                    self.pages_scanned += 1
                    if condition:
                        self.db.notify('source-auth:'+self.name, {'message': f'{self.name}: {condition}: {evidence}. Restore the source session or inspect its layout.'})
                        self.stop_reason = 'source unavailable'
                        break
                    pattern = r'/jobs/view/\d+' if self.name == 'linkedin' else r'/(?:stu/)?jobs/\d+'
                    soup = BeautifulSoup(await page.content(),'html.parser')
                    batch = []
                    for link in soup.find_all('a',href=re.compile(pattern)):
                        if scanned >= limits['max_results_per_query']:
                            break
                        card = link.find_parent('li') or link.find_parent('article') or link.parent
                        title = link.get_text(' ',strip=True)
                        if not title:
                            continue
                        job_url = urljoin(page.url,link['href'])
                        identity = canonical_url(job_url)
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
                    if stale_boundary(batch,newest_first_guaranteed=self.newest_first_guaranteed,
                                      days=self.config['jobs']['max_listing_age_days']):
                        self.stop_reason = 'stale boundary'
                        log.info('[PAGINATION] Reached stale boundary; stopping source pagination')
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
                    if urlsplit(next_url).netloc != urlsplit(search_url).netloc:
                        self.stop_reason = 'invalid next-page host'
                        break
                    url = recency_url(self.name,next_url,self.config['jobs']['max_listing_age_days'])
                else:
                    self.stop_reason = 'page cap'
                log.info('[PAGINATION] %s: %s; results=%s',self.name,self.stop_reason,scanned)
        finally:
            await page.close()
        return result
