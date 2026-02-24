"""
Props API Routes
Fixed version — adds timeouts and player limits to prevent Statcast hangs.

GET /api/props              → all props sorted by confidence
GET /api/props/{prop_type}  → props filtered by type
POST /api/props/refresh     → manually trigger a data refresh
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

# ── Simple in-memory cache ────────────────────────────────────────────────────
_cache: dict = {}
_cache_time: dict = {}
CACHE_TTL_SECONDS = 300   # 5 minutes

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_BATTERS_PER_TEAM  = 5    # only score first 5 in lineup (top of order)
MAX_GAMES_PER_DAY     = 6    # cap games processed to avoid timeout
PLAYER_TIMEOUT_SEC    = 8    # max seconds per player Statcast call
GAME_TIMEOUT_SEC      = 15   # max seconds per game lineup+weather call

# ── Prop type slug map ────────────────────────────────────────────────────────
PROP_SLUG_MAP = {
    "home-run":     "Home Run",
    "hit":          "Hit",
    "stolen-base":  "Stolen Base",
    "strikeout":    "Strikeout",
    "rbi":          "RBI",
}


def _is_cache_fresh(key: str) -> bool:
    if key not in _cache_time:
        return False
    age = (datetime.utcnow() - _cache_time[key]).total_seconds()
    return age < CACHE_TTL_SECONDS


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/")
async def get_all_props(
    sort_by:   str            = Query("confidence", enum=["confidence", "edge"]),
    min_grade: str            = Query("all",        enum=["all", "a", "b", "c"]),
    prop_type: Optional[str]  = Query(None),
):
    """Returns all of today's player props ranked by confidence."""
    cache_key = "all_props"

    if not _is_cache_fresh(cache_key):
        print("Cache miss — building props from live data...")
        props = await _build_all_props()
        _cache[cache_key]      = props
        _cache_time[cache_key] = datetime.utcnow()
    else:
        print("Cache hit — returning cached props")
        props = _cache[cache_key]

    # Filter by prop type
    if prop_type:
        props = [p for p in props if p["prop_type"] == prop_type]

    # Filter by grade tier
    if min_grade == "a":
        props = [p for p in props if p["confidence"] >= 80]
    elif min_grade == "b":
        props = [p for p in props if p["confidence"] >= 70]
    elif min_grade == "c":
        props = [p for p in props if p["confidence"] >= 60]

    # Sort
    if sort_by == "edge":
        props.sort(key=lambda x: x.get("edge", 0), reverse=True)
    else:
        props.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    cache_age = (datetime.utcnow() - _cache_time.get(cache_key, datetime.utcnow())).seconds

    return {
        "props":        props,
        "count":        len(props),
        "generated_at": datetime.utcnow().isoformat(),
        "cache_age_s":  cache_age,
    }


@router.get("/{prop_slug}")
async def get_props_by_type(
    prop_slug: str,
    sort_by: str = Query("confidence", enum=["confidence", "edge"]),
):
    """Returns props for a single prop type. Example: GET /api/props/home-run"""
    prop_type = PROP_SLUG_MAP.get(prop_slug.lower())
    if not prop_type:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown prop type '{prop_slug}'. Valid: {list(PROP_SLUG_MAP.keys())}"
        )
    return await get_all_props(sort_by=sort_by, prop_type=prop_type)


@router.post("/refresh")
async def refresh_props(background_tasks: BackgroundTasks):
    """Manually triggers a background data refresh."""
    background_tasks.add_task(_refresh_cache)
    return {"message": "Refresh started. Check /api/props in ~30 seconds."}


# ── Data Pipeline ─────────────────────────────────────────────────────────────

async def _build_all_props() -> list[dict]:
    """
    Main data pipeline:
    1. Get today's games from MLB API
    2. Get game totals/odds context
    3. For each game (capped at MAX_GAMES_PER_DAY):
       - Get lineup + weather (with timeout)
       - Score top batters (with timeout per player)
    4. Return all scored props
    """
    print(f"🔄  Building props — {datetime.utcnow().strftime('%H:%M:%S')} UTC")

    # Step 1 — get games and odds concurrently
    try:
        games, game_totals = await asyncio.wait_for(
            asyncio.gather(get_todays_games(), get_game_totals()),
            timeout=15.0
        )
    except asyncio.TimeoutError:
        print("❌  Timeout fetching games/odds")
        return []
    except Exception as e:
        print(f"❌  Error fetching games/odds: {e}")
        return []

    if not games:
        print("⚠️  No games found for today")
        return []

    print(f"📅  Found {len(games)} games today")

    # Build implied totals lookup: team abbr → totals context
    totals_lookup: dict = {}
    for gt in game_totals:
        totals_lookup[gt.get("home_team", "")] = {
            "game_total":   gt.get("game_total"),
            "team_implied": gt.get("home_implied"),
            "event_id":     gt.get("event_id"),
        }
        totals_lookup[gt.get("away_team", "")] = {
            "game_total":   gt.get("game_total"),
            "team_implied": gt.get("away_implied"),
            "event_id":     gt.get("event_id"),
        }

    all_results = []

    # Process games — cap at MAX_GAMES_PER_DAY
    for game in games[:MAX_GAMES_PER_DAY]:
        print(f"⚾  Processing: {game['away_team']} @ {game['home_team']}")
        try:
            results = await _process_game(game, totals_lookup)
            all_results.extend(results)
            print(f"   ✓ {len(results)} props scored")
        except Exception as e:
            print(f"   ✗ Error: {e}")
            continue

    print(f"✅  Done — {len(all_results)} total props built")
    return all_results


