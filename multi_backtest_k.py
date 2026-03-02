"""
Multi-date Strikeout model validation.

Tests both Batter Strikeout and Pitcher Strikeout across many dates.
Shows K rate by grade to validate model signal.

Usage:
    python multi_backtest_k.py
    python multi_backtest_k.py --prop "Strikeout"
    python multi_backtest_k.py --prop "Pitcher Strikeout"
"""

import asyncio
import httpx
import argparse
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_backtest import (
    get_games, get_boxscore, get_mlb_season_stats,
    build_park_data, did_prop_hit, safe_float, safe_int,
    get_pitcher_sc, split_name, PROP_THRESHOLDS
)
from models.scoring import (
    score_prop, PropInput, HitterData, PitcherData,
    ParkData, WeatherData, SituationalData
)
from services.statcast import _lookup_player_id, PYBASEBALL_AVAILABLE

MLB_BASE = "https://statsapi.mlb.com/api/v1"

TEST_DATES = [
    "2024-06-01",
    "2024-08-15",
    "2024-09-08",
]


def build_batter_k_input(name, team, opp, mlb_stats, pitcher_hand, lineup_pos, park, weather) -> PropInput:
    avg     = safe_float(mlb_stats.get("avg"), 0.250)
    k_rate  = safe_float(mlb_stats.get("strikeoutRate"))
    ab      = safe_int(mlb_stats.get("atBats"), 1)
    so      = safe_int(mlb_stats.get("strikeOuts"), 0)
    pa      = safe_int(mlb_stats.get("plateAppearances"), ab)

    # Real k_rate from season stats if not directly available
    if k_rate is None and pa > 0:
        k_rate = round(so / pa * 100, 1)

    # Estimate whiff rate from k_rate (MLB avg: K%=22% → whiff%=25%)
    whiff_est = max(15.0, min(40.0, (k_rate or 22) * 1.15)) if k_rate else None
    # Estimate chase rate — high K hitters chase more
    chase_est = max(20.0, min(40.0, 25.0 + ((k_rate or 22) - 22) * 0.5)) if k_rate else None

    # k_last_10g from season pace (will improve with Statcast)
    k_per_30ab = round((so / max(1, ab)) * 30) if ab > 0 else None

    hitter = HitterData(
        k_rate   = k_rate,
        whiff_rate = whiff_est,
        chase_rate = chase_est,
        k_last_10g = min(15, k_per_30ab) if k_per_30ab else None,
        pitcher_hand = pitcher_hand,
        vs_rhp_avg = avg if pitcher_hand == "R" else None,
        vs_lhp_avg = avg if pitcher_hand == "L" else None,
    )
    return PropInput(
        prop_type="Strikeout", player_name=name, team=team, opponent=opp,
        implied_prob=0.60, over_odds=-115,
        hitter=hitter, park=park, weather=weather,
        situational=SituationalData(
            lineup_position   = lineup_pos,
            proj_plate_apps   = max(2.5, 4.5 - (lineup_pos - 1) * 0.15) if lineup_pos else 3.5,
            implied_team_total= 4.5,
        )
    )


def build_pitcher_k_input(name, team, opp, mlb_stats, park, weather, implied_prob=0.50) -> PropInput:
    k9    = safe_float(mlb_stats.get("strikeoutsPer9Inn"), 8.0)
    ip    = safe_float(mlb_stats.get("inningsPitched"), 50.0)
    era   = safe_float(mlb_stats.get("era"), 4.50)
    games = safe_int(mlb_stats.get("gamesPlayed"), 1)
    gs    = safe_int(mlb_stats.get("gamesStarted"), 0)

    # Starter detection: use gamesStarted if available, else IP per game
    # A starter averages ~5-6 IP/game, reliever ~1 IP/game
    ip_per_game = ip / max(1, games)
    is_starter  = gs > (games * 0.5) if gs > 0 else ip_per_game >= 3.5
    proj_ip     = 5.5 if is_starter else 1.2

    # K% from K/9 (K/9 / 9 * ~27 batters faced ≈ K%)
    k_pct  = min(38, max(12, (k9 / 9) * 28)) if k9 else 22.0
    # Whiff/SwStr estimated from K% — better than k9 * constant
    whiff  = max(18, min(40, k_pct * 1.05))
    sw_str = max(7,  min(17, k_pct * 0.45))

    pitcher = PitcherData(
        k_per_9        = k9,
        k_per_9_recent = k9,
        k_pct          = k_pct,
        k_pct_recent   = k_pct,
        whiff_rate     = whiff,
        sw_str_pct     = sw_str,
        proj_innings   = proj_ip,
    )
    return PropInput(
        prop_type="Pitcher Strikeout", player_name=name, team=team, opponent=opp,
        implied_prob=implied_prob, over_odds=-115,
        pitcher=pitcher, park=park, weather=weather,
        situational=SituationalData(
            proj_innings = proj_ip,
            is_starter   = is_starter,
            game_total   = 8.5,
        )
    )


