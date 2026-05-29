"""GET /me, GET/PUT /me/kb, GET /me/rubric/{task}, PUT /me/fcm-token, PUT /me/brief-preferences — current user surface."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
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


@router.put("/me/fcm-token", status_code=204)
async def put_fcm_token(payload: FcmTokenRequest, user: CurrentUser = Depends(current_user)) -> None:
    await db_execute(
        "UPDATE users SET fcm_token = $token WHERE id = $id",
        {"token": payload.token, "id": user.id},
    )


class BriefPreferencesRequest(BaseModel):
    interests: Optional[List[str]] = None
    location_city: Optional[str] = None
    brief_notify_time: Optional[str] = None  # "HH:MM" format
    brief_enabled: Optional[bool] = None


@router.put("/me/brief-preferences", status_code=204)
async def put_brief_preferences(
    payload: BriefPreferencesRequest,
    user: CurrentUser = Depends(current_user),
) -> None:
    updates: list[str] = []
    params: dict = {"id": user.id}
    if payload.interests is not None:
        updates.append("interests = $interests")
        params["interests"] = payload.interests
    if payload.location_city is not None:
        updates.append("location_city = $location_city")
        params["location_city"] = payload.location_city
    if payload.brief_notify_time is not None:
        updates.append("brief_notify_time = $brief_notify_time::time")
        params["brief_notify_time"] = payload.brief_notify_time
    if payload.brief_enabled is not None:
        updates.append("brief_enabled = $brief_enabled")
        params["brief_enabled"] = payload.brief_enabled
    if not updates:
        return
    await db_execute(
        f"UPDATE users SET {', '.join(updates)} WHERE id = $id",
        params,
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
