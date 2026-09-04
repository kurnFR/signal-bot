import os
import sys
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from web.auth import seed_default_admin
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event to ensure security initialization
@app.on_event("startup")
def on_startup():
    seed_default_admin()

# Include API Routers
app.include_router(auth_router)
app.include_router(paper_router)
app.include_router(search_router)
app.include_router(master_data_router)
app.include_router(backtest_router)
app.include_router(settings_router)
app.include_router(custom_strat_router)
app.include_router(telegram_router)

# Mount Static Files
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
