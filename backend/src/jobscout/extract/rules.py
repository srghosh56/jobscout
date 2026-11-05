"""Deterministic rules used (a) to derive ``german_level`` / ``english_sufficient`` from
the LLM's ``languages_required`` plus text cues, and (b) as a rule-based extractor for
the ``fake`` provider (tests, CI, offline demo). Every rule is table-tested in
``tests/test_rules.py``, including tricky ads.
"""

from __future__ import annotations

import re
from typing import Any

LEVEL_ORDER = {"none": 0, "unspecified": 1, "basic": 2, "good": 3, "fluent": 4, "native": 5}

# --- German requirement cues ------------------------------------------------
_GERMAN = r"(?:german|deutsch(?:kenntnisse)?|deutschen sprache)"
_OPTIONAL = re.compile(
    rf"{_GERMAN}[^.\n]{{0,60}}(?:is a plus|a plus|nice to have|of advantage|advantageous|"
    rf"wünschenswert|von vorteil|ein plus|not required|nicht erforderlich|not necessary|"
    rf"keine voraussetzung|optional|helpful|bonus|willingness to learn)|"
    rf"(?:no|kein(?:e)?)\s+{_GERMAN}\s+(?:required|needed|erforderlich|notwendig)|"
    rf"(?:plus|bonus|nice to have|von vorteil)[^.\n]{{0,30}}{_GERMAN}",
    re.I,
)
_LEVEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "native",
        re.compile(
            rf"(?:native|muttersprach\w*)[^.\n]{{0,25}}{_GERMAN}|{_GERMAN}[^.\n]{{0,20}}(?:muttersprach\w*|native)",
            re.I,
        ),
    ),
    (
        "fluent",
        re.compile(
            rf"(?:fluent|fließend\w*|verhandlungssicher\w*|excellent|exzellent\w*|business[- ]fluent|"
            rf"sehr gut\w*|very good|proficient|\bc1\b|\bc2\b)[^.\n]{{0,30}}{_GERMAN}|"
            rf"{_GERMAN}[^.\n]{{0,30}}(?:fluent|fließend|verhandlungssicher|\bc1\b|\bc2\b|sehr gut)",
            re.I,
        ),
    ),
    (
        "good",
        re.compile(
            rf"(?:good|gute?n?|solid\w*|fundiert\w*|strong|\bb2\b)[^.\n]{{0,25}}{_GERMAN}|"
            rf"{_GERMAN}[^.\n]{{0,25}}(?:\bb2\b|good|gut)",
            re.I,
        ),
    ),
    (
        "basic",
        re.compile(
            rf"(?:basic|grund\w*|some|\ba2\b|\bb1\b|conversational)[^.\n]{{0,25}}{_GERMAN}|"
            rf"{_GERMAN}[^.\n]{{0,25}}(?:\ba2\b|\bb1\b|basic)",
            re.I,
        ),
    ),
]
_GERMAN_REQUIRED_GENERIC = re.compile(
    rf"(?:{_GERMAN}[^.\n]{{0,25}}(?:required|mandatory|erforderlich|notwendig|vorausgesetzt))|"
    rf"(?:(?:requires?|must speak|you speak)\s+[^.\n]{{0,15}}{_GERMAN})|"
    rf"(?:deutschkenntnisse)",
    re.I,
)
_ENGLISH_WORKING = re.compile(
    r"english[- ]speaking|working language is english|company language is english|"
    r"(?:team|office|unternehmens|arbeits)sprache (?:ist )?englisch|english is (?:our|the) (?:working|company) language|"
    r"no german (?:required|needed|necessary)|german (?:is )?not (?:required|necessary)|"
    r"international team|english only",
    re.I,
)


def _level_from_text(text: str) -> str:
    """Highest German level stated as a *requirement* in the text ('none' if none)."""
    if not re.search(_GERMAN, text, re.I):
        return "none"
    # Remove optional mentions ("German is a plus") before looking for requirements.
    req_text = _OPTIONAL.sub(" ", text)
    for level, pat in _LEVEL_PATTERNS:
        if pat.search(req_text):
            return level
    if _GERMAN_REQUIRED_GENERIC.search(req_text):
        return "unspecified"
    return "none"


