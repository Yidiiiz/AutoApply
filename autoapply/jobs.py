"""Conservative identity, freshness, location and eligibility rules."""
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import fact
from .models import Eligibility


def normalize(text):
    return _normalize_text(str(text))


@lru_cache(maxsize=4096)
def _normalize_text(text):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize('NFKC', text).casefold()).strip()


def canonical_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Application URL must be an HTTP(S) URL without credentials")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in
             {"ref", "referrer", "source", "src", "gh_src", "lever-source", "lever-origin", "fbclid", "gclid", "trk", "trackingid"}]
    path = re.sub(r"/+$", "", parts.path) or "/"
    if "lever.co" in (parts.hostname or ""):
        path = re.sub(r"/apply$", "", path)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(sorted(query)), ""))


def ats_identity(url):
    p = urlsplit(canonical_url(url))
    host, path, q = p.hostname, p.path, dict(parse_qsl(p.query))
    if "gh_jid" in q:
        return "greenhouse", f"greenhouse:{q['gh_jid']}"
    if host.endswith("greenhouse.io"):
        match = re.search(r"/jobs/(\d+)", path)
        if match:
            return "greenhouse", f"greenhouse:{match[1]}"
    for suffix, name in [("lever.co", "lever"), ("ashbyhq.com", "ashby"),
                         ("smartrecruiters.com", "smartrecruiters"), ("myworkdayjobs.com", "workday"),
                         ("linkedin.com", "linkedin"), ("joinhandshake.com", "handshake")]:
        if host == suffix or host.endswith("." + suffix):
            pieces = path.strip("/").split("/")
            match = re.search(r"(?:view|jobs)/(\d+)", path) if name in {"linkedin", "handshake"} else None
            identifier = match[1] if match else pieces[-1]
            # Ashby's /application is a tab on the requisition, not its ID.
            # Using the last component both duplicates overview URLs and
            # collapses different requisitions into one tenant:application key.
            if name == "ashby" and len(pieces) == 3 and pieces[-1] == "application":
                identifier = pieces[-2]
            if name == "workday":
                identifier = re.split(r"_", identifier)[-1]
            if identifier:
                tenant = pieces[0] if name in {"lever", "ashby", "smartrecruiters"} else host
                return name, f"{name}:{tenant}:{identifier}"
    return "generic", ""


def job_identity(url):
    _, key = ats_identity(url)
    return key or "url:" + hashlib.sha256(canonical_url(url).encode()).hexdigest()


def parse_date(text, reference=None):
    from .freshness import parse_posted
    value = parse_posted(text, reference)
    return value.isoformat() if value else None


def freshness(posted, max_days=30, today=None):
    from .freshness import freshness_state
    state = freshness_state(posted, max_days, today)
    return True if state == 'FRESH' else False if state == 'STALE' else None


US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
US_NAMES = "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming".split()
US_CITIES = {"sf", "nyc", "new york", "san francisco", "seattle", "boston", "austin", "chicago", "atlanta", "denver", "college park", "pittsburgh", "san jose", "san diego", "los angeles", "sunnyvale", "mountain view"}
_US_NAME = re.compile(r'\b(?:' + '|'.join(re.escape(s) for s in US_NAMES) + r')\b')


@lru_cache(maxsize=1024)
def us_location(location):
    text = normalize(location)
    foreign = re.search(r"\b(canada|toronto|vancouver|montreal|ontario|london|united kingdom|uk|india|bangalore|bengaluru|singapore|germany|berlin|france|paris|australia|sydney|china|beijing|japan|tokyo|ireland|dublin|mexico|switzerland|poland|netherlands|israel|brazil)\b", text)
    explicit = bool(re.search(r"\b(united states|usa|u s|us)\b", text))
    state = bool(set(re.findall(r"\b[A-Z]{2}\b", location)) & US_STATES)
    domestic = explicit or state or bool(_US_NAME.search(text)) or text in US_CITIES
    if foreign:
        return None if domestic else False
    return True if domestic else None


