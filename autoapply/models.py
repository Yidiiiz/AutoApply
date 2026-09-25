from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class State(StrEnum):
    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    CHECKING = "CHECKING"
    CLOSED = "CLOSED"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    INELIGIBLE = "INELIGIBLE"
    READY = "READY"
    APPLYING = "APPLYING"
    NEEDS_INPUT = "NEEDS_INPUT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    RETRY = "RETRY"


FINAL = {State.SUBMITTED, State.ALREADY_APPLIED, State.CLOSED, State.INVALID,
         State.DUPLICATE, State.INELIGIBLE}


class ApplicationState(StrEnum):
    DISCOVERED = "DISCOVERED"
    OPENED = "OPENED"
    FILLING = "FILLING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    FAILED = "FAILED"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"
    UNKNOWN = "UNKNOWN"
    RATE_LIMITED = "RATE_LIMITED"


class SecurityState(StrEnum):
    NONE = "NONE"
    PASSIVE_PROTECTION_DETECTED = "PASSIVE_PROTECTION_DETECTED"
    INTERACTIVE_CHALLENGE = "INTERACTIVE_CHALLENGE"
    RATE_LIMITED = "RATE_LIMITED"
    SPAM_REJECTED = "SPAM_REJECTED"
    AUTOMATION_REJECTED = "AUTOMATION_REJECTED"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PHONE_VERIFICATION = "PHONE_VERIFICATION"
    IDENTITY_VERIFICATION = "IDENTITY_VERIFICATION"
    FRAUD_REVIEW = "FRAUD_REVIEW"
    UNKNOWN_SECURITY_FAILURE = "UNKNOWN_SECURITY_FAILURE"


class VerificationState(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Listing:
    company: str
    title: str
    location: str
    url: str
    source: str
    source_id: str = ""
    posted_at: str | None = None
    date_evidence: str = ""
    description: str = ""
    closed: bool = False
    posted_at_source: str = ""
    posted_at_confidence: str = "unknown"
    original_posted_at: str | None = None
    reposted_at: str | None = None
    updated_at: str | None = None


@dataclass
class Question:
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    options: list[str] = field(default_factory=list)
    max_length: int | None = None
    value: str = ""
    scope: str = ""
    semantic_key: str = ""


@dataclass
class Answer:
    value: str | list[str]
    source: str
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)


@dataclass
class Eligibility:
    eligible: bool | None
    reasons: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    standing_matches: list[dict] = field(default_factory=list)
