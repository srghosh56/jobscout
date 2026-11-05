"""Synthetic API payloads (fictional companies and texts, never real postings)."""

from __future__ import annotations

from typing import Any

LOREM_EN = (
    "We are looking for a Machine Learning Engineer to build retrieval-augmented generation "
    "(RAG) systems. You will work with Python, PyTorch and PostgreSQL in an English-speaking "
    "team. Requirements: 3+ years of experience, strong Python skills, experience with LLMs. "
    "German is a plus but not required. We offer visa sponsorship and relocation support. "
    "Salary: 70,000 - 85,000 EUR per year. "
)
LOREM_DE = (
    "Wir suchen einen Softwareentwickler (m/w/d) für unser Team in Hamburg. Ihre Aufgaben: "
    "Entwicklung von Webanwendungen mit Java und Spring. Ihr Profil: abgeschlossenes Studium, "
    "mindestens 5 Jahre Berufserfahrung, sehr gute Deutschkenntnisse (C1) und gute "
    "Englischkenntnisse. Wir bieten eine unbefristete Stelle und flexible Arbeitszeiten. "
)


def ba_search_page(refnrs: list[str]) -> dict[str, Any]:
    return {
        "ergebnisliste": [
            {
                "referenznummer": r,
                "stellenangebotsTitel": f"Softwareentwickler (m/w/d) {i}",
                "firma": "Beispiel Software GmbH",
                "stellenlokationen": [{"adresse": {"ort": "Hamburg", "land": "DEUTSCHLAND"}}],
                "veroeffentlichungszeitraum": {"von": "2026-10-20"},
                "homeofficemoeglich": True,
                "hauptberuf": "Softwareentwickler/in",
            }
            for i, r in enumerate(refnrs)
        ],
        "maxErgebnisse": len(refnrs),
        "page": 1,
        "size": 50,
    }


def ba_detail(refnr: str, text: str = LOREM_DE * 2) -> dict[str, Any]:
    return {
        "stellenangebotsTitel": "Softwareentwickler (m/w/d)",
        "stellenangebotsBeschreibung": text,
        "firma": "Beispiel Software GmbH",
        "stellenlokationen": [{"adresse": {"ort": "Hamburg", "land": "DEUTSCHLAND"}}],
        "vertragsdauer": "UNBEFRISTET",
        "referenznummer": refnr,
    }


def arbeitnow_page(n: int = 3, next_page: bool = False) -> dict[str, Any]:
    return {
        "data": [
            {
                "slug": f"ml-engineer-berlin-{i}",
                "company_name": f"Fictional AI {i} GmbH",
                "title": "Machine Learning Engineer (RAG)",
                "description": f"<p>{LOREM_EN}</p><ul><li>Python</li><li>RAG</li></ul>"
                "<p>Find more English Speaking Jobs in Germany on Arbeitnow</p>",
                "remote": i % 2 == 0,
                "url": f"https://www.arbeitnow.com/jobs/companies/fictional/ml-{i}",
                "tags": ["Machine Learning"],
                "job_types": ["full time"],
                "location": "Munich" if i % 2 else "Berlin",
                "created_at": 1793500000 + i,
            }
            for i in range(n)
        ],
        "links": {
            "next": "https://www.arbeitnow.com/api/job-board-api?page=2" if next_page else None
        },
        "meta": {"terms": "synthetic"},
    }
