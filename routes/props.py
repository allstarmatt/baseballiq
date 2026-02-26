"""
Props API Routes — All 5 Prop Types
GET /api/props              → all props sorted by confidence
GET /api/props/{prop_slug}  → props filtered by type
POST /api/props/refresh     → manual cache refresh
"""

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from typing import Optional
import asyncio
from datetime import datetime

from services.mlb      import get_todays_games, get_game_lineup
from services.statcast import get_hitter_statcast, get_pitcher_statcast
from services.odds     import get_game_totals, get_player_props
from services.weather  import get_stadium_weather
from models.scoring    import (
    PropInput, HitterData, PitcherData, ParkData, WeatherData, SituationalData,
    score_prop
)
from models.park_factors import get_park_data

router = APIRouter()

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}
_cache_time: dict = {}
CACHE_TTL_SECONDS = 300

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_BATTERS_PER_TEAM = 4
MAX_GAMES_PER_DAY    = 4
PLAYER_TIMEOUT_SEC   = 8
GAME_TIMEOUT_SEC     = 12

# ── Prop type config ──────────────────────────────────────────────────────────
PROP_SLUG_MAP = {
    "home-run":          "Home Run",
    "hit":               "Hit",
    "stolen-base":       "Stolen Base",
    "strikeout":         "Strikeout",
    "rbi":               "RBI",
    "pitcher-strikeout": "Pitcher Strikeout",
}

# Maps prop type → Odds API market key
ODDS_MARKET_MAP = {
    "Home Run":          "batter_home_runs",
    "Hit":               "batter_hits",
    "Stolen Base":       "batter_stolen_bases",
    "Strikeout":         "batter_strikeouts",
    "RBI":               "batter_rbis",
    "Pitcher Strikeout": "pitcher_strikeouts",
}

ALL_PROP_TYPES = list(ODDS_MARKET_MAP.keys())


def _is_cache_fresh(key: str) -> bool:
    if key not in _cache_time:
        return False
    age = (datetime.utcnow() - _cache_time[key]).total_seconds()
    return age < CACHE_TTL_SECONDS


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def get_all_props(
    sort_by:   str           = Query("confidence", enum=["confidence", "edge"]),
    min_grade: str           = Query("all",        enum=["all", "a", "b", "c"]),
    prop_type: Optional[str] = Query(None),
):
    cache_key = "all_props"
    if not _is_cache_fresh(cache_key):
        props = await _build_all_props()
        _cache[cache_key]      = props
        _cache_time[cache_key] = datetime.utcnow()
    else:
        props = _cache[cache_key]

    if prop_type:
        props = [p for p in props if p["prop_type"] == prop_type]
    if min_grade == "a":
        props = [p for p in props if p["confidence"] >= 80]
    elif min_grade == "b":
        props = [p for p in props if p["confidence"] >= 70]
    elif min_grade == "c":
        props = [p for p in props if p["confidence"] >= 60]

    if sort_by == "edge":
        props.sort(key=lambda x: x.get("edge", 0), reverse=True)
    else:
        props.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    cache_age = (datetime.utcnow() - _cache_time.get(cache_key, datetime.utcnow())).seconds
    return {"props": props, "count": len(props), "generated_at": datetime.utcnow().isoformat(), "cache_age_s": cache_age}


@router.get("/{prop_slug}")
async def get_props_by_type(prop_slug: str, sort_by: str = Query("confidence", enum=["confidence", "edge"])):
    prop_type = PROP_SLUG_MAP.get(prop_slug.lower())
    if not prop_type:
        raise HTTPException(status_code=404, detail=f"Unknown prop type '{prop_slug}'")
    return await get_all_props(sort_by=sort_by, prop_type=prop_type)


