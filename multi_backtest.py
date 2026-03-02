"""
Multi-date backtest — the REAL model validation.

Tests across many dates and shows HR hit rate by grade.
If the model is working, A+/A players should hit HRs at a higher rate than D players.

Usage:
    python multi_backtest.py
"""

import asyncio
import httpx
import sys
import os
from datetime import date
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_backtest import (
    get_games, get_boxscore, get_mlb_season_stats,
    build_park_data, build_hitter_input,
    did_prop_hit, safe_float,
    PROP_THRESHOLDS
)
from models.scoring import score_prop, WeatherData

MLB_BASE = "https://statsapi.mlb.com/api/v1"

TEST_DATES = [
    "2024-05-15", "2024-05-22", "2024-06-01", "2024-06-10",
    "2024-06-20", "2024-07-04", "2024-07-15", "2024-07-25",
    "2024-08-01", "2024-08-08", "2024-08-15", "2024-08-22",
    "2024-09-01", "2024-09-08", "2024-09-15",
]


async def run_one_date(dt_str, prop_type, client):
    season = int(dt_str[:4])
    results = []

    try:
        games = await get_games(dt_str, client)
    except Exception:
        return []

    for game in games[:8]:
        home, away = game["home_team"], game["away_team"]
        park = build_park_data(game["venue"])
        weather = WeatherData(
            hr_wind_effect="dome" if park.is_dome else "neutral",
            temp_f=72.0
        )

        try:
            box = await get_boxscore(game["game_pk"], client)
        except Exception:
            continue

        # Cache pitcher MLB stats
        pitcher_mlb_cache = {}
        for side in ["home", "away"]:
            opp_side = "away" if side == "home" else "home"
            pi = game.get(f"{opp_side}_pitcher")
            if pi and pi.get("id") and pi["id"] not in pitcher_mlb_cache:
                mlb_p = await get_mlb_season_stats(pi["id"], season, "pitching", client)
                pitcher_mlb_cache[pi["id"]] = mlb_p

        for side in ["home", "away"]:
            team_abbr   = home if side == "home" else away
            opp_side    = "away" if side == "home" else "home"
            opp_pitcher = game.get(f"{opp_side}_pitcher")
            pitcher_hand    = opp_pitcher.get("hand", "R") if opp_pitcher else "R"
            opp_pitcher_id  = opp_pitcher.get("id") if opp_pitcher else None
            p_mlb = pitcher_mlb_cache.get(opp_pitcher_id, {})

            team_data  = box.get("teams", {}).get(side, {})
            players    = team_data.get("players", {})
            player_ids = team_data.get("battingOrder", [])

            for order_idx, pid in enumerate(player_ids[:9]):
                key    = f"ID{pid}"
                player = players.get(key, {})
                name   = player.get("person", {}).get("fullName", "Unknown")

                actual_stats = player.get("stats", {}).get("batting", {})
                actual = did_prop_hit(actual_stats, prop_type)
                if actual is None:
                    continue

                mlb_stats = await get_mlb_season_stats(pid, season, "hitting", client)

                # Build prop without Statcast (for speed across many dates)
                prop_input = build_hitter_input(
                    name, team_abbr, team_abbr,
                    mlb_stats, {},
                    pitcher_hand, order_idx + 1,
                    prop_type, park, weather
                )

                # Attach pitcher season stats
                if p_mlb:
                    hr9 = safe_float(p_mlb.get("homeRunsPer9"))
                    if hr9:
                        prop_input.pitcher.hr_per_9        = hr9
                        prop_input.pitcher.hr_per_9_recent = hr9
                        prop_input.pitcher.ground_ball_rate = max(30.0, min(60.0, 50.0 - (hr9 - 1.2) * 8))

                result = score_prop(prop_input)
                results.append({
                    "name":   name,
                    "grade":  result.grade,
                    "conf":   result.confidence,
                    "actual": actual,
                    "date":   dt_str,
                })

    return results


async def run_multi():
    prop_type = "Home Run"
    print()
    print("=" * 58)
    print("  Multi-Date HR Model Validation")
    print(f"  {len(TEST_DATES)} dates | MLB API stats | no Statcast (for speed)")
    print("=" * 58)
    print()

    all_results = []

    async with httpx.AsyncClient() as client:
        for dt_str in TEST_DATES:
            print(f"  {dt_str}...", end=" ", flush=True)
            results = await run_one_date(dt_str, prop_type, client)
            hits = sum(1 for r in results if r["actual"])
            rate = hits / max(1, len(results)) * 100
            print(f"{len(results)} players, {hits} HRs ({rate:.1f}%)")
            all_results.extend(results)

    if not all_results:
        print("No results.")
        return

    total      = len(all_results)
    total_hrs  = sum(1 for r in all_results if r["actual"])
    baseline   = total_hrs / total

    print()
    print(f"  Total player-games : {total}")
    print(f"  Total HRs          : {total_hrs}")
    print(f"  Baseline HR rate   : {baseline*100:.1f}%")

    print()
    print("  HR RATE BY GRADE  (key validation — A+ should be highest)")
    print("  " + "-" * 58)
    print(f"  {'GRADE':<6} {'PLAYERS':>8} {'HIT HR':>8} {'HR RATE':>9}  {'VS BASELINE':>12}  BAR")
    print("  " + "-" * 58)

    by_grade = defaultdict(list)
    for r in all_results:
        by_grade[r["grade"]].append(r)

    for grade in ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "D"]:
        grp = by_grade.get(grade, [])
        if not grp:
            continue
        hr_count = sum(1 for r in grp if r["actual"])
        hr_rate  = hr_count / len(grp)
        lift     = hr_rate / baseline if baseline > 0 else 1.0
        lift_str = f"+{(lift-1)*100:.0f}%" if lift >= 1 else f"{(lift-1)*100:.0f}%"
        bar      = chr(9608) * int(hr_rate * 200)
        print(f"  {grade:<6} {len(grp):>8} {hr_count:>8} {hr_rate*100:>8.1f}%  {lift_str:>12}  {bar}")

    print()
    print(f"  Target: A+ HR rate > {baseline*200:.1f}% (2x baseline)")
    print(f"  Target: D  HR rate < {baseline*50:.1f}% (0.5x baseline)")
    print()

    # YES precision across all dates
    threshold = PROP_THRESHOLDS.get(prop_type, 70.0)
    pred_yes  = [r for r in all_results if r["conf"] >= threshold]
    yes_hits  = [r for r in pred_yes if r["actual"]]
    if pred_yes:
        print(f"  YES predictions    : {len(pred_yes)} ({len(pred_yes)/total*100:.1f}% of players)")
        print(f"  YES precision      : {len(yes_hits)/len(pred_yes)*100:.1f}% ({len(yes_hits)}/{len(pred_yes)})")
        print(f"  vs baseline        : {len(yes_hits)/len(pred_yes)/baseline:.1f}x")
    print()
    print("=" * 58)
    print()


if __name__ == "__main__":
    asyncio.run(run_multi())
