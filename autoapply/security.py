"""Passive security inspection only; never read challenge responses or solve challenges."""
import re
import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from .models import SecurityState as S
from .providers import detect_ats
from .retry import ErrorCategory as E


def safe_url(value):
    """Diagnostic URLs exclude credentials, queries, fragments and opaque verification paths."""
    p = urlsplit(value)
    host = p.hostname or ""
    path = p.path
    if re.search(r"verify|verification|oauth|callback|token|session|withpersona|withclear", value, re.I):
        path = "/[redacted-verification-path]"
    return urlunsplit((p.scheme, host + (f":{p.port}" if p.port else ""), path, "", ""))


def safe_text(value):
    text = re.sub(r"https?://\S+", lambda m: safe_url(m[0]), str(value))
    text = re.sub(r"\b(?:bearer\s+\S+|[\w.-]*(?:token|password|secret|cookie|session)[\w.-]*\s*[:=]\s*\S+)", "[redacted]", text, flags=re.I)
    text = re.sub(r"\b[A-Za-z0-9_-]{32,}\b|\b\d{4,}\b", "[redacted]", text)
    return text[:1000]


@dataclass
class SecurityResult:
    provider: str = "UNKNOWN"
    type: str = "none"
    interactive: bool = False
    confidence: float = 0
    state: S = S.NONE
    message: str = ""
    category: str = ""
    http_status: int | None = None

    @property
    def blocking(self):
        return self.state not in {S.NONE, S.PASSIVE_PROTECTION_DETECTED}


RULES = [
    (S.SPAM_REJECTED, E.SPAM_REJECTION, r"(?:flagged as (?:possible )?spam|possible spam|spam detected)"),
    (S.AUTOMATION_REJECTED, E.AUTOMATION_REJECTION, r"automated activity|automation detected|bot detected|bot activity"),
    (S.RATE_LIMITED, E.RATE_LIMIT, r"too many requests|rate[ -]?limit(?:ed|ing)?|temporar(?:y|ily) throttl\w*|try again later"),
    (S.IDENTITY_VERIFICATION, E.IDENTITY_VERIFICATION, r"verify (?:your )?identity|identity verification|government id|selfie verification|liveness(?: check| verification)?"),
    (S.PHONE_VERIFICATION, E.PHONE_VERIFICATION, r"verify (?:your )?phone|phone verification|sms verification"),
    (S.EMAIL_VERIFICATION, E.EMAIL_VERIFICATION, r"verify (?:your )?email|email verification|verification code|one[ -]time (?:code|password)"),
    (S.FRAUD_REVIEW, E.FRAUD_REVIEW, r"fraud review|fraud detected|fraudulent activity"),
    (S.INTERACTIVE_CHALLENGE, E.CAPTCHA, r"verify (?:that )?you are human|are you (?:a )?(?:human|robot)|(?:complete|solve) (?:the |this )?(?:captcha|recaptcha)|security (?:check|verification)|verification required|(?:managed|security|browser) challenge|browser verification|checking your browser"),
    (S.UNKNOWN_SECURITY_FAILURE, E.UNKNOWN_SECURITY_FAILURE, r"suspicious activity|unusual (?:activity|traffic)|access denied|forbidden|application rejected|submission blocked|application limit reached"),
]


def classify_message(text, status=None, *, prominent=False):
    for state, category, pattern in RULES:
        match = re.search(pattern, text, re.I)
        if match:
            # Store the containing line, bounded/redacted; never an entire form or response.
            line = text[text.rfind('\n', 0, match.start()) + 1:text.find('\n', match.end()) if '\n' in text[match.end():] else len(text)]
            return SecurityResult(type=state.lower(), interactive=state != S.RATE_LIMITED,
                                  confidence=.95, state=state, message=safe_text(line), category=category, http_status=status)
    if prominent and not re.search(r"protected by|privacy policy|terms of service", text, re.I) and re.search(r"\b(?:captcha|recaptcha|fraud)\b|^(?:complete |solve )?(?:the )?challenge[.!?\s]*$", text, re.I):
        return SecurityResult(type="challenge", interactive=True, confidence=.85, state=S.INTERACTIVE_CHALLENGE,
                              message=safe_text(text), category=E.BOT_CHALLENGE, http_status=status)
    if status in {401, 403, 429}:
        rate = status == 429
        return SecurityResult(type="http", interactive=not rate, confidence=.9,
                              state=S.RATE_LIMITED if rate else S.UNKNOWN_SECURITY_FAILURE,
                              message=f"HTTP {status}", category=E.RATE_LIMIT if rate else E.UNKNOWN_SECURITY_FAILURE,
                              http_status=status)
    return SecurityResult(http_status=status)