def german_level(languages_required: list[dict[str, Any]], text: str = "") -> str:
    """Combine the LLM's languages_required with text cues; take the stricter one."""
    llm_level = "none"
    for lang in languages_required or []:
        name = str(lang.get("language", "")).lower()
        if name.startswith(("german", "deutsch", "de")):
            lvl = str(lang.get("level") or "unspecified")
            if LEVEL_ORDER.get(lvl, 1) > LEVEL_ORDER[llm_level]:
                llm_level = lvl
    text_level = _level_from_text(text) if text else "none"
    if text and llm_level != "none" and _OPTIONAL.search(text) and text_level == "none":
        # The LLM listed German, but the text says it's optional: trust the text.
        return "none"
    return max(llm_level, text_level, key=lambda lv: LEVEL_ORDER.get(lv, 1))


def english_sufficient(
    languages_required: list[dict[str, Any]], text: str, posting_language: str
) -> str:
    """'no' if German at good or above is required (JobLens rule); 'yes' if no German
    requirement and the ad is English or says English is the working language;
    otherwise 'unclear'."""
    level = german_level(languages_required, text)
    if LEVEL_ORDER.get(level, 1) >= LEVEL_ORDER["good"]:
        return "no"
    if level in ("basic", "unspecified"):
        # German is required at some level: English alone isn't enough, but we
        # can't tell how much German. Basic German is explicitly < good → 'unclear'.
        return "unclear"
    if _ENGLISH_WORKING.search(text):
        return "yes"
    if posting_language == "en":
        return "yes"
    return "unclear"


# --- salary -------------------------------------------------------------------
_NUM = r"\d{1,3}(?:[.,\s]\d{3})+|\d+(?:[.,]\d+)?\s?[kK]|\d{2,6}"
_SAL = re.compile(
    rf"(?:€|eur|euro)?\s?(?P<a>{_NUM})\s?(?:€|eur|euro)?"
    rf"(?:\s?(?:-|–|to|bis)\s?(?:€|eur|euro)?\s?(?P<b>{_NUM}))?\s?(?:€|eur|euro)?"
    r"(?P<tail>[^.\n]{0,40})",
    re.I,
)
_PERIOD = [
    ("hour", re.compile(r"per hour|/\s?h\b|/\s?hour|stunde|stündlich|hourly|pro std", re.I)),
    ("month", re.compile(r"per month|/\s?month|monat|monthly|mtl", re.I)),
    ("year", re.compile(r"per year|/\s?year|p\.?\s?a\.?|jahr|annual|yearly|brutto|gross", re.I)),
]
_SAL_CONTEXT = re.compile(r"salary|gehalt|vergütung|compensation|pay|€|eur|euro", re.I)


def _to_number(s: str) -> float:
    s = s.strip().replace(" ", "")
    if s[-1:] in "kK":
        return float(s[:-1].replace(",", ".")) * 1000
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    return float(s.replace(",", "."))


def extract_salary(text: str) -> dict[str, Any] | None:
    for line in re.split(r"[\n]", text):
        if not _SAL_CONTEXT.search(line) or not re.search(r"€|eur|euro|gehalt|salary", line, re.I):
            continue
        for m in _SAL.finditer(line):
            if not re.search(r"€|eur|euro", m.group(0), re.I) and not re.search(
                r"salary|gehalt", line[: m.start()], re.I
            ):
                continue
            try:
                a = _to_number(m.group("a"))
                b = _to_number(m.group("b")) if m.group("b") else None
            except ValueError:
                continue
            if a < 8 and b is None:
                continue
            period = "year"
            ctx = m.group("tail") + " " + line
            for name, pat in _PERIOD:
                if pat.search(ctx):
                    period = name
                    break
            if period == "year" and a < 1000:
                continue  # "5 years", "40 Stunden" etc.
            lo, hi = (a, b) if b is None or a <= b else (b, a)
            return {"min": lo, "max": hi, "currency": "EUR", "period": period}
    return None


