"""
Static Park Factors
HR factors sourced from FanGraphs (updated annually).
Altitude, dimensions sourced from MLB.com.

Usage:
    from models.park_factors import get_park_data
    park = get_park_data("Fenway Park")
"""

PARK_DATA: dict[str, dict] = {
    "Fenway Park":              {"hr_factor": 1.09, "altitude_ft": 19,   "lf": 310, "cf": 420, "rf": 302, "lf_wall": 37, "rf_wall": 3,  "surface": "grass",  "dome": False},
    "Yankee Stadium":           {"hr_factor": 1.21, "altitude_ft": 55,   "lf": 318, "cf": 408, "rf": 314, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Coors Field":              {"hr_factor": 1.38, "altitude_ft": 5200, "lf": 347, "cf": 415, "rf": 350, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Oracle Park":              {"hr_factor": 0.80, "altitude_ft": 0,    "lf": 339, "cf": 399, "rf": 309, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Globe Life Field":         {"hr_factor": 1.13, "altitude_ft": 551,  "lf": 329, "cf": 407, "rf": 326, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Dodger Stadium":           {"hr_factor": 0.93, "altitude_ft": 512,  "lf": 330, "cf": 395, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Wrigley Field":            {"hr_factor": 1.10, "altitude_ft": 595,  "lf": 355, "cf": 400, "rf": 353, "lf_wall": 8,  "rf_wall": 11, "surface": "grass",  "dome": False},
    "Great American Ball Park": {"hr_factor": 1.19, "altitude_ft": 482,  "lf": 328, "cf": 404, "rf": 325, "lf_wall": 12, "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Petco Park":               {"hr_factor": 0.88, "altitude_ft": 42,   "lf": 336, "cf": 396, "rf": 322, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Truist Park":              {"hr_factor": 1.06, "altitude_ft": 1050, "lf": 335, "cf": 400, "rf": 325, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "American Family Field":    {"hr_factor": 1.02, "altitude_ft": 635,  "lf": 342, "cf": 400, "rf": 345, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Progressive Field":        {"hr_factor": 0.96, "altitude_ft": 649,  "lf": 325, "cf": 405, "rf": 325, "lf_wall": 14, "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Kauffman Stadium":         {"hr_factor": 0.98, "altitude_ft": 1013, "lf": 330, "cf": 410, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Rogers Centre":            {"hr_factor": 0.94, "altitude_ft": 287,  "lf": 328, "cf": 400, "rf": 328, "lf_wall": 8,  "rf_wall": 8,  "surface": "turf",   "dome": True},
    "Target Field":             {"hr_factor": 0.97, "altitude_ft": 834,  "lf": 339, "cf": 403, "rf": 328, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Busch Stadium":            {"hr_factor": 0.94, "altitude_ft": 535,  "lf": 336, "cf": 400, "rf": 335, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "T-Mobile Park":            {"hr_factor": 0.91, "altitude_ft": 0,    "lf": 331, "cf": 401, "rf": 326, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Oakland Coliseum":         {"hr_factor": 0.91, "altitude_ft": 25,   "lf": 330, "cf": 400, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "turf",   "dome": False},
    "loanDepot park":           {"hr_factor": 1.01, "altitude_ft": 6,    "lf": 344, "cf": 407, "rf": 335, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": True},
    "Citizens Bank Park":       {"hr_factor": 1.14, "altitude_ft": 20,   "lf": 329, "cf": 401, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Nationals Park":           {"hr_factor": 1.06, "altitude_ft": 0,    "lf": 336, "cf": 402, "rf": 335, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Camden Yards":             {"hr_factor": 1.08, "altitude_ft": 6,    "lf": 333, "cf": 400, "rf": 318, "lf_wall": 7,  "rf_wall": 7,  "surface": "grass",  "dome": False},
    "Minute Maid Park":         {"hr_factor": 1.07, "altitude_ft": 38,   "lf": 315, "cf": 435, "rf": 326, "lf_wall": 19, "rf_wall": 7,  "surface": "grass",  "dome": False},
    "Tropicana Field":          {"hr_factor": 0.95, "altitude_ft": 15,   "lf": 315, "cf": 404, "rf": 322, "lf_wall": 12, "rf_wall": 8,  "surface": "turf",   "dome": True},
    "PNC Park":                 {"hr_factor": 0.91, "altitude_ft": 730,  "lf": 325, "cf": 399, "rf": 320, "lf_wall": 6,  "rf_wall": 21, "surface": "grass",  "dome": False},
    "Angel Stadium":            {"hr_factor": 0.95, "altitude_ft": 152,  "lf": 347, "cf": 396, "rf": 350, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Chase Field":              {"hr_factor": 1.07, "altitude_ft": 1082, "lf": 330, "cf": 407, "rf": 334, "lf_wall": 7,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Citi Field":               {"hr_factor": 0.93, "altitude_ft": 20,   "lf": 335, "cf": 408, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Guaranteed Rate Field":    {"hr_factor": 1.06, "altitude_ft": 595,  "lf": 330, "cf": 400, "rf": 335, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
    "Comerica Park":            {"hr_factor": 0.87, "altitude_ft": 600,  "lf": 345, "cf": 420, "rf": 330, "lf_wall": 8,  "rf_wall": 8,  "surface": "grass",  "dome": False},
}


def get_park_data(venue_name: str) -> dict:
    """
    Returns park factor data for a given stadium name.
    Falls back to neutral values if stadium not found.
    """
    return PARK_DATA.get(venue_name, {
        "hr_factor": 1.00, "altitude_ft": 200, "lf": 330, "cf": 400, "rf": 330,
        "lf_wall": 8, "rf_wall": 8, "surface": "grass", "dome": False
    })


def get_hr_friendliness(venue_name: str) -> str:
    """Returns a human-readable HR tendency label for a park."""
    data = get_park_data(venue_name)
    factor = data["hr_factor"]

    if data["dome"]:
        return "Dome — controlled conditions, neutral"
    elif factor >= 1.20:
        return f"Extremely HR-friendly (factor {factor}) — Coors-tier"
    elif factor >= 1.10:
        return f"HR-friendly (factor {factor})"
    elif factor >= 1.03:
        return f"Slightly HR-friendly (factor {factor})"
    elif factor >= 0.97:
        return f"Neutral park (factor {factor})"
    elif factor >= 0.90:
        return f"Slightly HR-suppressing (factor {factor})"
    else:
        return f"HR-suppressing (factor {factor}) — pitcher's park"
