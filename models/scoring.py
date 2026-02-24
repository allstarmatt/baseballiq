"""
BaseballIQ Scoring Engine
The core algorithm that converts raw data → confidence % → letter grade.

This is the "secret sauce" — tweak the weights as you gather real results
and track model accuracy over time.

Architecture:
  - Each factor category returns a score 0–100
  - Category scores are weighted and combined → raw_score 0–100
  - raw_score maps to confidence % and letter grade
  - Edge = model_prob - book_implied_prob
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Weights (must sum to 1.0) ─────────────────────────────────────────────────
# Adjust these as your model improves — these are Phase 1 starting weights.
WEIGHTS = {
    "hitter":      0.28,   # Exit velo, barrel%, xwOBA, platoon, form
    "pitcher":     0.22,   # HR/9, pitch mix matchup, hard contact allowed
    "park":        0.18,   # HR factor, altitude, dimensions
    "weather":     0.18,   # Wind direction/speed, temp, humidity, carry modifier
    "situational": 0.14,   # Lineup pos, game total, implied team total, trend
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001, "Weights must sum to 1.0"

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class HitterData:
    # Statcast
    exit_velo_avg:      Optional[float] = None   # mph
    exit_velo_max:      Optional[float] = None   # mph
    barrel_pct:         Optional[float] = None   # %
    hard_hit_pct:       Optional[float] = None   # % balls 95+ mph
    launch_angle_avg:   Optional[float] = None   # degrees
    fly_ball_rate:      Optional[float] = None   # %
    pull_rate:          Optional[float] = None   # %
    # Advanced
    xslg:               Optional[float] = None
    xwoba:              Optional[float] = None
    # Platoon
    vs_rhp_avg:         Optional[float] = None
    vs_lhp_avg:         Optional[float] = None
    pitcher_hand:       Optional[str]   = None   # "R" or "L"
    # Rolling form
    hr_last_10g:        Optional[int]   = None
    hits_last_10g:      Optional[int]   = None
    barrel_pct_14d:     Optional[float] = None


@dataclass
class PitcherData:
    hand:                   Optional[str]   = None
    hr_per_9:               Optional[float] = None
    hr_per_9_recent:        Optional[float] = None   # last 5 starts
    fly_ball_rate_allowed:  Optional[float] = None   # %
    ground_ball_rate:       Optional[float] = None   # %
    barrel_pct_allowed:     Optional[float] = None   # %
    hard_contact_pct:       Optional[float] = None   # %
    pitch_mix:              Optional[dict]  = None   # {"FF": 40.2, "SL": 30.1, ...}
    avg_fastball_velo:      Optional[float] = None
    # Matchup
    batter_avg_vs_primary_pitch: Optional[float] = None


@dataclass
class ParkData:
    name:           str   = ""
    hr_factor:      float = 1.00   # 1.0 = neutral, >1.0 = HR-friendly
    altitude_ft:    int   = 0
    is_dome:        bool  = False
    # Rough left-field and right-field distances
    lf_dist:        Optional[int] = None
    cf_dist:        Optional[int] = None
    rf_dist:        Optional[int] = None


@dataclass
class WeatherData:
    temp_f:           float = 72.0
    wind_speed_mph:   float = 5.0
    hr_wind_effect:   str   = "neutral"   # "favorable" | "unfavorable" | "neutral" | "dome"
    wind_component:   float = 0.0         # positive = tailwind, negative = headwind
    humidity_pct:     float = 50.0
    carry_modifier_ft:float = 0.0         # estimated extra carry in feet


@dataclass
class SituationalData:
    lineup_position:    Optional[int]   = None   # 1–9
    proj_plate_apps:    Optional[float] = None
    bullpen_era:        Optional[float] = None   # opponent bullpen ERA
    game_total:         Optional[float] = None   # Vegas O/U
    implied_team_total: Optional[float] = None   # our team's implied runs
    trend_score:        Optional[float] = None   # -1.0 to +1.0 (recent form)


@dataclass
class PropInput:
    prop_type:    str
    player_name:  str
    team:         str
    opponent:     str
    implied_prob: float          # sportsbook implied probability 0–1
    over_odds:    int            # American odds for Over
    hitter:       HitterData = field(default_factory=HitterData)
    pitcher:      PitcherData = field(default_factory=PitcherData)
    park:         ParkData    = field(default_factory=ParkData)
    weather:      WeatherData = field(default_factory=WeatherData)
    situational:  SituationalData = field(default_factory=SituationalData)


@dataclass
class ScoringResult:
    confidence:       float   # 0–100
    grade:            str     # A+, A, A-, B+, B, B-, C+, C, D
    grade_desc:       str     # "Elite", "Excellent", etc.
    model_prob:       float   # model's estimated true probability (0–1)
    edge:             float   # model_prob - implied_prob
    edge_str:         str     # "+14.2%"
    category_scores:  dict    # scores per category for transparency
    signal:           str     # "positive" | "negative" | "neutral"


# ── Category Scorers ──────────────────────────────────────────────────────────

def score_hitter(h: HitterData, prop_type: str) -> float:
    """
    Returns 0–100 score for hitter factors.
    Higher = more favorable for the prop hitting.
    """
    score = 50.0   # start at neutral
    points = 0
    weight = 0

    # Exit velo avg (league avg ~88–89 mph)
    if h.exit_velo_avg is not None:
        points += _normalize(h.exit_velo_avg, low=84, high=96, weight=10)
        weight += 10

    # Barrel % (league avg ~8%)
    if h.barrel_pct is not None:
        points += _normalize(h.barrel_pct, low=4, high=20, weight=15)
        weight += 15

    # Hard hit % (league avg ~36%)
    if h.hard_hit_pct is not None:
        points += _normalize(h.hard_hit_pct, low=28, high=55, weight=12)
        weight += 12

    # Launch angle — for HRs, optimal is 25–35°; for hits, 10–20° is better
    if h.launch_angle_avg is not None and prop_type == "Home Run":
        points += _normalize(h.launch_angle_avg, low=10, high=30, weight=8)
        weight += 8

    # Fly ball rate (more FB = more HR opportunities)
    if h.fly_ball_rate is not None and prop_type == "Home Run":
        points += _normalize(h.fly_ball_rate, low=25, high=50, weight=8)
        weight += 8

    # xwOBA (league avg ~.320)
    if h.xwoba is not None:
        points += _normalize(h.xwoba, low=0.28, high=0.44, weight=12)
        weight += 12

    # Platoon split
    if h.pitcher_hand and h.vs_rhp_avg and h.pitcher_hand == "R":
        points += _normalize(h.vs_rhp_avg, low=.220, high=.340, weight=8)
        weight += 8
    elif h.pitcher_hand and h.vs_lhp_avg and h.pitcher_hand == "L":
        points += _normalize(h.vs_lhp_avg, low=.220, high=.340, weight=8)
        weight += 8

    # Rolling form — HR last 10 games
    if h.hr_last_10g is not None and prop_type == "Home Run":
        points += _normalize(h.hr_last_10g, low=0, high=5, weight=15)
        weight += 15

    # Rolling form — hits last 10 games
    if h.hits_last_10g is not None and prop_type == "Hit":
        points += _normalize(h.hits_last_10g, low=6, high=18, weight=15)
        weight += 15

    if weight == 0:
        return 50.0

    return max(0, min(100, (points / weight) * 100))


def score_pitcher(p: PitcherData, prop_type: str) -> float:
    """
    Returns 0–100 score for pitcher factors.
    Higher = pitcher is MORE hittable / favorable for our prop.
    """
    score = 50.0
    points = 0
    weight = 0

    # HR/9 — higher = more HR-prone (good for us)
    if p.hr_per_9 is not None and prop_type == "Home Run":
        points += _normalize(p.hr_per_9, low=0.5, high=2.0, weight=20)
        weight += 20

    # Recent HR/9 form
    if p.hr_per_9_recent is not None and prop_type == "Home Run":
        points += _normalize(p.hr_per_9_recent, low=0.5, high=2.5, weight=15)
        weight += 15

    # Fly ball rate allowed (more FB allowed = more HR opportunities)
    if p.fly_ball_rate_allowed is not None and prop_type == "Home Run":
        points += _normalize(p.fly_ball_rate_allowed, low=25, high=45, weight=12)
        weight += 12

    # Barrel % allowed (higher = more damage allowed)
    if p.barrel_pct_allowed is not None:
        points += _normalize(p.barrel_pct_allowed, low=4, high=12, weight=15)
        weight += 15

    # Hard contact % allowed
    if p.hard_contact_pct is not None:
        points += _normalize(p.hard_contact_pct, low=28, high=44, weight=12)
        weight += 12

    # Batter avg vs primary pitch
    if p.batter_avg_vs_primary_pitch is not None:
        points += _normalize(p.batter_avg_vs_primary_pitch, low=.180, high=.380, weight=15)
        weight += 15

    if weight == 0:
        return 50.0

    return max(0, min(100, (points / weight) * 100))


def score_park(p: ParkData, prop_type: str) -> float:
    """
    Returns 0–100 score for park factors.
    Higher = park is more favorable for our prop.
    """
    if p.is_dome:
        return 50.0   # dome = neutral

    score = 50.0
    points = 0
    weight = 0

    # HR park factor (1.0 = neutral, 1.15 = very friendly, 0.85 = suppressing)
    if prop_type in ("Home Run", "RBI"):
        points += _normalize(p.hr_factor, low=0.82, high=1.20, weight=40)
        weight += 40

    # Altitude (higher = more carry)
    if p.altitude_ft:
        points += _normalize(p.altitude_ft, low=0, high=5200, weight=30)
        weight += 30

    # Short dimensions (RF distance for right-handed pull hitters)
    if p.rf_dist:
        # Shorter RF = better for RHH pull HR. Under 330 = favorable
        points += _normalize(330 - p.rf_dist, low=-20, high=30, weight=15)
        weight += 15

    if weight == 0:
        return 50.0

    return max(0, min(100, (points / weight) * 100))


def score_weather(w: WeatherData, prop_type: str) -> float:
    """
    Returns 0–100 score for weather conditions.
    Higher = weather is more favorable for HR/hits.
    """
    if w.hr_wind_effect == "dome":
        return 50.0

    points = 0
    weight = 0

    # Temperature (warm air = less dense = more carry)
    points += _normalize(w.temp_f, low=40, high=95, weight=20)
    weight += 20

    # Wind effect (favorable = OUT, unfavorable = IN)
    if w.hr_wind_effect == "favorable":
        wind_score = min(100, 50 + w.wind_component * 3)
    elif w.hr_wind_effect == "unfavorable":
        wind_score = max(0, 50 + w.wind_component * 3)   # component is negative
    else:
        wind_score = 50

    points += wind_score * 0.35
    weight += 35

    # Overall carry modifier
    if w.carry_modifier_ft:
        carry_normalized = _normalize(w.carry_modifier_ft, low=-15, high=20, weight=25)
        points += carry_normalized
        weight += 25

    # Humidity (slightly positive)
    points += _normalize(w.humidity_pct, low=20, high=80, weight=10)
    weight += 10

    if weight == 0:
        return 50.0

    return max(0, min(100, (points / weight) * 100))


def score_situational(s: SituationalData, prop_type: str) -> float:
    """
    Returns 0–100 score for situational factors.
    Higher = more favorable situational context.
    """
    points = 0
    weight = 0

    # Lineup position — 2–5 get more RBI/HR opportunities
    if s.lineup_position is not None:
        if s.lineup_position in [2, 3, 4, 5]:
            pos_score = 70
        elif s.lineup_position in [1, 6]:
            pos_score = 55
        else:
            pos_score = 40
        points += pos_score * 0.15
        weight += 15

    # Projected plate appearances (more PAs = more chances)
    if s.proj_plate_apps is not None:
        points += _normalize(s.proj_plate_apps, low=3.0, high=5.5, weight=15)
        weight += 15

    # Implied team total (more runs = more HR/hit opportunities)
    if s.implied_team_total is not None:
        points += _normalize(s.implied_team_total, low=2.5, high=6.5, weight=25)
        weight += 25

    # Weak bullpen (high ERA = more opportunities for hits/HRs late)
    if s.bullpen_era is not None:
        points += _normalize(s.bullpen_era, low=2.8, high=6.0, weight=20)
        weight += 20

    # Recent trend (-1.0 = cold streak, +1.0 = hot streak)
    if s.trend_score is not None:
        points += _normalize(s.trend_score, low=-1.0, high=1.0, weight=25)
        weight += 25

    if weight == 0:
        return 50.0

    return max(0, min(100, (points / weight) * 100))


# ── Main Scoring Function ─────────────────────────────────────────────────────

def score_prop(prop: PropInput) -> ScoringResult:
    """
    Master scoring function.
    Takes a PropInput and returns a fully computed ScoringResult.
    """
    # 1. Score each category
    category_scores = {
        "hitter":      score_hitter(prop.hitter, prop.prop_type),
        "pitcher":     score_pitcher(prop.pitcher, prop.prop_type),
        "park":        score_park(prop.park, prop.prop_type),
        "weather":     score_weather(prop.weather, prop.prop_type),
        "situational": score_situational(prop.situational, prop.prop_type),
    }

    # 2. Weighted average → raw score 0–100
    raw_score = sum(
        score * WEIGHTS[cat]
        for cat, score in category_scores.items()
    )

    # 3. Convert raw score to model probability
    # raw_score of 50 = baseline (same as book). Scaling: 50±25 pts = ±15% prob.
    score_delta    = raw_score - 50        # -50 to +50
    model_prob     = prop.implied_prob + (score_delta / 50) * 0.20
    model_prob     = max(0.01, min(0.99, model_prob))

    # 4. Edge = how much better our model thinks it is vs the book
    edge = model_prob - prop.implied_prob

    # 5. Confidence = how certain we are (function of edge + data completeness)
    data_completeness = _data_completeness(prop)
    confidence_raw    = 50 + (edge * 200) * data_completeness
    confidence        = max(0, min(99, confidence_raw))

    # 6. Grade
    grade, grade_desc = _get_grade(confidence)

    # 7. Signal
    signal = "positive" if edge > 0.03 else "negative" if edge < -0.03 else "neutral"

    return ScoringResult(
        confidence=round(confidence, 1),
        grade=grade,
        grade_desc=grade_desc,
        model_prob=round(model_prob, 3),
        edge=round(edge, 3),
        edge_str=f"+{round(edge*100, 1)}%" if edge >= 0 else f"{round(edge*100, 1)}%",
        category_scores={k: round(v, 1) for k, v in category_scores.items()},
        signal=signal,
    )


# ── Grade Lookup ──────────────────────────────────────────────────────────────

def _get_grade(confidence: float) -> tuple[str, str]:
    if confidence >= 90: return "A+", "Elite"
    if confidence >= 85: return "A",  "Excellent"
    if confidence >= 80: return "A−", "Very Strong"
    if confidence >= 75: return "B+", "Strong"
    if confidence >= 70: return "B",  "Good"
    if confidence >= 65: return "B−", "Moderate"
    if confidence >= 60: return "C+", "Lean"
    if confidence >= 55: return "C",  "Weak"
    return "D", "Avoid"


# ── Utilities ─────────────────────────────────────────────────────────────────

def _normalize(value: float, low: float, high: float, weight: float) -> float:
    """
    Normalizes `value` to 0–100 range within [low, high], then scales by weight.
    Returns weighted points (not a 0–100 score).
    """
    clamped = max(low, min(high, value))
    normalized = (clamped - low) / (high - low)   # 0.0–1.0
    return normalized * weight                      # 0–weight


def _data_completeness(prop: PropInput) -> float:
    """
    Returns 0.5–1.0 indicating how complete the input data is.
    Less data = less confident in the score.
    """
    checks = [
        prop.hitter.exit_velo_avg is not None,
        prop.hitter.barrel_pct is not None,
        prop.hitter.xwoba is not None,
        prop.pitcher.hr_per_9 is not None,
        prop.pitcher.barrel_pct_allowed is not None,
        prop.park.hr_factor != 1.00,    # non-default = we have real data
        prop.weather.temp_f != 72.0,    # non-default = we have real data
        prop.situational.implied_team_total is not None,
    ]
    filled = sum(checks)
    return 0.5 + (filled / len(checks)) * 0.5   # 0.5 to 1.0
