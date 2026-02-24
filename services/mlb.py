"""
MLB Stats API Service
Fetches: today's schedule, lineups, probable pitchers, team rosters
API docs: https://statsapi.mlb.com/api/v1/

No API key required — completely free and public.
"""

import httpx
from datetime import date
from typing import Optional
from config import settings


BASE = settings.mlb_api_base_url


async def get_todays_games() -> list[dict]:
    """
    Returns today's MLB schedule with game IDs, teams, venues, and start times.
    
    Example response item:
    {
        "game_id": 745456,
        "home_team": "BOS",
        "away_team": "NYY",
        "venue": "Fenway Park",
        "start_time": "2024-04-15T23:10:00Z",
        "game_pk": 745456
    }
    """
    today = date.today().strftime("%Y-%m-%d")
    url = f"{BASE}/schedule"
    params = {
        "sportId": 1,           # 1 = MLB
        "date": today,
        "hydrate": "team,venue,probablePitcher,linescore",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            games.append({
                "game_id":    game["gamePk"],
                "game_pk":    game["gamePk"],
                "status":     game["status"]["detailedState"],
                "start_time": game["gameDate"],
                "venue":      game["venue"]["name"],
                "home_team":  game["teams"]["home"]["team"]["abbreviation"],
                "away_team":  game["teams"]["away"]["team"]["abbreviation"],
                "home_team_id":  game["teams"]["home"]["team"]["id"],
                "away_team_id":  game["teams"]["away"]["team"]["id"],
                "home_probable_pitcher": _extract_pitcher(game["teams"]["home"]),
                "away_probable_pitcher": _extract_pitcher(game["teams"]["away"]),
            })

    return games


def _extract_pitcher(team_data: dict) -> Optional[dict]:
    """Pull probable pitcher info from team data if available."""
    pitcher = team_data.get("probablePitcher")
    if not pitcher:
        return None
    return {
        "id":       pitcher["id"],
        "name":     pitcher["fullName"],
        "hand":     pitcher.get("pitchHand", {}).get("code", "?"),
    }


async def get_game_lineup(game_pk: int) -> dict:
    """
    Returns the confirmed batting lineup for both teams in a given game.
    Note: lineups are usually posted ~1 hour before first pitch.
    
    Returns:
    {
        "home": [{"name": "Aaron Judge", "id": 592450, "batting_order": 3, ...}],
        "away": [...]
    }
    """
    url = f"{BASE}/game/{game_pk}/boxscore"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    result = {"home": [], "away": []}

    for side in ["home", "away"]:
        team_data = data.get("teams", {}).get(side, {})
        batters = team_data.get("battingOrder", [])
        players = team_data.get("players", {})

        for order_idx, player_id in enumerate(batters):
            key = f"ID{player_id}"
            player = players.get(key, {})
            person = player.get("person", {})
            result[side].append({
                "id":            person.get("id"),
                "name":          person.get("fullName"),
                "batting_order": order_idx + 1,
                "position":      player.get("position", {}).get("abbreviation"),
                "hand":          player.get("batSide", {}).get("code", "?"),
            })

    return result


async def get_player_season_stats(player_id: int, season: Optional[int] = None) -> dict:
    """
    Returns a player's season hitting or pitching stats from the MLB Stats API.
    Useful as a lightweight supplement to Statcast data.
    """
    if not season:
        season = date.today().year

    url = f"{BASE}/people/{player_id}/stats"
    params = {
        "stats": "season",
        "season": season,
        "group": "hitting",     # "hitting" or "pitching"
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    stats_list = data.get("stats", [])
    if not stats_list:
        return {}

    splits = stats_list[0].get("splits", [])
    if not splits:
        return {}

    return splits[0].get("stat", {})


async def get_pitcher_stats(pitcher_id: int, season: Optional[int] = None) -> dict:
    """
    Returns a pitcher's season stats: ERA, HR/9, K/9, WHIP, FB rate, etc.
    """
    if not season:
        season = date.today().year

    url = f"{BASE}/people/{pitcher_id}/stats"
    params = {
        "stats": "season",
        "season": season,
        "group": "pitching",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    stats_list = data.get("stats", [])
    if not stats_list:
        return {}

    splits = stats_list[0].get("splits", [])
    if not splits:
        return {}

    return splits[0].get("stat", {})
