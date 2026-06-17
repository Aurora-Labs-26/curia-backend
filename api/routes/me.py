"""GET /me, GET/PUT /me/kb, GET /me/rubric/{task}, PUT /me/fcm-token — current user surface."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from loguru import logger
from pydantic import BaseModel

from api.auth import CurrentUser, current_user
from api.schemas import MeResponse, RubricResponse
from core.db.connection import db_execute, db_fetchrow
from core.kb import UserKB, load_kb, save_kb
from optimization.guidelines import get_guidelines_async
from optimization.rubrics.generator import generate_judge_prompt_async

router = APIRouter()


class FcmTokenRequest(BaseModel):
    token: str


class SpeakerPreferenceRequest(BaseModel):
    preferred_speaker: Optional[str] = None   # single-host: "kenji" | "arjun" | "emeka"
    preferred_pair: Optional[List[str]] = None  # two-host: ["kenji", "emeka"]


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(current_user)) -> MeResponse:
    row = await db_fetchrow(
        "SELECT id, email, name, role FROM users WHERE id = $id",
        {"id": user.id},
    )
    if not row:
        raise HTTPException(404, "user not found")
    return MeResponse(
        id=row["id"],
        email=row.get("email"),
        name=row.get("name"),
        role=row.get("role") or "user",
    )


@router.delete("/me", status_code=204, response_class=Response)
async def delete_me(user: CurrentUser = Depends(current_user)) -> Response:
    """
    Permanently delete the authenticated user's account and ALL associated data
    (sources, episodes, jobs, ideas, KB, audio, Firebase auth user). Required by
    App Store Guideline 5.1.1(v). Irreversible. Idempotent: deleting an
    already-gone account still returns 204.
    """
    from core.account import delete_account

    summary = await delete_account(user.id)
    logger.info(f"[me] account deleted user_id={user.id} summary={summary}")
    return Response(status_code=204)


@router.get("/me/kb", response_model=UserKB)
async def get_kb(user: CurrentUser = Depends(current_user)) -> UserKB:
    """Return the user's KB as a structured object (not a JSON string)."""
    return await load_kb(user.id)


@router.put("/me/kb", response_model=UserKB)
async def put_kb(payload: UserKB, user: CurrentUser = Depends(current_user)) -> UserKB:
    """
    Replace the user's KB. Pydantic validation runs on the request body —
    invalid payload → 422 with field-level errors.
    """
    await save_kb(user.id, payload)
    return payload


@router.get("/me/speaker-preference")
async def get_speaker_preference(user: CurrentUser = Depends(current_user)) -> dict:
    """Return just the speaker preference fields from user_kb."""
    kb = await load_kb(user.id)
    return {
        "preferred_speaker": kb.preferences.preferred_speaker,
        "preferred_pair": kb.preferences.preferred_pair,
    }


@router.put("/me/speaker-preference", status_code=204)
async def put_speaker_preference(
    payload: SpeakerPreferenceRequest,
    user: CurrentUser = Depends(current_user),
) -> None:
    """
    Atomic update of speaker preferences in user_kb.
    Uses a targeted jsonb_set so it never overwrites other KB fields.
    """
    await db_execute(
        """
        UPDATE users
        SET user_kb = COALESCE(user_kb, '{}'::jsonb)
            || jsonb_build_object(
                'preferences', COALESCE(user_kb->'preferences', '{}'::jsonb)
                || jsonb_build_object(
                    'preferred_speaker', $preferred_speaker::text,
                    'preferred_pair',    $preferred_pair::jsonb
                )
            ),
            updated_at = now()
        WHERE id = $id
        """,
        {
            "id": user.id,
            "preferred_speaker": payload.preferred_speaker,
            "preferred_pair": (
                json.dumps(payload.preferred_pair)
                if payload.preferred_pair is not None else "null"
            ),
        },
    )


@router.put("/me/fcm-token", status_code=204)
async def put_fcm_token(payload: FcmTokenRequest, user: CurrentUser = Depends(current_user)) -> None:
    await db_execute(
        "UPDATE users SET fcm_token = $token WHERE id = $id",
        {"token": payload.token, "id": user.id},
    )


@router.get("/me/rubric/{task}", response_model=RubricResponse)
async def get_my_rubric(
    task: str = Path(..., regex="^(transcript|outline)$"),
    user: CurrentUser = Depends(current_user),
) -> RubricResponse:
    """
    Render the judge prompt this user is being scored against for `task`.
    Transparency feature — user can see exactly what the LLM judge sees,
    explain why an episode scored what it did, and edit their KB to shift it.
    """
    try:
        await get_guidelines_async(task)
    except KeyError as e:
        raise HTTPException(404, str(e))

    kb = await load_kb(user.id)
    prompt = await generate_judge_prompt_async(task=task, user_kb=kb, output="<output goes here>")
    return RubricResponse(task=task, user_id=user.id, judge_prompt=prompt)
