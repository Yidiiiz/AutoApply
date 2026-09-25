"""ATS recognition is independent of form support and stable job identities."""
from dataclasses import dataclass
from urllib.parse import urlsplit
import re


@dataclass
class ATSResult:
    provider: str
    confidence: float
    evidence: str


DOMAINS = {
    "ashby": ("ashbyhq.com",), "greenhouse": ("greenhouse.io", "greenhouse.com"),
    "lever": ("lever.co",), "workday": ("myworkdayjobs.com", "myworkdaysite.com"),
    "workable": ("workable.com",), "indeed": ("indeed.com",),
    "linkedin": ("linkedin.com",), "smartrecruiters": ("smartrecruiters.com",),
    "icims": ("icims.com",), "taleo": ("taleo.net",), "jobvite": ("jobvite.com",),
    "bamboohr": ("bamboohr.com",), "rippling": ("rippling.com",),
    "sap_successfactors": ("successfactors.com", "successfactors.eu", "successfactors.asia", "successfactors.cn"),
    "adp": ("adp.com",),
}


def detect_ats(url, markers=(), has_form=False):
    host = (urlsplit(url).hostname or "").lower()
    for provider, domains in DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return ATSResult(provider, 1.0, "hostname")
    if re.search(r"[?&]gh_jid=\d+", url):
        return ATSResult("greenhouse", .95, "job URL parameter")
    for marker in markers:
        value = marker.lower()
        for provider, domains in DOMAINS.items():
            if any(d in value for d in domains) or f"{provider}-application" in value:
                return ATSResult(provider, .85, "embedded provider marker")
    return ATSResult("custom" if has_form else "UNKNOWN", .6 if has_form else 0, "form" if has_form else "no known marker")
