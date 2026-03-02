"""
BaseballIQ Scoring Engine — All 6 Prop Types
Handles: Home Run, Hit, Stolen Base, Strikeout, RBI, Pitcher Strikeout

Each prop type uses different factor weights because what matters for a HR
is very different from what matters for a stolen base or a strikeout.

HR model updated: added ISO, HR/FB rate, pull% to air, home/away splits,
pitcher HR/FB allowed, fatigue, day/night, count tendencies, lineup protection.
"""

from dataclasses import dataclass, field
from typing import Optional


# ── Per-prop-type weights (each dict must sum to 1.0) ────────────────────────
WEIGHTS = {
    "Home Run": {
        "hitter":      0.45,   # hitter Statcast is by far the strongest HR predictor
        "pitcher":     0.20,   # pitcher HR tendencies matter but less than batter profile
        "park":        0.15,   # park factors are real but secondary
        "weather":     0.10,   # weather matters but not game-by-game dominant
        "situational": 0.10,
    },
    "Hit": {
        "hitter":      0.35,
        "pitcher":     0.25,
        "park":        0.10,
        "weather":     0.10,
        "situational": 0.20,
    },
    "Stolen Base": {
        "hitter":      0.30,
        "pitcher":     0.25,
        "park":        0.05,
        "weather":     0.05,
        "situational": 0.35,
    },
    "Strikeout": {
        "hitter":      0.30,
        "pitcher":     0.40,
        "park":        0.05,
        "weather":     0.05,
        "situational": 0.20,
    },
    "RBI": {
        "hitter":      0.25,
        "pitcher":     0.20,
        "park":        0.15,
        "weather":     0.10,
        "situational": 0.30,
    },
    "Pitcher Strikeout": {
        "hitter":      0.25,
        "pitcher":     0.45,
        "park":        0.05,
        "weather":     0.05,
        "situational": 0.20,
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
    # ── NEW HR-specific power metrics ──
    iso:                Optional[float] = None   # Isolated Power (SLG - AVG). Elite: .250+, avg: .150
    hr_per_fb:          Optional[float] = None   # HR/FB rate %. Elite: 20%+, avg: 10-12%
    pull_air_pct:       Optional[float] = None   # % of batted balls pulled in the air. Elite: 15%+
    hr_home:            Optional[int]   = None   # HRs hit at home this season
    hr_away:            Optional[int]   = None   # HRs hit away this season
    is_home_game:       Optional[bool]  = None   # Is today a home game for this batter?
    games_played:       Optional[int]   = None   # Games played (for rate calculations)
    hr_rate:            Optional[float] = None   # HR per PA (overall rate)
    # Contact metrics (Hit)
    contact_rate:       Optional[float] = None
    babip:              Optional[float] = None
    line_drive_rate:    Optional[float] = None
    # Speed metrics (Stolen Base)
    sprint_speed:       Optional[float] = None
    sb_attempt_rate:    Optional[float] = None
    sb_success_rate:    Optional[float] = None
    # Strikeout metrics
    k_rate:             Optional[float] = None
    whiff_rate:         Optional[float] = None
    chase_rate:         Optional[float] = None
    # Advanced
    xslg:               Optional[float] = None
    xwoba:              Optional[float] = None
    # Platoon
    vs_rhp_avg:         Optional[float] = None
    vs_lhp_avg:         Optional[float] = None
    vs_rhp_iso:         Optional[float] = None   # NEW: ISO vs RHP specifically
    vs_lhp_iso:         Optional[float] = None   # NEW: ISO vs LHP specifically
    pitcher_hand:       Optional[str]   = None
    batter_hand:        Optional[str]   = None   # NEW: L/R (affects wind/park interactions)
    # Rolling form
    hr_last_10g:        Optional[int]   = None
    hits_last_10g:      Optional[int]   = None
    k_last_10g:         Optional[int]   = None
    sb_last_10g:        Optional[int]   = None
    rbi_last_10g:       Optional[int]   = None
    hr_last_30g:        Optional[int]   = None   # NEW: 30-game HR trend (bigger sample)


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
    # ── NEW pitcher HR-specific metrics ──
    hr_per_fb_allowed:       Optional[float] = None  # HR/FB% allowed. Elite (low): <8%, bad: >14%
    fastball_hr_rate:        Optional[float] = None  # HR rate on fastballs specifically
    offspeed_hr_rate:        Optional[float] = None  # HR rate on offspeed pitches
    pitches_per_start_last3: Optional[float] = None  # Avg pitch count last 3 starts (fatigue proxy)
    days_rest:               Optional[int]   = None  # Days since last outing (4=normal, <4=short)
    season_innings:          Optional[float] = None  # Total IP this season (workload/fatigue)
    era_last_3:              Optional[float] = None  # ERA last 3 starts (recent form)
    xfip:                    Optional[float] = None  # xFIP (normalizes HR allowed, park-neutral)
    # Hit metrics
    babip_allowed:           Optional[float] = None
    hits_per_9:              Optional[float] = None
    # Strikeout metrics
    k_per_9:                 Optional[float] = None
    k_per_9_recent:          Optional[float] = None
    whiff_rate:              Optional[float] = None
    # Stolen base metrics
    delivery_time:           Optional[float] = None
    holds_per_game:          Optional[float] = None
    # Pitch mix
    pitch_mix:               Optional[dict]  = None
    avg_fastball_velo:       Optional[float] = None
    batter_avg_vs_primary:   Optional[float] = None
    # Pitcher strikeout specific
    sw_str_pct:              Optional[float] = None
    k_pct:                   Optional[float] = None
    k_pct_recent:            Optional[float] = None
    strikeout_pitch_pct:     Optional[float] = None
    proj_innings:            Optional[float] = None
    opp_team_k_rate:         Optional[float] = None


@dataclass
class ParkData:
    name:           str   = ""
    hr_factor:      float = 1.00
    altitude_ft:    int   = 0
    is_dome:        bool  = False
    lf_dist:        Optional[int] = None
    cf_dist:        Optional[int] = None
    rf_dist:        Optional[int] = None
    # NEW: directional wall heights (affects HR rates by batter hand)
    lf_wall_height: Optional[float] = None   # e.g. Green Monster = 37ft
    rf_wall_height: Optional[float] = None
    surface:        Optional[str]   = None   # "turf" or "grass"


@dataclass
class WeatherData:
    temp_f:            float = 72.0
    wind_speed_mph:    float = 5.0
    hr_wind_effect:    str   = "neutral"   # "favorable", "unfavorable", "neutral", "dome"
    wind_component:    float = 0.0
    humidity_pct:      float = 50.0
    carry_modifier_ft: float = 0.0
    # NEW weather fields
    wind_direction:    Optional[str]   = None   # "out_to_lf", "out_to_rf", "out_to_cf", "in", "cross"
    is_day_game:       Optional[bool]  = None   # Day games: ball carries slightly better in warm air


@dataclass
class SituationalData:
    lineup_position:    Optional[int]   = None
    proj_plate_apps:    Optional[float] = None
    bullpen_era:        Optional[float] = None
    game_total:         Optional[float] = None
    implied_team_total: Optional[float] = None
    trend_score:        Optional[float] = None
    # SB specific
    runners_on_pct:     Optional[float] = None
    next_batter_obp:    Optional[float] = None
    catcher_pop_time:   Optional[float] = None
    # Pitcher strikeout specific
    proj_innings:       Optional[float] = None
    is_starter:         Optional[bool]  = None
    # NEW HR-specific situational
    pitcher_count_tendency: Optional[float] = None  # Avg count when batter swings (>1.0 = hitter's counts)
    lineup_protection:  Optional[float]  = None     # OPS of batter hitting behind this player
    is_day_game:        Optional[bool]   = None     # Redundant with weather but useful for context
    hr_in_last_5g:      Optional[bool]   = None     # Has batter hit HR in last 5 games? (hot streak)
    career_hr_vs_pitcher: Optional[int] = None      # Career HRs vs today's specific pitcher


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
        # ── Core power metrics (unchanged) ──
        if h.exit_velo_avg is not None:
            points += _norm(h.exit_velo_avg, 84, 96, 10)
            weight += 10
        if h.barrel_pct is not None:
            points += _norm(h.barrel_pct, 4, 20, 14)
            weight += 14
        if h.hard_hit_pct is not None:
            points += _norm(h.hard_hit_pct, 28, 55, 8)
            weight += 8
        if h.fly_ball_rate is not None:
            points += _norm(h.fly_ball_rate, 25, 50, 7)
            weight += 7
        if h.launch_angle_avg is not None:
            points += _norm(h.launch_angle_avg, 10, 30, 6)
            weight += 6
        if h.xwoba is not None:
            points += _norm(h.xwoba, 0.28, 0.44, 8)
            weight += 8

        # ── NEW: ISO — best single HR predictor ──
        if h.iso is not None:
            # ISO elite: .250+, avg: .150, weak: .100
            points += _norm(h.iso, 0.080, 0.280, 18)
            weight += 18

        # ── NEW: HR/FB rate — how often fly balls become HRs ──
        if h.hr_per_fb is not None:
            # Elite: 20%+, avg: 10-12%, weak: <7%
            points += _norm(h.hr_per_fb, 5, 25, 16)
            weight += 16

        # ── NEW: Pull% to air — pulled fly balls are the HRs ──
        if h.pull_air_pct is not None:
            # Elite: 15%+, avg: 8-10%
            points += _norm(h.pull_air_pct, 4, 18, 12)
            weight += 12

        # ── NEW: Home vs away HR split ──
        if h.is_home_game is not None and h.games_played and h.games_played > 10:
            if h.is_home_game and h.hr_home is not None:
                # Normalize home HR rate per 81 games
                home_rate = (h.hr_home / max(1, h.games_played / 2)) * 81
                points += _norm(home_rate, 5, 45, 8)
                weight += 8
            elif not h.is_home_game and h.hr_away is not None:
                away_rate = (h.hr_away / max(1, h.games_played / 2)) * 81
                points += _norm(away_rate, 5, 45, 8)
                weight += 8

        # ── NEW: Platoon ISO (better than avg for HR) ──
        if h.pitcher_hand == "R" and h.vs_rhp_iso is not None:
            points += _norm(h.vs_rhp_iso, 0.080, 0.260, 10)
            weight += 10
        elif h.pitcher_hand == "L" and h.vs_lhp_iso is not None:
            points += _norm(h.vs_lhp_iso, 0.080, 0.260, 10)
            weight += 10
        # Fallback to avg if ISO splits unavailable
        elif h.pitcher_hand and h.vs_rhp_avg and h.pitcher_hand == "R":
            points += _norm(h.vs_rhp_avg, 0.220, 0.340, 7)
            weight += 7
        elif h.pitcher_hand and h.vs_lhp_avg and h.pitcher_hand == "L":
            points += _norm(h.vs_lhp_avg, 0.220, 0.340, 7)
            weight += 7

        # ── Recent form ──
        if h.hr_last_10g is not None:
            points += _norm(h.hr_last_10g, 0, 5, 12)
            weight += 12
        # NEW: 30-game trend (bigger sample, smooths noise)
        if h.hr_last_30g is not None:
            points += _norm(h.hr_last_30g, 0, 12, 8)
            weight += 8

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
        # ── Core metrics (trimmed weights to fit new factors) ──
        if p.hr_per_9 is not None:
            points += _norm(p.hr_per_9, 0.5, 2.0, 14)
            weight += 14
        if p.hr_per_9_recent is not None:
            points += _norm(p.hr_per_9_recent, 0.5, 2.5, 10)
            weight += 10

        # ── NEW: HR/FB allowed — better predictor than raw HR/9 ──
        # Normalizes for fly ball rate; elite pitchers: <8%, bad: >14%
        if p.hr_per_fb_allowed is not None:
            points += _norm(p.hr_per_fb_allowed, 5, 18, 18)
            weight += 18

        # ── NEW: xFIP — park/luck-neutral HR predictor ──
        if p.xfip is not None:
            # xFIP elite: <3.20, avg: 4.00, bad: >5.00
            points += _norm(p.xfip, 2.8, 5.5, 12)
            weight += 12

        if p.fly_ball_rate_allowed is not None:
            points += _norm(p.fly_ball_rate_allowed, 25, 45, 10)
            weight += 10
        if p.barrel_pct_allowed is not None:
            points += _norm(p.barrel_pct_allowed, 4, 12, 12)
            weight += 12
        if p.hard_contact_pct is not None:
            points += _norm(p.hard_contact_pct, 28, 44, 8)
            weight += 8

        # ── NEW: Pitch-specific HR rates ──
        if p.fastball_hr_rate is not None:
            # HR rate on fastballs: high = dangerous for HR props
            points += _norm(p.fastball_hr_rate, 1, 5, 8)
            weight += 8
        if p.offspeed_hr_rate is not None:
            points += _norm(p.offspeed_hr_rate, 0.5, 4, 6)
            weight += 6

        # ── NEW: Fatigue indicators ──
        if p.days_rest is not None:
            # Short rest (3 days) = worse command = more HRs
            # Normal rest (4-5 days) = baseline
            # Extra rest (6+) = slightly better
            if p.days_rest <= 3:
                fatigue_score = 75   # short rest = more HR risk
            elif p.days_rest <= 5:
                fatigue_score = 50   # normal
            else:
                fatigue_score = 35   # extra rest = sharper
            points += fatigue_score * 0.06
            weight += 6

        if p.era_last_3 is not None:
            # Poor recent form = more HR risk
            points += _norm(p.era_last_3, 1.5, 7.0, 8)
            weight += 8

        if p.batter_avg_vs_primary is not None:
            points += _norm(p.batter_avg_vs_primary, 0.18, 0.38, 8)
            weight += 8

        if p.ground_ball_rate is not None:
            points += _norm(50 - p.ground_ball_rate, -10, 25, 8)
            weight += 8

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
            points += _norm(p.ground_ball_rate, 35, 58, 20)
            weight += 20

    elif prop_type == "Stolen Base":
        if p.delivery_time is not None:
            points += _norm(p.delivery_time, 1.1, 1.7, 40)
            weight += 40
        if p.ground_ball_rate is not None:
            points += _norm(p.ground_ball_rate, 35, 58, 30)
            weight += 30
        if p.k_per_9 is not None:
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
        # K/9 is the primary signal — elite starters: 10+, avg: 8, weak: <6
        if p.k_per_9 is not None:
            points += _norm(p.k_per_9, 5, 14, 28)
            weight += 28
        # K% is better than K/9 (normalizes for balls in play)
        if p.k_pct is not None:
            points += _norm(p.k_pct, 15, 36, 22)
            weight += 22
        # Recent K% matters more than season — pitcher may be peaking or declining
        if p.k_pct_recent is not None:
            points += _norm(p.k_pct_recent, 15, 38, 18)
            weight += 18
        # SwStr% is the best leading indicator — swing and miss before Ks register
        if p.sw_str_pct is not None:
            points += _norm(p.sw_str_pct, 7, 17, 20)
            weight += 20
        # Whiff rate on contact — higher = harder to make contact
        if p.whiff_rate is not None:
            points += _norm(p.whiff_rate, 18, 42, 12)
            weight += 12
        # Fastball velo — harder = more swing and miss
        if p.avg_fastball_velo is not None:
            points += _norm(p.avg_fastball_velo, 88, 100, 8)
            weight += 8
        # Opponent team K rate — facing a high-K team amplifies pitcher Ks
        if p.opp_team_k_rate is not None:
            points += _norm(p.opp_team_k_rate, 18, 32, 12)
            weight += 12

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
            points += _norm(p.hr_factor, 0.82, 1.20, 35)
            weight += 35
        if p.altitude_ft:
            points += _norm(p.altitude_ft, 0, 5200, 25)
            weight += 25
        if p.rf_dist:
            points += _norm(330 - p.rf_dist, -20, 30, 12)
            weight += 12
        if p.lf_dist:
            points += _norm(330 - p.lf_dist, -20, 30, 12)
            weight += 12
        # NEW: Wall height — shorter wall = more HRs even at same distance
        if p.lf_wall_height is not None:
            # Low wall (8ft) = easier HR, high wall (37ft Green Monster) = harder
            points += _norm(20 - p.lf_wall_height, -17, 12, 8)
            weight += 8
        if p.rf_wall_height is not None:
            points += _norm(20 - p.rf_wall_height, -17, 12, 8)
            weight += 8

    elif prop_type == "Hit":
        if p.hr_factor:
            points += _norm(p.hr_factor, 0.85, 1.15, 50)
            weight += 50
        if p.cf_dist:
            points += _norm(p.cf_dist, 390, 440, 50)
            weight += 50

    elif prop_type in ("Stolen Base", "Strikeout"):
        return 50.0

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_weather(w: WeatherData, prop_type: str) -> float:
    if w.hr_wind_effect == "dome":
        return 50.0

    if prop_type in ("Stolen Base", "Strikeout"):
        return 50.0

    points = 0.0
    weight = 0.0

    points += _norm(w.temp_f, 40, 95, 18)
    weight += 18

    if w.hr_wind_effect == "favorable":
        wind_score = min(100, 50 + w.wind_component * 3)
    elif w.hr_wind_effect == "unfavorable":
        wind_score = max(0, 50 + w.wind_component * 3)
    else:
        wind_score = 50

    points += wind_score * 0.38
    weight += 38

    if w.carry_modifier_ft:
        points += _norm(w.carry_modifier_ft, -15, 20, 26)
        weight += 26

    points += _norm(w.humidity_pct, 20, 80, 8)
    weight += 8

    # NEW: Day game bonus — warm afternoon air carries ball slightly further
    if w.is_day_game is not None:
        day_score = 58 if w.is_day_game else 48
        points += day_score * 0.10
        weight += 10

    if weight == 0:
        return 50.0
    return max(0, min(100, (points / weight) * 100))


def score_situational(s: SituationalData, prop_type: str) -> float:
    points = 0.0
    weight = 0.0

    if prop_type == "Home Run":
        if s.lineup_position is not None:
            pos_score = 72 if s.lineup_position in [2,3,4,5] else 55 if s.lineup_position in [1,6] else 38
            points += pos_score * 0.12
            weight += 12

        if s.implied_team_total is not None:
            points += _norm(s.implied_team_total, 2.5, 6.5, 22)
            weight += 22

        if s.bullpen_era is not None:
            points += _norm(s.bullpen_era, 2.8, 6.0, 18)
            weight += 18

        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 18)
            weight += 18

        # NEW: Count tendency — does pitcher fall behind? Hitter's counts = more HRs
        if s.pitcher_count_tendency is not None:
            # >1.0 = pitcher falls behind often (good for batter)
            points += _norm(s.pitcher_count_tendency, 0.7, 1.4, 12)
            weight += 12

        # NEW: Lineup protection — good batter behind = pitcher can't pitch around
        if s.lineup_protection is not None:
            # OPS of batter behind: .700 = avg, .900 = elite protection
            points += _norm(s.lineup_protection, 0.600, 0.950, 10)
            weight += 10

        # NEW: Hot streak — HR in last 5 games is meaningful signal
        if s.hr_in_last_5g is not None:
            points += (68 if s.hr_in_last_5g else 46) * 0.08
            weight += 8

        # NEW: Career HR vs this pitcher
        if s.career_hr_vs_pitcher is not None:
            points += _norm(s.career_hr_vs_pitcher, 0, 5, 8)
            weight += 8

        # Ensure weights still sum sensibly
        # Total: 12+22+18+18+12+10+8+8 = 108 → normalized in final calc

    elif prop_type == "Hit":
        if s.lineup_position is not None:
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
            pos_score = 85 if s.lineup_position in [1,2] else 65 if s.lineup_position in [3,4] else 40
            points += pos_score * 0.25
            weight += 25
        if s.runners_on_pct is not None:
            points += _norm(s.runners_on_pct, 25, 50, 25)
            weight += 25
        if s.catcher_pop_time is not None:
            points += _norm(s.catcher_pop_time, 1.8, 2.3, 30)
            weight += 30
        if s.game_total is not None:
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
            pos_score = 65 if s.lineup_position in [7,8,9] else 55 if s.lineup_position in [5,6] else 45
            points += pos_score * 0.25
            weight += 25
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 20)
            weight += 20
        if s.implied_team_total is not None:
            points += _norm(10 - s.implied_team_total, 3, 7, 20)
            weight += 20

    elif prop_type == "RBI":
        if s.lineup_position is not None:
            pos_score = 80 if s.lineup_position in [3,4,5] else 60 if s.lineup_position in [2,6] else 40
            points += pos_score * 0.20
            weight += 20
        if s.implied_team_total is not None:
            points += _norm(s.implied_team_total, 2.5, 6.5, 30)
            weight += 30
        if s.next_batter_obp is not None:
            points += _norm(s.next_batter_obp, 0.28, 0.42, 25)
            weight += 25
        if s.bullpen_era is not None:
            points += _norm(s.bullpen_era, 2.8, 6.0, 15)
            weight += 15
        if s.trend_score is not None:
            points += _norm(s.trend_score, -1.0, 1.0, 10)
            weight += 10

    elif prop_type == "Pitcher Strikeout":
        # Starter vs reliever is the biggest situational factor
        # Starters get 5-6 IP of K opportunities; relievers get 1-2
        if s.is_starter is not None:
            starter_score = 78 if s.is_starter else 22
            points += starter_score * 0.30
            weight += 30
        # Projected innings — more innings = more K opportunities
        if s.proj_innings is not None:
            points += _norm(s.proj_innings, 1.0, 7.0, 35)
            weight += 35
        # Low-scoring games = pitcher is dominant = more Ks
        if s.game_total is not None:
            points += _norm(10 - s.game_total, 1, 5, 20)
            weight += 20
        # Opponent run environment — low implied total = good pitching matchup
        if s.implied_team_total is not None:
            points += _norm(5 - s.implied_team_total, -1, 3, 15)
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

    hr_prop = prop.prop_type == "Home Run"

    # For HR props: hitter profile must gate the final score.
    # A weak hitter (Mullins, hitter=41) facing a bad pitcher should NOT
    # outscore an elite hitter (Ohtani, hitter=87) facing a good pitcher.
    # We apply a hitter floor multiplier: if hitter score < 55, compress
    # the pitcher bonus so it can't rescue a weak power profile.
    if hr_prop:
        h_score = category_scores["hitter"]
        p_score = category_scores["pitcher"]
        # Hitter gate: scales from 0.3x (weak hitter=0) to 1.0x (strong hitter=70+)
        hitter_gate = min(1.0, max(0.3, h_score / 70.0))
        # Re-weight pitcher contribution through the gate
        p_contribution = (p_score - 50) * (1 - w["pitcher"]) + (p_score - 50) * w["pitcher"] * hitter_gate
        # Rebuild raw score with gated pitcher
        raw_score = (
            h_score          * w["hitter"] +
            (50 + p_contribution * w["pitcher"]) * w["pitcher"] +
            category_scores["park"]        * w["park"] +
            category_scores["weather"]     * w["weather"] +
            category_scores["situational"] * w["situational"]
        )
    else:
        raw_score = sum(score * w[cat] for cat, score in category_scores.items())

    score_delta = raw_score - 50
    completeness = _data_completeness(prop)

    # Wider edge band for HR props — rare event needs more signal amplification
    edge_scale = 0.45 if hr_prop else 0.35

    model_prob  = prop.implied_prob + (score_delta / 50) * edge_scale
    model_prob  = max(0.01, min(0.99, model_prob))
    edge        = model_prob - prop.implied_prob

    confidence = 50 + (edge * 200) * completeness

    # Elite hitter bonus: reward genuinely elite power profiles above 70
    if hr_prop:
        h_score = category_scores["hitter"]
        if h_score > 70:
            elite_bonus = ((h_score - 70) / 30) * 14 * w["hitter"]
            confidence += elite_bonus

    # HR grade shift: HRs happen ~6% of the time per game.
    # Shift up so grades reflect rarity — a 65 raw conf is genuinely strong.
    if hr_prop:
        confidence += 8.0

    confidence = max(0, min(99, confidence))
    # Use HR-specific grades which reflect rarity of the event
    if hr_prop:
        grade, desc = _get_hr_grade(confidence)
    else:
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


