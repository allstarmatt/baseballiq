"""
BaseballIQ Scoring Engine — All 5 Prop Types
Handles: Home Run, Hit, Stolen Base, Strikeout, RBI

Each prop type uses different factor weights because what matters for a HR
is very different from what matters for a stolen base or a strikeout.
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Per-prop-type weights (each dict must sum to 1.0) ────────────────────────
WEIGHTS = {
    "Home Run": {
        "hitter":      0.28,
        "pitcher":     0.22,
        "park":        0.20,
        "weather":     0.18,
        "situational": 0.12,
    },
    "Hit": {
        "hitter":      0.35,   # contact ability is king for hits
        "pitcher":     0.25,
        "park":        0.10,   # park matters less for hits
        "weather":     0.10,
        "situational": 0.20,   # lineup spot / PA projection matters more
    },
    "Stolen Base": {
        "hitter":      0.30,   # speed metrics
        "pitcher":     0.25,   # pitcher delivery / catcher arm
        "park":        0.05,   # park barely matters for SB
        "weather":     0.05,
        "situational": 0.35,   # game context is huge for SB
    },
    "Strikeout": {
        "hitter":      0.30,   # K rate, whiff rate
        "pitcher":     0.40,   # pitcher K rate is the dominant factor
        "park":        0.05,
        "weather":     0.05,
        "situational": 0.20,
    },
    "RBI": {
        "hitter":      0.25,
        "pitcher":     0.20,
        "park":        0.15,
        "weather":     0.10,
        "situational": 0.30,   # lineup spot + run environment matters most
    },
    "Pitcher Strikeout": {
        "hitter":      0.25,   # opposing lineup K rate
        "pitcher":     0.45,   # pitcher K stuff is the dominant factor
        "park":        0.05,
        "weather":     0.05,
        "situational": 0.20,   # projected innings, game total
    },
}

for prop_type, w in WEIGHTS.items():
    assert abs(sum(w.values()) - 1.0) < 0.001, f"Weights must sum to 1.0 for {prop_type}"


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class HitterData:
    # Power metrics (HR, RBI)
    exit_velo_avg:      Optional[float] = None
    exit_velo_max:      Optional[float] = None
    barrel_pct:         Optional[float] = None
    hard_hit_pct:       Optional[float] = None
    launch_angle_avg:   Optional[float] = None
    fly_ball_rate:      Optional[float] = None
    pull_rate:          Optional[float] = None
    # Contact metrics (Hit)
    contact_rate:       Optional[float] = None   # 1 - K rate
    babip:              Optional[float] = None
    line_drive_rate:    Optional[float] = None
    # Speed metrics (Stolen Base)
    sprint_speed:       Optional[float] = None   # ft/sec, avg ~27, elite >29
    sb_attempt_rate:    Optional[float] = None
    sb_success_rate:    Optional[float] = None
    # Strikeout metrics
    k_rate:             Optional[float] = None   # % of PAs ending in K
    whiff_rate:         Optional[float] = None   # swings & misses / swings
    chase_rate:         Optional[float] = None   # O-swing %
    # Advanced
    xslg:               Optional[float] = None
    xwoba:              Optional[float] = None
    # Platoon
    vs_rhp_avg:         Optional[float] = None
    vs_lhp_avg:         Optional[float] = None
    pitcher_hand:       Optional[str]   = None
    # Rolling form
    hr_last_10g:        Optional[int]   = None
    hits_last_10g:      Optional[int]   = None
    k_last_10g:         Optional[int]   = None
    sb_last_10g:        Optional[int]   = None
    rbi_last_10g:       Optional[int]   = None


@dataclass
class PitcherData:
    hand:                    Optional[str]   = None
    # HR metrics
    hr_per_9:                Optional[float] = None
    hr_per_9_recent:         Optional[float] = None
    fly_ball_rate_allowed:   Optional[float] = None
    ground_ball_rate:        Optional[float] = None
    barrel_pct_allowed:      Optional[float] = None
    hard_contact_pct:        Optional[float] = None
    # Hit metrics
    babip_allowed:           Optional[float] = None
    hits_per_9:              Optional[float] = None
    # Strikeout metrics
    k_per_9:                 Optional[float] = None
    k_per_9_recent:          Optional[float] = None
    whiff_rate:              Optional[float] = None
    # Stolen base metrics
    delivery_time:           Optional[float] = None   # seconds to plate (slower = easier to steal)
    holds_per_game:          Optional[float] = None
    # Pitch mix
    pitch_mix:               Optional[dict]  = None
    avg_fastball_velo:       Optional[float] = None
    batter_avg_vs_primary:   Optional[float] = None
    # Pitcher strikeout specific
    sw_str_pct:              Optional[float] = None   # swinging strike % (best K predictor)
    k_pct:                   Optional[float] = None   # K% (Ks / batters faced)
    k_pct_recent:            Optional[float] = None   # K% last 3 starts
    strikeout_pitch_pct:     Optional[float] = None   # % of pitches that are K-inducing (slider/curve/change)
    proj_innings:            Optional[float] = None   # projected innings today
    opp_team_k_rate:         Optional[float] = None   # opposing team K rate this season


@dataclass
class ParkData:
    name:           str   = ""
    hr_factor:      float = 1.00
    altitude_ft:    int   = 0
    is_dome:        bool  = False
    lf_dist:        Optional[int] = None
    cf_dist:        Optional[int] = None
    rf_dist:        Optional[int] = None


@dataclass
class WeatherData:
    temp_f:            float = 72.0
    wind_speed_mph:    float = 5.0
    hr_wind_effect:    str   = "neutral"
    wind_component:    float = 0.0
    humidity_pct:      float = 50.0
    carry_modifier_ft: float = 0.0


@dataclass
class SituationalData:
    lineup_position:    Optional[int]   = None
    proj_plate_apps:    Optional[float] = None
    bullpen_era:        Optional[float] = None
    game_total:         Optional[float] = None
    implied_team_total: Optional[float] = None
    trend_score:        Optional[float] = None
    # SB specific
    runners_on_pct:     Optional[float] = None   # how often batter reaches base
    next_batter_obp:    Optional[float] = None   # OBP of batter behind (steal opportunities)
    catcher_pop_time:   Optional[float] = None   # seconds (lower = harder to steal)
    # Pitcher strikeout specific
    proj_innings:       Optional[float] = None   # projected innings pitcher will throw
    is_starter:         Optional[bool]  = None   # starter vs reliever


@dataclass
class PropInput:
    prop_type:    str
    player_name:  str
    team:         str
    opponent:     str
    implied_prob: float
    over_odds:    int
    line:         float = 0.5
    hitter:       HitterData      = field(default_factory=HitterData)
    pitcher:      PitcherData     = field(default_factory=PitcherData)
    park:         ParkData        = field(default_factory=ParkData)
    weather:      WeatherData     = field(default_factory=WeatherData)
    situational:  SituationalData = field(default_factory=SituationalData)


@dataclass
class ScoringResult:
    confidence:      float
    grade:           str
    grade_desc:      str
    model_prob:      float
    edge:            float
    edge_str:        str
    category_scores: dict
    signal:          str


# ── Category Scorers ──────────────────────────────────────────────────────────

def score_hitter(h: HitterData, prop_type: str) -> float:
    points = 0.0
    weight = 0.0

    if prop_type == "Home Run":
        if h.exit_velo_avg is not None:
            points += _norm(h.exit_velo_avg, 84, 96, 12)
            weight += 12
        if h.barrel_pct is not None:
            points += _norm(h.barrel_pct, 4, 20, 18)
            weight += 18
        if h.hard_hit_pct is not None:
            points += _norm(h.hard_hit_pct, 28, 55, 12)
            weight += 12
        if h.fly_ball_rate is not None:
            points += _norm(h.fly_ball_rate, 25, 50, 10)
            weight += 10
        if h.launch_angle_avg is not None:
            points += _norm(h.launch_angle_avg, 10, 30, 8)
            weight += 8
        if h.xwoba is not None:
            points += _norm(h.xwoba, 0.28, 0.44, 12)
            weight += 12
        if h.hr_last_10g is not None:
            points += _norm(h.hr_last_10g, 0, 5, 18)
            weight += 18
        if h.pitcher_hand and h.vs_rhp_avg and h.pitcher_hand == "R":
            points += _norm(h.vs_rhp_avg, 0.220, 0.340, 10)
            weight += 10
        elif h.pitcher_hand and h.vs_lhp_avg and h.pitcher_hand == "L":
            points += _norm(h.vs_lhp_avg, 0.220, 0.340, 10)
            weight += 10

    elif prop_type == "Hit":
        if h.contact_rate is not None:
            points += _norm(h.contact_rate, 65, 92, 20)
            weight += 20
        if h.babip is not None:
            points += _norm(h.babip, 0.260, 0.380, 15)
            weight += 15
        if h.line_drive_rate is not None:
            points += _norm(h.line_drive_rate, 15, 30, 12)
            weight += 12
        if h.hard_hit_pct is not None:
            points += _norm(h.hard_hit_pct, 28, 50, 10)
            weight += 10
        if h.hits_last_10g is not None:
            points += _norm(h.hits_last_10g, 4, 18, 20)
            weight += 20
        if h.xwoba is not None:
            points += _norm(h.xwoba, 0.28, 0.42, 12)
            weight += 12
        if h.pitcher_hand and h.vs_rhp_avg and h.pitcher_hand == "R":
            points += _norm(h.vs_rhp_avg, 0.220, 0.340, 11)
            weight += 11
        elif h.pitcher_hand and h.vs_lhp_avg and h.pitcher_hand == "L":
            points += _norm(h.vs_lhp_avg, 0.220, 0.340, 11)
            weight += 11

    elif prop_type == "Stolen Base":
        if h.sprint_speed is not None:
            points += _norm(h.sprint_speed, 24, 31, 30)
            weight += 30
        if h.sb_success_rate is not None:
            points += _norm(h.sb_success_rate, 60, 95, 25)
            weight += 25
        if h.sb_attempt_rate is not None:
            points += _norm(h.sb_attempt_rate, 0, 20, 20)
            weight += 20
        if h.sb_last_10g is not None:
            points += _norm(h.sb_last_10g, 0, 4, 25)
            weight += 25

    elif prop_type == "Strikeout":
        if h.k_rate is not None:
            points += _norm(h.k_rate, 10, 38, 30)
            weight += 30
        if h.whiff_rate is not None:
            points += _norm(h.whiff_rate, 15, 40, 25)
            weight += 25
        if h.chase_rate is not None:
            points += _norm(h.chase_rate, 20, 40, 20)
            weight += 20
        if h.k_last_10g is not None:
            points += _norm(h.k_last_10g, 3, 15, 25)
            weight += 25

    elif prop_type == "RBI":
        if h.exit_velo_avg is not None:
            points += _norm(h.exit_velo_avg, 84, 96, 10)
            weight += 10
        if h.barrel_pct is not None:
            points += _norm(h.barrel_pct, 4, 18, 15)
            weight += 15
        if h.hard_hit_pct is not None:
            points += _norm(h.hard_hit_pct, 28, 52, 12)
            weight += 12
        if h.xwoba is not None:
            points += _norm(h.xwoba, 0.28, 0.44, 15)
            weight += 15
        if h.rbi_last_10g is not None:
            points += _norm(h.rbi_last_10g, 0, 10, 20)
            weight += 20
        if h.pitcher_hand and h.vs_rhp_avg and h.pitcher_hand == "R":
            points += _norm(h.vs_rhp_avg, 0.220, 0.340, 14)
            weight += 14
        elif h.pitcher_hand and h.vs_lhp_avg and h.pitcher_hand == "L":
            points += _norm(h.vs_lhp_avg, 0.220, 0.340, 14)
            weight += 14

    elif prop_type == "Pitcher Strikeout":
        # For pitcher Ks, the "hitter" category represents opposing lineup weakness
        # High opposing K rate = good for pitcher strikeout prop
        if h.k_rate is not None:
            points += _norm(h.k_rate, 10, 35, 40)
            weight += 40
        if h.whiff_rate is not None:
            points += _norm(h.whiff_rate, 15, 38, 30)
            weight += 30
        if h.chase_rate is not None:
            points += _norm(h.chase_rate, 20, 40, 30)
            weight += 30

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_pitcher(p: PitcherData, prop_type: str) -> float:
    points = 0.0
    weight = 0.0

    if prop_type == "Home Run":
        if p.hr_per_9 is not None:
            points += _norm(p.hr_per_9, 0.5, 2.0, 20)
            weight += 20
        if p.hr_per_9_recent is not None:
            points += _norm(p.hr_per_9_recent, 0.5, 2.5, 15)
            weight += 15
        if p.fly_ball_rate_allowed is not None:
            points += _norm(p.fly_ball_rate_allowed, 25, 45, 12)
            weight += 12
        if p.barrel_pct_allowed is not None:
            points += _norm(p.barrel_pct_allowed, 4, 12, 15)
            weight += 15
        if p.hard_contact_pct is not None:
            points += _norm(p.hard_contact_pct, 28, 44, 12)
            weight += 12
        if p.batter_avg_vs_primary is not None:
            points += _norm(p.batter_avg_vs_primary, 0.18, 0.38, 15)
            weight += 15
        if p.ground_ball_rate is not None:
            # Low GB rate = more fly balls = more HR chances
            points += _norm(50 - p.ground_ball_rate, -10, 25, 11)
            weight += 11

    elif prop_type == "Hit":
        if p.babip_allowed is not None:
            points += _norm(p.babip_allowed, 0.260, 0.360, 20)
            weight += 20
        if p.hits_per_9 is not None:
            points += _norm(p.hits_per_9, 6, 11, 25)
            weight += 25
        if p.hard_contact_pct is not None:
            points += _norm(p.hard_contact_pct, 28, 44, 20)
            weight += 20
        if p.barrel_pct_allowed is not None:
            points += _norm(p.barrel_pct_allowed, 4, 12, 15)
            weight += 15
        if p.ground_ball_rate is not None:
            # High GB rate = more hits in play for hit props
            points += _norm(p.ground_ball_rate, 35, 58, 20)
            weight += 20

    elif prop_type == "Stolen Base":
        if p.delivery_time is not None:
            # Slower delivery = easier to steal (1.2s = fast, 1.6s = slow)
            points += _norm(p.delivery_time, 1.1, 1.7, 40)
            weight += 40
        if p.ground_ball_rate is not None:
            # Higher GB = more opportunities (ball in play)
            points += _norm(p.ground_ball_rate, 35, 58, 30)
            weight += 30
        if p.k_per_9 is not None:
            # Low K pitcher = more balls in play = more SB attempts
            points += _norm(10 - p.k_per_9, 0, 6, 30)
            weight += 30

    elif prop_type == "Strikeout":
        if p.k_per_9 is not None:
            points += _norm(p.k_per_9, 5, 14, 30)
            weight += 30
        if p.k_per_9_recent is not None:
            points += _norm(p.k_per_9_recent, 5, 15, 25)
            weight += 25
        if p.whiff_rate is not None:
            points += _norm(p.whiff_rate, 20, 40, 25)
            weight += 25
        if p.avg_fastball_velo is not None:
            points += _norm(p.avg_fastball_velo, 88, 100, 10)
            weight += 10
        if p.batter_avg_vs_primary is not None:
            # Low batting avg vs primary pitch = more Ks
            points += _norm(0.400 - p.batter_avg_vs_primary, 0.05, 0.22, 10)
            weight += 10

    elif prop_type == "RBI":
        if p.hr_per_9 is not None:
            points += _norm(p.hr_per_9, 0.5, 2.0, 15)
            weight += 15
        if p.hard_contact_pct is not None:
            points += _norm(p.hard_contact_pct, 28, 44, 20)
            weight += 20
        if p.barrel_pct_allowed is not None:
            points += _norm(p.barrel_pct_allowed, 4, 12, 20)
            weight += 20
        if p.hits_per_9 is not None:
            points += _norm(p.hits_per_9, 6, 11, 20)
            weight += 20
        if p.babip_allowed is not None:
            points += _norm(p.babip_allowed, 0.260, 0.360, 15)
            weight += 15
        if p.ground_ball_rate is not None:
            points += _norm(p.ground_ball_rate, 35, 55, 10)
            weight += 10

    elif prop_type == "Pitcher Strikeout":
        # Pitcher's own K ability — this is the dominant factor
        if p.sw_str_pct is not None:
            # SwStr% is the single best predictor — elite is 13%+
            points += _norm(p.sw_str_pct, 7, 16, 25)
            weight += 25
        if p.k_pct is not None:
            # K% (Ks per batter faced) — elite is 30%+
            points += _norm(p.k_pct, 15, 35, 20)
            weight += 20
        if p.k_pct_recent is not None:
            # Recent K% — last 3 starts matters more than season
            points += _norm(p.k_pct_recent, 15, 38, 20)
            weight += 20
        if p.k_per_9 is not None:
            points += _norm(p.k_per_9, 5, 14, 15)
            weight += 15
        if p.whiff_rate is not None:
            points += _norm(p.whiff_rate, 20, 40, 10)
            weight += 10
        if p.avg_fastball_velo is not None:
            # Harder throwers miss more bats
            points += _norm(p.avg_fastball_velo, 88, 100, 5)
            weight += 5
        if p.strikeout_pitch_pct is not None:
            # % of pitches that are K pitches (slider/curve/change)
            points += _norm(p.strikeout_pitch_pct, 30, 65, 5)
            weight += 5
        if p.opp_team_k_rate is not None:
            # Opposing team K rate — high = better for pitcher Ks
            points += _norm(p.opp_team_k_rate, 18, 32, 10)
            weight += 10

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_park(p: ParkData, prop_type: str) -> float:
    if p.is_dome:
        return 50.0

    points = 0.0
    weight = 0.0

    if prop_type in ("Home Run", "RBI"):
        if p.hr_factor:
            points += _norm(p.hr_factor, 0.82, 1.20, 40)
            weight += 40
        if p.altitude_ft:
            points += _norm(p.altitude_ft, 0, 5200, 30)
            weight += 30
        if p.rf_dist:
            points += _norm(330 - p.rf_dist, -20, 30, 15)
            weight += 15
        if p.lf_dist:
            points += _norm(330 - p.lf_dist, -20, 30, 15)
            weight += 15

    elif prop_type == "Hit":
        # Larger parks suppress hits slightly; turf helps
        if p.hr_factor:
            points += _norm(p.hr_factor, 0.85, 1.15, 50)
            weight += 50
        if p.cf_dist:
            # Deeper CF = more triples/doubles territory
            points += _norm(p.cf_dist, 390, 440, 50)
            weight += 50

    elif prop_type == "Stolen Base":
        # Park barely matters for SB
        return 50.0

    elif prop_type == "Strikeout":
        # Park barely matters for K props
        return 50.0

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_weather(w: WeatherData, prop_type: str) -> float:
    if w.hr_wind_effect == "dome":
        return 50.0

    # Weather mainly affects HR and RBI; minimal effect on SB/K/Hit
    if prop_type in ("Stolen Base", "Strikeout"):
        return 50.0

    points = 0.0
    weight = 0.0

    points += _norm(w.temp_f, 40, 95, 20)
    weight += 20

    if w.hr_wind_effect == "favorable":
        wind_score = min(100, 50 + w.wind_component * 3)
    elif w.hr_wind_effect == "unfavorable":
        wind_score = max(0, 50 + w.wind_component * 3)
    else:
        wind_score = 50

    points += wind_score * 0.40
    weight += 40

    if w.carry_modifier_ft:
        points += _norm(w.carry_modifier_ft, -15, 20, 30)
        weight += 30

    points += _norm(w.humidity_pct, 20, 80, 10)
    weight += 10

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_situational(s: SituationalData, prop_type: str) -> float:
    points = 0.0
    weight = 0.0

    if prop_type == "Home Run":
        if s.lineup_position is not None:
            pos_score = 70 if s.lineup_position in [2,3,4,5] else 55 if s.lineup_position in [1,6] else 40
            points += pos_score * 0.15
            weight += 15
        if s.implied_team_total is not None:
            points += _norm(s.implied_team_total, 2.5, 6.5, 30)
            weight += 30
        if s.bullpen_era is not None:
            points += _norm(s.bullpen_era, 2.8, 6.0, 25)
            weight += 25
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 30)
            weight += 30

    elif prop_type == "Hit":
        if s.lineup_position is not None:
            # Top of order gets more PAs
            pos_score = 75 if s.lineup_position in [1,2,3] else 60 if s.lineup_position in [4,5] else 45
            points += pos_score * 0.20
            weight += 20
        if s.proj_plate_apps is not None:
            points += _norm(s.proj_plate_apps, 3.0, 5.5, 30)
            weight += 30
        if s.implied_team_total is not None:
            points += _norm(s.implied_team_total, 2.5, 6.5, 25)
            weight += 25
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 25)
            weight += 25

    elif prop_type == "Stolen Base":
        if s.lineup_position is not None:
            # Leadoff and 2-hole get most SB chances
            pos_score = 85 if s.lineup_position in [1,2] else 65 if s.lineup_position in [3,4] else 40
            points += pos_score * 0.25
            weight += 25
        if s.runners_on_pct is not None:
            points += _norm(s.runners_on_pct, 25, 50, 25)
            weight += 25
        if s.catcher_pop_time is not None:
            # Higher pop time = easier to steal
            points += _norm(s.catcher_pop_time, 1.8, 2.3, 30)
            weight += 30
        if s.game_total is not None:
            # Close games = more steal attempts
            points += _norm(s.game_total, 5, 11, 10)
            weight += 10
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 10)
            weight += 10

    elif prop_type == "Strikeout":
        if s.proj_plate_apps is not None:
            points += _norm(s.proj_plate_apps, 3.0, 5.5, 35)
            weight += 35
        if s.lineup_position is not None:
            # Lower order tends to K more
            pos_score = 65 if s.lineup_position in [7,8,9] else 55 if s.lineup_position in [5,6] else 45
            points += pos_score * 0.25
            weight += 25
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 20)
            weight += 20
        if s.implied_team_total is not None:
            # Low team total = pitcher's game = more Ks
            points += _norm(10 - s.implied_team_total, 3, 7, 20)
            weight += 20

    elif prop_type == "RBI":
        if s.lineup_position is not None:
            # 3-5 hitters drive in most runs
            pos_score = 80 if s.lineup_position in [3,4,5] else 60 if s.lineup_position in [2,6] else 40
            points += pos_score * 0.20
            weight += 20
        if s.implied_team_total is not None:
            points += _norm(s.implied_team_total, 2.5, 6.5, 30)
            weight += 30
        if s.next_batter_obp is not None:
            # High OBP batters ahead = more runners on
            points += _norm(s.next_batter_obp, 0.28, 0.42, 25)
            weight += 25
        if s.bullpen_era is not None:
            points += _norm(s.bullpen_era, 2.8, 6.0, 15)
            weight += 15
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 10)
            weight += 10

    elif prop_type == "Pitcher Strikeout":
        if s.proj_innings is not None:
            # More innings = more K opportunities (starter = ~6, reliever = ~1)
            points += _norm(s.proj_innings, 1.0, 7.0, 40)
            weight += 40
        if s.game_total is not None:
            # Low game total = pitcher's game = more Ks expected
            points += _norm(10 - s.game_total, 1, 5, 25)
            weight += 25
        if s.implied_team_total is not None:
            # Low opposing team total = pitcher dominant
            points += _norm(5 - s.implied_team_total, -1, 3, 20)
            weight += 20
        if s.is_starter is not None:
            # Starters face entire lineup multiple times = far more K chances
            points += 80 * 0.15 if s.is_starter else 25 * 0.15
            weight += 15

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


# ── Master Scoring Function ───────────────────────────────────────────────────

def score_prop(prop: PropInput) -> ScoringResult:
    w = WEIGHTS.get(prop.prop_type, WEIGHTS["Home Run"])

    category_scores = {
        "hitter":      score_hitter(prop.hitter, prop.prop_type),
        "pitcher":     score_pitcher(prop.pitcher, prop.prop_type),
        "park":        score_park(prop.park, prop.prop_type),
        "weather":     score_weather(prop.weather, prop.prop_type),
        "situational": score_situational(prop.situational, prop.prop_type),
    }

    raw_score  = sum(score * w[cat] for cat, score in category_scores.items())
    score_delta = raw_score - 50
    model_prob  = prop.implied_prob + (score_delta / 50) * 0.20
    model_prob  = max(0.01, min(0.99, model_prob))
    edge        = model_prob - prop.implied_prob
    completeness = _data_completeness(prop)
    confidence  = max(0, min(99, 50 + (edge * 200) * completeness))
    grade, desc = _get_grade(confidence)
    signal      = "positive" if edge > 0.03 else "negative" if edge < -0.03 else "neutral"

    return ScoringResult(
        confidence=round(confidence, 1),
        grade=grade,
        grade_desc=desc,
        model_prob=round(model_prob, 3),
        edge=round(edge, 3),
        edge_str=f"+{round(edge*100,1)}%" if edge >= 0 else f"{round(edge*100,1)}%",
        category_scores={k: round(v, 1) for k, v in category_scores.items()},
        signal=signal,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(value: float, low: float, high: float, weight: float) -> float:
    clamped    = max(low, min(high, value))
    normalized = (clamped - low) / (high - low)
    return normalized * weight


def _get_grade(confidence: float) -> tuple:
    if confidence >= 90: return "A+", "Elite"
    if confidence >= 85: return "A",  "Excellent"
    if confidence >= 80: return "A−", "Very Strong"
    if confidence >= 75: return "B+", "Strong"
    if confidence >= 70: return "B",  "Good"
    if confidence >= 65: return "B−", "Moderate"
    if confidence >= 60: return "C+", "Lean"
    if confidence >= 55: return "C",  "Weak"
    return "D", "Avoid"


def _data_completeness(prop: PropInput) -> float:
    checks = [
        prop.hitter.exit_velo_avg is not None,
        prop.hitter.barrel_pct is not None,
        prop.hitter.xwoba is not None,
        prop.pitcher.hr_per_9 is not None or prop.pitcher.k_per_9 is not None,
        prop.pitcher.barrel_pct_allowed is not None,
        prop.park.hr_factor != 1.00,
        prop.weather.temp_f != 72.0,
        prop.situational.implied_team_total is not None,
    ]
    return 0.5 + (sum(checks) / len(checks)) * 0.5
