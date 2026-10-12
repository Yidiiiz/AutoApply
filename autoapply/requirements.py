"""Text-only qualification scope. No applicant facts or employer I/O here."""
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    text: str
    preferred: bool = False


def heading_kind(text):
    # Full-line grammars deliberately do not match sentences containing these words.
    heading = re.sub(r"[^\w]+", " ", unicodedata.normalize('NFKC', text).casefold().replace('&', ' and ')).strip()
    if re.fullmatch(r'(?:(?:preferred|desired)(?: qualifications| requirements| skills(?: and experience)?| experience)?|nice to have(?:s)?|bonus(?: qualifications| skills| points)?)', heading):
        return 'preferred'
    if re.fullmatch(r'(?:(?:minimum|required|basic|common|additional|technical|itar|export control) (?:qualifications|requirements)(?: and experience)?|required|requirements|qualifications|(?:the )?must haves?)', heading):
        return 'required'
    if re.fullmatch(
        r'(?:(?:our|your|employee|internship) )?(?:benefits(?: and perks)?|perks(?: and benefits)?|'
        r'compensation(?: and benefits)?|salary(?: range| and benefits)?|pay range|what we offer|'
        r'equal (?:employment )?opportunity(?: employer| statement| policy)?|eeo(?: statement| policy)?|'
        r'(?:recruiting |candidate |data )?privacy(?: notice| policy)?|application (?:information|questions)|'
        r'voluntary self identification|(?:create a )?job alerts?|(?:join (?:our|the) )?talent community|'
        r'additional information|company description|job description|responsibilities|roles and responsibilities|'
        r'your role|apply for this job|upload instructions)', heading
    ) or re.fullmatch(r'(?:united states(?: of america)?|us|usa) (?:intern )?pay range', heading):
        return 'outside'
    return None


def administrative(text):
    # This complete two-sentence form instruction describes how to answer; it
    # does not say that applicants needing sponsorship are ineligible. Keep
    # added/changed clauses in the qualification assessment rather than hiding
    # them behind an instruction prefix.
    instruction = unicodedata.normalize('NFKC', text).translate(str.maketrans({'“': '"', '”': '"', '’': "'"}))
    if re.fullmatch(
        r"Over the years, we've discovered that the most effective and successful associates "
        r'at apexanalytix are people who have a specific combination of values, skills, and behaviors '
        r'that we call "The apex Way"\. Read more about The apex Way - https://www\.apexanalytix\.com/careers/?',
        instruction, re.I):
        return True
    if re.fullmatch(
        r'Select "Yes" if you will ever need company sponsorship to work in this country '
        r'\(now or in the future\), including if your current visa or permit expires\. '
        r'Select "No" only if you never require sponsorship\.', instruction, re.I):
        return True
    return bool(re.search(
        r'\b(?:to the best of your knowledge|by submitting your application|'
        r'(?:equal opportunity|equal employment opportunity) employer|does not tolerate discrimination|'
        r'we do not discriminate|personal data collected|information will be kept confidential|'
        r'pay (?:may |will )?depend|pay range posted|when making an offer|'
        r'gain new exposure|rewarding internship experience|accepted file types|'
        r'upload (?:your |a )?(?:resume|cv)|indicates a required field|'
        r'employees are required to apply through)\b', text, re.I))


def duration_requirement(text):
    return bool(re.search(r'\b(?:program|internship)\b', text, re.I) and (
        re.search(r'\b(?:availability|available)\b', text, re.I)
        or re.search(r'\b(?:program|internship) (?:takes place|runs) (?:from|between)\b', text, re.I)))


def explicit_gate(text):
    """Narrow gates also survive application/administrative section boundaries."""
    return bool(re.search(
        r'\b(?:must|required|minimum|at least|only|requires|no|not|cannot|unable)\b', text, re.I)
        and re.search(r'\b(?:resid\w*|citizen\w*|authoriz\w*|sponsor\w*|gpa|graduat\w*|'
                      r'enrolled|pursuing|degree|clearance|age|years? old|availab\w*|'
                      r'located|location|live|based|relocat\w*)\b', text, re.I))


def extract_requirements(description):
    result, mode, excluded = [], 'outside', False
    for raw in description.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip(' \t•*')
        if not line:
            continue
        kind = heading_kind(line)
        if kind:
            mode = kind
            excluded = kind == 'outside'
            continue
        # A heading followed by a same-line qualification is also common.
        prefix, colon, tail = line.partition(':')
        if colon and heading_kind(prefix):
            mode = heading_kind(prefix)
            excluded = mode == 'outside'
            line = tail.strip()
            if not line:
                continue
        if administrative(line):
            continue
        # Inline major preferences must not erase the required degree/standing.
        parts = re.split(r',\s*(?=preferably\b)', line, maxsplit=1, flags=re.I)
        # Semicolons delimit independent mandatory/preferred qualifications.
        # Keep other compound clauses intact so no unknown qualifier is lost.
        preference = r'\b(?:preferred|preferably|nice[ -]+to[ -]+have|a (?:plus|bonus))\b'
        if re.search(preference, line, re.I):
            parts = [clause.strip() for part in parts
                     for clause in re.split(r';|(?<=required),\s*', part, flags=re.I) if clause.strip()]
        for part in parts:
            schedule = bool(re.search(r'\b(?:program|internship)\b', part, re.I)
                            and re.search(r'\b(?:takes place|runs from|in.person|on.site)\b', part, re.I)
                            and re.search(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|June|July|August)\b', part, re.I))
            preferred = (mode == 'preferred' and not schedule) or bool(re.search(preference, part, re.I))
            contextual = mode in {'required', 'preferred'}
            explicit = explicit_gate(part) or duration_requirement(part)
            # Outside sections, generic keywords alone are insufficient.
            explicit_skill = bool(not excluded and re.search(r'\b(?:must|required|minimum|at least)\b', part, re.I)
                                  and re.search(r'\b(?:experience|skills?|knowledge|proficien\w*|ability|able|'
                                                r'work|comfort\w*|familiar\w*|interest)\b', part, re.I))
            if contextual or explicit or explicit_skill:
                result.append(Requirement(part, preferred))
    return list(dict.fromkeys(result))
