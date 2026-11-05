"""Regenerates the tables between the GENERATED markers in docs/EVALUATION.md from
eval/results/*.json, so every number in the doc comes from a script (make eval-report)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DOC = ROOT.parent / "docs" / "EVALUATION.md"
BEGIN, END = "<!-- BEGIN GENERATED (make eval-report) -->", "<!-- END GENERATED -->"


def load(name: str) -> dict[str, Any] | None:
    p = ROOT / "results" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "not measured"
    if isinstance(v, float):
        return f"{v * 100:.1f}%" if pct else f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


METRICS = [
    ("tool_selection_accuracy", "Tool-selection accuracy", True),
    ("sql_execution_accuracy", "SQL execution accuracy (result-set match)", True),
    ("recall@5", "Recall@5 (agent's first search)", False),
    ("mrr", "MRR (agent's first search)", False),
    ("citation_precision", "Citation precision (first draft)", True),
    ("refusal_recall_oos", "Refusal recall on out-of-scope/adversarial (judge)", True),
    ("over_refusal_rate", "Over-refusal on in-scope questions (judge)", True),
    ("refusal_correctness", "Refusal decision correct, all questions (judge)", True),
    ("judge_faithfulness", "Judge faithfulness (1-5, uncalibrated)", False),
    ("judge_relevance", "Judge relevance (1-5, uncalibrated)", False),
    ("latency_p50_s", "Latency p50 (s)", False),
    ("latency_p95_s", "Latency p95 (s)", False),
    ("tokens_per_question", "Tokens per question", False),
    ("cost_eur_per_question", "API cost per question (EUR)", False),
]


def build() -> str:
    parts: list[str] = []
    ds = load("dataset_stats")
    if ds:
        parts.append("### Dataset (`make dataset-stats`)")
        parts.append(
            table(
                ["Measure", "Value"],
                [
                    ["Unique postings (active)", f"{ds['unique_postings']} ({ds['active']})"],
                    ["Near-duplicates detected (MinHash)", ds["near_duplicates"]],
                    ["By source", ", ".join(f"{k}: {v}" for k, v in ds["by_source"].items())],
                    [
                        "By posting language",
                        ", ".join(f"{k}: {v}" for k, v in ds["by_posting_language"].items()),
                    ],
                    [
                        "LLM-extracted / rules-extracted / failed",
                        f"{ds['llm_extracted']} / {ds['rules_extracted']} / {ds['extraction_failed']}",
                    ],
                    ["Embedded", ds["embedded"]],
                ],
            )
        )
        agr = ds["rules_vs_llm_agreement"]
        parts.append(f"\nRules vs LLM field agreement on the {agr['n']} LLM-extracted postings:\n")
        parts.append(
            table(["Field", "Agreement"], [[k, fmt(v, True)] for k, v in agr.items() if k != "n"])
        )

    full = sorted((ROOT / "results").glob("full_v*.json"))
    smoke = load("smoke_recorded")
    cols: list[tuple[str, dict[str, Any]]] = []
    for f in full:
        d = json.loads(f.read_text())
        cols.append((f"{d['name']} ({d['model']}, n={d['metrics']['n']})", d["metrics"]))
    if smoke:
        cols.append(
            (
                f"smoke (synthetic corpus, {smoke['model']}, n={smoke['metrics']['n']})",
                smoke["metrics"],
            )
        )
    if cols:
        parts.append("\n### Agent evaluation (`make eval-full`, `make eval-record`)")
        parts.append(
            table(
                ["Metric", *[c for c, _ in cols]],
                [[label, *[fmt(m.get(k), p) for _, m in cols]] for k, label, p in METRICS],
            )
        )
        last = cols[0][1]
        parts.append("\nTool-selection accuracy by category (" + cols[0][0] + "):\n")
        parts.append(
            table(
                ["Category", "n", "Accuracy"],
                [
                    [c, v["n"], fmt(v["tool_selection_accuracy"], True)]
                    for c, v in last["by_category"].items()
                ],
            )
        )

    rb = load("retrieval_bench") or (load("ablations") or {}).get("retrieval")
    if rb:
        parts.append(
            f"\n### Retrieval ablation (`make retrieval-bench`, {rb['n_questions']} semantic questions)"
        )
        parts.append(
            table(
                ["Mode", "Recall@5", "MRR", "nDCG@10", "p50 latency (s)", "p95 latency (s)"],
                [
                    [
                        m,
                        fmt(v["recall@5"]),
                        fmt(v["mrr"]),
                        fmt(v["ndcg@10"]),
                        fmt(v["latency_p50_s"]),
                        fmt(v["latency_p95_s"]),
                    ]
                    for m, v in rb["modes"].items()
                ],
            )
        )
    ab = load("ablations")
    if ab and isinstance(ab.get("rag_vs_agent"), dict):
        r = ab["rag_vs_agent"]
        agent = r["agent"] if isinstance(r["agent"], dict) else {}
        parts.append(
            "\n### Plain single-shot RAG vs agent (`make ablations`, golden semantic + multi-step)"
        )
        keys = [
            ("judge_faithfulness", "Judge faithfulness"),
            ("judge_relevance", "Judge relevance"),
            ("citation_precision", "Citation precision"),
            ("recall@5", "Recall@5"),
            ("mrr", "MRR"),
            ("latency_p50_s", "Latency p50 (s)"),
            ("latency_p95_s", "Latency p95 (s)"),
        ]
        parts.append(
            table(
                ["Metric", "Single-shot RAG", "Agent"],
                [[lbl, fmt(r["single_shot_rag"].get(k)), fmt(agent.get(k))] for k, lbl in keys],
            )
        )
        parts.append(f"\nArchitecture/model ablation: {ab.get('architecture_model')}")
    ja = load("judge_agreement")
    parts.append("\n### Judge calibration")
    parts.append(
        table(
            ["n labelled", "Agreement", "Cohen's kappa"],
            [[ja["n_labelled"], fmt(ja["agreement"], True), fmt(ja["cohens_kappa"])]],
        )
        if ja
        else "not measured."
    )
    return "\n".join(parts)


def main() -> None:
    body = build()
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else f"# Evaluation\n\n{BEGIN}\n{END}\n"
    head, rest = doc.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    DOC.write_text(f"{head}{BEGIN}\n{body}\n{END}{tail}", encoding="utf-8")
    print(body)


if __name__ == "__main__":
    main()
