"""Argument repair: bad IDs and broken profiles from a small model are corrected."""

from __future__ import annotations

from typing import Any

import pytest

from jobscout.agent import repair
from jobscout.agent.repair import ordered_ids, repair_ids, repair_profile
from jobscout.tools.schemas import CandidateProfile

FIRST, SECOND = "an-0000000001", "ba-0000000002"


def test_ids_keep_their_order_without_duplicates() -> None:
    results = [{"results": [{"id": SECOND}, {"id": FIRST}]}, {"id": SECOND}]
    assert ordered_ids(results) == [SECOND, FIRST]


@pytest.mark.parametrize("bad", ["<posting_id>", "first posting ID", "an-3f9a1c2d7e", ""])
def test_a_bad_id_is_replaced_by_the_first_result(bad: str) -> None:
    fixed = repair_ids("bluecard_check", {"posting_id": bad}, [FIRST, SECOND], "check it")
    assert fixed["posting_id"] == FIRST


def test_a_retrieved_id_is_kept() -> None:
    fixed = repair_ids("get_posting", {"posting_id": SECOND}, [FIRST, SECOND], "show it")
    assert fixed["posting_id"] == SECOND


def test_an_id_from_the_question_is_kept() -> None:
    question = f"Does {SECOND} meet the threshold?"
    fixed = repair_ids("bluecard_check", {"posting_id": SECOND}, [FIRST], question)
    assert fixed["posting_id"] == SECOND


def test_a_blue_card_check_by_salary_gets_no_posting_id() -> None:
    args = {"salary_min": 60000}
    assert repair_ids("bluecard_check", args, [FIRST], "is 60k enough?") == args


def test_nothing_changes_without_earlier_results() -> None:
    args = {"posting_id": "<posting_id>"}
    assert repair_ids("get_posting", args, [], "show it") == args


def test_compare_is_filled_from_results() -> None:
    fixed = repair_ids("compare_postings", {"posting_ids": ["invented"]}, [FIRST, SECOND], "x")
    assert fixed["posting_ids"] == [FIRST, SECOND]


async def test_a_valid_profile_is_kept() -> None:
    args = {"profile": {"skills": ["python"]}}
    assert await repair_profile(args, "my cv") == args


async def test_a_broken_profile_is_rebuilt_from_the_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rebuilt(_question: str) -> CandidateProfile:
        return CandidateProfile(skills=["python"])

    monkeypatch.setattr(repair, "extract_profile", rebuilt)
    fixed = await repair_profile({"profile": {"education": "MSc"}}, "my cv")
    assert fixed["profile"]["skills"] == ["python"]


async def test_profile_repair_failure_keeps_the_original_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_question: str) -> Any:
        raise TimeoutError

    monkeypatch.setattr(repair, "extract_profile", broken)
    args = {"profile": {"education": "MSc"}}
    assert await repair_profile(args, "my cv") == args