# --- misc fields ---------------------------------------------------------------
_YEARS = re.compile(
    r"(?:(?:at least|minimum|min\.?|mindestens)\s+)?(\d{1,2})\s?\+?\s?(?:-\s?\d{1,2}\s?)?"
    r"(?:years?|jahre?n?)\b[^.\n]{0,40}?(?:experience|erfahrung|berufserfahrung)",
    re.I,
)


def extract_min_years(text: str) -> int | None:
    m = _YEARS.search(text)
    return int(m.group(1)) if m and int(m.group(1)) <= 40 else None


def mention(text: str, yes: str, no: str | None = None) -> str:
    if no and re.search(no, text, re.I):
        return "no"
    return "yes" if re.search(yes, text, re.I) else "not_mentioned"


VISA_YES = r"visa sponsor|sponsor(?:ing|ship)? (?:your |a |the )?(?:work )?visa|visa support|visum|blue card support|work permit support|unterstützung beim visum"
VISA_NO = r"(?:no|cannot|unable to|don't|do not|not able to)\s+(?:offer\s+|provide\s+)?(?:visa|sponsor)|must (?:already )?have (?:a )?(?:valid )?(?:eu )?work permit|eu citizens only"
RELOC_YES = r"relocation (?:support|package|assistance|bonus|budget)|we (?:help|support) (?:you )?(?:with )?relocat|umzugs(?:unterstützung|kosten|hilfe)|relocation-paket"
RELOC_NO = r"no relocation|relocation (?:is )?not (?:offered|provided|available)"


def remote_policy(text: str, source_flag: bool | None = None) -> str:
    t = text.lower()
    if re.search(
        r"\bhybrid\b|teilweise (?:im )?home ?office|\d (?:days|tage) (?:im büro|in the office|on.?site)",
        t,
    ):
        return "hybrid"
    if re.search(
        r"fully remote|100\s?% remote|remote[- ]first|vollständig remote|komplett remote", t
    ):
        return "remote"
    if source_flag:
        return "remote"
    if re.search(r"\bremote\b|home ?office|mobiles arbeiten", t):
        return "hybrid"
    if re.search(r"on[- ]?site|vor ort|in our office", t):
        return "onsite"
    return "unknown"


def seniority(title: str, text: str = "", min_years: int | None = None) -> str:
    t = title.lower()
    if re.search(r"intern\b|internship|praktik", t):
        return "intern"
    if re.search(r"working student|werkstudent", t):
        return "intern"
    if re.search(r"principal|staff|head of|director|distinguished|lead architect", t):
        return "staff_plus"
    if re.search(r"senior|sr\.|lead\b", t):
        return "senior"
    if re.search(r"junior|jr\.|graduate|entry|berufseinsteiger|trainee", t):
        return "junior"
    if min_years is not None:
        return "junior" if min_years <= 1 else "mid" if min_years <= 4 else "senior"
    return "unknown"


SKILL_VOCAB = [
    "python",
    "java",
    "javascript",
    "typescript",
    "golang",
    "rust",
    "c++",
    "c#",
    "scala",
    "kotlin",
    "swift",
    "php",
    "ruby",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "kafka",
    "spark",
    "airflow",
    "dbt",
    "docker",
    "kubernetes",
    "terraform",
    "aws",
    "azure",
    "gcp",
    "linux",
    "git",
    "react",
    "angular",
    "vue",
    "node.js",
    "django",
    "fastapi",
    "flask",
    "spring",
    ".net",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "pandas",
    "numpy",
    "llm",
    "rag",
    "nlp",
    "computer vision",
    "machine learning",
    "deep learning",
    "mlops",
    "langchain",
    "power bi",
    "tableau",
    "excel",
    "sap",
    "jira",
    "ci/cd",
    "graphql",
    "microservices",
    "elasticsearch",
    "snowflake",
    "databricks",
    "hadoop",
    "ansible",
    "jenkins",
    "figma",
]


# Ambiguous short names need case-sensitive, programming-context patterns: the English
# word "go" and the letter "R" would otherwise match almost every posting.
_GO = re.compile(
    r"\bGo(?:lang)?\b(?=\s*(?:[,/)]|and|or|\(golang\)|developer|programming|microservices))|\bGolang\b"
)
_R = re.compile(
    r"(?:Python|SQL|SAS|Stata|MATLAB|Julia)\s*(?:,|/|and|or)\s*R\b|\bR\s*(?:,|/|and|or)\s*(?:Python|SQL|SAS|Stata|MATLAB)|\bR (?:programming|language|Studio)\b"
)


