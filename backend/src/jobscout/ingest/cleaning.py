"""Cleaning and normalisation of raw postings (pure functions, unit-tested)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

BOILERPLATE_PATTERNS = [
    re.compile(r"find more english speaking jobs in germany on arbeitnow.*$", re.I | re.S),
]

_WS = re.compile(r"[ \t ]+")
_NL = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """HTML → readable plain text, keeping paragraph/list breaks."""
    if not html:
        return ""
    if "<" not in html:
        return normalize_whitespace(html)
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    for tag in tree.css("br"):
        tag.replace_with("\n")
    for tag in tree.css("p, div, li, h1, h2, h3, h4, h5, h6, tr, ul, ol"):
        tag.insert_after("\n")
    for tag in tree.css("li"):
        tag.insert_before("- ")
    root = tree.body or tree.root
    text = root.text(separator="", strip=False) if root is not None else ""
    return normalize_whitespace(text)


def normalize_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return _NL.sub("\n\n", "\n".join(lines)).strip()


def strip_boilerplate(text: str) -> str:
    for pat in BOILERPLATE_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


_DE_WORDS = frozenset(
    [
        "und",
        "der",
        "die",
        "das",
        "mit",
        "für",
        "wir",
        "sie",
        "ihr",
        "ihre",
        "sind",
        "eine",
        "einen",
        "bei",
        "auf",
        "von",
        "zu",
        "im",
        "ist",
        "oder",
        "werden",
        "wird",
        "kenntnisse",
        "erfahrung",
        "aufgaben",
        "unser",
        "unsere",
        "bieten",
        "dich",
        "du",
        "deine",
    ]
)
_EN_WORDS = frozenset(
    [
        "and",
        "the",
        "with",
        "for",
        "we",
        "you",
        "your",
        "our",
        "are",
        "an",
        "a",
        "at",
        "on",
        "of",
        "to",
        "in",
        "is",
        "or",
        "will",
        "experience",
        "skills",
        "team",
        "work",
        "our",
        "offer",
        "responsibilities",
        "requirements",
    ]
)


def detect_language(text: str) -> str:
    """Stop-word vote between German and English ('de' | 'en')."""
    words = re.findall(r"[a-zäöüß]+", text.lower()[:5000])
    de = sum(w in _DE_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    return "de" if de > en else "en"


CITY_ALIASES = {
    "munich": "München",
    "muenchen": "München",
    "münchen": "München",
    "cologne": "Köln",
    "koeln": "Köln",
    "köln": "Köln",
    "frankfurt": "Frankfurt am Main",
    "frankfurt am main": "Frankfurt am Main",
    "frankfurt (main)": "Frankfurt am Main",
    "nuremberg": "Nürnberg",
    "nürnberg": "Nürnberg",
    "dusseldorf": "Düsseldorf",
    "duesseldorf": "Düsseldorf",
    "düsseldorf": "Düsseldorf",
    "hanover": "Hannover",
    "berlin": "Berlin",
    "hamburg": "Hamburg",
    "stuttgart": "Stuttgart",
    "leipzig": "Leipzig",
    "dresden": "Dresden",
    "karlsruhe": "Karlsruhe",
    "bonn": "Bonn",
    "essen": "Essen",
    "dortmund": "Dortmund",
    "bremen": "Bremen",
    "mannheim": "Mannheim",
    "heidelberg": "Heidelberg",
    "darmstadt": "Darmstadt",
    "potsdam": "Potsdam",
    "aachen": "Aachen",
}


def normalize_city(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"\s*\(.*?\)\s*$", "", s) if s.lower() not in CITY_ALIASES else s
    s = re.sub(r"^\d{5}\s+", "", s)  # leading postcode
    s = s.split(",")[0].strip()
    key = s.lower()
    if key in ("germany", "deutschland", "remote", "homeoffice", "home office"):
        return None
    if key in CITY_ALIASES:
        return CITY_ALIASES[key]
    for alias, canon in CITY_ALIASES.items():
        if key.startswith(alias + " ") or key.startswith(alias + "-"):
            return canon
    return s or None


FOREIGN = {
    "France": "france paris lyon toulouse bordeaux montpellier nantes lille marseille nice rennes strasbourg grenoble arrondissement la défense",
    "United Kingdom": "united kingdom uk england scotland london manchester cambridge oxford edinburgh bristol birmingham leeds glasgow belfast reading",
    "Netherlands": "netherlands holland amsterdam utrecht rotterdam delft veenendaal de lier veghel eindhoven the hague den haag leiden groningen",
    "Switzerland": "switzerland schweiz suisse zürich zurich bern geneva genève genf lucerne luzern basel winterthur lausanne zug",
    "Austria": "austria österreich wien vienna graz linz salzburg innsbruck",
    "Other": "spain madrid barcelona portugal lisbon lisboa porto ireland dublin belgium brussels bruxelles antwerp italy milan milano rome poland warsaw krakow kraków czech prague praha sweden stockholm denmark copenhagen luxembourg usa united states new york san francisco canada toronto india bangalore singapore dubai",
}
_FOREIGN_RE = {
    country: re.compile(
        r"\b(?:"
        + "|".join(
            re.escape(w)
            for w in sorted(set(words.split()) | {words.split()[0]}, key=len, reverse=True)
        )
        + r")\b",
        re.I,
    )
    for country, words in FOREIGN.items()
}
_MULTIWORD = [
    ("United Kingdom", "united kingdom"),
    ("Netherlands", "de lier"),
    ("Netherlands", "the hague"),
    ("France", "la défense"),
    ("Other", "new york"),
    ("Other", "san francisco"),
    ("Other", "united states"),
]


# Arbeitnow runs one site per country. Every posting on these sites states the same country
# in its text, while arbeitnow.com never states a foreign one.
ARBEITNOW_HOME_SITES = ("arbeitnow.com", "arbeitnow.de")
ARBEITNOW_SITES = {
    "arbeitnow.co.uk": "United Kingdom",
    "arbeitnow.fr": "France",
    "arbeitnow.ch": "Switzerland",
    "arbeitnow.nl": "Netherlands",
}


def arbeitnow_site_country(url: str) -> str | None:
    """The foreign country whose Arbeitnow site lists this job, else None."""
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    if not host.startswith("arbeitnow.") or host in ARBEITNOW_HOME_SITES:
        return None
    return ARBEITNOW_SITES.get(host, "Other")


def classify_country(location: str | None) -> str | None:
    """'Germany' for German or German-sounding locations, a country name for known
    foreign ones, None when unknown (e.g. 'Remote')."""
    if not location:
        return None
    low = location.lower()
    if re.search(r"germany|deutschland", low):
        return "Germany"
    for country, phrase in _MULTIWORD:
        if phrase in low:
            return country
    if normalize_city(location) in set(CITY_ALIASES.values()):
        return "Germany"
    for country, pat in _FOREIGN_RE.items():
        if pat.search(low):
            return country
    if any(re.search(rf"\b{re.escape(alias)}\b", low) for alias in CITY_ALIASES):
        return "Germany"
    if re.search(r"remote|anywhere|homeoffice|home office", low):
        return None
    return "Germany"


JOB_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "data_ml",
        re.compile(
            r"machine learning|\bml\b|\bai\b|\bki\b|künstliche intelligenz|data scien|"
            r"deep learning|\bnlp\b|computer vision|\bllm\b|mlops|data analy|datenanaly|"
            r"data engineer|analytics|business intelligence|\bbi\b",
            re.I,
        ),
    ),
    (
        "devops_cloud",
        re.compile(
            r"devops|cloud|\bsre\b|site reliability|platform engineer|kubernetes|infra", re.I
        ),
    ),
    ("security", re.compile(r"security|sicherheit|\bsoc\b|pentest|cyber", re.I)),
    (
        "software_engineering",
        re.compile(
            r"software|developer|entwickler|engineer|programmier|backend|frontend|full.?stack|"
            r"\bjava\b|python|\.net|web|mobile|\bios\b|android|embedded",
            re.I,
        ),
    ),
    (
        "product_design",
        re.compile(r"product (manager|owner)|produktmanag|ux|ui designer|design", re.I),
    ),
    (
        "engineering_hw",
        re.compile(r"ingenieur|mechanical|elektro|electrical|maschinenbau|hardware", re.I),
    ),
    (
        "sales_marketing",
        re.compile(r"sales|vertrieb|marketing|account manager|business develop", re.I),
    ),
]


def classify_job_family(title: str) -> str:
    for name, pattern in JOB_FAMILIES:
        if pattern.search(title):
            return name
    return "other"


def content_hash(*parts: str | None) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def posting_id(source: str, source_id: str) -> str:
    prefix = {"arbeitsagentur": "ba", "arbeitnow": "an"}.get(source, source[:2])
    return f"{prefix}-{hashlib.sha1(source_id.encode()).hexdigest()[:10]}"  # noqa: S324


@dataclass
class CleanPosting:
    """A normalised posting ready for upsert (before LLM extraction)."""

    source: str
    source_id: str
    url: str
    title: str
    company: str | None
    raw_text: str
    city: str | None
    cities: list[str] = field(default_factory=list)
    country: str | None = "Germany"
    published_at: date | None = None
    source_remote_flag: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return posting_id(self.source, self.source_id)

    @property
    def posting_language(self) -> str:
        return detect_language(self.title + "\n" + self.raw_text)

    @property
    def job_family(self) -> str:
        return classify_job_family(self.title)

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.company, self.raw_text, ",".join(self.cities))
