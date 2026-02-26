"""
BaseballIQ Backtest Script — Real Data Edition
===============================================
Uses your actual services for every data point:
  - Statcast (barrel%, EV, xwOBA, pull%, HR/FB) via services/statcast.py
  - Park factors (HR factor, dimensions, walls) via models/park_factors.py
  - Weather (temp, wind, carry) via services/weather.py
  - Pitcher stats (HR/9, K/9, barrel% allowed, xFIP) via statcast + MLB API
  - Season stats (AVG, SLG, OBP, ISO) via MLB Stats API

Usage:
    python test_backtest.py
    python test_backtest.py --date 2024-08-15 --prop "Home Run"
    python test_backtest.py --date 2024-09-01 --prop "Hit"
    python test_backtest.py --date 2024-07-04 --prop "Pitcher Strikeout"

Note: First run is slow (Statcast pulls ~2-5s per player).
      pybaseball caches to disk so subsequent runs on same date are fast.
"""

import asyncio
import httpx
import argparse
from datetime import date, datetime, timedelta
from typing import Optional
from collections import defaultdict

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Your existing services ────────────────────────────────────────────────────
from models.scoring import (
    score_prop, PropInput, HitterData, PitcherData,
    ParkData, WeatherData, SituationalData
)
from models.park_factors import get_park_data
from services.statcast import (
    get_hitter_statcast, get_pitcher_statcast,
    _lookup_player_id, PYBASEBALL_AVAILABLE
)
from services.weather import get_stadium_weather

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# Thresholds define when to predict YES.
# HR threshold is HIGH because HRs are rare — we only want to flag
# the genuinely elite power matchups, not half the lineup.
# On a 7-game slate (~126 players), roughly 5-8 should get YES for HR props.
PROP_THRESHOLDS = {
    "Home Run":          70.0,   # only elite power hitters in good matchups
    "Hit":               62.0,
    "Stolen Base":       58.0,
    "Strikeout":         60.0,
    "RBI":               60.0,
    "Pitcher Strikeout": 60.0,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def safe_float(val, default=None):
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default

def did_prop_hit(stats: dict, prop_type: str) -> Optional[bool]:
    if not stats:
        return None
    if prop_type == "Home Run":
        v = stats.get("homeRuns");    return int(v) >= 1 if v is not None else None
    elif prop_type == "Hit":
        v = stats.get("hits");        return int(v) >= 1 if v is not None else None
    elif prop_type == "Stolen Base":
        v = stats.get("stolenBases"); return int(v) >= 1 if v is not None else None
    elif prop_type == "Strikeout":
        v = stats.get("strikeOuts");  return int(v) >= 1 if v is not None else None
    elif prop_type == "RBI":
        v = stats.get("rbi");         return int(v) >= 1 if v is not None else None
    elif prop_type == "Pitcher Strikeout":
        v = stats.get("strikeOuts");  return int(v) >= 4 if v is not None else None
    return None

def split_name(full_name: str) -> tuple:
    """'Shohei Ohtani' -> ('Ohtani', 'Shohei')"""
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return full_name, ""


# ── MLB API calls ─────────────────────────────────────────────────────────────

async def get_games(dt_str: str, client: httpx.AsyncClient) -> list:
    resp = await client.get(f"{MLB_BASE}/schedule", params={
        "sportId": 1, "date": dt_str,
        "hydrate": "team,venue,probablePitcher",
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    games = []
    for de in data.get("dates", []):
        for g in de.get("games", []):
            status = g.get("status", {}).get("detailedState", "")
            if not any(s in status for s in ["Final", "Completed", "Game Over"]):
                continue
            def ep(td):
                p = td.get("probablePitcher")
                return {"id": p["id"], "name": p["fullName"], "hand": p.get("pitchHand", {}).get("code", "R")} if p else None
            games.append({
                "game_pk":      g["gamePk"],
                "venue":        g["venue"]["name"],
                "home_team":    g["teams"]["home"]["team"]["abbreviation"],
                "away_team":    g["teams"]["away"]["team"]["abbreviation"],
                "home_pitcher": ep(g["teams"]["home"]),
                "away_pitcher": ep(g["teams"]["away"]),
            })
    return games

async def get_boxscore(game_pk: int, client: httpx.AsyncClient) -> dict:
    resp = await client.get(f"{MLB_BASE}/game/{game_pk}/boxscore", timeout=10)
    resp.raise_for_status()
    return resp.json()

async def get_mlb_season_stats(player_id: int, season: int, group: str, client: httpx.AsyncClient) -> dict:
    resp = await client.get(f"{MLB_BASE}/people/{player_id}/stats", params={
        "stats": "season", "season": season, "group": group,
    }, timeout=8)
    if resp.status_code != 200:
        return {}
    splits = resp.json().get("stats", [{}])[0].get("splits", [])
    return splits[0].get("stat", {}) if splits else {}


# ── Real data builders ────────────────────────────────────────────────────────

# MLB Stats API venue names don't always match park_factors.py keys
VENUE_NAME_MAP = {
    'Oriole Park at Camden Yards': 'Camden Yards',
    'Oakland-Alameda County Coliseum': 'Oakland Coliseum',
    'LoanDepot Park': 'loanDepot park',
    'Marlins Park': 'loanDepot park',
    'SunTrust Park': 'Truist Park',
    'Busch Stadium (2006)': 'Busch Stadium',
    'US Cellular Field': 'Guaranteed Rate Field',
    'Safeco Field': 'T-Mobile Park',
    'Angels Stadium': 'Angel Stadium',
    'Angels Stadium of Anaheim': 'Angel Stadium',
    'Nationals Park': 'Nationals Park',
    'Great American Ball Park': 'Great American Ball Park',
    'Globe Life Park in Arlington': 'Globe Life Field',
    'Globe Life Park': 'Globe Life Field',
}

def build_park_data(venue_name: str) -> ParkData:
    normalized = VENUE_NAME_MAP.get(venue_name, venue_name)
    raw = get_park_data(normalized)
    return ParkData(
        name           = venue_name,
        hr_factor      = raw.get("hr_factor", 1.00),
        altitude_ft    = raw.get("altitude_ft", 0),
        is_dome        = raw.get("dome", False),
        lf_dist        = raw.get("lf"),
        cf_dist        = raw.get("cf"),
        rf_dist        = raw.get("rf"),
        lf_wall_height = raw.get("lf_wall"),
        rf_wall_height = raw.get("rf_wall"),
        surface        = raw.get("surface", "grass"),
    )

async def build_weather_data(venue_name: str, is_dome: bool) -> WeatherData:
    if is_dome:
        return WeatherData(hr_wind_effect="dome")
    try:
        w = await get_stadium_weather(venue_name)
        if "error" in w:
            return WeatherData()
        carry = w.get("carry_modifier", "0ft")
        try:
            carry_ft = float(str(carry).replace("ft", "").replace("+", ""))
        except:
            carry_ft = 0.0
        return WeatherData(
            temp_f            = w.get("temp_f", 72.0),
            wind_speed_mph    = w.get("wind_speed_mph", 5.0),
            hr_wind_effect    = w.get("hr_wind_effect", "neutral"),
            wind_component    = w.get("wind_component", 0.0) if isinstance(w.get("wind_component"), (int, float)) else 0.0,
            humidity_pct      = w.get("humidity_pct", 50.0),
            carry_modifier_ft = carry_ft,
        )
    except Exception as e:
        print(f"  ⚠ Weather failed for {venue_name}: {e}")
        return WeatherData()


# ── Statcast date-range fetchers ──────────────────────────────────────────────

def _date_range_before(backtest_date: str, days_back: int = 30):
    end_dt   = datetime.strptime(backtest_date, "%Y-%m-%d").date() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=days_back)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")

def _fetch_hitter_statcast_range(player_id: int, start: str, end: str) -> dict:
    try:
        from pybaseball import statcast_batter
        import pandas as pd
        df = statcast_batter(start, end, player_id)
        if df is None or df.empty:
            return {}
        batted = df[df["type"] == "X"].copy()
        if batted.empty:
            return {}
        total   = len(batted)
        hard    = batted[batted["launch_speed"] >= 95]
        fbs     = batted[batted["launch_angle"] >= 25]
        barrels = int(batted["barrel"].sum()) if "barrel" in batted.columns else 0
        hrs     = df[df["events"] == "home_run"]
        hr_per_fb = round(len(hrs) / max(1, len(fbs)) * 100, 1) if len(fbs) > 0 else None
        pull_air = None
        if "bb_type" in batted.columns:
            pulled_fb = len(batted[(batted["bb_type"] == "fly_ball")]) * 0.45
            pull_air = round(pulled_fb / total * 100, 1)
        xwoba = round(df["estimated_woba_using_speedangle"].dropna().mean(), 3) if "estimated_woba_using_speedangle" in df.columns else None
        return {
            "exit_velo_avg":    round(batted["launch_speed"].mean(), 1) if batted["launch_speed"].notna().any() else None,
            "barrel_pct":       round(barrels / total * 100, 1),
            "hard_hit_pct":     round(len(hard) / total * 100, 1),
            "launch_angle_avg": round(batted["launch_angle"].mean(), 1) if batted["launch_angle"].notna().any() else None,
            "fly_ball_rate":    round(len(fbs) / total * 100, 1),
            "hr_per_fb":        hr_per_fb,
            "pull_air_pct":     pull_air,
            "xwoba":            xwoba,
        }
    except Exception:
        return {}

def _fetch_pitcher_statcast_range(player_id: int, start: str, end: str) -> dict:
    try:
        from pybaseball import statcast_pitcher
        import pandas as pd
        df = statcast_pitcher(start, end, player_id)
        if df is None or df.empty:
            return {}
        result = {}
        total_pitches = len(df)
        if total_pitches > 0:
            velo_by_pitch = df.groupby("pitch_type")["release_speed"].mean().round(1).to_dict()
            for fb_type in ["FF", "SI", "FT"]:
                if fb_type in velo_by_pitch:
                    result["avg_fastball_velo"] = velo_by_pitch[fb_type]
                    break
            swings = df[df["description"].isin(["swinging_strike","swinging_strike_blocked","foul","foul_tip","hit_into_play"])]
            whiffs = df[df["description"].isin(["swinging_strike","swinging_strike_blocked"])]
            if len(swings) > 0:
                result["whiff_rate"] = round(len(whiffs) / len(swings) * 100, 1)
            result["sw_str_pct"] = round(len(whiffs) / total_pitches * 100, 1)
        batted = df[df["type"] == "X"].copy()
        if not batted.empty:
            total_bb = len(batted)
            hard     = batted[batted["launch_speed"] >= 95]
            fbs      = batted[batted["launch_angle"] >= 25]
            gbs      = batted[batted["launch_angle"] < 10]
            barrels  = int(batted["barrel"].sum()) if "barrel" in batted.columns else 0
            hrs_a    = df[df["events"] == "home_run"]
            result.update({
                "hard_hit_pct_allowed":  round(len(hard) / total_bb * 100, 1),
                "barrel_pct_allowed":    round(barrels / total_bb * 100, 1),
                "fly_ball_rate_allowed": round(len(fbs) / total_bb * 100, 1),
                "ground_ball_rate":      round(len(gbs) / total_bb * 100, 1),
                "hr_per_fb_allowed":     round(len(hrs_a) / max(1, len(fbs)) * 100, 1) if len(fbs) > 0 else None,
            })
        return result
    except Exception:
        return {}

async def get_hitter_sc(savant_id: int, backtest_date: str) -> dict:
    start, end = _date_range_before(backtest_date)
    return await asyncio.to_thread(_fetch_hitter_statcast_range, savant_id, start, end)

async def get_pitcher_sc(savant_id: int, backtest_date: str) -> dict:
    start, end = _date_range_before(backtest_date)
    return await asyncio.to_thread(_fetch_pitcher_statcast_range, savant_id, start, end)


# ── PropInput builders ────────────────────────────────────────────────────────

def build_hitter_input(name, team, opp, mlb_stats, sc, pitcher_hand, lineup_pos, prop_type, park, weather) -> PropInput:
    avg    = safe_float(mlb_stats.get("avg"),  0.250)
    slg    = safe_float(mlb_stats.get("slg"),  0.400)
    obp    = safe_float(mlb_stats.get("obp"),  0.320)
    hr     = safe_int(mlb_stats.get("homeRuns"), 0)
    ab     = safe_int(mlb_stats.get("atBats"),   1)
    pa     = safe_int(mlb_stats.get("plateAppearances"), ab)
    sb     = safe_int(mlb_stats.get("stolenBases"), 0)
    hits   = safe_int(mlb_stats.get("hits"), 0)
    games  = safe_int(mlb_stats.get("gamesPlayed"), max(1, ab // 4))
    k_rate = safe_float(mlb_stats.get("strikeoutRate"), None)

    iso = round(slg - avg, 3) if slg and avg else 0.150

    # Real Statcast takes priority; ISO-based estimates are fallback only
    exit_velo   = sc.get("exit_velo_avg")    or max(84.0, min(96.0, 87.0 + (iso * 27)))
    barrel_pct  = sc.get("barrel_pct")       or max(2.0,  min(20.0, 2.0  + (iso * 55)))
    hard_hit    = sc.get("hard_hit_pct")     or max(28.0, min(55.0, 28.0 + (iso * 90)))
    launch_ang  = sc.get("launch_angle_avg") or max(10.0, min(28.0, 12.0 + (iso * 45)))
    fly_ball_r  = sc.get("fly_ball_rate")    or 35.0
    hr_per_fb   = sc.get("hr_per_fb")        or (round((hr / max(1, ab * 0.35)) * 100, 1) if ab > 20 else None)
    pull_air    = sc.get("pull_air_pct")     or max(4.0, min(18.0, 5.0 + (iso * 40)))
    xwoba       = sc.get("xwoba")            or max(0.28, min(0.52, obp * 0.45 + slg * 0.38))

    lp = (0.830 if lineup_pos in [3,4] else 0.790 if lineup_pos in [2,5] else 0.750 if lineup_pos in [1,6] else 0.700) if lineup_pos else 0.740

    hitter = HitterData(
        exit_velo_avg    = exit_velo,
        barrel_pct       = barrel_pct,
        hard_hit_pct     = hard_hit,
        launch_angle_avg = launch_ang,
        fly_ball_rate    = fly_ball_r,
        xwoba            = xwoba,
        iso              = iso,
        hr_per_fb        = hr_per_fb,
        pull_air_pct     = pull_air,
        hr_rate          = round(hr / max(1, pa), 4),
        games_played     = games,
        pitcher_hand     = pitcher_hand,
        vs_rhp_avg       = avg if pitcher_hand == "R" else None,
        vs_lhp_avg       = avg if pitcher_hand == "L" else None,
        vs_rhp_iso       = iso if pitcher_hand == "R" else None,
        vs_lhp_iso       = iso if pitcher_hand == "L" else None,
        contact_rate     = (1 - (k_rate / 100)) * 100 if k_rate else None,
        k_rate           = k_rate,
        babip            = safe_float(mlb_stats.get("babip"), None),
        sprint_speed     = 27.5 if sb > 15 else 26.5,
        sb_success_rate  = 78.0 if sb > 10 else 70.0,
        hr_last_10g      = min(5,  round((hr / max(1, ab)) * 30)),
        hr_last_30g      = min(12, round((hr / max(1, ab)) * 90)),
        hits_last_10g    = min(18, hits // max(1, ab // 30)),
        sb_last_10g      = min(4,  sb // max(1, ab // 30)),
    )
    return PropInput(
        prop_type=prop_type, player_name=name, team=team, opponent=opp,
        implied_prob=0.35, over_odds=-115,
        hitter=hitter, park=park, weather=weather,
        situational=SituationalData(
            lineup_position    = lineup_pos,
            proj_plate_apps    = max(2.5, 4.5 - (lineup_pos - 1) * 0.15) if lineup_pos else 3.5,
            implied_team_total = 4.5,
            lineup_protection  = lp,
        )
    )

def build_pitcher_input(name, team, opp, mlb_stats, sc, prop_type, park, weather) -> PropInput:
    k9   = safe_float(mlb_stats.get("strikeoutsPer9Inn"), 8.0)
    ip   = safe_float(mlb_stats.get("inningsPitched"),    50.0)
    hr9  = safe_float(mlb_stats.get("homeRunsPer9"),       1.20)

    pitcher = PitcherData(
        k_per_9              = k9,
        k_per_9_recent       = k9,
        k_pct                = min(35, max(12, k9 / 9 * 100 * 0.28)),
        k_pct_recent         = min(35, max(12, k9 / 9 * 100 * 0.28)),
        whiff_rate           = sc.get("whiff_rate")           or max(20, min(38, k9 * 2.2)),
        sw_str_pct           = sc.get("sw_str_pct")           or max(7,  min(16, k9 * 1.1)),
        avg_fastball_velo    = sc.get("avg_fastball_velo")    or 92.5,
        barrel_pct_allowed   = sc.get("barrel_pct_allowed"),
        hard_contact_pct     = sc.get("hard_hit_pct_allowed"),
        fly_ball_rate_allowed= sc.get("fly_ball_rate_allowed"),
        ground_ball_rate     = sc.get("ground_ball_rate"),
        hr_per_fb_allowed    = sc.get("hr_per_fb_allowed"),
        hr_per_9             = hr9,
        hr_per_9_recent      = hr9,
        proj_innings         = 6.0 if ip > 30 else 1.5,
    )
    return PropInput(
        prop_type=prop_type, player_name=name, team=team, opponent=opp,
        implied_prob=0.50, over_odds=-115,
        pitcher=pitcher, park=park, weather=weather,
        situational=SituationalData(
            proj_innings = pitcher.proj_innings,
            is_starter   = ip > 30,
            game_total   = 8.5,
        )
    )


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_backtest(dt_str: str, prop_type: str):
    season = int(dt_str[:4])
    is_pitcher_prop = prop_type == "Pitcher Strikeout"
    threshold = PROP_THRESHOLDS.get(prop_type, 60.0)

    print(f"\n{'='*62}")
    print(f"  BaseballIQ Backtest — {dt_str} | {prop_type}")
    if PYBASEBALL_AVAILABLE:
        print(f"  Data: MLB API + Statcast + Park Factors + Weather ✓")
    else:
        print(f"  ⚠  pybaseball not installed — using MLB API + estimates")
        print(f"     Run: pip install pybaseball  for full Statcast data")
    print(f"{'='*62}\n")

    async with httpx.AsyncClient() as client:
        print("Fetching schedule...", end=" ", flush=True)
        games = await get_games(dt_str, client)
        if not games:
            print(f"\n❌ No completed games found for {dt_str}")
            return
        print(f"{len(games)} games\n")

        all_results = []

        for game in games[:8]:
            home, away = game["home_team"], game["away_team"]
            venue = game["venue"]
            print(f"  {away} @ {home} — {venue}")

            # Real park + weather
            park    = build_park_data(venue)
            weather = await build_weather_data(venue, park.is_dome)
            if park.is_dome:
                print(f"    Park: {park.hr_factor:.2f}x HR factor | dome")
            else:
                print(f"    Park: {park.hr_factor:.2f}x HR factor | {weather.temp_f}°F | wind {weather.hr_wind_effect} | carry {weather.carry_modifier_ft:+.1f}ft")

            try:
                box = await get_boxscore(game["game_pk"], client)
            except Exception as e:
                print(f"    ❌ Boxscore failed: {e}\n")
                continue

            # Pre-fetch pitcher Statcast (one pull per starting pitcher)
            pitcher_sc_cache = {}
            pitcher_mlb_cache = {}
            for side in ["home", "away"]:
                opp_side = "away" if side == "home" else "home"
                pi = game.get(f"{opp_side}_pitcher")
                if pi and pi.get("id") and pi["id"] not in pitcher_sc_cache:
                    pname = pi.get("name", "")
                    print(f"    Pitcher Statcast: {pname}...", end=" ", flush=True)
                    last, first = split_name(pname)
                    savant_id = await asyncio.to_thread(_lookup_player_id, last, first) if PYBASEBALL_AVAILABLE else None
                    sc = await get_pitcher_sc(savant_id, dt_str) if savant_id else {}
                    mlb_p = await get_mlb_season_stats(pi["id"], season, "pitching", client)
                    pitcher_sc_cache[pi["id"]]  = sc
                    pitcher_mlb_cache[pi["id"]] = mlb_p
                    print(f"{'✓ real Statcast' if sc else 'MLB stats only'}")

            game_results = []

            for side in ["home", "away"]:
                team_abbr  = home if side == "home" else away
                opp_abbr   = away if side == "home" else home
                opp_side   = "away" if side == "home" else "home"
                opp_pitcher = game.get(f"{opp_side}_pitcher")
                pitcher_hand = opp_pitcher.get("hand", "R") if opp_pitcher else "R"
                opp_pitcher_id = opp_pitcher.get("id") if opp_pitcher else None

                # Pitcher's real data
                p_sc  = pitcher_sc_cache.get(opp_pitcher_id, {})
                p_mlb = pitcher_mlb_cache.get(opp_pitcher_id, {})

                team_data  = box.get("teams", {}).get(side, {})
                players    = team_data.get("players", {})
                player_ids = team_data.get("pitchers" if is_pitcher_prop else "battingOrder", [])

                print(f"    [{team_abbr}]", end=" ", flush=True)

                for order_idx, pid in enumerate(player_ids[:9]):
                    key    = f"ID{pid}"
                    player = players.get(key, {})
                    person = player.get("person", {})
                    name   = person.get("fullName", "Unknown")

                    stat_group   = "pitching" if is_pitcher_prop else "batting"
                    actual_stats = player.get("stats", {}).get(stat_group, {})
                    actual = did_prop_hit(actual_stats, prop_type)
                    if actual is None:
                        continue

                    # MLB season stats
                    group     = "pitching" if is_pitcher_prop else "hitting"
                    mlb_stats = await get_mlb_season_stats(pid, season, group, client)

                    # Real Statcast for this hitter
                    hitter_sc = {}
                    if PYBASEBALL_AVAILABLE:
                        last, first = split_name(name)
                        savant_id = await asyncio.to_thread(_lookup_player_id, last, first)
                        if savant_id:
                            hitter_sc = await get_hitter_sc(savant_id, dt_str)

                    # Build PropInput with real data everywhere
                    if is_pitcher_prop:
                        prop_input = build_pitcher_input(
                            name, team_abbr, team_abbr,
                            mlb_stats, hitter_sc,
                            prop_type, park, weather)
                    else:
                        prop_input = build_hitter_input(
                            name, team_abbr, opp_abbr,
                            mlb_stats, hitter_sc,
                            pitcher_hand, order_idx + 1,
                            prop_type, park, weather
                        )
                        # Wire pitcher data: MLB season stats first (baseline),
                        # Statcast on top where available (more granular)
                        if p_mlb:
                            hr9 = safe_float(p_mlb.get("homeRunsPer9"))
                            k9  = safe_float(p_mlb.get("strikeoutsPer9Inn"))
                            prop_input.pitcher.hr_per_9        = hr9
                            prop_input.pitcher.hr_per_9_recent = hr9
                            prop_input.pitcher.k_per_9         = k9
                            prop_input.pitcher.k_per_9_recent  = k9
                            prop_input.pitcher.hits_per_9      = safe_float(p_mlb.get("hitsPer9Inn"))
                            prop_input.pitcher.babip_allowed   = safe_float(p_mlb.get("babip"))
                            # Estimate GB rate from HR/9 if Statcast unavailable
                            # High HR/9 pitchers tend to be fly-ball heavy (low GB rate)
                            if hr9:
                                prop_input.pitcher.ground_ball_rate = max(30.0, min(60.0, 50.0 - (hr9 - 1.2) * 8))
                        if p_sc:
                            # Statcast overrides MLB estimates where data exists
                            for sc_key, attr in [
                                ("barrel_pct_allowed",   "barrel_pct_allowed"),
                                ("hard_hit_pct_allowed", "hard_contact_pct"),
                                ("fly_ball_rate_allowed","fly_ball_rate_allowed"),
                                ("ground_ball_rate",     "ground_ball_rate"),
                                ("hr_per_fb_allowed",    "hr_per_fb_allowed"),
                                ("whiff_rate",           "whiff_rate"),
                                ("sw_str_pct",           "sw_str_pct"),
                                ("avg_fastball_velo",    "avg_fastball_velo"),
                            ]:
                                val = p_sc.get(sc_key)
                                if val is not None:
                                    setattr(prop_input.pitcher, attr, val)

                    result    = score_prop(prop_input)
                    predicted = result.confidence >= threshold

                    game_results.append({
                        "name":       name,
                        "team":       team_abbr,
                        "confidence": result.confidence,
                        "grade":      result.grade,
                        "edge":       result.edge_str,
                        "predicted":  predicted,
                        "actual":     actual,
                        "correct":    predicted == actual,
                        "matchup":    f"{away}@{home}",
                        "had_sc":     bool(hitter_sc),
                        "category_scores": result.category_scores,
                    })
                    print(".", end="", flush=True)

                print(f" {len([r for r in game_results if r['team'] == team_abbr])} done")

            print(f"    → {len(game_results)} players evaluated\n")
            all_results.extend(game_results)

    if not all_results:
        print("❌ No results. Try a mid-season date e.g. 2024-08-15")
        return

    all_results.sort(key=lambda x: x["confidence"], reverse=True)

    correct  = [r for r in all_results if r["correct"]]
    top10    = all_results[:10]
    top10_c  = [r for r in top10 if r["correct"]]
    pred_yes = [r for r in all_results if r["predicted"]]
    yes_hits = [r for r in pred_yes if r["actual"]]
    with_sc  = [r for r in all_results if r["had_sc"]]

    print(f"\n{'─'*62}")
    print(f"  RESULTS SUMMARY")
    print(f"{'─'*62}")
    print(f"  Total players scored  : {len(all_results)}")
    print(f"  With real Statcast    : {len(with_sc)} / {len(all_results)}")
    print(f"  Overall accuracy      : {len(correct)/len(all_results)*100:.1f}%  ({len(correct)}/{len(all_results)})")
    print(f"  Top-10 accuracy       : {len(top10_c)/len(top10)*100:.1f}%  ({len(top10_c)}/{len(top10)})  ← key metric")
    yes_prec = f"{len(yes_hits)/len(pred_yes)*100:.1f}%  ({len(yes_hits)}/{len(pred_yes)})" if pred_yes else "N/A — no YES predictions"
    print(f"  YES precision         : {yes_prec}")
    print(f"{'─'*62}")

    print(f"\n  TOP 20 PICKS\n")
    print(f"  {'PLAYER':<22} {'TEAM':<5} {'GR':<4} {'CONF':<7} {'EDGE':<8} {'SC':<4} {'PRED':<5} {'ACTUAL':<6} ✓")
    print(f"  {'─'*75}")
    for r in all_results[:20]:
        print(
            f"  {r['name']:<22} {r['team']:<5} {r['grade']:<4} "
            f"{r['confidence']:<7.1f} {r['edge']:<8} "
            f"{'✓' if r['had_sc'] else '~':<4} "
            f"{'YES' if r['predicted'] else 'NO':<5} "
            f"{'HIT' if r['actual'] else 'MISS':<6} "
            f"{'✅' if r['correct'] else '❌'}"
        )

    print(f"\n  ACCURACY BY GRADE\n")
    by_grade = defaultdict(list)
    for r in all_results:
        by_grade[r["grade"]].append(r)
    for grade in ["A+", "A", "A−", "B+", "B", "B−", "C+", "C", "D"]:
        grp = by_grade.get(grade, [])
        if not grp:
            continue
        acc = sum(1 for r in grp if r["correct"]) / len(grp) * 100
        bar = "█" * int(acc / 5)
        print(f"  {grade:<4} ({len(grp):>3} players)  {acc:>5.1f}%  {bar}")

    if with_sc and len(with_sc) < len(all_results):
        no_sc     = [r for r in all_results if not r["had_sc"]]
        sc_acc    = sum(1 for r in with_sc if r["correct"]) / len(with_sc)  * 100
        no_sc_acc = sum(1 for r in no_sc   if r["correct"]) / len(no_sc)   * 100
        print(f"\n  STATCAST IMPACT")
        print(f"  With real Statcast : {sc_acc:.1f}%  ({len(with_sc)} players)")
        print(f"  Estimates only     : {no_sc_acc:.1f}%  ({len(no_sc)} players)")

    print(f"\n{'='*62}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BaseballIQ Backtest — Real Data")
    parser.add_argument("--date", default="2024-08-15")
    parser.add_argument("--prop", default="Home Run")
    args = parser.parse_args()
    valid = ["Home Run", "Hit", "Stolen Base", "Strikeout", "RBI", "Pitcher Strikeout"]
    if args.prop not in valid:
        print(f"❌ Must be one of: {', '.join(valid)}")
        sys.exit(1)
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("❌ Date must be YYYY-MM-DD")
        sys.exit(1)
    asyncio.run(run_backtest(args.date, args.prop))
