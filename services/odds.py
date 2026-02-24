"""
Odds API Service
Fetches live sportsbook lines for player props and game totals.

Sign up for an API key at: https://the-odds-api.com
Free tier: 500 requests/month (enough for testing)
Paid tier: starts at ~$50/mo for real-time updates

Endpoints used:
  - /sports/baseball_mlb/odds         → game moneylines & totals
  - /sports/baseball_mlb/events/{id}/odds  → player props per game
"""

import httpx
from typing import Optional
from config import settings


BASE    = settings.odds_api_base_url
API_KEY = settings.odds_api_key

# The bookmakers we want lines from (in priority order)
# Full list: https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
BOOKMAKERS = "fanduel,draftkings,betmgm,caesars"

# Prop markets we care about for Phase 1
PROP_MARKETS = {
    "Home Run":    "batter_home_runs",
    "Hit":         "batter_hits",
    "Stolen Base": "batter_stolen_bases",
    "Strikeout":   "batter_strikeouts",
    "RBI":         "batter_rbis",
}


async def get_game_totals() -> list[dict]:
    """
    Fetches game-level over/under totals and team run lines for all
    today's MLB games. Used for the 'Situational' factor (game total,
    implied team total).

    Returns list of:
    {
        "event_id": "abc123",
        "home_team": "Boston Red Sox",
        "away_team": "New York Yankees",
        "game_total": 8.5,
        "home_implied": 4.3,
        "away_implied": 4.2,
    }
    """
    url = f"{BASE}/sports/baseball_mlb/odds"
    params = {
        "apiKey":     API_KEY,
        "regions":    "us",
        "markets":    "totals,h2h",
        "oddsFormat": "american",
        "bookmakers": BOOKMAKERS,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for event in data:
        game_total    = None
        home_implied  = None
        away_implied  = None

        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market["key"] == "totals":
                    for outcome in market.get("outcomes", []):
                        if outcome["name"] == "Over":
                            game_total = outcome.get("point")

                if market["key"] == "h2h":
                    # Derive implied totals from moneyline + game total
                    for outcome in market.get("outcomes", []):
                        price = outcome.get("price", 0)
                        implied = _american_to_implied(price)
                        if outcome["name"] == event.get("home_team"):
                            home_implied = implied
                        else:
                            away_implied = implied
            break  # only use the first bookmaker for totals

        if game_total and home_implied and away_implied:
            # Implied team totals = win prob * game total (simplified)
            total = game_total
            home_team_total = round(total * home_implied, 1)
            away_team_total = round(total * away_implied, 1)
        else:
            home_team_total = None
            away_team_total = None

        results.append({
            "event_id":         event["id"],
            "home_team":        event.get("home_team"),
            "away_team":        event.get("away_team"),
            "commence_time":    event.get("commence_time"),
            "game_total":       game_total,
            "home_implied":     home_team_total,
            "away_implied":     away_team_total,
        })

    return results


async def get_player_props(event_id: str, prop_type: str) -> list[dict]:
    """
    Fetches player prop lines for a specific game and prop type.

    Args:
        event_id:  The Odds API event ID (from get_game_totals)
        prop_type: One of "Home Run", "Hit", "Stolen Base", "Strikeout", "RBI"

    Returns list of:
    {
        "player_name": "Aaron Judge",
        "prop_type": "Home Run",
        "line": 0.5,
        "over_odds": +320,
        "under_odds": -420,
        "implied_prob_over": 23.8,
        "bookmaker": "fanduel",
    }
    """
    market = PROP_MARKETS.get(prop_type)
    if not market:
        return []

    url = f"{BASE}/sports/baseball_mlb/events/{event_id}/odds"
    params = {
        "apiKey":     API_KEY,
        "regions":    "us",
        "markets":    market,
        "oddsFormat": "american",
        "bookmakers": BOOKMAKERS,
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            print(f"Odds API error for event {event_id}: {e}")
            return []

    props = []

    for bookmaker in data.get("bookmakers", []):
        bm_name = bookmaker["key"]
        for market_data in bookmaker.get("markets", []):
            if market_data["key"] != market:
                continue

            # Group outcomes by player name
            players: dict[str, dict] = {}
            for outcome in market_data.get("outcomes", []):
                name   = outcome.get("description", outcome.get("name", ""))
                side   = outcome["name"]   # "Over" or "Under"
                price  = outcome["price"]
                point  = outcome.get("point", 0.5)

                if name not in players:
                    players[name] = {"line": point, "over": None, "under": None}

                if side == "Over":
                    players[name]["over"] = price
                else:
                    players[name]["under"] = price

            for player_name, odds_data in players.items():
                over_odds = odds_data["over"]
                if over_odds is None:
                    continue

                implied = _american_to_implied(over_odds)
                props.append({
                    "player_name":       player_name,
                    "prop_type":         prop_type,
                    "line":              odds_data["line"],
                    "over_odds":         over_odds,
                    "under_odds":        odds_data["under"],
                    "implied_prob_over": round(implied * 100, 1),
                    "bookmaker":         bm_name,
                })

        break  # only use first available bookmaker per game

    return props


async def get_all_hr_props() -> list[dict]:
    """
    Convenience function: get HR props for all of today's games.
    This is the main Phase 1 entry point.
    """
    games  = await get_game_totals()
    all_props = []

    for game in games:
        event_id = game["event_id"]
        props    = await get_player_props(event_id, "Home Run")

        # Attach game context to each prop
        for prop in props:
            prop["game_context"] = {
                "home_team":    game["home_team"],
                "away_team":    game["away_team"],
                "game_total":   game["game_total"],
                "home_implied": game["home_implied"],
                "away_implied": game["away_implied"],
            }

        all_props.extend(props)

    return all_props


# ── Helpers ───────────────────────────────────────────────────────────────────

def _american_to_implied(american_odds: int) -> float:
    """Convert American odds to implied probability (0.0–1.0)."""
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return abs(american_odds) / (abs(american_odds) + 100)


def _implied_to_american(implied_prob: float) -> int:
    """Convert implied probability back to American odds."""
    if implied_prob >= 0.5:
        return round(-implied_prob / (1 - implied_prob) * 100)
    else:
        return round((1 - implied_prob) / implied_prob * 100)
