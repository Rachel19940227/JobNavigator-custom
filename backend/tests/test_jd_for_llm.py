from backend.analyzer.jd_for_llm import prepare_jd_for_llm


def test_removes_low_value_sections_but_keeps_core_sections():
    jd = """About Us
We have been changing the world for 100 years.

Responsibilities
- Lead requirements elicitation.
- Coordinate UAT.

Benefits
Health insurance, free lunches, and generous vacation.

Qualifications
- Five years of business analysis experience.
- SQL and Power BI.

Equal Opportunity
We are an equal opportunity employer and welcome all applicants.
"""

    result = prepare_jd_for_llm(jd, 8000)

    assert "changing the world" not in result
    assert "Health insurance" not in result
    assert "equal opportunity employer" not in result
    assert "Lead requirements elicitation" in result
    assert "Five years of business analysis" in result
    assert "SQL and Power BI" in result


def test_removes_standalone_eeo_paragraph_without_heading():
    jd = """Responsibilities
Build internal tools.

We are an equal opportunity employer. We do not discriminate based on race, religion, or gender.

Requirements
Python and SQL.
"""

    result = prepare_jd_for_llm(jd, 8000)

    assert "equal opportunity employer" not in result
    assert "Build internal tools" in result
    assert "Python and SQL" in result


def test_single_newline_ats_text_drops_only_eeo_line():
    jd = "Responsibilities\nBuild internal tools.\nWe are an equal opportunity employer.\nRequirements\nPython and SQL."

    result = prepare_jd_for_llm(jd, 8000)

    assert "equal opportunity employer" not in result
    assert "Build internal tools" in result
    assert "Python and SQL" in result


def test_cleaning_happens_before_character_limit():
    jd = "About Us\n" + ("marketing copy " * 100) + "\nRequirements\nCritical skill: COBOL"

    result = prepare_jd_for_llm(jd, 80)

    assert "Critical skill: COBOL" in result
    assert "marketing copy" not in result
    assert len(result) <= 80


def test_does_not_remove_inline_benefit_or_diversity_responsibilities():
    jd = """Responsibilities
- Design customer benefits products.
- Lead diversity analytics reporting.
Requirements
- SQL
"""

    result = prepare_jd_for_llm(jd, 8000)

    assert "customer benefits products" in result
    assert "diversity analytics reporting" in result


def test_falls_back_to_original_when_every_section_is_low_value():
    jd = "Benefits\nHealth coverage and vacation"

    assert prepare_jd_for_llm(jd, 8000) == jd
