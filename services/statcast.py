"""
Statcast Service
Pulls advanced metrics from Baseball Savant via the pybaseball library.

Metrics pulled:
  Hitter  — exit_velocity_avg, max_exit_velocity, barrel_batted_rate,
             hard_hit_percent, launch_angle_avg, fly_ball_rate,
             pull_percent, xslg, xwoba
  Pitcher — barrel_batted_rate (allowed), hard_hit_percent (allowed),
             fly_ball_rate (allowed), gb_rate, hr_per_9

Run `pip install pybaseball` to use this module.

NOTE: pybaseball calls are synchronous and can be slow (1-5 seconds).
We run them in a thread pool so they don't block FastAPI's async event loop.
Data is cached aggressively — Statcast only updates once per day.
"""

import asyncio
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional
import pandas as pd

# pybaseball imports — will raise ImportError if not installed
try:
    from pybaseball import (
        statcast_batter,
        statcast_pitcher,
        batting_stats,
        pitching_stats,
        playerid_lookup,
    )
    from pybaseball import cache as pb_cache
    pb_cache.enable()   # cache responses to disk — avoids re-downloading
    PYBASEBALL_AVAILABLE = True
except ImportError:
    PYBASEBALL_AVAILABLE = False
    print("⚠️  pybaseball not installed. Run: pip install pybaseball")


# ── Player ID Lookup ──────────────────────────────────────────────────────────

@lru_cache(maxsize=500)
def _lookup_player_id(last_name: str, first_name: str) -> Optional[int]:
    """
    Converts a player name → Baseball Savant / Statcast player ID.
    Results are cached in memory so we don't hit the API repeatedly.
    """
    if not PYBASEBALL_AVAILABLE:
        return None
    try:
        result = playerid_lookup(last_name, first_name)
        if result.empty:
            return None
        # Return the most recent player (handles same-name players)
        row = result.sort_values("mlb_played_last", ascending=False).iloc[0]
        return int(row["key_mlbam"])
    except Exception as e:
        print(f"Player ID lookup failed for {first_name} {last_name}: {e}")
        return None


def _get_date_range(days_back: int = 30) -> tuple[str, str]:
    """Returns (start_date, end_date) strings for Statcast queries."""
    end   = date.today()
    start = end - timedelta(days=days_back)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ── Hitter Statcast Metrics ───────────────────────────────────────────────────

def _fetch_hitter_statcast_sync(player_id: int, days_back: int = 30) -> dict:
    """
    Synchronous Statcast pull for a hitter.
    Called via asyncio.to_thread() so it doesn't block the event loop.
    """
    if not PYBASEBALL_AVAILABLE:
        return {}

    start_date, end_date = _get_date_range(days_back)

    try:
        df = statcast_batter(start_date, end_date, player_id)
        if df.empty:
            return {}

        # Only keep batted ball events (ignore strikeouts / walks for most metrics)
        batted = df[df["type"] == "X"].copy()
        if batted.empty:
            return {}

        # Hard hit = exit velo >= 95 mph
        hard_hit = batted[batted["launch_speed"] >= 95]

        # Barrel: launch_speed >= 98 AND launch_angle between 26-30° at 98mph,
        # scaling up. pybaseball marks barrels in the `barrel` column if present.
        barrel_col = "barrel" if "barrel" in batted.columns else None
        barrel_pct = (
            round(batted[barrel_col].sum() / len(batted) * 100, 1)
            if barrel_col else None
        )

        # Fly balls: launch_angle >= 25°
        fly_balls = batted[batted["launch_angle"] >= 25]

        # Pull rate: pulled if hit_direction in ["L", "R"] matching batter hand
        # Simplified: use `hc_x` coordinate — pull = away from center
        pull_events = batted[batted["hit_location"].isin([1, 2, 3])] if "hit_location" in batted.columns else pd.DataFrame()

        return {
            "exit_velo_avg":     round(batted["launch_speed"].mean(), 1),
            "exit_velo_max":     round(batted["launch_speed"].max(), 1),
            "barrel_pct":        barrel_pct,
            "hard_hit_pct":      round(len(hard_hit) / len(batted) * 100, 1),
            "launch_angle_avg":  round(batted["launch_angle"].mean(), 1),
            "fly_ball_rate":     round(len(fly_balls) / len(batted) * 100, 1),
            "pull_rate":         round(len(pull_events) / len(batted) * 100, 1) if not pull_events.empty else None,
            "total_batted_balls": len(batted),
            "sample_days":       days_back,
        }

    except Exception as e:
        print(f"Statcast hitter fetch failed for player {player_id}: {e}")
        return {}


async def get_hitter_statcast(player_id: int, days_back: int = 30) -> dict:
    """Async wrapper — runs Statcast pull in a thread pool."""
    return await asyncio.to_thread(_fetch_hitter_statcast_sync, player_id, days_back)