@router.post("/refresh")
async def refresh_props(background_tasks: BackgroundTasks):
    background_tasks.add_task(_refresh_cache)
    return {"message": "Refresh started."}


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def _build_all_props() -> list[dict]:
    print(f"🔄  Building all props — {datetime.utcnow().strftime('%H:%M:%S')} UTC")

    try:
        games, game_totals = await asyncio.wait_for(
            asyncio.gather(get_todays_games(), get_game_totals()),
            timeout=15.0
        )
    except Exception as e:
        print(f"❌  Error fetching games/odds: {e}")
        return []

    if not games:
        print("⚠️  No games today")
        return []

    print(f"📅  {len(games)} games found")

    # Build totals lookup
    totals_lookup: dict = {}
    for gt in game_totals:
        totals_lookup[gt.get("home_team", "")] = {"game_total": gt.get("game_total"), "team_implied": gt.get("home_implied"), "event_id": gt.get("event_id")}
        totals_lookup[gt.get("away_team", "")] = {"game_total": gt.get("game_total"), "team_implied": gt.get("away_implied"), "event_id": gt.get("event_id")}

    # Fetch odds for all prop types per game
    odds_lookup = await _build_odds_lookup(games[:MAX_GAMES_PER_DAY], totals_lookup)

    all_results = []
    for game in games[:MAX_GAMES_PER_DAY]:
        print(f"⚾  {game['away_team']} @ {game['home_team']}")
        try:
            results = await _process_game(game, totals_lookup, odds_lookup)
            all_results.extend(results)
            print(f"   ✓ {len(results)} props")
        except Exception as e:
            print(f"   ✗ {e}")

    print(f"✅  {len(all_results)} total props")
    return all_results


async def _build_odds_lookup(games: list, totals_lookup: dict) -> dict:
    """
    Fetches odds for all prop types for all games.
    Returns dict: {player_name_lower: {prop_type: {over_odds, implied_prob, line}}}
    """
    lookup: dict = {}

    for game in games:
        home_team = game.get("home_team", "")
        tc = totals_lookup.get(home_team, {})
        event_id = tc.get("event_id")
        if not event_id:
            continue

        for prop_type in ALL_PROP_TYPES:
            try:
                props = await asyncio.wait_for(
                    get_player_props(event_id, prop_type),
                    timeout=8.0
                )
                for prop in props:
                    name_key = prop["player_name"].lower().strip()
                    if name_key not in lookup:
                        lookup[name_key] = {}
                    lookup[name_key][prop_type] = {
                        "over_odds":    prop.get("over_odds", 300),
                        "under_odds":   prop.get("under_odds"),
                        "implied_prob": prop.get("implied_prob_over", 25.0) / 100,
                        "line":         prop.get("line", 0.5),
                    }
            except Exception as e:
                print(f"   Odds fetch failed for {prop_type}: {e}")

    return lookup