def extract_skills(text: str, vocab: list[str] = SKILL_VOCAB) -> list[str]:
    low = text.lower()
    found = []
    for skill in vocab:
        if skill == "golang":
            continue
        if re.search(rf"(?<![\w+#.]){re.escape(skill)}(?![\w+#])", low):
            found.append(skill)
    if _GO.search(text):
        found.append("go")
    if _R.search(text):
        found.append("r")
    if re.search(r"\bREST(?:ful)?\b", text):
        found.append("rest")
    return found


def detect_languages_required(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lvl = _level_from_text(text)
    if lvl != "none":
        out.append({"language": "German", "level": lvl})
    if re.search(
        r"(?:fluent|good|excellent|very good|sehr gute?|gute?|fließend\w*)[^.\n]{0,25}(?:english|englisch)",
        text,
        re.I,
    ):
        out.append(
            {
                "language": "English",
                "level": "fluent"
                if re.search(r"fluent|excellent|fließend|sehr gut", text, re.I)
                else "good",
            }
        )
    elif re.search(r"\benglish\b|\benglisch", text, re.I):
        out.append({"language": "English", "level": "unspecified"})
    return out


def rule_based_extract(text: str) -> dict[str, Any]:
    """Rule-only JobLens extraction (fake provider / offline). Input: title + text."""
    from jobscout.ingest.cleaning import detect_language

    title = text.strip().split("\n", 1)[0][:200]
    years = extract_min_years(text)
    langs = detect_languages_required(text)
    lang = detect_language(text)
    return {
        "title": title or "Untitled",
        "company": None,
        "locations": [],
        "remote_policy": remote_policy(text),
        "seniority": seniority(title, text, years),
        "min_years_experience": years,
        "required_skills": extract_skills(text),
        "nice_to_have_skills": [],
        "languages_required": langs,
        "english_sufficient": english_sufficient(langs, text, lang),
        "contract_type": "internship"
        if re.search(r"intern|praktik", title, re.I)
        else "working_student"
        if re.search(r"werkstudent|working student", title, re.I)
        else "fixed_term"
        if re.search(r"befristet|fixed[- ]term", text, re.I)
        and not re.search(r"unbefristet", text, re.I)
        else "permanent"
        if re.search(r"unbefristet|permanent|festanstellung", text, re.I)
        else "unknown",
        "contract_duration_months": None,
        "salary": extract_salary(text),
        "visa_sponsorship": mention(text, VISA_YES, VISA_NO),
        "relocation_support": mention(text, RELOC_YES, RELOC_NO),
        "application_deadline": None,
        "posting_language": lang,
    }


def rule_based_profile(text: str) -> dict[str, Any]:
    """Rule-only CV profile (fake provider). See jobscout.cv.profile.CandidateProfile."""
    years = None
    m = re.search(r"(\d{1,2})\+?\s+years", text, re.I)
    if m:
        years = int(m.group(1))
    cities = [
        c
        for c in ("Berlin", "Munich", "München", "Hamburg", "Frankfurt", "Köln", "Stuttgart")
        if c.lower() in text.lower()
    ]
    langs = []
    gl = _level_from_text(text.replace("I speak", "").replace("my", ""))
    if re.search(r"german|deutsch", text, re.I):
        lvl = (
            "basic"
            if re.search(
                r"(a1|a2|b1|basic)[^.\n]{0,15}german|german[^.\n]{0,15}(a1|a2|b1|basic)", text, re.I
            )
            else gl
            if gl != "none"
            else "unspecified"
        )
        langs.append({"language": "German", "level": lvl})
    if re.search(r"english", text, re.I):
        langs.append({"language": "English", "level": "fluent"})
    return {
        "headline": text.strip().split("\n", 1)[0][:120],
        "skills": extract_skills(text),
        "years_experience": years,
        "languages": langs,
        "education": [],
        "preferred_cities": cities,
        "target_roles": [],
    }