# ── Advanced Hitter Metrics (xwOBA, xSLG) ────────────────────────────────────

def _fetch_hitter_advanced_sync(season: Optional[int] = None) -> pd.DataFrame:
    """
    Pulls season-level advanced metrics (xwOBA, xSLG) for all hitters
    from FanGraphs via pybaseball.batting_stats().
    Returns a DataFrame indexed by player name.
    """
    if not PYBASEBALL_AVAILABLE:
        return pd.DataFrame()

    if not season:
        season = date.today().year

    try:
        # qual=20 means minimum 20 plate appearances (catches most starters)
        df = batting_stats(season, qual=20)
        return df
    except Exception as e:
        print(f"FanGraphs batting stats fetch failed: {e}")
        return pd.DataFrame()


async def get_hitter_advanced(season: Optional[int] = None) -> pd.DataFrame:
    """Async wrapper for season-level advanced metrics."""
    return await asyncio.to_thread(_fetch_hitter_advanced_sync, season)


# ── Pitcher Statcast Metrics ──────────────────────────────────────────────────

def _fetch_pitcher_statcast_sync(player_id: int, days_back: int = 30) -> dict:
    """
    Synchronous Statcast pull for a pitcher.
    Returns barrel% allowed, hard contact%, fly-ball rate, pitch mix, velo.
    """
    if not PYBASEBALL_AVAILABLE:
        return {}

    start_date, end_date = _get_date_range(days_back)

    try:
        df = statcast_pitcher(start_date, end_date, player_id)
        if df.empty:
            return {}

        # Batted balls against
        batted = df[df["type"] == "X"].copy()

        # Pitch mix from all pitches thrown
        pitch_counts = df["pitch_type"].value_counts()
        total_pitches = len(df)
        pitch_mix = {
            str(pt): round(count / total_pitches * 100, 1)
            for pt, count in pitch_counts.items()
            if pd.notna(pt)
        }

        # Velocity by pitch type
        velo_by_pitch = (
            df.groupby("pitch_type")["release_speed"]
            .mean()
            .round(1)
            .to_dict()
        )

        result = {
            "pitch_mix":      pitch_mix,
            "velo_by_pitch":  velo_by_pitch,
            "avg_fastball_velo": None,
            "sample_days":    days_back,
        }

        # Fastball velocity (FF = 4-seam, SI = sinker, FT = 2-seam)
        for fb_type in ["FF", "SI", "FT"]:
            if fb_type in velo_by_pitch:
                result["avg_fastball_velo"] = velo_by_pitch[fb_type]
                break

        if not batted.empty:
            hard_hit  = batted[batted["launch_speed"] >= 95]
            fly_balls = batted[batted["launch_angle"] >= 25]
            ground_balls = batted[batted["launch_angle"] < 10]

            barrel_col = "barrel" if "barrel" in batted.columns else None
            result.update({
                "hard_hit_pct_allowed":    round(len(hard_hit) / len(batted) * 100, 1),
                "fly_ball_rate_allowed":   round(len(fly_balls) / len(batted) * 100, 1),
                "ground_ball_rate":        round(len(ground_balls) / len(batted) * 100, 1),
                "barrel_pct_allowed":      round(batted[barrel_col].sum() / len(batted) * 100, 1) if barrel_col else None,
                "exit_velo_avg_allowed":   round(batted["launch_speed"].mean(), 1),
            })

        return result

    except Exception as e:
        print(f"Statcast pitcher fetch failed for player {player_id}: {e}")
        return {}


async def get_pitcher_statcast(player_id: int, days_back: int = 30) -> dict:
    """Async wrapper — runs pitcher Statcast pull in a thread pool."""
    return await asyncio.to_thread(_fetch_pitcher_statcast_sync, player_id, days_back)


# ── Pitcher Season Stats (HR/9, ERA, etc.) ────────────────────────────────────

def _fetch_pitcher_season_sync(season: Optional[int] = None) -> pd.DataFrame:
    """
    Pulls season-level pitching stats for all pitchers via FanGraphs.
    Includes HR/9, ERA, FIP, K/9, BB/9.
    """
    if not PYBASEBALL_AVAILABLE:
        return pd.DataFrame()

    if not season:
        season = date.today().year

    try:
        df = pitching_stats(season, qual=10)  # min 10 IP
        return df
    except Exception as e:
        print(f"FanGraphs pitching stats fetch failed: {e}")
        return pd.DataFrame()


async def get_pitcher_season_stats(season: Optional[int] = None) -> pd.DataFrame:
    """Async wrapper for season-level pitcher stats."""
    return await asyncio.to_thread(_fetch_pitcher_season_sync, season)
