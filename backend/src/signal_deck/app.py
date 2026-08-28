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
from .config_discovery import PROJECTS, add_config_root, load_config_roots, scan_configs
from .live import TRADE_TAPE_LIMIT, LiveIngestionManager
from .process_control import ProcessRegistry, RunNotFoundError
from .runs import discover_all_runs

DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

SSE_POLL_TIMEOUT_SECONDS = 1.0


class LoginRequest(BaseModel):
    password: str


class AddConfigRootRequest(BaseModel):
    root: str


class StartRunRequest(BaseModel):
    project: str
    run_type: str
    config: str


def _by_key(mapping: dict) -> dict:
    """Serialize a `{key: [dataclass, ...]}` field (symbol_prices, channel_latency)."""
    return {k: [asdict(v) for v in values] for k, values in mapping.items()}


def _format_sse(delta) -> str:
    """One SSE frame for a `Delta`, or the closing frame for `None` (run ended)."""
    if delta is None:
        return "event: done\ndata: {}\n\n"
    payload = {
        "equity": [asdict(p) for p in delta.equity],
        "trades": [asdict(t) for t in delta.trades],
        "pnl": [asdict(p) for p in delta.pnl],
        "win_rates": [asdict(w) for w in delta.win_rates],
        "fills": [asdict(f) for f in delta.fills],
        "health": [asdict(h) for h in delta.health],
        "symbol_prices": _by_key(delta.symbol_prices),
        "channel_latency": _by_key(delta.channel_latency),
        "fill_history": _by_key(delta.fill_history),
    }
    return f"data: {json.dumps(payload)}\n\n"


def _overview_json(overview) -> dict:
    return {
        "run_id": overview.run_id,
        "project": overview.project,
        "status": overview.status,
        "encrypted_locked": overview.encrypted_locked,
        "live_tracked": overview.live_tracked,
        "equity": [asdict(p) for p in overview.equity],
        # Full (uncapped) history is retained for the /trades page-through; the
        # first-paint response here still only ever hands back the latest 50.
        "trades": [asdict(t) for t in overview.trades[-TRADE_TAPE_LIMIT:]],
        "pnl": [asdict(p) for p in overview.pnl],
        "win_rates": [asdict(w) for w in overview.win_rates],
        "fills": [asdict(f) for f in overview.fills],
        "health": [asdict(h) for h in overview.health],
        "symbol_prices": _by_key(overview.symbol_prices),
        "channel_latency": _by_key(overview.channel_latency),
        "fill_history": _by_key(overview.fill_history),
    }


