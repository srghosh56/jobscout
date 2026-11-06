"""Golden-set review state: a person's approval survives a rebuild of the answer key."""

from __future__ import annotations

from typing import Any


def carry_approvals(new: list[dict[str, Any]], old: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep ``approved`` when the question and its logic are unchanged. Expected answers may
    move with the data, because the approval covers the question and logic, not one number."""
    approved = {
        q["id"]: (q["question"], q.get("reference_sql")) for q in old if q.get("approved") is True
    }
    return [
        {**q, "approved": True}
        if approved.get(q["id"]) == (q["question"], q.get("reference_sql"))
        else q
        for q in new
    ]
