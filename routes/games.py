"""
Games API Routes

GET /api/games        → today's schedule with probable pitchers
GET /api/games/{id}   → single game detail with lineup
"""

from fastapi import APIRouter, HTTPException
from services.mlb import get_todays_games, get_game_lineup

router = APIRouter()


@router.get("/")
async def list_games():
    """Returns today's MLB schedule with probable pitchers and venues."""
    games = await get_todays_games()
    return {"games": games, "count": len(games)}


@router.get("/{game_pk}")
async def get_game(game_pk: int):
    """Returns lineup for a specific game."""
    try:
        lineup = await get_game_lineup(game_pk)
        return {"game_pk": game_pk, "lineup": lineup}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Game not found: {e}")
