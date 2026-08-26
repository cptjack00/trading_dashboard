from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import SESSION_COOKIE, create_session_token, verify_session_token
from .config import Settings
from .live import LiveIngestionManager
from .runs import discover_all_runs

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

SSE_POLL_TIMEOUT_SECONDS = 1.0


class LoginRequest(BaseModel):
    password: str


def _format_sse(delta) -> str:
    """One SSE frame for a `Delta`, or the closing frame for `None` (run ended)."""
    if delta is None:
        return "event: done\ndata: {}\n\n"
    payload = {"equity": [asdict(p) for p in delta.equity], "trades": [asdict(t) for t in delta.trades]}
    return f"data: {json.dumps(payload)}\n\n"


def _overview_json(overview) -> dict:
    return {
        "run_id": overview.run_id,
        "project": overview.project,
        "status": overview.status,
        "encrypted_locked": overview.encrypted_locked,
        "equity": [asdict(p) for p in overview.equity],
        "trades": [asdict(t) for t in overview.trades],
    }


def create_app(settings: Settings, *, frontend_dist: Path = DEFAULT_FRONTEND_DIST) -> FastAPI:
    live_manager = LiveIngestionManager(settings.rustle_runs_dir, settings.ticktrader_runs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        live_manager.start()
        yield
        await live_manager.stop()

    app = FastAPI(title="Signal Deck", lifespan=lifespan)
    app.state.live_manager = live_manager

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

    def require_session(request: Request) -> None:
        token = request.cookies.get(SESSION_COOKIE)
        if not token or not verify_session_token(token, settings.secret):
            raise HTTPException(status_code=401, detail="not authenticated")

    @app.get("/api/session")
    def session_status(request: Request) -> dict[str, bool]:
        require_session(request)
        return {"authenticated": True}

    @app.get("/api/runs")
    def list_runs(request: Request) -> list[dict]:
        require_session(request)
        runs = discover_all_runs(settings.rustle_runs_dir, settings.ticktrader_runs_dir)
        return [asdict(run) for run in runs]

    @app.get("/api/runs/{project}/{run_id}/overview")
    def run_overview(project: str, run_id: str, request: Request) -> dict:
        require_session(request)
        overview = live_manager.get_overview(project, run_id)
        if overview is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _overview_json(overview)

    @app.get("/api/runs/{project}/{run_id}/stream")
    async def run_stream(project: str, run_id: str, request: Request) -> StreamingResponse:
        require_session(request)
        queue = live_manager.subscribe(project, run_id)
        if queue is None:
            raise HTTPException(status_code=404, detail="run is not live")

        async def events():
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        delta = await asyncio.wait_for(queue.get(), timeout=SSE_POLL_TIMEOUT_SECONDS)
                    except asyncio.TimeoutError:
                        continue
                    yield _format_sse(delta)
                    if delta is None:
                        return
            finally:
                live_manager.unsubscribe(project, run_id, queue)

        return StreamingResponse(events(), media_type="text/event-stream")

    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app