def location_rank(location, groups):
    if us_location(location) is not True:
        return 0
    if "remote" in normalize(location):
        return 10
    text = normalize(location)
    for index, group in enumerate(groups):
        if any(re.search(r"\b" + re.escape(normalize(place)) + r"\b", text) for place in group):
            return 60 - index * 10
    return 5


def priority(listing, config):
    text = normalize(listing.title + " " + listing.source)
    text = re.sub(r"summer(?=\d{4})", "summer ", text)
    summer = "summer" in text
    target = normalize(config["jobs"]["target_season"])
    score = 500 if target in text else 300 if summer else 200 if "intern" in text else 100
    if "co op" in text or "coop" in text:
        score -= 25
    score += location_rank(listing.location, config["locations"]["preferred_groups"])
    if listing.posted_at:
        score += max(0, 14 - (datetime.now(timezone.utc).date() - datetime.fromisoformat(listing.posted_at).date()).days)
    return score


def phd_rejection(title, description=''):
    """PhD titles are always out; descriptions must also mention bachelor."""
    def mentions(text):
        return re.search(r'\b(?:phd|ph\s+d)\b', (text or '').lower().replace('.', ''))
    if mentions(title):
        return 'phd_title'
    if mentions(description) and 'bachelor' not in description.lower():
        return 'phd_without_bachelor'
    return ''


