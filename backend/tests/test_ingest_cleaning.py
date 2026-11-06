from __future__ import annotations

import pytest

from jobscout.ingest import cleaning as c
from jobscout.ingest.dedup import DocForDedup, find_duplicates, shingles


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.arbeitnow.com/jobs/companies/acme/engineer-berlin-1", None),
        ("https://www.arbeitnow.co.uk/jobs/companies/acme/manager-cardiff-2", "United Kingdom"),
        ("https://www.arbeitnow.fr/jobs/companies/acme/ingenieur-lyon-3", "France"),
        ("https://www.arbeitnow.ch/jobs/companies/acme/engineer-nyon-4", "Switzerland"),
        ("https://www.arbeitnow.nl/jobs/companies/acme/engineer-assen-5", "Netherlands"),
        ("https://www.arbeitnow.es/jobs/companies/acme/engineer-madrid-6", "Other"),
        ("https://example.com/jobs/engineer-7", None),
        ("", None),
    ],
)
def test_arbeitnow_site_country(url: str, expected: str | None) -> None:
    assert c.arbeitnow_site_country(url) == expected


def test_html_to_text_keeps_structure_and_drops_scripts() -> None:
    html = "<h2>Join</h2><p>We&nbsp;build</p><ul><li>Python</li></ul><script>evil()</script>"
    assert c.html_to_text(html) == "Join\nWe build\n- Python"


def test_html_to_text_plain_and_empty() -> None:
    assert c.html_to_text("") == ""
    assert c.html_to_text("a   b\r\n\n\n\nc") == "a b\n\nc"


def test_strip_boilerplate() -> None:
    assert (
        c.strip_boilerplate("Job text. Find more English Speaking Jobs in Germany on Arbeitnow")
        == "Job text."
    )


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        (
            "Wir suchen einen Entwickler mit Erfahrung und sehr guten Kenntnissen für unser Team",
            "de",
        ),
        ("We are looking for an engineer with experience and skills for our team", "en"),
    ],
)
def test_detect_language(text: str, lang: str) -> None:
    assert c.detect_language(text) == lang


@pytest.mark.parametrize(
    ("raw", "city"),
    [
        ("Munich", "München"),
        ("80331 München", "München"),
        ("Frankfurt", "Frankfurt am Main"),
        ("Berlin, Germany", "Berlin"),
        ("Köln (Innenstadt)", "Köln"),
        ("Hamburg-Altona", "Hamburg"),
        ("Kleinstadt", "Kleinstadt"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_city(raw: str | None, city: str | None) -> None:
    assert c.normalize_city(raw) == city


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Senior Machine Learning Engineer", "data_ml"),
        ("Data Scientist (m/w/d)", "data_ml"),
        ("DevOps Engineer", "devops_cloud"),
        ("IT Security Analyst", "security"),
        ("Backend Developer Java", "software_engineering"),
        ("Product Owner", "product_design"),
        ("Maschinenbauingenieur", "engineering_hw"),
        ("Vertriebsmitarbeiter", "sales_marketing"),
        ("Koch", "other"),
    ],
)
def test_job_family(title: str, family: str) -> None:
    assert c.classify_job_family(title) == family


def test_ids_and_hash_are_stable() -> None:
    assert c.posting_id("arbeitsagentur", "X-1") == c.posting_id("arbeitsagentur", "X-1")
    assert c.posting_id("arbeitsagentur", "X-1").startswith("ba-")
    assert c.posting_id("arbeitnow", "slug").startswith("an-")
    assert c.content_hash("a", "b") != c.content_hash("ab", "")


def test_minhash_finds_near_duplicates_across_sources() -> None:
    base = " ".join(f"word{i}" for i in range(200))
    docs = [
        DocForDedup("ba-1", base, "2026-10-01"),
        DocForDedup("an-1", base + " extra tail", "2026-10-02"),
        DocForDedup("an-2", " ".join(f"other{i}" for i in range(200)), "2026-10-03"),
    ]
    assert find_duplicates(docs) == {"an-1": "ba-1"}


def test_shingles_short_text() -> None:
    assert shingles("a b") == {"a b"}
    assert shingles("") == set()


@pytest.mark.parametrize(
    ("loc", "country"),
    [
        ("Berlin", "Germany"),
        ("München", "Germany"),
        ("Germany", "Germany"),
        ("Parsdorf", "Germany"),
        ("Paris", "France"),
        ("9th arrondissement of Paris", "France"),
        ("London - JJ Mack", "United Kingdom"),
        ("United Kingdom", "United Kingdom"),
        ("Zürich", "Switzerland"),
        ("De Lier", "Netherlands"),
        ("Remote job", None),
        ("Anywhere in France", "France"),
        ("", None),
        ("Berlin/Remote", "Germany"),
    ],
)
def test_classify_country(loc: str, country: str | None) -> None:
    assert c.classify_country(loc) == country
