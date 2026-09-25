"""Explicit retry policy. Submission intent always takes precedence."""
import random
from dataclasses import dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    PRE_SUBMIT_TARGET_UNSTABLE = 'PRE_SUBMIT_TARGET_UNSTABLE'
    PRE_SUBMIT_DROPDOWN_STATE_FAILURE = 'PRE_SUBMIT_DROPDOWN_STATE_FAILURE'
    SUBMIT_ELEMENT_OBSTRUCTED = "SUBMIT_ELEMENT_OBSTRUCTED"
    UPLOAD_PENDING = "UPLOAD_PENDING"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    UPLOAD_NOT_STARTED = "UPLOAD_NOT_STARTED"
    UPLOAD_STALLED = "UPLOAD_STALLED"
    UPLOAD_RACE_DETECTED = "UPLOAD_RACE_DETECTED"
    SUBMIT_CLICK_NOT_DELIVERED = "SUBMIT_CLICK_NOT_DELIVERED"
    SCROLL_FAILED = "SCROLL_FAILED"
    ELEMENT_NOT_REACHABLE = "ELEMENT_NOT_REACHABLE"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    EXTERNAL_EXECUTION_APPROVAL_REQUIRED = "EXTERNAL_EXECUTION_APPROVAL_REQUIRED"
    EXECUTION_APPROVAL_BLOCKED = "EXECUTION_APPROVAL_BLOCKED"
    FORM_VALIDATION_ERROR = "FORM_VALIDATION_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    SITE_ERROR = "SITE_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    CAPTCHA = "CAPTCHA"
    BOT_CHALLENGE = "BOT_CHALLENGE"
    SPAM_REJECTION = "SPAM_REJECTION"
    AUTOMATION_REJECTION = "AUTOMATION_REJECTION"
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PHONE_VERIFICATION = "PHONE_VERIFICATION"
    IDENTITY_VERIFICATION = "IDENTITY_VERIFICATION"
    FRAUD_REVIEW = "FRAUD_REVIEW"
    UNKNOWN_SECURITY_FAILURE = "UNKNOWN_SECURITY_FAILURE"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    AUTH_REQUIRED = "AUTH_REQUIRED"


@dataclass
class RetryDecision:
    allowed: bool
    delay: float = 0


class RetryPolicy:
    retryable = {ErrorCategory.NETWORK_ERROR, ErrorCategory.SITE_ERROR, ErrorCategory.RATE_LIMIT}

    def decide(self, category, attempts, max_retries, submitted_intent=False):
        if submitted_intent or category not in self.retryable or attempts > max_retries:
            return RetryDecision(False)
        # Both exponent and final delay are bounded, including jitter.
        delay = min(60 * 2 ** min(10, max(0, attempts - 1)), 1800)
        return RetryDecision(True, min(1800, delay + random.uniform(0, 5)))


class SiteError(RuntimeError):
    pass
