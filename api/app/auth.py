"""Google Sign-In gate: verify Google ID tokens and issue our own session cookie.

Only one account is allowed in for now (ALLOWED_LOGIN_EMAIL). The Google
client ID is public and safe to expose to the frontend; nothing here needs
a Google client secret since we only verify ID tokens, we don't run an
OAuth code exchange.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
ALLOWED_LOGIN_EMAIL = os.getenv("ALLOWED_LOGIN_EMAIL", "").strip().lower()
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

SESSION_COOKIE = "finbg_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600

if not SESSION_SECRET:
    # Only acceptable for local dev against a throwaway DB; production must
    # set a real SESSION_SECRET or every restart invalidates every session
    # (harmless) and, worse, a guessed default would let anyone forge one.
    SESSION_SECRET = "dev-only-insecure-secret-change-me"

_serializer = URLSafeTimedSerializer(SESSION_SECRET)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleLoginRequest(BaseModel):
    credential: str


def _issue_session(response: Response, email: str) -> None:
    token = _serializer.dumps({"email": email})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def current_session_email(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload.get("email")


def require_session_email(request: Request) -> str:
    email = current_session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Требуется вход через Google.")
    return email


@router.post("/google")
def login_with_google(payload: GoogleLoginRequest, response: Response) -> dict:
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID не настроен на сервере.",
        )
    if not ALLOWED_LOGIN_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="ALLOWED_LOGIN_EMAIL не настроен на сервере.",
        )
    try:
        claims = google_id_token.verify_oauth2_token(
            payload.credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as error:
        raise HTTPException(
            status_code=401, detail="Не удалось проверить токен Google."
        ) from error

    email = str(claims.get("email", "")).strip().lower()
    if not claims.get("email_verified") or email != ALLOWED_LOGIN_EMAIL:
        raise HTTPException(
            status_code=403,
            detail=f"Доступ запрещён для {email or 'этого аккаунта'}.",
        )

    _issue_session(response, email)
    return {"email": email}


@router.get("/me")
def me(request: Request) -> dict:
    email = current_session_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="Нет активной сессии.")
    return {"email": email}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