async def run_one_date(dt_str, prop_type, client):
    season = int(dt_str[:4])
    is_pitcher_prop = prop_type == "Pitcher Strikeout"
    results = []

    try:
        games = await get_games(dt_str, client)
    except Exception:
        return []

    for game in games[:8]:
        home, away = game["home_team"], game["away_team"]
        park = build_park_data(game["venue"])
        weather = WeatherData(hr_wind_effect="dome" if park.is_dome else "neutral", temp_f=72.0)

        try:
            box = await get_boxscore(game["game_pk"], client)
        except Exception:
            continue

        # Cache pitcher MLB stats for batter K props
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

            if is_pitcher_prop:
                player_ids = team_data.get("pitchers", [])
            else:
                player_ids = team_data.get("battingOrder", [])

            for order_idx, pid in enumerate(player_ids[:9]):
                key    = f"ID{pid}"
                player = players.get(key, {})
                name   = player.get("person", {}).get("fullName", "Unknown")

                stat_group   = "pitching" if is_pitcher_prop else "batting"
                actual_stats = player.get("stats", {}).get(stat_group, {})

                if is_pitcher_prop:
                    # Use dynamic line — set below after building prop_input
                    # Store raw K count here; evaluate after line is determined
                    raw_ks = safe_int(actual_stats.get("strikeOuts"), None)
                    actual = None  # will be set after prop_input built
                else:
                    actual = did_prop_hit(actual_stats, prop_type)
                    if actual is None:
                        continue

                group     = "pitching" if is_pitcher_prop else "hitting"
                mlb_stats = await get_mlb_season_stats(pid, season, group, client)

                # ── Step 1: Calculate line and implied prob BEFORE building prop ──
                prop_k_line  = None
                prop_implied = 0.50

                if is_pitcher_prop:
                    k9_val = safe_float(mlb_stats.get("strikeoutsPer9Inn"), 8.0) or 8.0
                    ip_val = safe_float(mlb_stats.get("inningsPitched"), 0) or 0
                    gs_val = safe_int(mlb_stats.get("gamesStarted"), 0)
                    gp_val = safe_int(mlb_stats.get("gamesPlayed"), 1)
                    ip_pg  = ip_val / max(1, gp_val)
                    is_sp  = gs_val > (gp_val * 0.5) if gs_val > 0 else ip_pg >= 3.5

                    if not is_sp:
                        prop_k_line = None  # skip relievers
                    elif k9_val >= 10.0:
                        prop_k_line = 6.5
                    elif k9_val >= 8.5:
                        prop_k_line = 5.5
                    elif k9_val >= 7.0:
                        prop_k_line = 4.5
                    else:
                        prop_k_line = 3.5

                    # implied_prob reflects VALUE vs line: expected Ks vs line
                    if prop_k_line is not None:
                        proj_ip_est = 5.5 if k9_val >= 8.5 else 5.0 if k9_val >= 7.0 else 4.5
                        expected_k  = k9_val * proj_ip_est / 9
                        k_edge = expected_k - prop_k_line
                        # Multiply by 0.25 per K (was 0.10 — too small to produce grade spread)
                        prop_implied = max(0.15, min(0.85, 0.50 + k_edge * 0.25))
                    else:
                        prop_implied = 0.50

                    # ── Step 2: Build prop with correct implied prob ──
                    prop_input = build_pitcher_k_input(
                        name, team_abbr, team_abbr, mlb_stats, park, weather,
                        implied_prob=prop_implied
                    )

                    # ── Step 3: Layer real Statcast on top ──
                    if PYBASEBALL_AVAILABLE:
                        last, first = split_name(name)
                        savant_id = await asyncio.to_thread(_lookup_player_id, last, first)
                        if savant_id:
                            sc = await get_pitcher_sc(savant_id, dt_str)
                            if sc:
                                if sc.get("whiff_rate"):
                                    prop_input.pitcher.whiff_rate = sc["whiff_rate"]
                                if sc.get("sw_str_pct"):
                                    prop_input.pitcher.sw_str_pct = sc["sw_str_pct"]
                                if sc.get("avg_fastball_velo"):
                                    prop_input.pitcher.avg_fastball_velo = sc["avg_fastball_velo"]

                else:
                    prop_input = build_batter_k_input(
                        name, team_abbr, team_abbr,
                        mlb_stats, pitcher_hand, order_idx + 1,
                        park, weather
                    )
                    # Wire pitcher K stats to influence batter K score
                    if p_mlb:
                        k9 = safe_float(p_mlb.get("strikeoutsPer9Inn"))
                        if k9:
                            prop_input.pitcher.k_per_9        = k9
                            prop_input.pitcher.k_per_9_recent = k9
                            prop_input.pitcher.whiff_rate     = max(20, min(38, k9 * 2.2))

                # For pitcher props: evaluate against dynamic line, skip relievers
                if is_pitcher_prop:
                    if prop_k_line is None or raw_ks is None:
                        continue  # skip relievers and missing data
                    actual = raw_ks > prop_k_line  # OVER the line = True

                result = score_prop(prop_input)

                # For Pitcher Strikeout: calculate confidence directly from K-edge
                # score_prop compresses everything into C/D — bypass it entirely
                if is_pitcher_prop and prop_k_line is not None:
                    proj_ip_est = 5.5 if k9_val >= 8.5 else 5.0 if k9_val >= 7.0 else 4.5
                    expected_k  = k9_val * proj_ip_est / 9
                    k_edge      = expected_k - prop_k_line
                    # +2.0K edge -> 95 (A+), +1.0 -> 75 (B+), 0 -> 55 (C),
                    # -1.0 -> 35 (D), -2.0 -> 15 (D)
                    direct_conf = 55 + (k_edge * 20)
                    direct_conf = max(10, min(97, direct_conf))
                    # Blend 70% direct edge + 30% model quality score
                    final_conf  = round((direct_conf * 0.70) + (result.confidence * 0.30), 1)
                    from models.scoring import _get_grade
                    final_grade, final_desc = _get_grade(final_conf)
                    print(f"    {name:<28} k_edge={k_edge:+.1f} direct={direct_conf:.0f} model={result.confidence:.0f} final={final_conf:.1f} {final_grade}")
                else:
                    final_conf  = result.confidence
                    final_grade = result.grade
                results.append({
                    "name":   name,
                    "grade":  final_grade,
                    "conf":   final_conf,
                    "actual": actual,
                    "date":   dt_str,
                    "line":   prop_k_line if is_pitcher_prop else None,
                    "ks":     raw_ks if is_pitcher_prop else None,
                    "category_scores": result.category_scores,
                })

    return results