def _get_hr_grade(confidence: float) -> tuple:
    """
    HR-specific grades. Since HRs happen ~5-10% of the time per game,
    grades reflect relative value vs peers, not absolute probability.
    A+ = top 1-2 picks on today's slate. D = avoid.
    """
    if confidence >= 82: return "A+", "Best HR pick today"
    if confidence >= 76: return "A",  "Strong HR value"
    if confidence >= 70: return "A−", "Good HR value"
    if confidence >= 64: return "B+", "Solid pick"
    if confidence >= 58: return "B",  "Lean play"
    if confidence >= 52: return "B−", "Slight lean"
    if confidence >= 46: return "C+", "Neutral"
    if confidence >= 40: return "C",  "Slight avoid"
    return "D", "Avoid"


def _data_completeness(prop: PropInput) -> float:
    """
    Returns 0.5–1.0 based on how much relevant data is populated.
    Each prop type checks only the fields that actually matter for that prop.
    """
    pt = prop.prop_type

    if pt == "Home Run":
        checks = [
            prop.hitter.exit_velo_avg is not None,
            prop.hitter.barrel_pct is not None,
            prop.hitter.iso is not None,
            prop.hitter.hr_per_fb is not None,
            prop.hitter.pull_air_pct is not None,
            prop.hitter.xwoba is not None,
            prop.pitcher.hr_per_9 is not None,
            prop.pitcher.hr_per_fb_allowed is not None,
            prop.pitcher.barrel_pct_allowed is not None,
            prop.park.hr_factor != 1.00,
            prop.weather.temp_f != 72.0,
            prop.situational.implied_team_total is not None,
            prop.situational.lineup_protection is not None,
        ]

    elif pt == "Strikeout":
        checks = [
            prop.hitter.k_rate is not None,
            prop.hitter.whiff_rate is not None,
            prop.hitter.chase_rate is not None,
            prop.hitter.k_last_10g is not None,
            prop.pitcher.k_per_9 is not None,
            prop.pitcher.whiff_rate is not None,
            prop.situational.proj_plate_apps is not None,
            prop.situational.implied_team_total is not None,
        ]

    elif pt == "Pitcher Strikeout":
        checks = [
            prop.pitcher.k_per_9 is not None,
            prop.pitcher.k_pct is not None,
            prop.pitcher.whiff_rate is not None,
            prop.pitcher.sw_str_pct is not None,
            prop.pitcher.avg_fastball_velo is not None,
            prop.situational.proj_innings is not None,
            prop.situational.is_starter is not None,
            prop.situational.game_total is not None,
        ]

    elif pt == "Hit":
        checks = [
            prop.hitter.contact_rate is not None,
            prop.hitter.babip is not None,
            prop.hitter.hard_hit_pct is not None,
            prop.hitter.xwoba is not None,
            prop.pitcher.babip_allowed is not None,
            prop.pitcher.hits_per_9 is not None,
            prop.situational.proj_plate_apps is not None,
            prop.situational.implied_team_total is not None,
        ]

    elif pt == "Stolen Base":
        checks = [
            prop.hitter.sprint_speed is not None,
            prop.hitter.sb_success_rate is not None,
            prop.hitter.sb_attempt_rate is not None,
            prop.pitcher.delivery_time is not None,
            prop.situational.lineup_position is not None,
        ]

    elif pt == "RBI":
        checks = [
            prop.hitter.exit_velo_avg is not None,
            prop.hitter.barrel_pct is not None,
            prop.hitter.xwoba is not None,
            prop.pitcher.hr_per_9 is not None,
            prop.pitcher.hard_contact_pct is not None,
            prop.situational.lineup_position is not None,
            prop.situational.implied_team_total is not None,
        ]

    else:
        checks = [prop.pitcher.k_per_9 is not None]

    filled = sum(1 for c in checks if c)
    return 0.5 + (filled / len(checks)) * 0.5
