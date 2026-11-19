import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("web.app")


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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
        from Screen.screening import verify_signal_schema
        verify_signal_schema()
    except Exception as e:
        logger.warning("Screener schema verification failed; run 'python -m db.migrate': %s", e)
    try:
        from wallet_tracker.store import ensure_tables as ensure_wallet_tables
        ensure_wallet_tables()
    except Exception as e:
        logger.warning(f"Could not initialize wallet tracking tables on startup: {e}")
    try:
        from db.db import verify_retailbot2_schema
        verify_retailbot2_schema()
    except Exception as e:
        logger.warning("RetailBot2 schema verification failed; run 'python -m db.migrate': %s", e)
    try:
        from db.db import verify_strategy_auto_promotion_schema
        verify_strategy_auto_promotion_schema()
    except Exception as e:
        logger.warning("Auto-promotion schema verification failed; run 'python -m db.migrate': %s", e)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Crypto Signal Bot — Quantitative Trading Platform",
    description="Institutional trading dashboard: Real-time search, historical backfill, strategy simulation, and Phase B paper trading.",
    version="2.0.0",
)

_raw_origins = os.getenv(
    "SIGNAL_BOT_ALLOWED_ORIGINS",
    "http://localhost:8050,http://127.0.0.1:8050,http://192.168.1.30:8050,http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.30:5173",
)
ALLOWED_ORIGINS = [origin.strip() for origin in _raw_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

PUBLIC_API_PATHS = {"/api/auth/login", "/api/system/test-identity"}
# A user flagged must_change_password may only do what is needed to fix that.
PASSWORD_CHANGE_ALLOWED_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/change-password"}
ADMIN_ONLY_PREFIXES = ("/api/settings", "/api/auth/audit-logs")
ADMIN_OR_TRADER_PREFIXES = (
    "/api/master-data/backfill",
    "/api/master-data/build-features",
    "/api/custom-strategy",
)
RESEARCH_CONTROL_PREFIXES = (
    "/api/research/retailbot2/control",
    "/api/research/retailbot2/strategies",
    "/api/research/screener/control",
)

TRADER_OR_ADMIN_PREFIXES = (
    "/api/paper/configs",
    "/api/paper/positions",
    "/api/paper/orders",
    "/api/paper/sync",
    "/api/paper/deploy",
    "/api/paper/circuit-breakers",
    "/api/backtest",
    "/api/ml",
    "/api/wallets",
)

# Viewer-readable GET routes are deliberately enumerated. New GET endpoints
# remain denied until their data exposure and side effects are reviewed here.
# Dynamic segments use FastAPI's {parameter} route-template notation.
AUTHENTICATED_READ_PATHS = (
    "/api/auth/me",
    "/api/paper/positions",
    "/api/paper/trades",
    "/api/paper/orders",
    "/api/paper/metrics",
    "/api/paper/configs",
    "/api/paper/circuit-breakers",
    "/api/crypto/search",
    "/api/crypto/popular",
    "/api/master-data/coverage",
    "/api/master-data/jobs",
    "/api/master-data/jobs/{job_id}",
    "/api/backtest/promotion-policy",
    "/api/backtest/promotion-audit",
    "/api/backtest/strategies",
    "/api/backtest/history",
    "/api/backtest/runs/{run_id}",
    "/api/system/status",
    "/api/system/test-identity",
    "/api/telegram/status",
    "/api/ml/experiments",
    "/api/ml/experiments/{experiment_id}",
    "/api/ml/models",
    "/api/ml/generator-models",
    "/api/ml/retraining/status",
    "/api/ml/rl/models",
    "/api/news/stats",
    "/api/news/signals",
    "/api/news/events",
    "/api/research/retailbot2/positions",
    "/api/research/retailbot2/trades",
    "/api/research/retailbot2/stats",
    "/api/research/retailbot2/strategies",
    "/api/research/retailbot2/control",
    "/api/research/screener/signals",
    "/api/research/screener/stats",
    "/api/research/screener/status",
    "/api/research/screener/control",
    "/api/wallets/meta",
    "/api/wallets/settings",
    "/api/wallets/status",
    "/api/wallets/positions",
    "/api/wallets/token-flow",
    "/api/wallets/events",
    "/api/wallets/tracked",
)


def _is_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def _matches_route_template(path: str, templates: tuple[str, ...]) -> bool:
    path_parts = path.strip("/").split("/")
    for template in templates:
        template_parts = template.strip("/").split("/")
        if len(path_parts) != len(template_parts):
            continue
        if all(
            expected == actual
            or (expected.startswith("{") and expected.endswith("}"))
            for actual, expected in zip(path_parts, template_parts)
        ):
            return True
    return False


def _matches_registered_api_path(scope) -> bool:
    """Distinguish an unregistered URL (404) from a registered but denied route."""
    for route in app.routes:
        if not getattr(route, "path", "").startswith("/api/"):
            continue
        match, _ = route.matches(scope)
        if match is not Match.NONE:
            return True
    return False


def _role_allowed(method: str, path: str, role: str) -> bool:
    # Every authenticated role must be able to end its own session or fix its
    # own password, including accounts forced through first-login rotation.
    if method.upper() == "POST" and path in {"/api/auth/logout", "/api/auth/change-password"}:
        return True
    if path.startswith("/api/users"):
        return role == "admin"
    if _is_prefix(path, ADMIN_ONLY_PREFIXES):
        return role == "admin"
    if _is_prefix(path, ADMIN_OR_TRADER_PREFIXES):
        return role in ("admin", "trader")
    if _is_prefix(path, TRADER_OR_ADMIN_PREFIXES) and method.upper() != "GET":
        return role in ("admin", "trader")
    if _is_prefix(path, RESEARCH_CONTROL_PREFIXES) and method.upper() != "GET":
        return role in ("admin", "trader")
    if path == "/api/telegram/status":
        return True
    if path == "/api/telegram/test":
        return role == "admin"
    # Read access is allowlisted after reviewing each route's data exposure
    # and side effects. Unknown GET routes fail closed just like mutations.
    if method.upper() == "GET":
        return _matches_route_template(path, AUTHENTICATED_READ_PATHS)
    return False


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
            # Preserve FastAPI's normal 404 for URLs that do not match any
            # registered API route. Registered but unclassified GET routes
            # remain denied by the explicit read allowlist.
            if request.method.upper() == "GET" and not _matches_registered_api_path(request.scope):
                return await call_next(request)
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


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.get("/api/system/test-identity")
def get_test_identity(request: Request):
    run_id = os.getenv("SIGNAL_BOT_TEST_RUN_ID")
    if not run_id:
        raise HTTPException(status_code=404, detail="Test identity not configured")
    client_host = request.client.host if request.client else None
    if not client_host or client_host not in LOOPBACK_HOSTS:
        raise HTTPException(status_code=404, detail="Test identity only accessible via loopback")
    return {"status": "ok", "run_id": run_id, "pid": os.getpid()}

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
MODERN_VIEWS = {"overview", "market-screener", "paper-trading", "strategy-library", "wallet-tracking",
                "backtest-studio", "ml-studio", "research", "news-ai", "master-data", "settings"}
modern_index_path = os.path.join(FRONTEND_DIST_DIR, "index.html")
if os.path.isfile(modern_index_path):
    @app.get("/modern/{view}", include_in_schema=False)
    def serve_modern_view(view: str):
        if view != "index.html" and view not in MODERN_VIEWS:
            raise HTTPException(404)
        return FileResponse(modern_index_path)


    app.mount("/modern", StaticFiles(directory=FRONTEND_DIST_DIR, html=True), name="modern")
    assets_dir = os.path.join(FRONTEND_DIST_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend_assets")

elif os.getenv("SIGNAL_BOT_USE_MODERN_UI", "0").lower() in ("1", "true", "yes"):
    logger.warning("Modern UI enabled but frontend/dist/index.html is missing. Serving legacy dashboard.")


@app.get("/")
def serve_index():
    if os.getenv("SIGNAL_BOT_USE_MODERN_UI", "0").lower() in ("1", "true", "yes"):
        modern_index = os.path.join(FRONTEND_DIST_DIR, "index.html")
        if os.path.exists(modern_index):
            return FileResponse(modern_index)

    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return {"message": "Crypto Signal Bot API is running."}

    # Inject the dashboard enhancement bundle without rewriting the large static
    # index.html. This keeps the trading/dashboard markup untouched.
    with open(index_path, "r", encoding="utf-8") as handle:
        html = handle.read()
    # The stylesheet is linked by index.html; inject only the versioned JS bundle here.
    # Avoid duplicate CSS requests and refresh browsers when the enhancement bundle changes.
    enhancement = '<script src="/static/js/dashboard_enhancements.js?v=20261104-1"></script>\n'
    if "dashboard_enhancements.js" not in html:
        html = html.replace("</body>", enhancement + "</body>", 1)
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host=os.getenv("SIGNAL_BOT_HOST", "127.0.0.1"), port=8050, reload=True)