async def run_multi(prop_type):
    print()
    print("=" * 60)
    print(f"  Strikeout Model Validation — {prop_type}")
    print(f"  {len(TEST_DATES)} dates | MLB API season stats")
    print("=" * 60)
    print()

    all_results = []
    async with httpx.AsyncClient() as client:
        for dt_str in TEST_DATES:
            print(f"  {dt_str}...", end=" ", flush=True)
            results = await run_one_date(dt_str, prop_type, client)
            hits = sum(1 for r in results if r["actual"])
            rate = hits / max(1, len(results)) * 100
            print(f"{len(results)} players, {hits} K ({rate:.1f}%)")
            all_results.extend(results)

    if not all_results:
        print("No results.")
        return

    total     = len(all_results)
    total_k   = sum(1 for r in all_results if r["actual"])
    baseline  = total_k / total

    # For pitcher props, show line distribution
    lines = [r["line"] for r in all_results if r.get("line") is not None]
    if lines:
        from collections import Counter
        lc = Counter(lines)
        print(f"  Line distribution  : " + "  ".join(f"o{k}: {v} pitchers" for k, v in sorted(lc.items())))

    print()
    print(f"  Total player-games : {total}")
    print(f"  Total OVER hits    : {total_k}")
    print(f"  Baseline OVER rate : {baseline*100:.1f}%")
    print()
    print(f"  K RATE BY GRADE  (A+ should be highest)")
    print("  " + "-" * 60)
    print(f"  {'GRADE':<6} {'PLAYERS':>8} {'HIT K':>7} {'K RATE':>9}  {'VS BASE':>10}  BAR")
    print("  " + "-" * 60)

    by_grade = defaultdict(list)
    for r in all_results:
        by_grade[r["grade"]].append(r)

    for grade in ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "D"]:
        grp = by_grade.get(grade, [])
        if not grp:
            continue
        k_count = sum(1 for r in grp if r["actual"])
        k_rate  = k_count / len(grp)
        lift    = k_rate / baseline if baseline > 0 else 1.0
        lift_str = f"+{(lift-1)*100:.0f}%" if lift >= 1 else f"{(lift-1)*100:.0f}%"
        bar     = chr(9608) * int(k_rate * 100)
        print(f"  {grade:<6} {len(grp):>8} {k_count:>7} {k_rate*100:>8.1f}%  {lift_str:>10}  {bar}")

    print()
    print(f"  Target: A+ K rate > {baseline*200:.1f}% (2x baseline)")
    print(f"  Target: D  K rate < {baseline*50:.1f}% (0.5x baseline)")

    threshold = PROP_THRESHOLDS.get(prop_type, 60.0)
    pred_yes  = [r for r in all_results if r["conf"] >= threshold]
    yes_hits  = [r for r in pred_yes if r["actual"]]
    if pred_yes:
        print()
        print(f"  YES predictions : {len(pred_yes)} ({len(pred_yes)/total*100:.1f}% of players)")
        print(f"  YES precision   : {len(yes_hits)/len(pred_yes)*100:.1f}% ({len(yes_hits)}/{len(pred_yes)})")
        print(f"  vs baseline     : {len(yes_hits)/len(pred_yes)/baseline:.2f}x")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prop", default="Strikeout", choices=["Strikeout", "Pitcher Strikeout"])
    args = parser.parse_args()
    asyncio.run(run_multi(args.prop))
