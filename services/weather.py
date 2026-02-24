"""
Weather Service
Fetches real-time weather conditions for each MLB stadium.
Uses Tomorrow.io API (free tier works fine for Phase 1).

Sign up: https://tomorrow.io
Free tier: 500 calls/day — more than enough.

Weather factors affect ball carry distance:
  - Temperature: +1°F ≈ +0.4ft carry (warm air = less dense = more carry)
  - Altitude:    Higher = less air resistance = more HR carry
  - Wind OUT:    Tailwind adds 5–15ft carry depending on speed
  - Wind IN:     Headwind suppresses HRs significantly
  - Humidity:    Slightly positive (humid air is less dense than dry air)
"""

import httpx
import math
from typing import Optional
from config import settings


BASE    = settings.weather_api_base_url
API_KEY = settings.weather_api_key


# ── Stadium coordinates lookup ────────────────────────────────────────────────
# Lat/lng for all 30 MLB stadiums + their outfield orientation
# "hr_wind_bearing" = compass bearing toward CF (wind blowing this direction = tailwind)
STADIUMS: dict[str, dict] = {
    "Fenway Park":             {"lat": 42.3467, "lng": -71.0972, "altitude_ft": 19,   "hr_wind_bearing": 220},
    "Yankee Stadium":          {"lat": 40.8296, "lng": -73.9262, "altitude_ft": 55,   "hr_wind_bearing": 135},
    "Coors Field":             {"lat": 39.7559, "lng": -104.9942,"altitude_ft": 5200, "hr_wind_bearing": 270},
    "Oracle Park":             {"lat": 37.7786, "lng": -122.3893,"altitude_ft": 0,    "hr_wind_bearing": 270},
    "Globe Life Field":        {"lat": 32.7473, "lng": -97.0845, "altitude_ft": 551,  "hr_wind_bearing": 0},
    "Dodger Stadium":          {"lat": 34.0739, "lng": -118.2400,"altitude_ft": 512,  "hr_wind_bearing": 315},
    "Wrigley Field":           {"lat": 41.9484, "lng": -87.6553, "altitude_ft": 595,  "hr_wind_bearing": 0},
    "Great American Ball Park":{"lat": 39.0979, "lng": -84.5069, "altitude_ft": 482,  "hr_wind_bearing": 315},
    "Petco Park":              {"lat": 32.7076, "lng": -117.1570,"altitude_ft": 42,   "hr_wind_bearing": 270},
    "Truist Park":             {"lat": 33.8908, "lng": -84.4678, "altitude_ft": 1050, "hr_wind_bearing": 315},
    "American Family Field":   {"lat": 43.0280, "lng": -87.9712, "altitude_ft": 635,  "hr_wind_bearing": 270},
    "Progressive Field":       {"lat": 41.4962, "lng": -81.6852, "altitude_ft": 649,  "hr_wind_bearing": 315},
    "Kauffman Stadium":        {"lat": 39.0517, "lng": -94.4803, "altitude_ft": 1013, "hr_wind_bearing": 0},
    "Rogers Centre":           {"lat": 43.6414, "lng": -79.3894, "altitude_ft": 287,  "hr_wind_bearing": 0},   # dome
    "Target Field":            {"lat": 44.9817, "lng": -93.2781, "altitude_ft": 834,  "hr_wind_bearing": 315},
    "Busch Stadium":           {"lat": 38.6226, "lng": -90.1928, "altitude_ft": 535,  "hr_wind_bearing": 315},
    "T-Mobile Park":           {"lat": 47.5914, "lng": -122.3325,"altitude_ft": 0,    "hr_wind_bearing": 315},  # retractable
    "Oakland Coliseum":        {"lat": 37.7516, "lng": -122.2005,"altitude_ft": 25,   "hr_wind_bearing": 270},
    "loanDepot park":          {"lat": 25.7781, "lng": -80.2197, "altitude_ft": 6,    "hr_wind_bearing": 0},    # dome
    "Citizens Bank Park":      {"lat": 39.9061, "lng": -75.1665, "altitude_ft": 20,   "hr_wind_bearing": 270},
    "Nationals Park":          {"lat": 38.8730, "lng": -77.0074, "altitude_ft": 0,    "hr_wind_bearing": 315},
    "Camden Yards":            {"lat": 39.2839, "lng": -76.6217, "altitude_ft": 6,    "hr_wind_bearing": 315},
    "Minute Maid Park":        {"lat": 29.7572, "lng": -95.3555, "altitude_ft": 38,   "hr_wind_bearing": 0},    # retractable
    "Tropicana Field":         {"lat": 27.7683, "lng": -82.6534, "altitude_ft": 15,   "hr_wind_bearing": 0},    # dome
    "PNC Park":                {"lat": 40.4469, "lng": -80.0057, "altitude_ft": 730,  "hr_wind_bearing": 270},
    "Angel Stadium":           {"lat": 33.8003, "lng": -117.8827,"altitude_ft": 152,  "hr_wind_bearing": 270},
    "Petco Park":              {"lat": 32.7076, "lng": -117.1570,"altitude_ft": 42,   "hr_wind_bearing": 270},
    "Chase Field":             {"lat": 33.4453, "lng": -112.0667,"altitude_ft": 1082, "hr_wind_bearing": 315},  # retractable
    "Citi Field":              {"lat": 40.7571, "lng": -73.8458, "altitude_ft": 20,   "hr_wind_bearing": 45},
    "Guaranteed Rate Field":   {"lat": 41.8300, "lng": -87.6338, "altitude_ft": 595,  "hr_wind_bearing": 270},
}

