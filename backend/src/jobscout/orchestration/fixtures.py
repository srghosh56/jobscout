"""Fictional API payloads for both job boards, served in-process (``ingest_mode=fixtures``).

Used by the orchestration tests and the Compose/kind demo runs: the real fetchers and
the real cleaning, upsert and dedup code run, but no request leaves the process and no
real posting text is involved. Every employer and contact detail is fictional (e-mail
addresses use the reserved ``.example`` domain). The contact details exist so the snapshot
masking and its privacy check have something to find.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from jobscout.ingest.sources import (
    ARBEITNOW_URL,
    BA_DETAILS,
    BA_SEARCH,
    DEFAULT_BA_CITIES,
    DEFAULT_BA_QUERIES,
)

ML_TEXT = (
    "We are a fictional applied research company building retrieval augmented generation "
    "systems for public sector documents. As Machine Learning Engineer you design embedding "
    "pipelines, evaluate retrieval quality with offline benchmarks and ship models to "
    "production with Python, PyTorch and PostgreSQL with pgvector. Our team of twelve works "
    "in English and German is not required for this role. You bring at least three years of "
    "experience with machine learning in production, solid software engineering habits and "
    "curiosity about evaluation. We offer visa sponsorship and relocation support for "
    "candidates moving to Germany. Salary: 70,000 - 85,000 EUR per year, thirty days of "
    "holiday and a hybrid setup with two office days per week in Munich."
)

BA_JOBS: list[dict[str, Any]] = [
    {
        "refnr": "10000-1000000001-S",
        "title": "Data Engineer (m/w/d)",
        "company": "Fiktiv Daten GmbH",
        "city": "Berlin",
        "published": "2026-11-16",
        "text": (
            "Die Fiktiv Daten GmbH ist ein erfundenes Unternehmen für Datenplattformen. Ihre "
            "Aufgaben: Aufbau und Betrieb von Datenpipelines mit Python, SQL und Apache "
            "Airflow, Modellierung von Datenmodellen in PostgreSQL und Pflege der "
            "Datenqualitätsprüfungen. Ihr Profil: abgeschlossenes Studium der Informatik oder "
            "vergleichbare Ausbildung, mindestens zwei Jahre Berufserfahrung, sehr gute "
            "Deutschkenntnisse und gute Englischkenntnisse. Wir bieten eine unbefristete "
            "Stelle, flexible Arbeitszeiten und Weiterbildung. Ihre Bewerbung senden Sie bitte "
            "an bewerbung@fiktiv-daten.example oder rufen Sie uns an unter Tel. +49 30 23125001."
        ),
    },
    {
        "refnr": "10000-1000000002-S",
        "title": "Machine Learning Engineer (m/w/d)",
        "company": "Beispiel KI AG",
        "city": "München",
        "published": "2026-11-15",
        "text": ML_TEXT,
    },
    {
        "refnr": "10000-1000000003-S",
        "title": "Softwareentwickler Java (m/w/d)",
        "company": "Muster Software GmbH",
        "city": "Hamburg",
        "published": "2026-11-14",
        "text": (
            "Die Muster Software GmbH ist ein erfundener Anbieter von Logistiksoftware in "
            "Hamburg. Ihre Aufgaben: Entwicklung von Webanwendungen mit Java, Spring Boot und "
            "Kafka, Code Reviews im Team und Betrieb der Anwendungen in Kubernetes. Ihr Profil: "
            "mindestens fünf Jahre Berufserfahrung in der Softwareentwicklung, fließende "
            "Deutschkenntnisse auf C1 Niveau und gute Englischkenntnisse. Wir bieten ein "
            "Gehalt von 65.000 bis 80.000 Euro im Jahr, betriebliche Altersvorsorge und "
            "dreißig Urlaubstage. Fragen beantwortet Frau Beispiel unter 040 / 23125002."
        ),
    },
]

ARBEITNOW_JOBS: list[dict[str, Any]] = [
    {
        "slug": "machine-learning-engineer-beispiel-ki-munich-1",
        "company_name": "Beispiel KI AG",
        "title": "Machine Learning Engineer",
        "location": "Munich",
        "remote": False,
        "created_at": 1763337600,  # 2026-11-17
        "description": f"<p>{ML_TEXT}</p>",
    },
    {
        "slug": "backend-developer-python-fictional-cloud-berlin-2",
        "company_name": "Fictional Cloud GmbH",
        "title": "Backend Developer (Python)",
        "location": "Berlin",
        "remote": True,
        "created_at": 1763251200,  # 2026-11-16
        "description": (
            "<p>Fictional Cloud GmbH is an invented start-up building a scheduling platform "
            "for clinics. You will design and run backend services in Python with FastAPI, "
            "PostgreSQL and Redis, own the CI pipeline and help with on-call. The team works "
            "fully in English, German is a plus but not required. You have three or more years "
            "of backend experience and care about testing and observability. We offer "
            "60,000 - 75,000 EUR per year, a remote-first setup within Germany and a yearly "
            "learning budget. Questions about the role: jobs@fictional-cloud.example.</p>"
        ),
    },
    {
        "slug": "frontend-developer-example-web-cologne-3",
        "company_name": "Example Web GmbH",
        "title": "Frontend Developer (React)",
        "location": "Cologne",
        "remote": False,
        "created_at": 1763164800,  # 2026-11-15
        "description": (
            "<p>Example Web GmbH is an invented agency in Cologne that builds accessible "
            "websites for cultural institutions. You will build user interfaces with React and "
            "TypeScript, work closely with designers and write component tests. Our working "
            "language is English and the team is international. You have two years of "
            "frontend experience and an eye for accessibility. We offer a permanent contract, "
            "a hybrid setup with one office day per week and a modern office near the "
            "cathedral. We do not state a salary range in this fictional advertisement.</p>"
        ),
    },
]


ARBEITNOW_JOB_URL = "https://www.arbeitnow.com/jobs/companies/fixture/"


def _ba_hit(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "referenznummer": job["refnr"],
        "stellenangebotsTitel": job["title"],
        "firma": job["company"],
        "stellenlokationen": [{"adresse": {"ort": job["city"], "land": "DEUTSCHLAND"}}],
        "veroeffentlichungszeitraum": {"von": job["published"]},
        "homeofficemoeglich": False,
    }


def _ba_detail(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "stellenangebotsTitel": job["title"],
        "stellenangebotsBeschreibung": job["text"],
        "firma": job["company"],
        "stellenlokationen": [{"adresse": {"ort": job["city"], "land": "DEUTSCHLAND"}}],
        "referenznummer": job["refnr"],
    }


def _json(data: Any) -> httpx.Response:
    return httpx.Response(
        200, content=json.dumps(data).encode(), headers={"content-type": "application/json"}
    )


def handle(request: httpx.Request) -> httpx.Response:
    """Answer the requests the real fetchers make, from the fictional payloads above."""
    url = str(request.url).split("?", 1)[0]
    params = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
    if url == BA_SEARCH:
        first = (
            params.get("was") == DEFAULT_BA_QUERIES[0]
            and params.get("wo") == DEFAULT_BA_CITIES[0]
            and params.get("page") == "1"
        )
        hits = [_ba_hit(j) for j in BA_JOBS] if first else []
        return _json({"ergebnisliste": hits, "maxErgebnisse": len(hits)})
    details_prefix = BA_DETAILS.format(code="")
    if url.startswith(details_prefix):
        refnr = base64.b64decode(url.removeprefix(details_prefix)).decode()
        for job in BA_JOBS:
            if job["refnr"] == refnr:
                return _json(_ba_detail(job))
        return httpx.Response(404)
    if url == ARBEITNOW_URL:
        jobs = ARBEITNOW_JOBS if params.get("page") == "1" else []
        no_tags: list[str] = []
        data = [
            {**j, "url": f"{ARBEITNOW_JOB_URL}{j['slug']}", "tags": no_tags, "job_types": no_tags}
            for j in jobs
        ]
        return _json({"data": data, "links": {"next": None}})
    if url.startswith(ARBEITNOW_JOB_URL):  # expiry checks: every fixture job is online
        return httpx.Response(200, request=request)
    return httpx.Response(404)


def fixture_transport() -> httpx.MockTransport:
    return httpx.MockTransport(handle)
