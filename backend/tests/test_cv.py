from __future__ import annotations

import pytest

from jobscout.cv.parse import CvParseError, extract_text, scrub_pii
from jobscout.cv.profile import extract_profile
from tests.fixtures.pdf import make_pdf

SYNTHETIC_CV = [
    "Alex Example - Data Scientist",  # fictional person
    "alex.example@mail.test +49 151 2345678 linkedin.com/in/alex-example",
    "5 years of experience with Python, SQL, PyTorch and RAG systems.",
    "Languages: English fluent, German A2 (basic).",
    "MSc Computer Science. Looking for roles in Berlin.",
]


def test_pdf_and_text_parsing() -> None:
    txt = extract_text("cv.pdf", make_pdf(SYNTHETIC_CV), "application/pdf")
    assert "PyTorch" in txt and "German A2" in txt
    assert "Python" in extract_text("cv.txt", "\n".join(SYNTHETIC_CV).encode(), "text/plain")
    assert extract_text("cv.txt", ("Käse " * 20).encode("latin-1"), None).startswith("Käse")


@pytest.mark.parametrize(
    ("name", "data", "msg"),
    [
        ("big.txt", b"x" * (3 * 1024 * 1024), "too large"),
        ("empty.txt", b"hi", "enough text"),
        ("broken.pdf", b"%PDF-1.4 garbage", "PDF"),
    ],
)
def test_parse_errors(name: str, data: bytes, msg: str) -> None:
    with pytest.raises(CvParseError, match=msg):
        extract_text(name, data, None)


def test_scrub_pii() -> None:
    s = scrub_pii(SYNTHETIC_CV[1])
    assert "@" not in s and "2345678" not in s and "linkedin" not in s
    assert s.count("[email]") == 1 and "[phone]" in s and "[url]" in s


async def test_profile_extraction_fake_model_and_no_cv_in_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from jobscout.logging import configure_logging

    configure_logging("INFO", json=True)
    marker = "ZEBRA-QUOKKA-7731"  # unique string that must never appear in logs
    cv = "\n".join(SYNTHETIC_CV) + f"\nProject codename {marker}."
    profile = await extract_profile(cv)
    assert {"python", "sql", "pytorch", "rag"} <= {s.lower() for s in profile.skills}
    assert profile.years_experience == 5 and "Berlin" in profile.preferred_cities
    assert any(lang.language == "German" and lang.level == "basic" for lang in profile.languages)
    captured = capsys.readouterr()
    logs = captured.out + captured.err
    assert "cv_profile_extracted" in logs
    assert marker not in logs and "alex.example" not in logs and "PyTorch" not in logs


async def test_profile_reask_then_fail() -> None:
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    class Bad:
        def with_structured_output(self, *a: object, **k: object) -> RunnableLambda[object, object]:
            return RunnableLambda(
                lambda _m: {
                    "raw": AIMessage("{}"),
                    "parsed": {"years_experience": -3},
                    "parsing_error": None,
                }
            )

    with pytest.raises(ValueError, match="could not extract"):
        await extract_profile("x" * 100, model=Bad())  # type: ignore[arg-type]


def test_redaction_processor_drops_sensitive_keys() -> None:
    from jobscout.logging import redact_processor

    ev = redact_processor(None, "info", {"event": "x", "cv_text": "secret", "api_key": "k", "n": 1})
    assert ev["cv_text"] == "[REDACTED]" and ev["api_key"] == "[REDACTED]" and ev["n"] == 1


@pytest.mark.db
async def test_opt_in_store_load_delete(clean_db: None) -> None:
    from sqlalchemy import func, select, text

    from jobscout.cv.store import delete_profile, load_profile, save_profile
    from jobscout.db.engine import session_scope
    from jobscout.db.models import CvProfile
    from jobscout.tools.schemas import CandidateProfile

    p = CandidateProfile(headline="Data scientist", skills=["Python"])
    pid = await save_profile(p, None)
    assert (await load_profile(pid, None)) == p
    async with session_scope() as s:
        stored = (await s.execute(text("SELECT profile::text FROM cv_profiles"))).scalar_one()
    assert "cv_text" not in stored and "raw" not in stored
    assert await delete_profile(pid, None) is True
    assert await delete_profile(pid, None) is False
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(CvProfile))).scalar_one() == 0