DOME_STADIUMS = {
    "Rogers Centre", "loanDepot park", "Minute Maid Park",
    "Tropicana Field", "Chase Field", "T-Mobile Park"
}


async def get_stadium_weather(stadium_name: str) -> dict:
    """
    Fetches current/forecast weather for a given stadium.

    Returns:
    {
        "temp_f": 82,
        "wind_speed_mph": 14,
        "wind_direction_deg": 220,
        "wind_direction_label": "SW",
        "humidity_pct": 51,
        "is_dome": False,
        "hr_wind_effect": "favorable",   # "favorable" | "unfavorable" | "neutral" | "dome"
        "hr_wind_label": "OUT to RF (14mph SW)",
        "carry_modifier": "+6ft",        # estimated ball carry vs neutral conditions
        "raw": {...}                     # full API response
    }
    """
    # Dome stadiums — weather irrelevant
    if stadium_name in DOME_STADIUMS:
        return _dome_weather(stadium_name)

    stadium = STADIUMS.get(stadium_name)
    if not stadium:
        return {"error": f"Stadium not found: {stadium_name}", "is_dome": False}

    url = f"{BASE}/weather/realtime"
    params = {
        "location": f"{stadium['lat']},{stadium['lng']}",
        "apikey":   API_KEY,
        "units":    "imperial",
        "fields":   "temperature,windSpeed,windDirection,humidity,pressureSurfaceLevel",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"Weather API failed for {stadium_name}: {e}")
            return {"error": str(e), "is_dome": False}

    values = data.get("data", {}).get("values", {})

    temp_f         = round(values.get("temperature", 72), 1)
    wind_speed     = round(values.get("windSpeed", 5), 1)
    wind_dir_deg   = round(values.get("windDirection", 180), 0)
    humidity       = round(values.get("humidity", 50), 0)
    pressure       = values.get("pressureSurfaceLevel", 29.92)

    # Determine if wind is blowing out (tailwind) or in (headwind) toward CF
    hr_bearing    = stadium["hr_wind_bearing"]
    wind_effect   = _calc_wind_effect(wind_dir_deg, hr_bearing, wind_speed)
    wind_label    = _wind_direction_label(wind_dir_deg)
    hr_wind_label = _build_wind_label(wind_effect, wind_speed, wind_label)
    carry_mod     = _estimate_carry_modifier(temp_f, stadium["altitude_ft"], wind_effect, humidity)

    return {
        "temp_f":              temp_f,
        "wind_speed_mph":      wind_speed,
        "wind_direction_deg":  wind_dir_deg,
        "wind_direction_label":wind_label,
        "humidity_pct":        humidity,
        "pressure":            pressure,
        "altitude_ft":         stadium["altitude_ft"],
        "is_dome":             False,
        "hr_wind_effect":      wind_effect["effect"],
        "hr_wind_label":       hr_wind_label,
        "carry_modifier":      carry_mod,
        "signal":              _weather_signal(wind_effect["effect"], temp_f),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dome_weather(stadium_name: str) -> dict:
    return {
        "temp_f": 72, "wind_speed_mph": 0, "wind_direction_deg": 0,
        "wind_direction_label": "N/A", "humidity_pct": 50,
        "is_dome": True, "hr_wind_effect": "dome",
        "hr_wind_label": "Dome — controlled conditions",
        "carry_modifier": "0ft", "signal": "neutral",
    }


def _calc_wind_effect(wind_dir: float, hr_bearing: float, wind_speed: float) -> dict:
    """
    Calculates whether wind is blowing out (favorable) or in (unfavorable).
    
    Wind direction in meteorology = direction wind is coming FROM.
    hr_bearing = direction toward CF (where we want the ball to go).
    
    If wind is blowing FROM behind home plate (opposite of CF bearing) → tailwind → favorable.
    """
    # The direction wind is blowing TOWARD
    wind_toward = (wind_dir + 180) % 360

    # Angle between wind_toward and CF bearing
    angle = abs(wind_toward - hr_bearing) % 360
    if angle > 180:
        angle = 360 - angle

    # Component of wind in the HR direction
    component = wind_speed * math.cos(math.radians(angle))

    if wind_speed < 3:
        effect = "neutral"
        strength = "calm"
    elif component > 4:
        effect = "favorable"
        strength = "strong" if component > 10 else "moderate"
    elif component < -4:
        effect = "unfavorable"
        strength = "strong" if component < -10 else "moderate"
    else:
        effect = "neutral"
        strength = "crosswind"

    return {"effect": effect, "component": round(component, 1), "strength": strength}


def _wind_direction_label(degrees: float) -> str:
    """Convert degrees to compass label (N, NE, E, etc.)"""
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
    idx = round(degrees / 22.5) % 16
    return dirs[idx]


def _build_wind_label(wind_effect: dict, speed: float, direction: str) -> str:
    if wind_effect["effect"] == "favorable":
        return f"OUT {speed}mph {direction} — Favorable for HRs"
    elif wind_effect["effect"] == "unfavorable":
        return f"IN {speed}mph {direction} — Suppresses HRs"
    else:
        return f"Crosswind {speed}mph {direction} — Neutral"


def _estimate_carry_modifier(temp_f: float, altitude_ft: int, wind_effect: dict, humidity: float) -> str:
    """
    Rough estimate of extra ball carry vs neutral (72°F, sea level, calm).
    Based on physics models: ~0.4ft per °F above 72, ~1ft per 1000ft altitude,
    and ~5-15ft for tailwinds.
    """
    carry = 0.0
    carry += (temp_f - 72) * 0.4
    carry += (altitude_ft / 1000) * 1.0
    carry += wind_effect.get("component", 0) * 0.8
    carry += (humidity - 50) * 0.05  # slight positive effect

    if carry > 0:
        return f"+{round(carry, 1)}ft"
    else:
        return f"{round(carry, 1)}ft"


def _weather_signal(wind_effect: str, temp_f: float) -> str:
    if wind_effect == "favorable" or (wind_effect == "neutral" and temp_f >= 80):
        return "positive"
    elif wind_effect == "unfavorable" or temp_f < 55:
        return "negative"
    return "neutral"
