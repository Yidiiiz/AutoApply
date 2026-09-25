"""Conservative identity, freshness, location and eligibility rules."""
import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import fact
from .models import Eligibility


def normalize(text):
    return re.sub(r"[^\w]+", " ", str(text).casefold()).strip()


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


def us_location(location):
    text = normalize(location)
    foreign = re.search(r"\b(canada|toronto|vancouver|montreal|ontario|london|united kingdom|uk|india|bangalore|bengaluru|singapore|germany|berlin|france|paris|australia|sydney|china|beijing|japan|tokyo|ireland|dublin|mexico|switzerland|poland|netherlands|israel|brazil)\b", text)
    explicit = bool(re.search(r"\b(united states|usa|u s|us)\b", text))
    state = bool(set(re.findall(r"\b[A-Z]{2}\b", location)) & US_STATES)
    domestic = explicit or state or any(re.search(r"\b" + re.escape(s) + r"\b", text) for s in US_NAMES) or text in US_CITIES
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


def eligibility(job, profile):
    from .standing import resolve_standing
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
        if normalize(fact(profile, "education.degree") or "") not in title:
            failures.append("Role title specifies an advanced degree")
    # Preferred qualifications are not hard requirements. Keep section context.
    required_lines, preferred, in_requirements = [], False, False
    hard_lines = []
    for line in re.split(r"[\n;]+", description):
        lower = line.lower().strip()
        if re.fullmatch(r"(?:preferred (?:qualifications|skills(?:\s*(?:&|and)\s*experience)?|experience)|nice to have|bonus qualifications)\s*:?", lower):
            preferred = True
            in_requirements = False
            continue
        elif re.fullmatch(r"(?:(?:minimum|required|basic|additional|technical|itar|export control) (?:qualifications|requirements)|requirements|qualifications|responsibilities|roles and responsibilities)\s*:?", lower):
            preferred = False
            in_requirements = "responsibilities" not in lower
            continue
        elif re.fullmatch(r"(?:pay range|additional information|company description|job description)\s*:?", lower):
            preferred = False
            in_requirements = False
            continue
        if not preferred and not re.search(r"\b(preferred|a plus|nice to have)\b", lower):
            required_lines.append(line)
            if in_requirements or re.search(r"\b(must|required|minimum|at least|only)\b", lower):
                hard_lines.append(line)
    standing_matches = []
    unmatched_lines = []
    for line in required_lines:
        match = resolve_standing(line, profile)
        if match:
            standing_matches.append(match)
            reasons.append("USER_PROVIDED standing assertion: " + ", ".join(match["assertion_ids"]))
        else:
            unmatched_lines.append(line)
    required_lines = unmatched_lines
    hard_lines = [line for line in hard_lines if line in required_lines]
    required = "\n".join(required_lines)
    if re.search(r'\b(?:pursuing|enrolled in|must have|requires?) (?:a |an )?graduate degree\b', required, re.I):
        degree = normalize(fact(profile, 'education.degree') or '')
        if re.search(r'bachelor|undergrad', degree):
            failures.append('Role requires a graduate degree; saved education is bachelor level')
    for line in hard_lines:
        lower = line.lower()
        if re.search(r"bachelor|undergraduate|currently enrolled|pursuing", lower):
            degree = normalize(fact(profile, "education.degree") or "")
            if re.search(r"bachelor|undergraduate", lower) and not re.search(r"bachelor|undergrad|\bbs\b|\bba\b", degree):
                uncertain.append("Required degree level is not verified: " + line[:250])
            if re.search(r"enrolled|pursuing", lower) and fact(profile, "education.currently_enrolled") is not True:
                uncertain.append("Current enrollment must be verified")
        if re.search(r"major|degree in|pursuing.{0,20}in ", lower):
            major = normalize(fact(profile, "education.major") or "")
            if not major or major not in normalize(line):
                uncertain.append("Required field of study needs verification: " + line[:250])
        if re.search(r"master|ph\.?d|doctoral|mba", lower) and not re.search(r"bachelor|undergraduate", lower):
            uncertain.append("Advanced degree requirement: " + line[:250])
        if re.search(r"\b(experience|proficien\w*|knowledge|familiar\w*|skills?|ability|able to|available|availability|certif\w*|years? old|security clearance|clearance)\b", lower):
            # Only this basic requirement can be satisfied by a verified boolean.
            # Specific languages, years, and additional qualifications still need review.
            basic_programming = re.fullmatch(
                r"\s*(?:previous|prior) programming experience is (?:a must|required)\."
                r"(?:\s+while we use [^.]+, we care more about engineering skills than knowledge of specific languages or frameworks\.?)?\s*",
                lower,
            )
            if basic_programming and fact(profile, "qualifications.programming_experience") is True:
                reasons.append("Previous programming experience verified")
                continue
            # Natural-language skill/availability requirements are not assumed from enrollment.
            uncertain.append("Verify qualification against your experience: " + line[:250])
    checks = [(r"(?:must|require\w*).{0,50}(?:u\.?s\.? citizen|united states citizen)", "citizenship.us_citizen", "U.S. citizenship"),
              (r"(?:must|require\w*).{0,50}(?:authorized|authorization).{0,30}(?:united states|u\.?s\.?)", "work_authorization.us_authorized", "U.S. work authorization")]
    for pattern, path, label in checks:
        if re.search(pattern, required, re.I):
            answer = fact(profile, path)
            if answer is True:
                reasons.append(label + " verified")
            elif answer is False:
                failures.append(label + " required")
            else:
                uncertain.append(label + " not verified")
    if re.search(r"(?:no|not.{0,20})(?:visa )?sponsorship|unable to sponsor|cannot sponsor", required, re.I):
        values = [fact(profile, "work_authorization." + key) for key in ["sponsorship_now", "sponsorship_future"]]
        if True in values:
            failures.append("Employer does not provide required sponsorship")
        elif None in values:
            uncertain.append("Sponsorship requirements not verified")
    # Evaluate every unmatched GPA line, including 'Minimum GPA 3.7'. A prior
    # matched 3.5 assertion must never hide an additional higher threshold.
    for line in required_lines:
        if not re.search(r"\bgpa\b", line, re.I):
            continue
        thresholds = re.findall(r"\b\d\.\d+\b", line)
        if len(thresholds) != 1:
            uncertain.append("GPA requirement needs verification: " + line[:250])
            continue
        minimum = float(thresholds[0])
        actual = fact(profile, "education.gpa")
        if actual is None:
            standing_gpa = resolve_standing("Minimum GPA of 3.5", profile) if minimum <= 3.5 else None
            if standing_gpa:
                standing_matches.append({**standing_gpa, "requirement": line})
                reasons.append("Verified minimum GPA 3.5 satisfies stated lower threshold")
            else:
                uncertain.append(f"Minimum GPA {minimum} needs verification")
        elif float(actual) < minimum:
            failures.append(f"GPA below required {minimum}")
    if re.search(r"(?:active|current|existing).{0,35}(?:clearance|top secret)|(?:clearance|top secret).{0,25}(?:required|must)", required, re.I):
        uncertain.append("Existing clearance requirement must be verified")
    for line in required_lines:
        # Degree level and graduate-program start dates are not graduation dates.
        if re.search(r"\b(?:graduation|graduating|graduate(?:d|s)?\s+(?:in|by|between|before|after))\b", line, re.I):
            years = [int(x) for x in re.findall(r"\b20\d{2}\b", line)]
            graduation = fact(profile, "education.graduation_date")
            if years:
                if not graduation:
                    uncertain.append("Graduation date missing")
                elif int(str(graduation)[:4]) < min(years) or int(str(graduation)[:4]) > max(years):
                    failures.append("Graduation year outside stated window")
                elif re.search(r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|before|after", line, re.I):
                    uncertain.append("Exact graduation date window requires review: " + line[:300])
                else:
                    reasons.append("Graduation year within stated window")
        if re.search(r"\b(must|required|minimum|at least|only)\b", line, re.I):
            if re.search(r"\b(age|years? of experience|certification|available|availability|security|clearance|major|degree|enrolled|pursuing|citizen|authorized|sponsor|gpa|graduat)\b", line, re.I):
                if re.search(r"bachelor|undergraduate|computer science|currently enrolled", line, re.I):
                    # Complex conjunctions still need review below.
                    if not re.search(r"master|ph\.?d|years? of experience|certif|clearance|\bage\b|available", line, re.I):
                        continue
                if not any(x in line.lower() for x in ["gpa", "graduat", "citizen", "sponsor", "authoriz"]):
                    uncertain.append("Verify requirement: " + line[:300])
    if not description.strip():
        uncertain.append("Job description was not captured")
    return Eligibility(False if failures else None if uncertain else True, failures or reasons, list(dict.fromkeys(uncertain)), standing_matches)
