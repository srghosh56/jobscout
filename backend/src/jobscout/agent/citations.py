"""Citation extraction and checking: every [posting_id] in an answer must refer to a
posting that a tool actually returned in this session."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

ID_RE = re.compile(r"\b(?:ba|an)-[0-9a-f]{10}\b")
BRACKET_RE = re.compile(r"\[([^\[\]]{0,200})\]")


def cited_ids(answer: str) -> list[str]:
    """IDs that appear inside square brackets, in order of first appearance."""
    out: list[str] = []
    for group in BRACKET_RE.findall(answer):
        for pid in ID_RE.findall(group):
            if pid not in out:
                out.append(pid)
    return out


def ids_in(payload: Any) -> set[str]:
    """All posting IDs anywhere in a (JSON-like) tool result."""
    found: set[str] = set()
    if isinstance(payload, str):
        found.update(ID_RE.findall(payload))
    elif isinstance(payload, dict):
        for v in payload.values():
            found |= ids_in(v)
    elif isinstance(payload, list | tuple):
        for v in payload:
            found |= ids_in(v)
    return found


@dataclass(frozen=True)
class CitationCheck:
    cited: list[str]
    valid: list[str]
    invalid: list[str]

    @property
    def ok(self) -> bool:
        return not self.invalid


def check_citations(answer: str, retrieved: Iterable[str]) -> CitationCheck:
    allowed = set(retrieved)
    cited = cited_ids(answer)
    return CitationCheck(
        cited=cited,
        valid=[c for c in cited if c in allowed],
        invalid=[c for c in cited if c not in allowed],
    )


def strip_invalid(answer: str, invalid: Iterable[str]) -> str:
    """Replace unverifiable citations with a visible marker."""
    for pid in invalid:
        answer = answer.replace(pid, "unverified")
    return answer
