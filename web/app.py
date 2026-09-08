import os
import sys
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.auth import seed_default_admin, authenticate_request
from web.routes.auth_routes import router as auth_router
from web.routes.paper_routes import router as paper_router
from web.routes.search import router as search_router
from web.routes.master_data import router as master_data_router
from web.routes.backtest import router as backtest_router
from web.routes.settings import router as settings_router
from web.routes.custom_strategy import router as custom_strat_router
from web.routes.telegram_routes import router as telegram_router

app = FastAPI(
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
ADMIN_ONLY_PREFIXES = ("/api/settings",)
ADMIN_OR_TRADER_PREFIXES = (
    "/api/master-data/backfill",
    "/api/master-data/build-features",
    "/api/custom-strategy",
)
TRADER_OR_ADMIN_PREFIXES = (
    "/api/paper/configs",
    "/api/paper/positions",
    "/api/paper/sync",
    "/api/paper/deploy",
    "/api/backtest",
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


@app.on_event("startup")
def on_startup():
    seed_default_admin()

app.include_router(auth_router)
app.include_router(paper_router)
app.include_router(search_router)
app.include_router(master_data_router)
app.include_router(backtest_router)
app.include_router(settings_router)
app.include_router(custom_strat_router)
app.include_router(telegram_router)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Crypto Signal Bot API is running."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.app:app", host="0.0.0.0", port=8050, reload=True)