async def _process_game(game: dict, totals_lookup: dict, odds_lookup: dict) -> list[dict]:
    game_pk = game["game_pk"]
    venue   = game["venue"]

    try:
        lineup, weather = await asyncio.wait_for(
            asyncio.gather(get_game_lineup(game_pk), get_stadium_weather(venue)),
            timeout=GAME_TIMEOUT_SEC
        )
    except Exception as e:
        print(f"   Lineup/weather failed: {e}")
        return []

    results = []

    for side in ["home", "away"]:
        team_abbr    = game[f"{side}_team"]
        team_context = totals_lookup.get(team_abbr, {})
        opp_side     = "away" if side == "home" else "home"
        pitcher_info = game.get(f"{opp_side}_probable_pitcher")
        batters      = (lineup.get(side) or [])[:MAX_BATTERS_PER_TEAM]

        # Pull Statcast for all batters + pitcher concurrently
        pitcher_id = pitcher_info.get("id") if pitcher_info else None
        statcast_tasks = [get_hitter_statcast(b["id"], days_back=30) for b in batters if b.get("id")]
        pitcher_task   = get_pitcher_statcast(pitcher_id, days_back=30) if pitcher_id else asyncio.sleep(0, result={})

        try:
            statcast_results = await asyncio.wait_for(
                asyncio.gather(*statcast_tasks, pitcher_task),
                timeout=PLAYER_TIMEOUT_SEC * len(batters)
            )
            pitcher_sc = statcast_results[-1]
            hitter_scs = statcast_results[:-1]
        except asyncio.TimeoutError:
            print(f"   Statcast timeout — using empty data")
            hitter_scs = [{} for _ in batters]
            pitcher_sc = {}

        for i, batter in enumerate(batters):
            hitter_sc = hitter_scs[i] if i < len(hitter_scs) else {}
            name_key  = (batter.get("name") or "").lower().strip()
            batter_odds = odds_lookup.get(name_key, {})

            # Score this batter for each prop type we have odds for
            for prop_type in ALL_PROP_TYPES:
                if prop_type == "Pitcher Strikeout":
                    continue  # handled separately below
                odds_data = batter_odds.get(prop_type)

                # Use real odds if available, else use defaults
                if odds_data:
                    implied_prob = odds_data["implied_prob"]
                    over_odds    = odds_data["over_odds"]
                    line         = odds_data["line"]
                else:
                    # Skip prop types with no odds data to avoid noise
                    if prop_type != "Home Run":
                        continue
                    implied_prob = 0.25
                    over_odds    = 300
                    line         = 0.5

                result = _build_prop_result(
                    batter, pitcher_info, game, team_abbr,
                    weather, team_context, side,
                    hitter_sc, pitcher_sc,
                    prop_type, implied_prob, over_odds, line
                )
                if result:
                    results.append(result)

        # ── Pitcher Strikeout prop (one per pitcher per game) ──────────────────
        own_pitcher_info = game.get(f"{side}_probable_pitcher")
        if own_pitcher_info and own_pitcher_info.get("id"):
            pitcher_name_key = (own_pitcher_info.get("name") or "").lower().strip()
            pitcher_odds_data = odds_lookup.get(pitcher_name_key, {}).get("Pitcher Strikeout")

            if pitcher_odds_data:
                try:
                    own_pitcher_sc = await asyncio.wait_for(
                        get_pitcher_statcast(own_pitcher_info["id"], days_back=30),
                        timeout=PLAYER_TIMEOUT_SEC
                    )
                except Exception:
                    own_pitcher_sc = {}

                opp_side       = "away" if side == "home" else "home"
                opp_team_abbr  = game[f"{opp_side}_team"]
                opp_context    = totals_lookup.get(opp_team_abbr, {})

                pitcher_result = _build_pitcher_k_result(
                    own_pitcher_info, game, team_abbr, opp_team_abbr,
                    weather, team_context, opp_context,
                    own_pitcher_sc,
                    pitcher_odds_data["implied_prob"],
                    pitcher_odds_data["over_odds"],
                    pitcher_odds_data["line"],
                )
                if pitcher_result:
                    results.append(pitcher_result)

    return results


