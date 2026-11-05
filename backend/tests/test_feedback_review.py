from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import select

from jobscout.db.engine import session_scope
from jobscout.db.models import Feedback
from jobscout.feedback_review import review

pytestmark = pytest.mark.db


async def test_list_approve_reject(
    clean_db: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        yaml.safe_dump({"version": 1, "questions": [{"id": "sql-01", "question": "x"}]})
    )
    async with session_scope() as s:
        s.add_all(
            [
                Feedback(
                    question="Welche Stellen gibt es in Köln?",
                    answer="wrong",
                    rating=-1,
                    comment="falsche Stadt",
                    trace_id="abc",
                ),
                Feedback(question="ok?", answer="fine", rating=1),
                Feedback(question="spam", answer="a", rating=-1),
            ]
        )
        await s.commit()
        ids = [f.id for f in (await s.execute(select(Feedback).order_by(Feedback.id))).scalars()]
    r = await review(path=golden)
    assert r["pending"] == 2 and "trace=abc" in capsys.readouterr().out
    r = await review(approve=[ids[0]], reject=[ids[2]], path=golden)
    assert r["pending"] == 0
    data = yaml.safe_load(golden.read_text())
    assert data["version"] == 2 and data["questions"][-1]["id"] == f"fb-{ids[0]:04d}"
    assert data["questions"][-1]["lang"] == "de"
    assert data["questions"][-1]["feedback"]["trace_id"] == "abc"
    await review(approve=[ids[0]], path=golden)  # idempotent: no duplicate question
    assert (
        sum(q["id"].startswith("fb-") for q in yaml.safe_load(golden.read_text())["questions"]) == 1
    )
