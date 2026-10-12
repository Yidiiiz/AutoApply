"""Operator policy: skill, willingness and requirement-confirmation questions are answered Yes.

Hardcoded at the user's request (2026-10-07). A question qualifies when its text
contains an affirmative keyword and none of the protected legal/identity topics,
whose truthful answer differs per applicant and must never be assumed.
"""
import re

SOURCE = 'operator_affirmative_rule'
ENABLED = True  # Always on in production; legacy policy tests switch it off.

KEYWORDS = (
    'ability', 'able to', 'capable', 'capability', 'requirement', 'required', 'verify', 'qualification',
    'qualified', 'proficient', 'proficiency', 'willing', 'willingness', 'confirm', 'comfortable',
    'familiar', 'familiarity', 'knowledge of', 'experience with', 'experience in', 'skill', 'competent',
    'competency', 'commit', 'available to', 'availability', 'open to', 'relocate', 'relocation',
    'commute', 'onsite', 'on-site', 'on site', 'in-office', 'in office', 'hybrid', 'travel',
    'work schedule', 'full-time', 'full time', 'duration', 'weeks', 'summer', 'learn', 'eager',
    'understand', 'acknowledge', 'agree', 'certify', 'meet the', 'meets the', 'do you have', 'can you',
    'are you able',
)

# Protected topics: answering these Yes could be false or harmful for some applicants.
EXCLUDED = re.compile(
    r'sponsor|visa|h-?1b|\bopt\b|\bcpt\b|immigration|citizen|green card|permanent resident|work authori[sz]|'
    r'authori[sz]ed to work|legally (?:authori[sz]ed|eligible|able)|export control|itar|\bear\b|u\.?s\.? person|'
    r'security clearance|clearance|convicted|conviction|criminal|felony|misdemeanor|background check|drug|'
    r'disabilit|veteran|gender|race|ethnic|hispanic|latino|sexual|orientation|pronoun|religio|pregnan|'
    r'previously (?:worked|employed|applied)|former employee|currently employed (?:by|at)|worked (?:for|at) .{0,40}before|'
    r'relative|family member|related to|referr|non-?compete|non-?solicit|conflict of interest|'
    r'government official|politically exposed|debarred|salary|compensation|pay expectation|'
    r'how many|how much|years of experience|number of|date|when can|start date|\bage\b|older than|under 18|'
    r'social security|\bssn\b|password|signature|sign here|gpa|grade point|degree|graduat|enrolled|'
    r'current(?:ly)? (?:a )?student|academic standing|\b18\b|driver.?s licen|certified (?:as|in)',
    re.I)

_KEYWORD = re.compile('|'.join(re.escape(k) for k in KEYWORDS), re.I)


def affirmative(text):
    """True when the question should be answered Yes under the operator policy."""
    text = ' '.join(str(text or '').split())
    return ENABLED and bool(text) and bool(_KEYWORD.search(text)) and not EXCLUDED.search(text)
