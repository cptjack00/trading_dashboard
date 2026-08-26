from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import SESSION_COOKIE, create_session_token, verify_session_token
from .config import Settings

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


class LoginRequest(BaseModel):
    password: str


def create_app(settings: Settings, *, frontend_dist: Path = DEFAULT_FRONTEND_DIST) -> FastAPI:
    app = FastAPI(title="Signal Deck")

    @app.post("/api/login")
    def login(body: LoginRequest, response: Response) -> dict[str, bool]:
        if not hmac.compare_digest(body.password, settings.secret):
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = create_session_token(settings.secret, settings.session_ttl_seconds)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            samesite="lax",
        )
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/session")
    def session_status(request: Request) -> dict[str, bool]:
        token = request.cookies.get(SESSION_COOKIE)
        if not token or not verify_session_token(token, settings.secret):
            raise HTTPException(status_code=401, detail="not authenticated")
        return {"authenticated": True}

    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
