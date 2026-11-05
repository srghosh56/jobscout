"""CV → postings matching and per-posting gap analysis (F4).

Ranking = hybrid retrieval with the profile as the query (top-50 candidates), then a
transparent re-score: 0.5·retrieval + 0.35·skill overlap − penalties for German level
and years above the candidate's. Every explanation cites posting fields by name.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from jobscout.db.engine import session_scope
from jobscout.db.models import Posting
from jobscout.extract.rules import LEVEL_ORDER
from jobscout.retrieval.filters import SearchFilters
from jobscout.retrieval.search import hybrid_search
from jobscout.tools import schemas as S

W_RETRIEVAL, W_SKILLS = 0.5, 0.35
PENALTY_GERMAN, PENALTY_YEARS = 0.3, 0.15


def _norm(s: str) -> str:
    return s.strip().lower().replace("-", " ").replace(".js", "js")


def candidate_german(profile: S.CandidateProfile) -> str:
    level = "none"
    for lang in profile.languages:
        if _norm(lang.language).startswith(("german", "deutsch")):
            lvl = lang.level
            if LEVEL_ORDER.get(lvl, 1) > LEVEL_ORDER[level]:
                level = lvl
    return level


def profile_query(profile: S.CandidateProfile) -> str:
    parts = [profile.headline, " ".join(profile.target_roles), " ".join(profile.skills[:25])]
    return " ".join(p for p in parts if p).strip() or "software"


def gap_for_posting(profile: S.CandidateProfile, p: Posting) -> S.GapResult:
    have = {_norm(s) for s in profile.skills}
    required = [s for s in (p.required_skills or []) if s.strip()]
    matched = [s for s in required if _norm(s) in have]
    missing = [s for s in required if _norm(s) not in have]
    gaps: list[S.Gap] = []
    for s in missing[:8]:
        gaps.append(
            S.Gap(
                kind="skill",
                detail=f"required_skills lists '{s}', not in your profile",
                severity="major",
            )
        )
    cand_de = candidate_german(profile)
    need_de = p.german_level or "none"
    if LEVEL_ORDER.get(need_de, 0) > LEVEL_ORDER.get(cand_de, 0) and need_de != "none":
        sev = "blocker" if LEVEL_ORDER.get(need_de, 0) >= LEVEL_ORDER["good"] else "major"
        gaps.append(
            S.Gap(
                kind="german",
                detail=f"german_level required: {need_de}; yours: {cand_de}",
                severity=sev,
            )
        )
    if (
        p.min_years_experience is not None
        and profile.years_experience is not None
        and p.min_years_experience > profile.years_experience
    ):
        diff = p.min_years_experience - profile.years_experience
        gaps.append(
            S.Gap(
                kind="years",
                detail=f"min_years_experience {p.min_years_experience}; yours {profile.years_experience:g}",
                severity="major" if diff > 2 else "minor",
            )
        )
    if profile.preferred_cities and p.city and p.remote_policy != "remote":
        prefs = {_norm(c) for c in profile.preferred_cities}
        from jobscout.ingest.cleaning import normalize_city

        prefs |= {_norm(normalize_city(c) or c) for c in profile.preferred_cities}
        if _norm(p.city) not in prefs:
            gaps.append(
                S.Gap(
                    kind="location",
                    detail=f"city {p.city} is not in your preferred cities",
                    severity="minor",
                )
            )
    if p.bluecard_status == "below":
        gaps.append(
            S.Gap(
                kind="bluecard",
                detail="bluecard_status: below (stated salary under the threshold)",
                severity="major",
            )
        )
    blockers = sum(g.severity == "blocker" for g in gaps)
    summary = (
        f"{len(matched)}/{len(required)} required skills matched"
        + (f"; {blockers} blocker(s)" if blockers else "")
        + (f"; missing: {', '.join(missing[:5])}" if missing else "")
    )
    return S.GapResult(
        posting_id=p.id, matched_skills=matched, missing_skills=missing, gaps=gaps, summary=summary
    )


def score_match(retrieval_score: float, gap: S.GapResult, n_required: int) -> float:
    overlap = len(gap.matched_skills) / n_required if n_required else 0.5
    penalty = 0.0
    for g in gap.gaps:
        if g.kind == "german":
            penalty += PENALTY_GERMAN if g.severity == "blocker" else PENALTY_GERMAN / 2
        elif g.kind == "years":
            penalty += PENALTY_YEARS if g.severity == "major" else PENALTY_YEARS / 3
    return round(W_RETRIEVAL * retrieval_score + W_SKILLS * overlap - penalty, 4)


def explain(p: Posting, gap: S.GapResult) -> str:
    bits = [f"[{p.id}] {p.title}"]
    if gap.matched_skills:
        bits.append(f"matches required_skills: {', '.join(gap.matched_skills[:6])}")
    bits.append(f"english_sufficient: {p.english_sufficient}")
    if p.min_years_experience is not None:
        bits.append(f"min_years_experience: {p.min_years_experience}")
    bits.append(f"bluecard_status: {p.bluecard_status}")
    return "; ".join(bits)


async def match_profile(
    profile: S.CandidateProfile, filters: SearchFilters | None = None, top_k: int = 8
) -> S.MatchResult:
    from jobscout.tools.impl import summary_from_hit

    f = filters or SearchFilters()
    if not f.city and len(profile.preferred_cities) == 1:
        f = f.model_copy(update={"city": profile.preferred_cities[0]})
    hits = await hybrid_search(profile_query(profile), f, top_k=50, mode="hybrid")
    if not hits:
        return S.MatchResult(profile=profile, matches=[])
    max_score = max(h.score for h in hits) or 1.0
    async with session_scope() as session:
        rows = {
            p.id: p
            for p in (
                await session.execute(select(Posting).where(Posting.id.in_([h.id for h in hits])))
            ).scalars()
        }
    scored: list[tuple[float, Any, Posting, S.GapResult]] = []
    for h in hits:
        p = rows.get(h.id)
        if p is None:
            continue
        gap = gap_for_posting(profile, p)
        score = score_match(h.score / max_score, gap, len(p.required_skills or []))
        scored.append((score, h, p, gap))
    scored.sort(key=lambda x: -x[0])
    return S.MatchResult(
        profile=profile,
        matches=[
            S.CvMatch(
                posting=summary_from_hit(h), score=score, explanation=explain(p, gap), gaps=gap
            )
            for score, h, p, gap in scored[:top_k]
        ],
    )
