"""
Today's Backtest — Pitcher Strikeout props with real sportsbook lines.

Pulls today's MLB games, fetches real K prop lines from The Odds API,
scores each pitcher with Statcast + MLB season stats, then at end of day
you can re-run with --results to see how the predictions did.

Usage:
    python todays_backtest.py                  # score today's props
    python todays_backtest.py --prop "Pitcher Strikeout"
"""

import asyncio
import httpx
import argparse
import sys
import os
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_backtest import (
    get_games, get_mlb_season_stats,
    build_park_data, safe_float, safe_int,
    get_pitcher_sc, split_name
)
from services.odds import get_game_totals, get_player_props, _american_to_implied
from models.scoring import (
    score_prop, PropInput, PitcherData,
    WeatherData, SituationalData
)
from services.statcast import _lookup_player_id, PYBASEBALL_AVAILABLE

TODAY = date.today().strftime("%Y-%m-%d")
SEASON = date.today().year

# Pitcher Strikeout market key
PITCHER_K_MARKET = "pitcher_strikeouts"


async def get_pitcher_k_props() -> dict:
    """
    Fetch today's pitcher strikeout lines from The Odds API.
    Returns dict: { "Gerrit Cole": {"line": 6.5, "over_odds": -115, "implied": 0.535} }
    """
    games = await get_game_totals()
    props_by_pitcher = {}

    async with httpx.AsyncClient() as client:
        for game in games:
            event_id = game["event_id"]
            url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
            params = {
                "apiKey":     os.getenv("ODDS_API_KEY") or _get_api_key(),
                "regions":    "us",
                "markets":    PITCHER_K_MARKET,
                "oddsFormat": "american",
                "bookmakers": "fanduel,draftkings,betmgm,caesars",
            }
            try:
                resp = await client.get(url, params=params, timeout=15)
                if resp.status_code == 422:
                    continue  # market not available for this game
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  Odds API error for event {event_id}: {e}")
                continue

            for bookmaker in data.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market["key"] != PITCHER_K_MARKET:
                        continue
                    players = {}
                    for outcome in market.get("outcomes", []):
                        name  = outcome.get("description", outcome.get("name", ""))
                        side  = outcome["name"]
                        price = outcome["price"]
                        point = outcome.get("point", 4.5)
                        if name not in players:
                            players[name] = {"line": point, "over": None, "under": None,
                                             "game": game}
                        if side == "Over":
                            players[name]["over"] = price
                        else:
                            players[name]["under"] = price

                    for pname, odds in players.items():
                        if odds["over"] is not None and pname not in props_by_pitcher:
                            props_by_pitcher[pname] = {
                                "line":      odds["line"],
                                "over_odds": odds["over"],
                                "under_odds": odds["under"],
                                "implied":   _american_to_implied(odds["over"]),
                                "game":      odds["game"],
                            }
                break  # first bookmaker only

    return props_by_pitcher


def _get_api_key():
    """Load API key from .env file."""
    try:
        from config import settings
        return settings.odds_api_key
    except Exception:
        from dotenv import load_dotenv
        load_dotenv()
        return os.getenv("ODDS_API_KEY", "")


def build_pitcher_prop(name, mlb_stats, sc, line, over_odds, implied, park, weather) -> PropInput:
    k9    = safe_float(mlb_stats.get("strikeoutsPer9Inn"), 8.0)
    ip    = safe_float(mlb_stats.get("inningsPitched"), 0.0)
    games = safe_int(mlb_stats.get("gamesPlayed"), 1)
    gs    = safe_int(mlb_stats.get("gamesStarted"), 0)

    ip_pg      = ip / max(1, games)
    is_starter = gs > (games * 0.5) if gs > 0 else ip_pg >= 3.5
    proj_ip    = 5.5 if is_starter else 1.2

    k_pct  = min(38, max(12, (k9 / 9) * 28))
    whiff  = sc.get("whiff_rate")  or max(18, min(40, k_pct * 1.05))
    sw_str = sc.get("sw_str_pct")  or max(7,  min(17, k_pct * 0.45))
    velo   = sc.get("avg_fastball_velo") or None

    pitcher = PitcherData(
        k_per_9         = k9,
        k_per_9_recent  = k9,
        k_pct           = k_pct,
        k_pct_recent    = k_pct,
        whiff_rate      = whiff,
        sw_str_pct      = sw_str,
        avg_fastball_velo = velo,
        proj_innings    = proj_ip,
    )
    return PropInput(
        prop_type    = "Pitcher Strikeout",
        player_name  = name,
        team         = "",
        opponent     = "",
        implied_prob = implied,
        over_odds    = over_odds,
        line         = line,
        pitcher      = pitcher,
        park         = park,
        weather      = weather,
        situational  = SituationalData(
            proj_innings = proj_ip,
            is_starter   = is_starter,
            game_total   = 8.5,
        )
    )


