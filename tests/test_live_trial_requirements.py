"""Public employer wording from the fill-only trial; synthetic applicant data."""
import pytest

from autoapply.requirements import extract_requirements


SPONSORSHIP_INSTRUCTION = (
    'Select “Yes” if you will ever need company sponsorship to work in this country '
    '(now or in the future), including if your current visa or permit expires. '
    'Select “No” only if you never require sponsorship.'
)


@pytest.mark.parametrize('heading', ['Common Requirements', 'COMMON REQUIREMENTS:', 'Common Qualifications'])
def test_common_required_section_retains_every_qualification(heading):
    qualifications = [
        "Education: Currently pursuing a Bachelor's or Master's in Computer Science, Computer Engineering, or a related field.",
        'Programming Proficiency: Solid foundation in at least one object-oriented language (e.g., Python, Java, or C++).',
        'Technical Fundamentals: Strong understanding of algorithms, data structures, and distributed computing.',
        'Soft Skills: Ability to learn quickly, communicate complex ideas effectively, and work collaboratively in a fast-paced environment.',
    ]
    parsed = extract_requirements('\n'.join([heading, *qualifications, 'Bonus Skills: Familiarity with cloud platforms', 'Benefits', 'Rewarding experience']))
    assert [r.text for r in parsed if not r.preferred] == qualifications
    assert [r.text for r in parsed if r.preferred] == ['Familiarity with cloud platforms']


@pytest.mark.parametrize('section', ['Application Questions', 'Required Qualifications'])
def test_sponsorship_answer_instructions_are_not_eligibility_gates(section):
    description = section + '\n' + SPONSORSHIP_INSTRUCTION + '\nNo visa sponsorship is available.\nMust be a U.S. citizen.'
    assert [r.text for r in extract_requirements(description)] == [
        'No visa sponsorship is available.', 'Must be a U.S. citizen.'
    ]


def test_instruction_with_added_requirement_remains_unresolved():
    text = SPONSORSHIP_INSTRUCTION + ' Applicants must have security clearance.'
    assert [r.text for r in extract_requirements('Application Questions\n' + text)] == [text]
