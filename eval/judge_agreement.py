"""Judge calibration.

  python eval/judge_agreement.py export  → eval/labels/judge_check.csv with 20 answers from the
                                           latest full run and an EMPTY my_label column.
  python eval/judge_agreement.py score   → agreement + Cohen's kappa between the judge and your
                                           labels (works once my_label is filled with pass/fail).

Binarisation: judge "pass" = faithfulness >= 4 and relevance >= 4 (or a correct refusal).
"""

from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

from jobscout.evaluation.metrics import cohens_kappa

ROOT = Path(__file__).resolve().parent
CSV = ROOT / "labels" / "judge_check.csv"
FIELDS = [
    "id",
    "category",
    "question",
    "answer",
    "judge_faithfulness",
    "judge_relevance",
    "judge_refused",
    "judge_label",
    "my_label",
    "my_notes",
]


def judge_label(j: dict) -> str:
    if j.get("faithfulness") is None:
        return "invalid"
    return "pass" if j["faithfulness"] >= 4 and j["relevance"] >= 4 else "fail"


def latest_full_run() -> Path:
    runs = sorted((ROOT / "runs").glob("full_v*.jsonl"))
    if not runs:
        sys.exit("no full run found: run `make eval-full` first")
    return runs[-1]


def export(n: int = 20, seed: int = 7) -> None:
    rows = [json.loads(line) for line in latest_full_run().read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r.get("judge")]
    random.Random(seed).shuffle(rows)  # noqa: S311 - sampling, not security
    picked = sorted(rows[:n], key=lambda r: r["id"])
    CSV.parent.mkdir(exist_ok=True)
    with CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in picked:
            j = r["judge"]
            w.writerow(
                {
                    "id": r["id"],
                    "category": r["category"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "judge_faithfulness": j.get("faithfulness"),
                    "judge_relevance": j.get("relevance"),
                    "judge_refused": j.get("refused"),
                    "judge_label": judge_label(j),
                    "my_label": "",
                    "my_notes": "",
                }
            )
    print(
        f"wrote {len(picked)} rows to {CSV}. TODO(owner): fill my_label with pass/fail, then run `make judge-agreement`."
    )


def score() -> None:
    with CSV.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["my_label"].strip().lower() in ("pass", "fail")]
    if not rows:
        print("no labels yet: fill the my_label column (pass/fail) in eval/labels/judge_check.csv")
        return
    judge = [r["judge_label"] for r in rows]
    mine = [r["my_label"].strip().lower() for r in rows]
    agreement = sum(a == b for a, b in zip(judge, mine, strict=True)) / len(rows)
    out = {
        "n_labelled": len(rows),
        "agreement": round(agreement, 3),
        "cohens_kappa": round(cohens_kappa(judge, mine), 3),
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "judge_agreement.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    {"export": export, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "score"]()
