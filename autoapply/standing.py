"""Conservative semantic intents backed exclusively by explicit user assertions.

Full-clause grammars and composition (not similarity/keyword scores) ensure an
unrecognized qualifier or additional claim causes abstention.
"""
import logging
import re
import unicodedata

SOURCE = "USER_PROVIDED_STANDING_ELIGIBILITY_ASSERTIONS"
MEANINGS = {
    "experience_project_lab_research": "Prior internship experience or significant (>1 year) project team, laboratory, or research experience",
    "communication": "Strong interpersonal and technical communication skills",
    "analytical_problem_solving": "Strong analytical/problem-solving skills and attention to detail",
    "cross_functional_teamwork": "Ability to work across departments and teams",
    "independent_team_initiative": "Ability to work independently and in a team, take initiative, and communicate effectively",
    "gpa_3_5": "Minimum GPA of 3.5",
    "programming_typescript_go_or_python": "Excellent programming skills in Typescript, Go or Python",
    "automation_testing": "Knowledge of automation testing methodologies, tools, and best practices",
    "creative_problem_solving_tradeoffs": "Ability to solve problems creatively and communicate trade-offs effectively",
    "age_18_or_older": "Already 18 or older",
    "undergraduate_good_standing": "Continuing undergraduate student in good academic standing",
    "office_familiarity": "Experience with personal computers and familiarity with Word, Excel and PowerPoint",
    "adaptability_resilience": "Ability to adapt to challenging situations and overcome setbacks",
    "full_time_experience_at_most_two_years": "No more than two years of full-time work experience",
    "substantial_equivalent_technical_experience": "Substantial equivalent technical experience",
}


def normalize_requirement(text):
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = re.sub(r"[–—-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" •*.?!:")
    text = re.sub(r"\s*\((?:required|optional)\)$", "", text)
    text = re.sub(r"^(?:do you have|do you possess|are you|can you|you must have|must have|required to have) ", "", text)
    return text


QUALITY = r"(?:(?:strong|excellent|good|effective|proven) )?"
PATTERNS = {
    "full_time_experience_at_most_two_years": [
        r"(?:you (?:have|must have) |have )?(?:no more than|at most|a maximum of) (?:two|2) years of full time (?:work|employment)(?: experience)?",
        r"(?:you have |have )?(?:two|2) years or less of full time (?:work|employment)(?: experience)?",
    ],
    "substantial_equivalent_technical_experience": [
        r"substantial (?:relevant|technical|relevant technical|project|software project) experience",
        r"substantial equivalent technical experience",
    ],
    "age_18_or_older": [r"(?:(?:age|already) )?18 or older(?: at the start of the internship)?"],
    "undergraduate_good_standing": [r"(?:a )?continuing undergraduate student in good academic standing"],
    "office_familiarity": [r"experience (?:working )?with personal computers and familiarity with word, excel and powerpoint"],
    "adaptability_resilience": [
        r"(?:proven )?ability to adapt and thrive in challenging situations, demonstrating resilience and problem solving skills to achieve goals while overcoming setbacks",
        r"ability to adapt to challenging situations and overcome setbacks",
    ],
    "programming_typescript_go_or_python": [
        QUALITY + r"programming skills in typescript, go,? or python",
    ],
    "automation_testing": [
        r"knowledge of (?:automation|automated) testing methodologies, tools,? and best practices",
    ],
    "creative_problem_solving_tradeoffs": [
        r"ability to solve problems creatively and communicate trade(?: |)offs effectively",
    ],
    "communication": [
        r"demonstrated effective communication skills",
        r"ability to present and communicate concepts and ideas",
        r"strong interpersonal and technical communication skills \(examples: leading a student project team, presenting research at conferences, etc\.\)",
        QUALITY + r"(?:(?:interpersonal|technical|written|verbal|oral)(?: and (?:technical|verbal|oral|written))? )?communication(?: skills| abilities)?",
        QUALITY + r"interpersonal skills",
        r"(?:ability to )?communicate effectively(?: (?:within|in) a team)?",
    ],
    "analytical_problem_solving": [
        QUALITY + r"analytical and problem solving skills with attention to detail",
        QUALITY + r"(?:analytical(?:/| and )problem solving|analytical|problem solving)(?: skills| abilities)?",
        QUALITY + r"attention to detail",
    ],
    "cross_functional_teamwork": [
        r"ability to work cross departmentally with different groups and teams",
        r"(?:ability to |able to )?(?:work|collaborate)(?: effectively)? across (?:departments and teams|teams and departments|departments|teams)",
        r"(?:ability to |able to )?collaborate(?: effectively)? with cross functional teams",
        r"team oriented and able to collaborate effectively",
    ],
    "independent_team_initiative": [
        r"ability to work in a team environment",
        r"(?:ability to |able to |can )?work (?:both )?independently(?: and (?:in a team|collaboratively|as part of a team))?",
        r"(?:ability to |able to )?work (?:in a team|collaboratively|as part of a team)",
        r"(?:ability to )?take initiative",
        r"self starter(?: who takes initiative)?",
    ],
    "gpa_3_5": [
        r"(?:minimum |a minimum )?gpa(?: of)?(?: at least)? 3\.5(?:0)?\+?(?: or (?:above|higher))?",
        r"3\.5(?:0)?\+?(?: minimum)? gpa",
    ],
    "experience_project_lab_research": [
        r"(?:prior|previous) internship experience,? or (?:significant|substantial)(?: \(>1\s*(?:year|yr)\))? (?:project team|project)(?:, laboratory,? or research|/lab/research|, lab,? or research) experience",
        r"(?:significant|substantial) (?:project team|project|laboratory|lab|research)(?: or (?:laboratory|lab|research))? experience",
        r"at least (?:one|1) year of (?:project, (?:laboratory|lab),? or research|project/(?:laboratory|lab)/research) experience",
    ],
}


def _intents(text):
    for assertion_id, patterns in PATTERNS.items():
        if any(re.fullmatch(pattern, text) for pattern in patterns):
            return [assertion_id]
    # Every component must be independently supported; unknown qualifiers cannot
    # be discarded. Try whole clauses first so experience disjunctions stay intact.
    parts = re.split(r",\s*(?:and )?", text) if "," in text else re.split(r" and ", text)
    if len(parts) > 1:
        result = []
        for part in parts:
            matched = _intents(part.strip())
            if not matched:
                return []
            result.extend(matched)
        return list(dict.fromkeys(result))
    return []


def resolve_standing(text, profile):
    ids = _intents(normalize_requirement(text))
    assertions = profile.get("eligibility_assertions", [])
    verified = {item.get("id") for item in assertions if isinstance(item, dict)
                and item.get("source") == "USER_PROVIDED" and item.get("scope") == "standing"
                and item.get("answer") is True and item.get("meaning") == MEANINGS.get(item.get("id"))}
    if not ids or not set(ids) <= verified:
        return None
    match = {"requirement": text, "assertion_ids": ids, "answer": True,
             "source": SOURCE, "confidence": 1.0,
             "reason": "Complete requirement matches verified semantic intent; no unsupported clause",
             "manual_verification_required": False}
    logging.getLogger("autoapply").info("[ANSWER] Requirement matched standing user assertion %s", match)
    return match