# Inspect metadata and rendered text, excluding editable/hidden values. No tokens, cookies,
# storage, request bodies, response bodies, identity documents, or iframe contents are read.
from .form_validation import VALIDATION_JS

SNAPSHOT = r"""options => {
 const inspectValidation = options?.validation !== false;
 const visible = e => {
   if (!e.getClientRects().length || ['hidden','collapse'].includes(getComputedStyle(e).visibility)) return false;
   for (let n=e; n; n=n.parentElement) {
     const s=getComputedStyle(n);
     if (n.hidden || s.display==='none' || Number(s.opacity)===0) return false;
   }
   return true;
 };
 const text = e => {
   const walker = document.createTreeWalker(e,NodeFilter.SHOW_TEXT);
   let n, parts=[];
   while(n=walker.nextNode()) {
     const parent=n.parentElement;
     if(parent && visible(parent) && !parent.closest('input,textarea,select,script,style,[hidden],[contenteditable],iframe')) parts.push(n.textContent);
   }
   return parts.join('\n');
 };
 const nodes = [...document.querySelectorAll('h1,h2,h3,[role="alert"],[role="dialog"],[aria-live],.error,.alert')].filter(visible);
 const markers = [...document.querySelectorAll('script[src],iframe,[class],[id],textarea[name]')].map(e => ({
   tag:e.tagName, marker:[e.getAttribute('src'),e.id,e.className,e.getAttribute('name'),e.title].join(' '),
   visible:visible(e), width:e.getBoundingClientRect().width, height:e.getBoundingClientRect().height,
   recaptcha_badge:!!e.closest('.grecaptcha-badge'),
   recaptcha_invisible:e.getAttribute('data-size')==='invisible' ||
     (e.tagName==='IFRAME' && /\/recaptcha\/(?:api2|enterprise)\/anchor(?:\?|$)/i.test(e.getAttribute('src')||'') &&
      new URL(e.getAttribute('src'),document.baseURI).searchParams.get('size')==='invisible')
 }));
 const validation=inspectValidation ? __NORMALIZED_VALIDATION__ : [];
 return {text:text(document.body).slice(0,150000),
   condition_text:(document.body?.innerText||'').slice(0,150000),
   password:[...document.querySelectorAll('input[type=password]')].some(visible),
   validation, alerts:[...document.querySelectorAll('[role=alert]')].filter(visible).map(e=>e.textContent.trim()).filter(Boolean),
   messages:nodes.map(text).filter(Boolean), markers,
   has_form:!!document.querySelector('form,input[type="email"],input[type="file"]'),
   invalid:validation.length,
   disabled:[...document.querySelectorAll('button,input[type="submit"]')].some(e => visible(e) && (e.disabled || e.getAttribute('aria-disabled')==='true') && /submit|send application/i.test(e.textContent || e.value))};
}""".replace('__NORMALIZED_VALIDATION__', '(' + VALIDATION_JS + ')()')


PROVIDERS = [
    ("Google reCAPTCHA", r"g-recaptcha|google\.com/recaptcha|recaptcha\.net|recaptcha", "captcha"),
    ("hCaptcha", r"h-captcha|hcaptcha\.com|hcaptcha", "captcha"),
    ("Cloudflare Turnstile", r"cf-turnstile|challenges\.cloudflare\.com", "captcha"),
    ("Cloudflare", r"cf-chl-|cdn-cgi/challenge|managed.challenge", "challenge"),
    ("Arkose Labs", r"arkoselabs|funcaptcha", "captcha"),
    ("DataDome", r"datadome|captcha-delivery\.com", "challenge"),
    ("Persona", r"(?:^|[/.])withpersona\.com|inquiry\.withpersona", "identity"),
    ("CLEAR", r"(?:^|[/.])(?:withclear\.com|clearme\.com)", "identity"),
]


