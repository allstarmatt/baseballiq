"""
BaseballIQ — FastAPI Backend
Includes background scheduler for automatic prop refresh.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import asyncio
import uvicorn

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from routes.props import router as props_router, _refresh_cache, _cache, _cache_time
from routes.games import router as games_router


# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="America/New_York")


async def scheduled_refresh():
    now = datetime.now().strftime("%H:%M ET")
    print(f"⏰  Scheduled refresh triggered at {now}")
    await _refresh_cache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("⚾  BaseballIQ API starting up...")

    # Warm cache 5 seconds after boot
    asyncio.create_task(_warm_cache_on_startup())

    # 10:00 AM ET — daily morning load
    scheduler.add_job(
        scheduled_refresh,
        CronTrigger(hour=10, minute=0, timezone="America/New_York"),
        id="morning_load",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Every 30 minutes during game hours 1pm–11pm ET
    scheduler.add_job(
        scheduled_refresh,
        CronTrigger(hour="13-23", minute="0,30", timezone="America/New_York"),
        id="game_hours_refresh",
        replace_existing=True,
        misfire_grace_time=120,
    )

    scheduler.start()
    print("⏰  Scheduler started — 10am ET daily + every 30min 1pm-11pm ET")

    yield

    scheduler.shutdown(wait=False)
    print("⚾  BaseballIQ API shutting down...")


async def _warm_cache_on_startup():
    print("🔥  Warming cache on startup...")
    try:
        await asyncio.sleep(5)
        await _refresh_cache()
        print("✅  Startup cache warm complete")
    except Exception as e:
        print(f"⚠️  Startup cache warm failed: {e}")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="BaseballIQ API",
    description="MLB prop analysis engine",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(props_router, prefix="/api/props", tags=["Props"])
app.include_router(games_router, prefix="/api/games", tags=["Games"])


@app.get("/health")
async def health():
    cache_age = None
    if "all_props" in _cache_time:
        cache_age = round((datetime.utcnow() - _cache_time["all_props"]).total_seconds())
    return {
        "status":       "ok",
        "service":      "BaseballIQ API",
        "props_cached": "all_props" in _cache,
        "prop_count":   len(_cache.get("all_props", [])),
        "cache_age_s":  cache_age,
        "next_refresh": _next_refresh_time(),
    }


@app.get("/cache/status")
async def cache_status():
    if "all_props" not in _cache:
        return {"cached": False, "message": "Cache empty — refresh in progress or no games today"}

    age_s   = round((datetime.utcnow() - _cache_time["all_props"]).total_seconds())
    props   = _cache["all_props"]
    by_type = {}
    for p in props:
        t = p.get("prop_type", "Unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "cached":            True,
        "prop_count":        len(props),
        "by_type":           by_type,
        "cache_age_s":       age_s,
        "cache_age_min":     round(age_s / 60, 1),
        "refreshed_at":      _cache_time["all_props"].isoformat() + "Z",
        "next_refresh":      _next_refresh_time(),
        "scheduler_running": scheduler.running,
        "scheduled_jobs": [
            {"id": job.id, "next_run": str(job.next_run_time)}
            for job in scheduler.get_jobs()
        ],
    }


def _next_refresh_time() -> str:
    jobs = scheduler.get_jobs()
    if not jobs:
        return "Scheduler not running"
    next_times = [job.next_run_time for job in jobs if job.next_run_time]
    if not next_times:
        return "Unknown"
    return str(min(next_times))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
