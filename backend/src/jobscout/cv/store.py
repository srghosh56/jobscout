"""Opt-in storage of structured CV profiles (never raw CV text). Deletion is
immediate and scoped to the API key that created the profile."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from jobscout.db.engine import session_scope
from jobscout.db.models import CvProfile
from jobscout.tools.schemas import CandidateProfile


async def save_profile(profile: CandidateProfile, api_key_id: int | None) -> uuid.UUID:
    async with session_scope() as session:
        row = CvProfile(profile=profile.model_dump(mode="json"), api_key_id=api_key_id)
        session.add(row)
        await session.commit()
        return row.id


async def load_profile(profile_id: uuid.UUID, api_key_id: int | None) -> CandidateProfile | None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(CvProfile).where(
                    CvProfile.id == profile_id, CvProfile.api_key_id == api_key_id
                )
            )
        ).scalar_one_or_none()
        return CandidateProfile.model_validate(row.profile) if row else None


async def delete_profile(profile_id: uuid.UUID, api_key_id: int | None) -> bool:
    async with session_scope() as session:
        res = await session.execute(
            delete(CvProfile).where(CvProfile.id == profile_id, CvProfile.api_key_id == api_key_id)
        )
        await session.commit()
        return bool(getattr(res, "rowcount", 0))
