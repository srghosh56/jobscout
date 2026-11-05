"""Single-shot routing accuracy on golden questions, which are not in the route examples.

Search-only set: semantic golden questions with no structured filters.
Needs-agent set: SQL, multi-step and CV questions, plus semantic questions with filters.
Writes eval/results/route_bench.json.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

from jobscout.agent.route import is_search_only

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    golden = yaml.safe_load((ROOT / "golden.yaml").read_text(encoding="utf-8"))["questions"]
    in_scope = [q for q in golden if q["category"] not in ("out_of_scope", "adversarial")]
    search_only = [
        q["question"]
        for q in in_scope
        if q["category"] == "semantic" and not q.get("retrieval_filters")
    ]
    needs_agent = [q["question"] for q in in_scope if q["question"] not in search_only]
    missed = [q for q in search_only if not await is_search_only(q)]
    wrongly_shortened = [q for q in needs_agent if await is_search_only(q)]
    result = {
        "search_only_total": len(search_only),
        "search_only_found": len(search_only) - len(missed),
        "needs_agent_total": len(needs_agent),
        "needs_agent_kept": len(needs_agent) - len(wrongly_shortened),
        "missed": missed,
        "wrongly_shortened": wrongly_shortened,
    }
    (ROOT / "results" / "route_bench.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