def _build_prop_result(
    batter, pitcher_info, game, team_abbr,
    weather, team_context, batting_side,
    hitter_sc, pitcher_sc,
    prop_type, implied_prob, over_odds, line
) -> Optional[dict]:

    park_raw = get_park_data(game.get("venue", ""))

    hitter_data = HitterData(
        exit_velo_avg    = hitter_sc.get("exit_velo_avg"),
        exit_velo_max    = hitter_sc.get("exit_velo_max"),
        barrel_pct       = hitter_sc.get("barrel_pct"),
        hard_hit_pct     = hitter_sc.get("hard_hit_pct"),
        launch_angle_avg = hitter_sc.get("launch_angle_avg"),
        fly_ball_rate    = hitter_sc.get("fly_ball_rate"),
        pull_rate        = hitter_sc.get("pull_rate"),
        pitcher_hand     = pitcher_info.get("hand") if pitcher_info else None,
    )

    pitcher_data = PitcherData(
        hand                  = pitcher_info.get("hand") if pitcher_info else None,
        fly_ball_rate_allowed = pitcher_sc.get("fly_ball_rate_allowed"),
        ground_ball_rate      = pitcher_sc.get("ground_ball_rate"),
        barrel_pct_allowed    = pitcher_sc.get("barrel_pct_allowed"),
        hard_contact_pct      = pitcher_sc.get("hard_hit_pct_allowed"),
        pitch_mix             = pitcher_sc.get("pitch_mix"),
        avg_fastball_velo     = pitcher_sc.get("avg_fastball_velo"),
    )

    park_data = ParkData(
        name        = game.get("venue", ""),
        hr_factor   = park_raw.get("hr_factor", 1.00),
        altitude_ft = park_raw.get("altitude_ft", 0),
        is_dome     = park_raw.get("dome", False),
        lf_dist     = park_raw.get("lf"),
        cf_dist     = park_raw.get("cf"),
        rf_dist     = park_raw.get("rf"),
    )

    try:
        carry_ft = float(str(weather.get("carry_modifier", "0ft")).replace("ft", "").replace("+", "") or 0)
    except ValueError:
        carry_ft = 0.0

    weather_data = WeatherData(
        temp_f            = weather.get("temp_f", 72),
        wind_speed_mph    = weather.get("wind_speed_mph", 5),
        hr_wind_effect    = weather.get("hr_wind_effect", "neutral"),
        wind_component    = weather.get("wind_component", 0),
        humidity_pct      = weather.get("humidity_pct", 50),
        carry_modifier_ft = carry_ft,
    )

    sit_data = SituationalData(
        lineup_position    = batter.get("batting_order"),
        proj_plate_apps    = _proj_pa(batter.get("batting_order")),
        game_total         = team_context.get("game_total"),
        implied_team_total = team_context.get("team_implied"),
    )

    prop_input = PropInput(
        prop_type    = prop_type,
        player_name  = batter.get("name", ""),
        team         = team_abbr,
        opponent     = game.get("away_team" if batting_side == "home" else "home_team", ""),
        implied_prob = implied_prob,
        over_odds    = int(over_odds) if over_odds else 300,
        line         = line,
        hitter       = hitter_data,
        pitcher      = pitcher_data,
        park         = park_data,
        weather      = weather_data,
        situational  = sit_data,
    )

    result = score_prop(prop_input)

    odds_str = f"+{int(over_odds)}" if int(over_odds) > 0 else str(int(over_odds))

    return {
        "player_name":     batter.get("name"),
        "team":            team_abbr,
        "opponent":        prop_input.opponent,
        "prop_type":       prop_type,
        "line":            f"Over {line} {prop_type}",
        "confidence":      result.confidence,
        "grade":           result.grade,
        "grade_desc":      result.grade_desc,
        "model_prob":      f"{round(result.model_prob * 100, 1)}%",
        "implied_prob":    f"{round(implied_prob * 100, 1)}%",
        "edge":            result.edge,
        "edge_str":        result.edge_str,
        "over_odds":       odds_str,
        "signal":          result.signal,
        "category_scores": result.category_scores,
        "game":            f"{game['away_team']} @ {game['home_team']}",
        "venue":           game.get("venue"),
        "lineup_pos":      batter.get("batting_order"),
        "pitcher":         pitcher_info.get("name") if pitcher_info else "TBD",
        "weather_summary": {
            "temp":   weather.get("temp_f"),
            "wind":   weather.get("hr_wind_label"),
            "carry":  weather.get("carry_modifier"),
            "signal": weather.get("signal"),
        },
        "hitter_statcast": {
            "exit_velo_avg": hitter_sc.get("exit_velo_avg"),
            "barrel_pct":    hitter_sc.get("barrel_pct"),
            "hard_hit_pct":  hitter_sc.get("hard_hit_pct"),
        },
    }


def _proj_pa(lineup_pos) -> float:
    pa_map = {1:4.8, 2:4.6, 3:4.5, 4:4.4, 5:4.2, 6:4.0, 7:3.8, 8:3.6, 9:3.5}
    return pa_map.get(lineup_pos, 3.8)