class SecurityDetector:
    async def snapshot(self, page, *, include_validation=True):
        data = await page.evaluate(SNAPSHOT, {'validation':include_validation})
        data["url"] = page.url
        return data

    def inspect(self, snapshot, status=None, dialogs=()):
        text = snapshot["text"]
        # Error/verification UI has priority over ordinary job-description prose.
        result = SecurityResult(http_status=status)
        for message in [*dialogs, *snapshot["messages"]]:
            candidate = classify_message(message, prominent=True)
            if candidate.blocking:
                result = candidate
                break
        if not result.blocking:
            # Strong rejection phrases also appear in unstructured banners.
            strong = re.search(r"[^\n]*(?:possible spam|flagged as spam|spam detected|automated activity|automation detected|bot detected|too many requests|verify (?:that )?you are human|submission blocked|access denied|verify (?:your )?(?:email|phone|identity)|verification code|one[ -]time (?:code|password)|checking your browser)[^\n]*", text, re.I)
            result = classify_message(strong[0] if strong else "", status)
        found = []
        for provider, pattern, kind in PROVIDERS:
            markers = [m for m in snapshot["markers"] if re.search(pattern, m["marker"], re.I)]
            on_provider = re.search(pattern, snapshot["url"], re.I) is not None
            if not markers and not on_provider:
                continue
            # Invisible badges and script presence are passive. A displayed challenge
            # iframe/widget (not a small reCAPTCHA badge) needs human attention.
            interactive = on_provider or any(self.interactive_marker(m, provider) for m in markers)
            found.append((interactive, provider, kind))
        if re.search(r"\bCLEAR\b.{0,60}identity verification", text):
            found.append((True, "CLEAR", "identity"))
        if not found:
            for provider, pattern, kind in PROVIDERS[:2]:
                if re.search(r"protected by\s+" + ("recaptcha" if provider == "Google reCAPTCHA" else "hcaptcha"), text, re.I):
                    found.append((False, provider, kind))
        if not found and any(m["tag"] == "IFRAME" and m["visible"] and m["width"] >= 100 and m["height"] >= 50
                             and re.search(r"captcha|challenge|human.verification", m["marker"], re.I) for m in snapshot["markers"]):
            found.append((True, "UNKNOWN", "challenge"))
        if found:
            interactive, provider, kind = sorted(found, key=lambda x: x[0], reverse=True)[0]
            result.provider, result.confidence = provider, max(result.confidence, .9)
            if not result.blocking:
                result.type, result.interactive = kind, interactive
                result.state = S.IDENTITY_VERIFICATION if interactive and kind == "identity" else S.INTERACTIVE_CHALLENGE if interactive else S.PASSIVE_PROTECTION_DETECTED
                result.category = E.IDENTITY_VERIFICATION if kind == "identity" and interactive else E.CAPTCHA if interactive else ""
                result.message = f"{'Interactive' if interactive else 'Passive'} {provider} detected"
        result.http_status = status
        return result

    @staticmethod
    def interactive_marker(marker, provider):
        if not marker['visible'] or marker['tag'] == 'SCRIPT' or marker['width'] < 100 or marker['height'] < 50:
            return False
        # An actual visible challenge frame always wins over passive badge metadata.
        if provider == 'Google reCAPTCHA':
            if marker['tag'] == 'IFRAME' and re.search(r'/recaptcha/(?:api2|enterprise)/bframe(?:[?\s]|$)', marker['marker'], re.I):
                return True
            if marker.get('recaptcha_badge') or marker.get('recaptcha_invisible'):
                return False
            # A provider-named container alone is integration, not rendered UI.
            # Normal anchor frames render the checkbox; challenge frames/dialog
            # text remain independently blocking. No provider frame is entered.
            return marker['tag'] == 'IFRAME' and bool(re.search(
                r'/recaptcha/(?:api2|enterprise)/anchor(?:[?\s]|$)|challenge', marker['marker'], re.I))
        return not re.search(r'badge|response', marker['marker'], re.I)

    async def detect(self, page, observation=None, *, include_validation=True):
        observation = observation or {}
        if observation.get("dialog_open"):
            snapshot = {"text": "", "messages": observation["dialogs"], "markers": [], "url": page.url,
                        "invalid": 0, "disabled": True, "has_form": False}
            result = self.inspect(snapshot, observation.get("status"), observation["dialogs"])
            if not result.blocking:
                result = SecurityResult(type="dialog", interactive=True, confidence=1,
                    state=S.UNKNOWN_SECURITY_FAILURE, category=E.UNKNOWN_SECURITY_FAILURE, message="Unresolved browser dialog requires manual review")
            return result, snapshot
        snapshot = await self.snapshot(page, include_validation=include_validation)
        # Embedded application forms get the same read-only inspection. Protected-provider
        # frames are recognized by metadata above; their contents are never inspected.
        for frame in page.frames:
            if frame == page.main_frame or any(re.search(pattern, frame.url, re.I) for _, pattern, _ in PROVIDERS):
                continue
            same_origin = urlsplit(frame.url).netloc == urlsplit(page.url).netloc and bool(urlsplit(frame.url).netloc)
            embedded_ats = detect_ats(frame.url).provider not in {"UNKNOWN", "custom"}
            if not same_origin and not embedded_ats:
                continue
            try:
                element = await frame.frame_element()
                if not await element.is_visible():
                    continue
                # Adapter frame policies differ. Only the main-document scan
                # can be delegated universally; retain embedded validation here.
                embedded = await asyncio.wait_for(frame.evaluate(SNAPSHOT), timeout=2)
                snapshot["text"] += "\n" + embedded["text"]
                snapshot["messages"].extend(embedded["messages"])
                snapshot["markers"].extend(embedded["markers"])
                snapshot["invalid"] += embedded["invalid"]
                snapshot["disabled"] = snapshot["disabled"] or embedded["disabled"]
                snapshot["has_form"] = snapshot["has_form"] or embedded["has_form"]
            except Exception:
                # Inaccessible application frames cannot justify a success claim.
                snapshot["messages"].append("Embedded application security check unavailable")
        return self.inspect(snapshot, observation.get("status"), [*observation.get("dialogs", ()), *observation.get("messages", ())]), snapshot


