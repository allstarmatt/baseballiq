"""
BaseballIQ — Player Name Matching
Solves the problem of sportsbooks using different name formats than MLB Stats API.

Examples:
  MLB API:   "Michael A. Taylor"   → FanDuel: "Mike Taylor"
  MLB API:   "William Contreras"   → DraftKings: "Will Contreras"
  MLB API:   "Nathaniel Lowe"      → BetMGM: "Nate Lowe"
  MLB API:   "Rafael Devers"       → Caesars: "Raffy Devers"

Strategy (applied in order):
  1. Exact match (lowercase, stripped)
  2. Known alias table (manually curated common mismatches)
  3. Last name + first initial match
  4. Last name only match (with team validation if possible)
  5. Fuzzy token match using character similarity
"""

import re
import unicodedata
from typing import Optional


# ── Known alias table ─────────────────────────────────────────────────────────
# Format: "mlb_api_name_lower": ["alias1", "alias2", ...]
# Add entries here as you discover mismatches in production logs
KNOWN_ALIASES: dict[str, list[str]] = {
    # Common nickname substitutions
    "michael a. taylor":    ["mike taylor", "michael taylor"],
    "william contreras":    ["will contreras", "willy contreras"],
    "nathaniel lowe":       ["nate lowe", "nat lowe"],
    "rafael devers":        ["raffy devers", "rafi devers"],
    "vladimir guerrero jr.":["vlad guerrero", "vlad guerrero jr", "vladimir guerrero"],
    "jose abreu":           ["jose a. abreu"],
    "michael brantley":     ["mike brantley"],
    "christopher morel":    ["chris morel"],
    "christopher paddack":  ["chris paddack"],
    "nicholas castellanos": ["nick castellanos", "nico castellanos"],
    "nicholas martinez":    ["nick martinez"],
    "nicholas pivetta":     ["nick pivetta"],
    "alexander reyes":      ["alex reyes"],
    "alexander cobb":       ["alex cobb"],
    "alexander wood":       ["alex wood"],
    "alexander claudio":    ["alex claudio"],
    "jonathan india":       ["jon india"],
    "jonathan loaisiga":    ["jon loaisiga", "johnny loaisiga"],
    "andrew mccutchen":     ["andy mccutchen"],
    "andrew heaney":        ["andy heaney"],
    "edward cabrera":       ["edward enrique cabrera", "ed cabrera"],
    "joshua lowe":          ["josh lowe"],
    "joshua naylor":        ["josh naylor"],
    "joshua bell":          ["josh bell"],
    "joshua rojas":         ["josh rojas"],
    "matthew mclain":       ["matt mclain"],
    "matthew olson":        ["matt olson"],
    "matthew boyd":         ["matt boyd"],
    "matthew strahm":       ["matt strahm"],
    "michael lorenzen":     ["mike lorenzen"],
    "michael wacha":        ["mike wacha"],
    "robert ray":           ["robbie ray", "rob ray"],
    "robert suarez":        ["robbie suarez", "rob suarez"],
    "james mccann":         ["jamie mccann", "jim mccann"],
    "anthony santander":    ["tony santander"],
    "ronald acuna jr.":     ["ronald acuna", "ronnie acuna", "ronald acuna jr"],
    "yordan alvarez":       ["jordan alvarez"],
    "freddie freeman":      ["freddy freeman"],
    "zach wheeler":         ["zachary wheeler", "zack wheeler"],
    "zach eflin":           ["zachary eflin", "zack eflin"],
    "jake odorizzi":        ["jacob odorizzi"],
    "jake burger":          ["jacob burger"],
    "jake fraley":          ["jacob fraley"],
    "corey seager":         ["cory seager"],
    "corey kluber":         ["cory kluber"],
    "trey mancini":         ["trevor mancini"],
    "tj friedl":            ["t.j. friedl", "tyler friedl"],
    "cj abrams":            ["c.j. abrams", "carter abrams"],
    "aj pollock":           ["a.j. pollock", "andrew pollock"],
    "dj lemahieu":          ["d.j. lemahieu", "david lemahieu"],
    "ha-seong kim":         ["haseong kim", "ha seong kim"],
    "hyun jin ryu":         ["hyunjin ryu", "hyun-jin ryu"],
    "jung hoo lee":         ["junghoo lee", "jung-hoo lee"],
    "shohei ohtani":        ["shohei otani"],
    "yoshinobu yamamoto":   ["yoshinobu yamomoto"],
    "adolis garcia":        ["adolis garcia"],
    "yordan alvarez":       ["yordan alvarez"],
}

# Build reverse lookup: alias → canonical mlb name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in KNOWN_ALIASES.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[_normalize(alias) if False else alias.lower().strip()] = canonical


def _normalize(name: str) -> str:
    """Normalize a name for comparison: lowercase, strip accents, remove punctuation."""
    # Remove accents (é → e, etc.)
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    # Lowercase and strip
    name = name.lower().strip()
    # Remove periods and extra spaces
    name = re.sub(r"[.\-']", "", name)
    name = re.sub(r"\s+", " ", name)
    # Remove suffixes
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name).strip()
    return name