def _build_pitcher_k_result(
    pitcher_info, game, team_abbr, opp_team_abbr,
    weather, team_context, opp_context,
    pitcher_sc,
    implied_prob, over_odds, line
) -> Optional[dict]:
    """Builds a scored Pitcher Strikeout prop. The player IS the pitcher."""

    park_raw = get_park_data(game.get("venue", ""))

    # For pitcher K props, the "hitter" category = opposing lineup weakness
    # We use the pitcher's own K-against metrics as a proxy for opp lineup
    hitter_data = HitterData(
        k_rate     = pitcher_sc.get("opp_k_rate"),      # opposing team K rate
        whiff_rate = pitcher_sc.get("opp_whiff_rate"),  # opposing team whiff rate
        chase_rate = pitcher_sc.get("opp_chase_rate"),  # opposing team chase rate
    )

    pitcher_data = PitcherData(
        sw_str_pct          = pitcher_sc.get("sw_str_pct"),
        k_pct               = pitcher_sc.get("k_pct"),
        k_pct_recent        = pitcher_sc.get("k_pct_recent"),
        k_per_9             = pitcher_sc.get("k_per_9"),
        k_per_9_recent      = pitcher_sc.get("k_per_9_recent"),
        whiff_rate          = pitcher_sc.get("whiff_rate"),
        avg_fastball_velo   = pitcher_sc.get("avg_fastball_velo"),
        strikeout_pitch_pct = pitcher_sc.get("strikeout_pitch_pct"),
        opp_team_k_rate     = pitcher_sc.get("opp_k_rate"),
    )

    park_data = ParkData(
        name        = game.get("venue", ""),
        hr_factor   = park_raw.get("hr_factor", 1.00),
        altitude_ft = park_raw.get("altitude_ft", 0),
        is_dome     = park_raw.get("dome", False),
    )

    try:
        carry_ft = float(str(weather.get("carry_modifier", "0ft")).replace("ft", "").replace("+", "") or 0)
    except ValueError:
        carry_ft = 0.0

    weather_data = WeatherData(
        temp_f            = weather.get("temp_f", 72),
        wind_speed_mph    = weather.get("wind_speed_mph", 5),
        hr_wind_effect    = weather.get("hr_wind_effect", "neutral"),
        humidity_pct      = weather.get("humidity_pct", 50),
        carry_modifier_ft = carry_ft,
    )

    sit_data = SituationalData(
        proj_innings       = 6.0,   # assume starter goes 6 — Phase 2: pull real projection
        is_starter         = True,
        game_total         = team_context.get("game_total"),
        implied_team_total = opp_context.get("team_implied"),  # opposing team's runs = pitcher's challenge
    )

    prop_input = PropInput(
        prop_type    = "Pitcher Strikeout",
        player_name  = pitcher_info.get("name", ""),
        team         = team_abbr,
        opponent     = opp_team_abbr,
        implied_prob = implied_prob,
        over_odds    = int(over_odds) if over_odds else 115,
        line         = line,
        hitter       = hitter_data,
        pitcher      = pitcher_data,
        park         = park_data,
        weather      = weather_data,
        situational  = sit_data,
    )

    result = score_prop(prop_input)
    odds_str = f"+{int(over_odds)}" if int(over_odds) > 0 else str(int(over_odds))

    return {
        "player_name":     pitcher_info.get("name"),
        "team":            team_abbr,
        "opponent":        opp_team_abbr,
        "prop_type":       "Pitcher Strikeout",
        "line":            f"Over {line} Ks",
        "confidence":      result.confidence,
        "grade":           result.grade,
        "grade_desc":      result.grade_desc,
        "model_prob":      f"{round(result.model_prob * 100, 1)}%",
        "implied_prob":    f"{round(implied_prob * 100, 1)}%",
        "edge":            result.edge,
        "edge_str":        result.edge_str,
        "over_odds":       odds_str,
        "signal":          result.signal,
        "category_scores": result.category_scores,
        "game":            f"{game['away_team']} @ {game['home_team']}",
        "venue":           game.get("venue"),
        "lineup_pos":      1,   # pitcher bats 9th in AL / doesn't bat — display as 1
        "pitcher":         pitcher_info.get("name"),
        "weather_summary": {
            "temp":   weather.get("temp_f"),
            "wind":   weather.get("hr_wind_label"),
            "carry":  weather.get("carry_modifier"),
            "signal": weather.get("signal"),
        },
        "hitter_statcast": {
            "k_per_9":    pitcher_sc.get("k_per_9"),
            "sw_str_pct": pitcher_sc.get("sw_str_pct"),
            "k_pct":      pitcher_sc.get("k_pct"),
        },
    }


async def _refresh_cache():
    try:
        props = await _build_all_props()
        _cache["all_props"]      = props
        _cache_time["all_props"] = datetime.utcnow()
        print(f"✅  Cache refreshed: {len(props)} props")
    except Exception as e:
        print(f"❌  Refresh failed: {e}")


# needed for type hint
from typing import Optional