async def _process_game(game: dict, totals_lookup: dict) -> list[dict]:
    """Processes one game with a timeout on lineup + weather fetch."""
    game_pk = game["game_pk"]
    venue   = game["venue"]

    # Fetch lineup + weather concurrently with timeout
    try:
        lineup, weather = await asyncio.wait_for(
            asyncio.gather(
                get_game_lineup(game_pk),
                get_stadium_weather(venue),
            ),
            timeout=GAME_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        print(f"   Timeout on lineup/weather for {game_pk} — skipping")
        return []
    except Exception as e:
        print(f"   Error on lineup/weather for {game_pk}: {e}")
        return []

    results = []

    for side in ["home", "away"]:
        team_abbr    = game[f"{side}_team"]
        team_context = totals_lookup.get(team_abbr, {})
        opp_side     = "away" if side == "home" else "home"
        pitcher_info = game.get(f"{opp_side}_probable_pitcher")

        # Only top N batters to keep it fast
        batters = (lineup.get(side) or [])[:MAX_BATTERS_PER_TEAM]

        for batter in batters:
            if not batter.get("id"):
                continue
            try:
                result = await asyncio.wait_for(
                    _score_player(batter, pitcher_info, game, team_abbr, weather, team_context, side),
                    timeout=PLAYER_TIMEOUT_SEC
                )
                if result:
                    results.append(result)
            except asyncio.TimeoutError:
                print(f"   Timeout: {batter.get('name', '?')} — using partial data")
                # Still add player with whatever data we have, scored on partial info
                partial = _score_player_partial(batter, pitcher_info, game, team_abbr, weather, team_context, side)
                if partial:
                    results.append(partial)
            except Exception as e:
                print(f"   Error scoring {batter.get('name', '?')}: {e}")

    return results


async def _score_player(
    batter: dict,
    pitcher_info: Optional[dict],
    game: dict,
    team_abbr: str,
    weather: dict,
    team_context: dict,
    batting_side: str,
) -> Optional[dict]:
    """Fetches Statcast data and scores a single player."""

    player_id  = batter.get("id")
    pitcher_id = pitcher_info.get("id") if pitcher_info else None

    # Pull Statcast for hitter + pitcher concurrently
    hitter_task  = get_hitter_statcast(player_id, days_back=30)
    pitcher_task = get_pitcher_statcast(pitcher_id, days_back=30) if pitcher_id else asyncio.sleep(0, result={})
    hitter_sc, pitcher_sc = await asyncio.gather(hitter_task, pitcher_task)

    return _build_prop_result(batter, pitcher_info, game, team_abbr, weather, team_context, batting_side, hitter_sc, pitcher_sc)


def _score_player_partial(
    batter: dict,
    pitcher_info: Optional[dict],
    game: dict,
    team_abbr: str,
    weather: dict,
    team_context: dict,
    batting_side: str,
) -> Optional[dict]:
    """Scores a player with no Statcast data — uses park/weather/situational only."""
    return _build_prop_result(batter, pitcher_info, game, team_abbr, weather, team_context, batting_side, {}, {})


def _build_prop_result(
    batter: dict,
    pitcher_info: Optional[dict],
    game: dict,
    team_abbr: str,
    weather: dict,
    team_context: dict,
    batting_side: str,
    hitter_sc: dict,
    pitcher_sc: dict,
) -> Optional[dict]:
    """Builds the PropInput, runs the scoring engine, returns the result dict."""

    park_raw  = get_park_data(game.get("venue", ""))

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
        name       = game.get("venue", ""),
        hr_factor  = park_raw.get("hr_factor", 1.00),
        altitude_ft= park_raw.get("altitude_ft", 0),
        is_dome    = park_raw.get("dome", False),
        lf_dist    = park_raw.get("lf"),
        cf_dist    = park_raw.get("cf"),
        rf_dist    = park_raw.get("rf"),
    )

    carry_raw = weather.get("carry_modifier", "0ft")
    try:
        carry_ft = float(str(carry_raw).replace("ft", "").replace("+", "") or 0)
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

    # Placeholder odds until we match player names to odds data
    implied_prob = 0.25
    over_odds    = 300

    prop_input = PropInput(
        prop_type    = "Home Run",
        player_name  = batter.get("name", ""),
        team         = team_abbr,
        opponent     = game.get("away_team" if batting_side == "home" else "home_team", ""),
        implied_prob = implied_prob,
        over_odds    = over_odds,
        hitter       = hitter_data,
        pitcher      = pitcher_data,
        park         = park_data,
        weather      = weather_data,
        situational  = sit_data,
    )

    result = score_prop(prop_input)

    return {
        "player_name":     batter.get("name"),
        "team":            team_abbr,
        "opponent":        prop_input.opponent,
        "prop_type":       "Home Run",
        "line":            "Over 0.5 HR",
        "confidence":      result.confidence,
        "grade":           result.grade,
        "grade_desc":      result.grade_desc,
        "model_prob":      f"{round(result.model_prob * 100, 1)}%",
        "implied_prob":    f"{round(implied_prob * 100, 1)}%",
        "edge":            result.edge,
        "edge_str":        result.edge_str,
        "over_odds":       f"+{over_odds}",
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


def _proj_pa(lineup_pos: Optional[int]) -> float:
    """Estimate projected plate appearances based on lineup spot."""
    pa_map = {1:4.8, 2:4.6, 3:4.5, 4:4.4, 5:4.2, 6:4.0, 7:3.8, 8:3.6, 9:3.5}
    return pa_map.get(lineup_pos, 3.8)


async def _refresh_cache():
    """Background task to rebuild the props cache."""
    try:
        props = await _build_all_props()
        _cache["all_props"]      = props
        _cache_time["all_props"] = datetime.utcnow()
        print(f"✅  Cache refreshed: {len(props)} props")
    except Exception as e:
        print(f"❌  Cache refresh failed: {e}")
