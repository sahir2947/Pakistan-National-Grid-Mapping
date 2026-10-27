"""Distance and direction between two points, for rider guidance.

Needs no database, so it can run on a phone every second.
"""

import math
from dataclasses import dataclass

from . import geodesy

_COMPASS_16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
               "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_COMPASS_8 = ("North", "North-East", "East", "South-East",
              "South", "South-West", "West", "North-West")


@dataclass(frozen=True)
class Direction:
    distance_m: float
    distance_ft: float
    bearing_deg: float
    compass: str          # 8-point name, e.g. "South-East"
    compass_short: str    # 16-point abbreviation, e.g. "SSE"
    turn_deg: float | None  # negative = left, positive = right; None without a heading
    turn: str | None        # e.g. "bear right"

    def to_dict(self):
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def direction(from_lat, from_lon, to_lat, to_lon, heading=None):
    """Straight-line distance and direction from one point to another.

    heading: the direction the rider is facing or moving, in degrees from
    north. When given, the result also says how far to turn.

    Uses the ellipsoid's scale at the mid-latitude. Compared with exact
    geodesics over Pakistan: distance within 0.01% up to 300 km; bearing
    within 0.01 degree at 1 km and 0.4 degree at 100 km.
    """
    mid = (from_lat + to_lat) / 2
    m_lat, m_lon = geodesy.metres_per_degree(mid)
    east = (to_lon - from_lon) * m_lon
    north = (to_lat - from_lat) * m_lat
    dist = math.hypot(east, north)
    brg = geodesy.bearing(east, north)

    turn_deg = turn = None
    if heading is not None:
        turn_deg = (brg - heading + 180.0) % 360.0 - 180.0
        turn = turn_words(turn_deg)
    return Direction(dist, dist * geodesy.FEET_PER_METRE, brg,
                     _COMPASS_8[round(brg / 45.0) % 8], _COMPASS_16[round(brg / 22.5) % 16],
                     turn_deg, turn)


def turn_words(turn_deg):
    """Plain-language turn instruction for a relative angle (negative = left)."""
    size = abs(turn_deg)
    if size < 10:
        return "ahead"
    if size >= 135:
        return "turn around"
    side = "right" if turn_deg > 0 else "left"
    if size < 30:
        return f"slightly {side}"
    if size < 60:
        return f"bear {side}"
    return f"turn {side}"