def eligibility(job, profile):
    from .standing import resolve_standing
    rejected = phd_rejection(job.get('title', ''), job.get('description') or '')
    if rejected:
        return Eligibility(False, [rejected], [])
    title = normalize(job["title"])
    description = job.get("description", "")
    reasons, uncertain, failures = [], [], []
    location = us_location(job["location"])
    if location is False:
        failures.append("Position is outside the United States")
    elif location is None:
        uncertain.append("U.S. work location is not established")
    else:
        reasons.append("U.S. location")
    student = bool(re.search(r"\b(intern\w*|co op|coop)\b", title)) or bool(re.search(r"\b(?:this |the )?(?:internship|co-op) (?:position|role|program|opportunity)\b", description, re.I))
    if not student:
        if re.search(r"\b(current students|currently enrolled|student program)\b", description, re.I):
            reasons.append("Explicitly accepts current students")
        else:
            failures.append("Not an internship/co-op or explicit current-student opportunity")
    else:
        reasons.append("Student opportunity")
    if re.search(r"\b(senior|staff|principal|lead)\b", title) and not student:
        failures.append("Experienced role")
    if re.search(r"\b(phd|ph d|doctoral|masters|master s|mba)\b", title) and not re.search(r"\b(bachelor|undergrad|bs|b s)\b", title):
        degree = normalize(fact(profile, "education.degree") or "")
        if not degree:
            uncertain.append("Role title specifies an advanced degree; degree is not verified")
        elif degree not in title:
            failures.append("Role title specifies an advanced degree")
    from .requirements import extract_requirements, duration_requirement
    standing_matches = []
    for item in extract_requirements(description):
        if item.preferred:
            continue
        line = item.text
        lower = normalize(line)
        match = resolve_standing(line, profile)
        if match:
            standing_matches.append(match)
            reasons.append("USER_PROVIDED standing assertion: " + ", ".join(match["assertion_ids"]))
            continue
        if duration_requirement(line):
            # A start date, or a guessed day within a month range, cannot prove
            # attendance throughout a program. Exact confirmations are scoped
            # to this complete requirement, never a global availability boolean.
            uncertain.append("Confirm full-program availability: " + line)
            continue
        if api_integration_evidence(line, profile):
            reasons.append("API/scripting/system integration supported by verified technical evidence")
            continue
        if student_requirement(line, profile):
            reasons.append("Student degree/standing verified")
            continue
        basic_programming = re.fullmatch(
            r"\s*(?:previous|prior) programming experience is (?:a must|required)\."
            r"(?:\s+while we use [^.]+, we care more about engineering skills than knowledge of specific languages or frameworks\.?)?\s*",
            line, re.I)
        if basic_programming and fact(profile, "qualifications.programming_experience") is True:
            reasons.append("Previous programming experience verified")
            continue
        # Recognize only complete, simple factual clauses. Unsupported compound
        # qualifications stay unknown rather than being satisfied by one fact.
        simple = lower.rstrip()
        checks = [
            (r"(?:(?:must be (?:a )?|required to be (?:a )?)(?:u s|us|united states) citizen|(?:u s|us|united states) citizenship (?:is )?required)", "citizenship.us_citizen", "U.S. citizenship"),
            (r"(?:must be |required to be )?(?:legally )?authorized to work in (?:the )?(?:u s|us|united states)(?: (?:is )?required)?", "work_authorization.us_authorized", "U.S. work authorization"),
        ]
        handled = False
        for pattern, path, label in checks:
            if re.fullmatch(pattern, simple):
                value = fact(profile, path)
                if value is True:
                    reasons.append(label + " verified")
                elif value is False:
                    failures.append(label + " required")
                else:
                    uncertain.append(label + " not verified")
                handled = True
                break
        if handled:
            continue
        if re.fullmatch(r"(?:must be (?:a )?(?:resident of|residing in)|(?:your )?(?:primary )?residence must be (?:located )?in) (?:the )?(?:u s|united states)", simple) or re.fullmatch(
            r"to be eligible for this position your primary residence must be located in the united states please indicate the state in which you currently reside", simple):
            country = normalize(fact(profile, "contact.country") or "")
            if country in {"us", "usa", "u s", "united states", "united states of america"}:
                reasons.append("U.S. residence verified")
            elif country:
                failures.append("U.S. residence required")
            else:
                uncertain.append("U.S. residence not verified")
            continue
        if re.search(r"(?:no |not.{0,20})(?:visa )?sponsorship|unable to sponsor|cannot sponsor", line, re.I):
            values = [fact(profile, "work_authorization." + key) for key in ["sponsorship_now", "sponsorship_future"]]
            if True in values:
                failures.append("Employer does not provide required sponsorship")
            elif None in values:
                uncertain.append("Sponsorship requirements not verified")
            # Do not discard any extra requirement attached to sponsorship.
            if re.fullmatch(r"(?:no (?:visa )?sponsorship(?: (?:is )?(?:available|provided))?|(?:we are )?unable to sponsor|(?:we )?cannot sponsor)", simple):
                continue
        if re.fullmatch(r"(?:minimum |a minimum )?gpa(?: of| at least)? \d \d+(?: required| or (?:above|higher))?", simple):
            threshold = re.search(r"\d\.\d+", line)
            if not threshold:
                uncertain.append("GPA requirement needs verification: " + line)
                continue
            minimum = float(threshold[0])
            actual = fact(profile, "education.gpa")
            standing_gpa = resolve_standing("Minimum GPA of 3.5", profile) if minimum <= 3.5 else None
            if actual is not None:
                if float(actual) < minimum:
                    failures.append(f"GPA below required {minimum}")
            elif standing_gpa:
                standing_matches.append({**standing_gpa, "requirement": line})
                reasons.append("Verified minimum GPA 3.5 satisfies stated lower threshold")
            else:
                uncertain.append(f"Minimum GPA {minimum} needs verification")
            continue
        if re.search(r'\b(?:pursuing|enrolled in|must have|requires?) (?:a |an )?graduate degree\b', line, re.I):
            if re.search(r'bachelor|undergrad', normalize(fact(profile, 'education.degree') or '')):
                failures.append('Role requires a graduate degree; saved education is bachelor level')
        if re.search(r"\b(?:graduation|graduating|graduate(?:d|s)?\s+(?:in|by|between|before|after))\b", line, re.I):
            years = [int(x) for x in re.findall(r"\b20\d{2}\b", line)]
            graduation = fact(profile, "education.graduation_date")
            if years:
                if not graduation:
                    uncertain.append("Graduation date missing")
                elif int(str(graduation)[:4]) < min(years) or int(str(graduation)[:4]) > max(years):
                    failures.append("Graduation year outside stated window")
                elif re.fullmatch(r'(?:must )?(?:graduate|graduating|graduation) (?:in 20\d{2}|between 20\d{2} and 20\d{2})', lower):
                    reasons.append("Graduation year within stated window")
                else:
                    # Year membership does not verify a compound clause or an
                    # exact month/day boundary.
                    uncertain.append("Exact graduation date window requires review: " + line)
                continue
        # A proven independent-work assertion does not establish limited
        # supervision, pace, energy or motivation. Ask only about the remainder.
        independent = re.match(r"ability to work independently (with .+)", line, re.I)
        if independent and resolve_standing("Ability to work independently", profile):
            standing_matches.append(resolve_standing("Ability to work independently", profile))
            line = "Ability to work " + independent[1]
        uncertain.append("Verify qualification: " + line)
    if not description.strip():
        uncertain.append("Job description was not captured")
    return Eligibility(False if failures else None if uncertain else True, failures or reasons, list(dict.fromkeys(uncertain)), standing_matches)


