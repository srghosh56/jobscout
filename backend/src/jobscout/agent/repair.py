"""Repairs tool arguments that a small model gets wrong, so a good plan is not lost.

Small models often choose the right tool but fill it badly: placeholder text instead of a
posting ID, an invented ID, or a CV profile in the wrong shape. Posting IDs are taken from
earlier results of the same turn, and a broken profile is rebuilt from the question.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from jobscout.agent.citations import ID_RE
from jobscout.cv.profile import extract_profile
from jobscout.logging import get_logger
from jobscout.tools.schemas import CandidateProfile

log = get_logger(__name__)

SINGLE_ID_TOOLS = {"get_posting", "gap_analysis", "bluecard_check"}
PROFILE_TOOLS = {"match_cv", "gap_analysis"}
# These run after the other tools of a step, so they can use the IDs those tools return.
DEPENDENT_TOOLS = SINGLE_ID_TOOLS | PROFILE_TOOLS | {"compare_postings"}


def ordered_ids(payloads: Sequence[Any]) -> list[str]:
    """Posting IDs in the order they appear, without duplicates."""
    return list(dict.fromkeys(ID_RE.findall(json.dumps(payloads, default=str))))


def repair_ids(name: str, args: dict[str, Any], known: list[str], question: str) -> dict[str, Any]:
    """Replaces IDs that did not come from a result or from the user's question."""
    trusted = {*known, *ID_RE.findall(question)}
    fixed = dict(args)
    salary_only = name == "bluecard_check" and "posting_id" not in fixed
    has_bad_id = fixed.get("posting_id") not in trusted
    if name in SINGLE_ID_TOOLS and known and has_bad_id and not salary_only:
        fixed["posting_id"] = known[0]
    if name == "compare_postings":
        given = fixed.get("posting_ids")
        chosen = [i for i in given if i in trusted] if isinstance(given, list) else []
        for candidate in known:
            if len(chosen) >= 2:
                break
            if candidate not in chosen:
                chosen.append(candidate)
        if len(chosen) >= 2:
            fixed["posting_ids"] = chosen
    return fixed


async def repair_profile(args: dict[str, Any], question: str) -> dict[str, Any]:
    """Rebuilds an invalid CV profile from the user's question; keeps a valid one."""
    try:
        CandidateProfile.model_validate(args.get("profile"))
        return args
    except ValidationError:
        pass
    try:
        profile = await extract_profile(question)
    except Exception as exc:
        log.warning("profile_repair_failed", error=type(exc).__name__)
        return args
    return {**args, "profile": profile.model_dump(mode="json")}


async def repair_args(
    name: str, args: dict[str, Any], known: list[str], question: str
) -> dict[str, Any]:
    fixed = repair_ids(name, args, known, question)
    if name in PROFILE_TOOLS:
        fixed = await repair_profile(fixed, question)
    return fixed
