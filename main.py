"""
BaseballIQ — FastAPI Backend
Phase 1 MVP: HR props powered by real MLB + Statcast + Odds data
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

from routes.props import router as props_router
from routes.games import router as games_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    print("⚾  BaseballIQ API starting up...")
    yield
    print("⚾  BaseballIQ API shutting down...")


app = FastAPI(
    title="BaseballIQ API",
    description="MLB prop analysis engine — Phase 1 MVP",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (allow your frontend to call this API) ───────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",  
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(props_router,  prefix="/api/props",  tags=["Props"])
app.include_router(games_router,  prefix="/api/games",  tags=["Games"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "BaseballIQ API"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