async def run_todays_backtest(prop_type="Pitcher Strikeout"):
    print()
    print("=" * 62)
    print(f"  BaseballIQ — Today's {prop_type} Props")
    print(f"  Date: {TODAY} | Real lines from The Odds API")
    print("=" * 62)
    print()

    print("  Fetching pitcher K lines...", end=" ", flush=True)
    try:
        pitcher_props = await get_pitcher_k_props()
    except Exception as e:
        print(f"FAILED: {e}")
        return

    if not pitcher_props:
        print("No pitcher K props found for today.")
        print("  (Lines may not be posted yet — try after 10am ET)")
        return

    print(f"{len(pitcher_props)} pitchers found")
    print()

    results = []
    async with httpx.AsyncClient() as client:
        for name, prop_data in pitcher_props.items():
            print(f"  Scoring {name}...", end=" ", flush=True)

            game    = prop_data["game"]
            line    = prop_data["line"]
            implied = prop_data["implied"]
            over_odds = prop_data["over_odds"]

            # Get venue/park
            venue = game.get("home_team", "")
            park  = build_park_data(venue)
            weather = WeatherData(
                hr_wind_effect = "dome" if park.is_dome else "neutral",
                temp_f = 72.0
            )

            # MLB season stats
            # Need to find player ID — search by name via MLB API
            mlb_stats = {}
            sc_data   = {}
            player_id = await _find_mlb_player_id(name, client)
            if player_id:
                mlb_stats = await get_mlb_season_stats(player_id, SEASON, "pitching", client)

            # Real Statcast
            if PYBASEBALL_AVAILABLE:
                last, first = split_name(name)
                savant_id = await asyncio.to_thread(_lookup_player_id, last, first)
                if savant_id:
                    sc_data = await get_pitcher_sc(savant_id, TODAY)

            prop_input = build_pitcher_prop(
                name, mlb_stats, sc_data,
                line, over_odds, implied,
                park, weather
            )
            result = score_prop(prop_input)

            k9_display = safe_float(mlb_stats.get("strikeoutsPer9Inn"))
            sc_marker  = "✓" if sc_data else "~"
            print(f"done  {sc_marker}")

            results.append({
                "name":       name,
                "line":       line,
                "over_odds":  over_odds,
                "implied":    round(implied * 100, 1),
                "grade":      result.grade,
                "conf":       result.confidence,
                "edge":       result.edge_str,
                "k9":         k9_display,
                "sc":         sc_marker,
                "game":       f"{game.get('away_team', '')} @ {game.get('home_team', '')}",
                "scores":     result.category_scores,
            })

    if not results:
        print("  No results.")
        return

    # Sort by confidence
    results.sort(key=lambda x: x["conf"], reverse=True)

    print()
    print("─" * 72)
    print(f"  {'PITCHER':<22} {'GAME':<28} {'LINE':>5} {'ODDS':>6} {'GR':>4} {'CONF':>6} {'EDGE':>7} SC")
    print("─" * 72)

    threshold = 70.0
    for r in results:
        pred   = "YES" if r["conf"] >= threshold else "NO "
        marker = "◀" if r["conf"] >= threshold else ""
        print(f"  {r['name']:<22} {r['game']:<28} o{r['line']:<4} {r['over_odds']:>+6} "
              f"{r['grade']:>4} {r['conf']:>5.1f} {r['edge']:>7} {r['sc']} {pred} {marker}")

    yes_picks = [r for r in results if r["conf"] >= threshold]
    print()
    print(f"  TODAY'S YES PICKS ({len(yes_picks)} pitchers above {threshold} confidence)")
    print("─" * 72)
    if yes_picks:
        for r in yes_picks:
            k9_str = f"K/9: {r['k9']:.1f}" if r['k9'] else "K/9: N/A"
            print(f"  ★  {r['name']:<22} o{r['line']}  {r['over_odds']:+d}  {r['grade']}  "
                  f"conf:{r['conf']:.1f}  {k9_str}  {r['game']}")
            cats = r["scores"]
            print(f"     pitcher:{cats.get('pitcher',0):.0f}  "
                  f"situational:{cats.get('situational',0):.0f}  "
                  f"park:{cats.get('park',0):.0f}")
    else:
        print("  No strong picks today. Highest confidence:")
        for r in results[:3]:
            print(f"     {r['name']:<22} o{r['line']}  conf:{r['conf']:.1f}  {r['grade']}")

    print()
    print(f"  Save this output — re-check tonight to see how picks did.")
    print(f"  Statcast (✓): {sum(1 for r in results if r['sc']=='✓')}/{len(results)} pitchers")
    print("=" * 62)
    print()


async def _find_mlb_player_id(name: str, client: httpx.AsyncClient) -> int | None:
    """Search MLB Stats API for a player ID by name."""
    try:
        url = "https://statsapi.mlb.com/api/v1/people/search"
        resp = await client.get(url, params={"names": name, "sportId": 1}, timeout=10)
        data = resp.json()
        people = data.get("people", [])
        if people:
            return people[0]["id"]
    except Exception:
        pass
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prop", default="Pitcher Strikeout")
    args = parser.parse_args()
    asyncio.run(run_todays_backtest(args.prop))
