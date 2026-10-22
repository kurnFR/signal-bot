import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("web.app")


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.auth import seed_default_admin, authenticate_request, enable_durable_rate_limit
from web.routes.auth_routes import router as auth_router
from web.routes.paper_routes import router as paper_router
from web.routes.search import router as search_router
from web.routes.master_data import router as master_data_router
from web.routes.backtest import router as backtest_router
from web.routes.settings import router as settings_router
from web.routes.system_status import router as system_status_router
from web.routes.custom_strategy import router as custom_strat_router
from web.routes.telegram_routes import router as telegram_router
from web.routes.ml_routes import router as ml_router
from web.routes.news_routes import router as news_router
from web.routes.research_routes import router as research_router
from web.routes.wallet_routes import router as wallet_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_admin()
    enable_durable_rate_limit()
    try:
        from web.job_store import mark_interrupted
        stale = mark_interrupted()
        if stale:
            logger.info("Marked %d background job(s) from a previous process as interrupted", stale)
    except Exception as e:
        logger.warning(f"Could not reconcile background jobs on startup: {e}")
    try:
        from Screen.screening import ensure_signal_table
        ensure_signal_table()
    except Exception as e:
        logger.warning(f"Could not initialize smart_money_signals table on startup: {e}")
    try:
        from wallet_tracker.store import ensure_tables as ensure_wallet_tables
        ensure_wallet_tables()
    except Exception as e:
        logger.warning(f"Could not initialize wallet tracking tables on startup: {e}")
    try:
        from paper.retailbot2 import DatabaseManager, BotConfig
        cfg = BotConfig()
        DatabaseManager(cfg, logger)
    except Exception as e:
        logger.warning(f"Could not initialize retailbot2 tables on startup: {e}")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Crypto Signal Bot — Quantitative Trading Platform",
    description="Institutional trading dashboard: Real-time search, historical backfill, strategy simulation, and Phase B paper trading.",
    version="2.0.0",
)

_raw_origins = os.getenv("SIGNAL_BOT_ALLOWED_ORIGINS", "http://localhost:8050,http://127.0.0.1:8050")
ALLOWED_ORIGINS = [origin.strip() for origin in _raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

PUBLIC_API_PATHS = {"/api/auth/login"}
# A user flagged must_change_password may only do what is needed to fix that.
PASSWORD_CHANGE_ALLOWED_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}
ADMIN_ONLY_PREFIXES = ("/api/settings",)
ADMIN_OR_TRADER_PREFIXES = (
    "/api/master-data/backfill",
    "/api/master-data/build-features",
    "/api/custom-strategy",
)
RESEARCH_CONTROL_PREFIXES = (
    "/api/research/retailbot2/control",
    "/api/research/screener/control",
)

TRADER_OR_ADMIN_PREFIXES = (
    "/api/paper/configs",
    "/api/paper/positions",
    "/api/paper/sync",
    "/api/paper/deploy",
    "/api/paper/circuit-breakers",
    "/api/backtest",
    "/api/ml",
    "/api/wallets",
)


def _is_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _role_allowed(method: str, path: str, role: str) -> bool:
    if path.startswith("/api/users"):
        return role == "admin"
    if _is_prefix(path, ADMIN_ONLY_PREFIXES):
        return role == "admin"
    if _is_prefix(path, ADMIN_OR_TRADER_PREFIXES):
        return role in ("admin", "trader")
    if _is_prefix(path, TRADER_OR_ADMIN_PREFIXES):
        if method.upper() == "GET":
            return True
        return role in ("admin", "trader")
    if _is_prefix(path, RESEARCH_CONTROL_PREFIXES):
        if method.upper() == "GET":
            return True
        return role in ("admin", "trader")
    if path == "/api/telegram/status":
        return True
    if path == "/api/telegram/test":
        return role == "admin"
    return True


@app.middleware("http")
async def api_security_boundary(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API_PATHS:
        return await call_next(request)

    try:
        user = authenticate_request(request)
        request.state.user = user
        role = str(user.get("role", "")).lower()
        if user.get("must_change_password") and path not in PASSWORD_CHANGE_ALLOWED_PATHS:
            return JSONResponse(
                status_code=403,
                content={"detail": "Password change required before continuing", "code": "password_change_required"},
            )
        if not _role_allowed(request.method, path, role):
            return JSONResponse(
                status_code=403,
                content={"detail": "Insufficient privileges for this operation"},
            )
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception:
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})

    return await call_next(request)


app.include_router(auth_router)
app.include_router(paper_router)
app.include_router(search_router)
app.include_router(master_data_router)
app.include_router(backtest_router)
app.include_router(settings_router)
app.include_router(system_status_router)
app.include_router(custom_strat_router)
app.include_router(telegram_router)
app.include_router(ml_router)
app.include_router(news_router)
app.include_router(research_router)
app.include_router(wallet_router)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return {"message": "Crypto Signal Bot API is running."}

    # Inject the dashboard enhancement bundle without rewriting the large static
    # index.html. This keeps the trading/dashboard markup untouched.
    with open(index_path, "r", encoding="utf-8") as handle:
        html = handle.read()
    # The stylesheet is linked by index.html; inject only the versioned JS bundle here.
    # Avoid duplicate CSS requests and refresh browsers when the enhancement bundle changes.
    enhancement = '<script src="/static/js/dashboard_enhancements.js?v=20261016-2"></script>\n'
    if "dashboard_enhancements.js" not in html:
        html = html.replace("</body>", enhancement + "</body>", 1)
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=os.getenv("SIGNAL_BOT_HOST", "127.0.0.1"), port=8050, reload=True)