CONFIRMATION = re.compile(r"\byour application (?:has been|was) (?:successfully )?(?:submitted|received)\b|\bthank you for applying\b|\bapplication submitted(?: successfully)?\b|\bwe (?:have|'ve) received your application\b", re.I)


def confirmation_evidence(snapshot):
    for line in re.split(r"[\r\n]+|(?<=[.!?])\s+", snapshot["text"]):
        # Evaluate sentences separately: a following conditional about next steps
        # does not negate an affirmative submission sentence. Ignore hypothetical
        # or negative language within the evidence sentence itself.
        if re.search(r"\b(?:not|never|no|if|when|once|until|will|example)\b|couldn.t|wasn.t", line, re.I):
            continue
        match = CONFIRMATION.search(line)
        if match:
            return "Visible text: " + match[0]
        match = re.fullmatch(r"\s*(?:Application )?confirmation (?:number|ID)\s*[:#]\s*([A-Z0-9-]{4,40})\s*", line, re.I)
        if match:
            return "Visible application confirmation number"  # Do not store an opaque identifier.
    return None


@dataclass(frozen=True)
class PageInspection:
    """One fresh boundary observation. Pure readers receive independent values."""
    security_json: str
    snapshot_json: str

    @property
    def security(self):
        return SecurityResult(**json.loads(self.security_json))

    @property
    def snapshot(self):
        return json.loads(self.snapshot_json)


@dataclass(frozen=True)
class SubmissionResult:
    inspection: PageInspection
    confirmation: str | None
    validation_error: bool

    @property
    def security(self):
        return self.inspection.security

    @property
    def snapshot(self):
        return self.inspection.snapshot


class PreSubmitState(StrEnum):
    NO_SECURITY_BLOCK = "NO_SECURITY_BLOCK"
    PASSIVE_PROTECTION_PRESENT = "PASSIVE_PROTECTION_PRESENT"
    INTERACTIVE_SECURITY_STEP = "INTERACTIVE_SECURITY_STEP"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class SubmissionClassifier:
    def __init__(self):
        self.security = SecurityDetector()

    async def classify(self, page, observation=None, *, include_validation=True):
        security, snapshot = await self.security.detect(page, observation, include_validation=include_validation)
        from dataclasses import asdict
        return self.classify_evidence(PageInspection(json.dumps(asdict(security)), json.dumps(snapshot)))

    @staticmethod
    def classify_evidence(inspection):
        security, snapshot = inspection.security, inspection.snapshot
        evidence = confirmation_evidence(snapshot)
        # A simultaneous explicit rejection invalidates new success-like text.
        if security.state in {S.SPAM_REJECTED, S.AUTOMATION_REJECTED, S.UNKNOWN_SECURITY_FAILURE, S.RATE_LIMITED}:
            evidence = None
        validation = bool(snapshot["invalid"]) or any(re.search(r"(?:required field|field is required|invalid email|please correct)", m, re.I) for m in snapshot["messages"])
        return SubmissionResult(inspection, evidence, validation)

    async def pre_submit(self, page, adapter, observation=None):
        # Adapter validation below owns the fresh normalized control scan here;
        # post-submit classification still observes validity itself.
        result = await self.classify(page, observation, include_validation=False)
        if result.security.blocking:
            return PreSubmitState.INTERACTIVE_SECURITY_STEP, [result.security.message]
        issues = await adapter.validate()
        if issues or result.validation_error:
            return PreSubmitState.VALIDATION_ERROR, issues or ["Required form fields are invalid"]
        if result.snapshot["disabled"]:
            return PreSubmitState.VALIDATION_ERROR, ["Submit control is disabled; wait for the site or inspect the preserved form"]
        return (PreSubmitState.PASSIVE_PROTECTION_PRESENT if result.security.state == S.PASSIVE_PROTECTION_DETECTED
                else PreSubmitState.NO_SECURITY_BLOCK), []

    @staticmethod
    def ats(snapshot):
        return detect_ats(snapshot["url"], [m["marker"] for m in snapshot["markers"]], snapshot["has_form"])