def _last_name(name: str) -> str:
    """Extract last name from a full name."""
    parts = _normalize(name).split()
    return parts[-1] if parts else ""


def _first_initial(name: str) -> str:
    """Extract first initial from a full name."""
    parts = _normalize(name).split()
    return parts[0][0] if parts else ""


def _first_name(name: str) -> str:
    """Extract first name from a full name."""
    parts = _normalize(name).split()
    return parts[0] if parts else ""


def _similarity(a: str, b: str) -> float:
    """
    Simple character-level similarity score (0.0–1.0).
    Uses longest common subsequence ratio.
    Good enough for name matching without installing difflib extras.
    """
    a = _normalize(a)
    b = _normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # Build LCS matrix
    m, n   = len(a), len(b)
    dp     = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    lcs_len = dp[m][n]
    return (2 * lcs_len) / (m + n)


def find_best_match(
    mlb_name: str,
    odds_names: list[str],
    threshold: float = 0.72,
) -> Optional[str]:
    """
    Find the best matching odds player name for a given MLB API player name.

    Args:
        mlb_name:   Player name from MLB Stats API (e.g. "Michael A. Taylor")
        odds_names: List of player names from sportsbook odds data
        threshold:  Minimum similarity score to accept a fuzzy match (0–1)

    Returns:
        Best matching odds name, or None if no match found
    """
    if not mlb_name or not odds_names:
        return None

    mlb_lower = mlb_name.lower().strip()
    mlb_norm  = _normalize(mlb_name)

    # ── Step 1: Exact match ───────────────────────────────────────────────────
    for odds_name in odds_names:
        if odds_name.lower().strip() == mlb_lower:
            return odds_name

    # ── Step 2: Normalized exact match ───────────────────────────────────────
    for odds_name in odds_names:
        if _normalize(odds_name) == mlb_norm:
            return odds_name

    # ── Step 3: Known alias table ─────────────────────────────────────────────
    canonical = _ALIAS_TO_CANONICAL.get(mlb_lower)
    if canonical:
        for odds_name in odds_names:
            if odds_name.lower().strip() == canonical:
                return odds_name
            if _normalize(odds_name) == _normalize(canonical):
                return odds_name

    # Also check if the odds name is an alias for the mlb name
    for odds_name in odds_names:
        canonical_for_odds = _ALIAS_TO_CANONICAL.get(odds_name.lower().strip())
        if canonical_for_odds and _normalize(canonical_for_odds) == mlb_norm:
            return odds_name

    # ── Step 4: Last name + first initial match ───────────────────────────────
    mlb_last    = _last_name(mlb_name)
    mlb_initial = _first_initial(mlb_name)

    for odds_name in odds_names:
        odds_last    = _last_name(odds_name)
        odds_initial = _first_initial(odds_name)
        if mlb_last == odds_last and mlb_initial == odds_initial:
            return odds_name

    # ── Step 5: Last name only (use with caution — only if one match) ─────────
    last_matches = [n for n in odds_names if _last_name(n) == mlb_last]
    if len(last_matches) == 1:
        return last_matches[0]

    # ── Step 6: Fuzzy similarity match ───────────────────────────────────────
    best_score = 0.0
    best_match = None

    for odds_name in odds_names:
        score = _similarity(mlb_name, odds_name)
        if score > best_score:
            best_score = score
            best_match = odds_name

    if best_score >= threshold:
        return best_match

    # No match found
    return None


def build_matched_odds_lookup(
    odds_props: list[dict],
    mlb_players: list[str],
) -> dict[str, dict]:
    """
    Builds a lookup dict mapping mlb_player_name_lower → odds data.
    Applies fuzzy matching to handle name format differences.

    Args:
        odds_props:  List of prop dicts from get_player_props()
        mlb_players: List of player names from MLB lineup

    Returns:
        {mlb_name_lower: {"over_odds": int, "implied_prob": float, "line": float}}
    """
    # Group odds props by player name
    odds_by_name: dict[str, dict] = {}
    for prop in odds_props:
        name = prop.get("player_name", "")
        if name:
            odds_by_name[name] = prop

    odds_names = list(odds_by_name.keys())
    result: dict[str, dict] = {}

    for mlb_name in mlb_players:
        match = find_best_match(mlb_name, odds_names)
        if match:
            result[mlb_name.lower().strip()] = odds_by_name[match]
        else:
            # Log unmatched players so we can add them to alias table
            print(f"   ⚠️  No odds match found for: '{mlb_name}'")

    return result


def log_unmatched(mlb_name: str, odds_names: list[str]) -> None:
    """Helper to log match attempts for debugging — useful in prod logs."""
    best = find_best_match(mlb_name, odds_names, threshold=0.0)
    score = _similarity(mlb_name, best) if best else 0
    print(f"   MATCH ATTEMPT: '{mlb_name}' → '{best}' (score: {score:.2f})")