def student_requirement(line, profile):
    """Closed grammars: enrollment cannot satisfy extra experience/skill clauses."""
    text = normalize(line)
    degree = normalize(fact(profile, 'education.degree') or '')
    enrolled = fact(profile, 'education.currently_enrolled') is True
    bachelor = bool(re.search(r"bachelor|undergrad|\bbs\b|\bba\b", degree))
    degree_clause = re.fullmatch(
        r"(?:(?:must be )?(?:currently )?(?:enrolled in|pursuing)) (?:a )?bachelor(?: s)? degree(?: program)?(?: in (.+))?", text)
    if enrolled and bachelor and degree_clause:
        field = degree_clause[1]
        return field is None or field == normalize(fact(profile, 'education.major') or '')
    if enrolled and bachelor and re.fullmatch(
        r"rising junior or senior pursuing a bachelor s degree or a graduate student in an accredited college or university", text):
        return normalize(fact(profile, 'education.school_year') or '') in {'junior', 'senior'}
    return False


def api_integration_evidence(line, profile):
    """Basic comfort only; no language, years, proficiency or AI extrapolation."""
    if not re.fullmatch(r'comfort working with apis scripting and connecting systems together', normalize(line)):
        return False
    skills = {normalize(s) for s in profile.get('skills', []) if isinstance(s, str)}
    if not skills.intersection({'apis', 'restful apis', 'rest apis'}):
        return False
    if {'scripting', 'system integration'} <= skills:
        return True
    # Existing verified source excerpts can establish these two basic activities.
    # Require positive first-person implementation wording, not technology words
    # appearing in arbitrary prose, plans, job descriptions or unverified projects.
    verified = profile.get('verified_facts', {})
    sources = profile.get('fact_sources', {})
    excerpts = [normalize(value) for key, value in verified.items()
                if isinstance(value, str) and isinstance(sources.get(key), dict)
                and sources[key].get('file') and sources[key].get('sha256')
                and sources[key].get('page') and 'manually checked' in sources[key].get('basis', '').lower()]
    scripting = 'python' in skills and any(re.fullmatch(
        r'i built a .{0,80}application that (?:polls .{1,80} in real time|periodically polls .{1,80}) to .{1,80} using python beautiful soup requests and pyinstaller', v)
        for v in excerpts)
    integration = any(re.fullmatch(
        r'(?:i architected server client communication and replication systems for real time multiplayer interaction|i implemented client server communication and state replication to support real time multiplayer gameplay)', v)
        for v in excerpts)
    return scripting and integration
