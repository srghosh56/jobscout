"""Table-driven tests for deterministic extraction rules, including tricky ads."""

from __future__ import annotations

from typing import Any

import pytest

from jobscout.extract import rules as r

GERMAN_GOOD = [{"language": "German", "level": "good"}]


@pytest.mark.parametrize(
    ("text", "langs", "lang", "expected"),
    [
        # plain cases
        ("We are an English-speaking team. Python required.", [], "en", "yes"),
        ("Fluent German (C1) and English required.", [], "en", "no"),
        ("Sehr gute Deutschkenntnisse in Wort und Schrift.", [], "de", "no"),
        ("Gute Deutsch- und Englischkenntnisse", [], "de", "no"),
        ("You speak German at B2 level.", [], "en", "no"),
        ("Native German speaker required.", [], "en", "no"),
        # tricky: German mentioned but optional
        ("German is a plus but not required. Our working language is English.", [], "en", "yes"),
        ("Deutschkenntnisse sind von Vorteil. Unsere Teamsprache ist Englisch.", [], "de", "yes"),
        ("Nice to have: German skills.", [], "en", "yes"),
        ("No German required - we are an international team.", [], "en", "yes"),
        # tricky: LLM says German good, but text says optional
        ("German is nice to have.", GERMAN_GOOD, "en", "yes"),
        # tricky: LLM says German good and text agrees
        ("Requires good German.", GERMAN_GOOD, "en", "no"),
        # tricky: basic German required → not 'no' (below good) but not clearly 'yes'
        ("Basic German (A2) is required for customer calls.", [], "en", "unclear"),
        # tricky: German-language ad without language requirements
        ("Wir suchen einen Entwickler für unser Team in Berlin.", [], "de", "unclear"),
        # tricky: English ad that requires German generically
        ("German language skills are required.", [], "en", "unclear"),
        # tricky: willingness to learn German
        ("Willingness to learn German is appreciated.", [], "en", "yes"),
    ],
)
def test_english_sufficient(
    text: str, langs: list[dict[str, Any]], lang: str, expected: str
) -> None:
    assert r.english_sufficient(langs, text, lang) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Salary: 70,000 - 85,000 EUR per year", {"min": 70000, "max": 85000, "period": "year"}),
        ("Gehalt: 65.000 € brutto jährlich", {"min": 65000, "max": None, "period": "year"}),
        ("Vergütung 4.500 € monatlich", {"min": 4500, "max": None, "period": "month"}),
        ("Stundenlohn: 25 € pro Stunde", {"min": 25, "max": None, "period": "hour"}),
        ("Salary range €60k–€75k", {"min": 60000, "max": 75000, "period": "year"}),
        ("Salary: 55000 EUR", {"min": 55000, "max": None, "period": "year"}),
        ("3+ years of experience. Competitive salary.", None),
        ("Wir bieten 30 Tage Urlaub und 40 Stunden pro Woche.", None),
    ],
)
def test_extract_salary(text: str, expected: dict[str, Any] | None) -> None:
    got = r.extract_salary(text)
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert {k: got[k] for k in expected} == expected


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("3+ years of experience in Python", 3),
        ("mindestens 5 Jahre Berufserfahrung", 5),
        ("at least 2 years experience", 2),
        ("2-4 years of professional experience", 2),
        ("30 Tage Urlaub", None),
    ],
)
def test_min_years(text: str, years: int | None) -> None:
    assert r.extract_min_years(text) == years


def test_visa_relocation_mentions() -> None:
    assert r.mention("We offer visa sponsorship.", r.VISA_YES, r.VISA_NO) == "yes"
    assert r.mention("Unfortunately we cannot sponsor visas.", r.VISA_YES, r.VISA_NO) == "no"
    assert r.mention("Great team.", r.VISA_YES, r.VISA_NO) == "not_mentioned"
    assert r.mention("Relocation package included", r.RELOC_YES, r.RELOC_NO) == "yes"
    assert r.mention("Umzugsunterstützung", r.RELOC_YES, r.RELOC_NO) == "yes"


@pytest.mark.parametrize(
    ("text", "flag", "policy"),
    [
        ("Hybrid: 2 days in the office", None, "hybrid"),
        ("Fully remote within Germany", None, "remote"),
        ("Homeoffice möglich", None, "hybrid"),
        ("Work on-site in Munich", None, "onsite"),
        ("Anything", True, "remote"),
        ("Nothing said", None, "unknown"),
    ],
)
def test_remote_policy(text: str, flag: bool | None, policy: str) -> None:
    assert r.remote_policy(text, flag) == policy


@pytest.mark.parametrize(
    ("title", "years", "level"),
    [
        ("Senior Data Scientist", None, "senior"),
        ("Junior Developer", None, "junior"),
        ("Werkstudent Data", None, "intern"),
        ("Staff Engineer", None, "staff_plus"),
        ("Data Scientist", 3, "mid"),
        ("Data Scientist", None, "unknown"),
    ],
)
def test_seniority(title: str, years: int | None, level: str) -> None:
    assert r.seniority(title, "", years) == level


def test_skills_vocab_boundaries() -> None:
    skills = r.extract_skills("We use Python, C++, Go and PostgreSQL; RAG pipelines; React.")
    assert {"python", "c++", "go", "postgresql", "rag", "react"} <= set(skills)
    assert "r" not in r.extract_skills("Your profile: great teamwork")


def test_rule_based_extract_validates_against_schema() -> None:
    from jobscout.schema import JobPosting

    text = "Machine Learning Engineer\nWe are an English-speaking team. 3+ years experience with Python and RAG. Salary: 70,000 - 85,000 EUR per year. Visa sponsorship available."
    out = JobPosting.model_validate(r.rule_based_extract(text))
    assert out.english_sufficient == "yes" and out.salary is not None
    assert out.visa_sponsorship == "yes" and out.min_years_experience == 3


def test_rule_based_profile() -> None:
    p = r.rule_based_profile(
        "Data scientist with 4 years of Python and SQL. Based in Berlin. German A2, English fluent."
    )
    assert p["years_experience"] == 4 and "python" in p["skills"]
    assert {"language": "German", "level": "basic"} in p["languages"]
    assert "Berlin" in p["preferred_cities"]


@pytest.mark.parametrize(
    ("text", "go", "r_"),
    [
        ("Ready to go the extra mile? Let's go!", False, False),
        ("Backend in Go, Python and Rust", True, False),
        ("Experience with Golang microservices", True, False),
        ("Statistics with Python or R", False, True),
        ("R&D department, our team", False, False),
    ],
)
def test_ambiguous_skill_names(text: str, go: bool, r_: bool) -> None:
    skills = r.extract_skills(text)
    assert ("go" in skills) is go and ("r" in skills) is r_


def test_rest_only_as_api_style() -> None:
    assert "rest" in r.extract_skills("We build RESTful APIs")
    assert "rest" not in r.extract_skills("The rest of the team works remotely")