def create_app(settings: Settings, *, frontend_dist: Path = DEFAULT_FRONTEND_DIST) -> FastAPI:
    live_manager = LiveIngestionManager(
        settings.rustle_runs_dir, settings.ticktrader_runs_dir,
        settings.rustle_cwd, settings.ticktrader_cwd,
    )
    process_registry = ProcessRegistry(settings.process_registry_file, settings.stop_log_file)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        live_manager.start()
        yield
        await live_manager.stop()

    app = FastAPI(title="Signal Deck", lifespan=lifespan)
    app.state.live_manager = live_manager
    app.state.process_registry = process_registry

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
        # Reconciles tracked runs' liveness on the same cadence the frontend
        # already polls this endpoint at, rather than running a second background loop.
        process_registry.reconcile()
        runs = discover_all_runs(
            settings.rustle_runs_dir,
            settings.ticktrader_runs_dir,
            # Live runs' PnL comes from live_manager's own ~1s incremental tail
            # instead of re-parsing each run's whole log here too - a run's own
            # Overview stream (SSE) is where per-second freshness actually
            # matters; this list only needs to be cheap, not fast, since which
            # runs exist barely changes minute to minute (the frontend polls it
            # every 60s, plus an on-demand manual rescan).
            live_pnl=live_manager.live_pnl_totals(),
        )
        return [asdict(run) for run in runs]

    def _require_known_project(project: str) -> None:
        if project not in PROJECTS:
            raise HTTPException(status_code=404, detail="unknown project")

    _RUNS_ROOTS = {"rustle": lambda: settings.rustle_runs_dir, "ticktrader": lambda: settings.ticktrader_runs_dir}
    _CWDS = {"rustle": lambda: settings.rustle_cwd, "ticktrader": lambda: settings.ticktrader_cwd}
    _CMDS = {
        ("rustle", "backtest"): lambda: settings.rustle_backtest_cmd,
        ("rustle", "live"): lambda: settings.rustle_live_cmd,
        ("ticktrader", "backtest"): lambda: settings.ticktrader_backtest_cmd,
        ("ticktrader", "live"): lambda: settings.ticktrader_live_cmd,
    }
    # rustle's `--out <path>` forces its own trade log into this run's own
    # directory instead of rustle's native `<mode>_<config-stem>/<date>/`
    # convention, which `runs.py`/`live.py` have no way to resolve on their
    # own (the date is picked internally by rustle, not passed in). ticktrader
    # has no equivalent flag wired up yet.
    _OUTPUT_FLAGS = {"rustle": "--out", "ticktrader": None}

    def _config_is_registered(project: str, config_path: str) -> bool:
        # A launchable config must come from one of the project's own scanned
        # roots (#8) - otherwise `/api/runs` would happily Popen the configured
        # trading binary against an arbitrary path on disk just because an
        # authenticated session named it.
        roots = load_config_roots(settings.config_roots_file).get(project, [])
        resolved = Path(config_path).resolve()
        return any(resolved.is_relative_to(Path(root).resolve()) for root in roots)

    @app.post("/api/runs")
    def start_run(body: StartRunRequest, request: Request) -> dict:
        require_session(request)
        _require_known_project(body.project)
        if body.run_type not in ("live", "backtest"):
            raise HTTPException(status_code=400, detail="run_type must be 'live' or 'backtest'")
        runs_root = _RUNS_ROOTS[body.project]()
        cwd = _CWDS[body.project]()
        cmd_prefix = _CMDS[(body.project, body.run_type)]()
        if runs_root is None or cwd is None or cmd_prefix is None:
            raise HTTPException(
                status_code=400, detail=f"{body.project} is not configured to launch {body.run_type} runs"
            )
        if not _config_is_registered(body.project, body.config):
            raise HTTPException(status_code=400, detail="config is not under a registered config root")
        try:
            return process_registry.start_run(
                project=body.project,
                run_type=body.run_type,
                config_path=body.config,
                cmd_prefix=cmd_prefix,
                cwd=cwd,
                runs_root=runs_root,
                output_flag=_OUTPUT_FLAGS[body.project],
            )
        except FileNotFoundError:
            raise HTTPException(status_code=400, detail="config file not found")

    @app.post("/api/runs/{project}/{run_id}/stop")
    def stop_run(project: str, run_id: str, request: Request) -> dict[str, bool]:
        require_session(request)
        _require_known_project(project)
        try:
            process_registry.stop_run(project=project, run_id=run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="run not found")
        return {"ok": True}

    @app.get("/api/config-roots/{project}")
    def get_config_roots(project: str, request: Request) -> dict[str, list[str]]:
        require_session(request)
        _require_known_project(project)
        roots = load_config_roots(settings.config_roots_file)
        return {"roots": roots.get(project, [])}

    @app.post("/api/config-roots/{project}")
    def post_config_root(project: str, body: AddConfigRootRequest, request: Request) -> dict[str, list[str]]:
        require_session(request)
        _require_known_project(project)
        if not Path(body.root).is_dir():
            raise HTTPException(status_code=400, detail="not a directory")
        roots = add_config_root(settings.config_roots_file, project, body.root)
        return {"roots": roots}

    @app.get("/api/config-scan/{project}")
    def get_config_scan(project: str, request: Request) -> dict[str, list[str]]:
        require_session(request)
        _require_known_project(project)
        roots = load_config_roots(settings.config_roots_file).get(project, [])
        return {"configs": scan_configs(roots)}

    @app.get("/api/runs/{project}/{run_id}/overview")
    def run_overview(project: str, run_id: str, request: Request) -> dict:
        require_session(request)
        overview = live_manager.get_overview(project, run_id)
        if overview is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _overview_json(overview)

    @app.get("/api/runs/{project}/{run_id}/trades")
    def run_trades(
        project: str, run_id: str, request: Request, before: float | None = None, limit: int = 100
    ) -> list[dict]:
        require_session(request)
        trades = live_manager.get_trades(project, run_id, before=before, limit=limit)
        if trades is None:
            raise HTTPException(status_code=404, detail="run not found")
        return [asdict(t) for t in trades]

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
