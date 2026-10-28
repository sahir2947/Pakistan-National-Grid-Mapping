"""Distance and direction between two points, and a navigation engine for rider guidance.

Needs no database, so it can run on a phone every second.

``direction`` answers one question: from here, how far and which way is there?
``NavigationSession`` is the engine behind an in-app "navigate to this pin" screen: feed it
GPS readings and compass readings as they arrive and read back what to show. It steadies
jittery GPS, picks the best heading available, and says when the rider has arrived. It draws
nothing, so each app builds its own screen on top. The map viewer carries an identical copy
in JavaScript (viewer/pakgrid.js), tested against this one.
"""

import math
import time as _time
from dataclasses import asdict, dataclass

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


def format_distance(metres, units="m"):
    """Distance as short text that doesn't flicker while the rider moves.

    Metres: 5 m steps under 100 m, 10 m steps under 1 km, then km to one decimal.
    Feet (units="ft"): 5 ft steps under 300 ft, 10 ft steps under a mile, then miles.
    """
    if units == "ft":
        ft = metres * geodesy.FEET_PER_METRE
        if ft < 300:
            return f"{round(ft / 5) * 5} ft"
        if ft < 5280:
            return f"{round(ft / 10) * 10} ft"
        return f"{round(ft / 528) / 10:.1f} mi"
    if units != "m":
        raise ValueError(f"units must be 'm' or 'ft', not {units!r}")
    if metres < 100:
        return f"{round(metres / 5) * 5} m"
    if metres < 1000:
        return f"{round(metres / 10) * 10} m"
    return f"{round(metres / 100) / 10:.1f} km"


@dataclass(frozen=True)
class NavState:
    """What a navigation screen shows at one moment. All fields are None while status is 'waiting'.

    status: 'waiting' (no position yet), 'navigating', 'almost' (within almost_m) or
        'arrived' (within arrived_m).
    heading_source: where heading_deg came from: 'gps' (direction of travel), 'compass',
        'manual' (given to set_position), 'movement' (direction between set_position calls),
        or None when no heading is known.
    arrow_deg / arrow_mode: how to rotate an arrow pointing at the target. 'relative' means
        0 = straight ahead (the arrow turns with the rider); 'north-up' means 0 = north,
        used while the heading is unknown.
    """
    status: str
    target_lat: float
    target_lon: float
    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None
    heading_deg: float | None = None
    heading_source: str | None = None
    distance_m: float | None = None
    distance_ft: float | None = None
    bearing_deg: float | None = None
    compass: str | None = None
    compass_short: str | None = None
    turn_deg: float | None = None
    turn: str | None = None
    arrow_deg: float | None = None
    arrow_mode: str | None = None

    def to_dict(self):
        return asdict(self)


class NavigationSession:
    """Guides a rider from their position to a target point.

        nav = NavigationSession(31.5204, 74.3587)
        state = nav.add_fix(lat, lon, accuracy=8, speed=6.1, heading=92)   # each GPS reading
        state = nav.add_compass(87)                                         # each compass reading
        format_distance(state.distance_m), state.compass_short, state.turn

    Every method returns the new NavState; ``state`` reads it at any time.

    Smoothing: the position is the accuracy-weighted mean (weights 1/accuracy²) of the last
    ``smoothing_count`` GPS readings within ``smoothing_window_s`` seconds. A reading further
    from the current position than its own accuracy (and at least 5 m) is real movement, so
    the average restarts from it rather than lagging behind the rider.

    Heading, in order of preference: the GPS direction of travel while moving at
    ``moving_mps`` or faster (a phone compass is unreliable on a motorbike); else the phone
    compass, if read within ``compass_fresh_s`` seconds; else none, and the arrow is north-up.

    Times are in seconds (any clock, as long as it is the same one throughout); by default
    the current time.
    """

    def __init__(self, target_lat, target_lon, *, arrived_m=15.0, almost_m=50.0, moving_mps=2.0,
                 compass_fresh_s=3.0, smoothing_window_s=10.0, smoothing_count=3):
        self.arrived_m = arrived_m
        self.almost_m = almost_m
        self.moving_mps = moving_mps
        self.compass_fresh_s = compass_fresh_s
        self.smoothing_window_s = smoothing_window_s
        self.smoothing_count = smoothing_count
        self._compass = None            # (heading, time)
        self.target = (float(target_lat), float(target_lon))
        self.reset()

    def set_target(self, lat, lon):
        """Change the target; the rider's position and heading are kept."""
        self.target = (float(lat), float(lon))
        return self.state

    def reset(self):
        """Forget the rider's position, heading and GPS history (the target stays)."""
        self.position = None
        self.accuracy_m = None
        self.heading = None             # (degrees, source)
        self._fixes = []                # (lat, lon, accuracy, time)
        self._anchor = None
        self._live = False
        return self.state

    def add_fix(self, lat, lon, accuracy=None, speed=None, heading=None, time=None):
        """A GPS reading: position, accuracy in metres, speed in m/s, direction of travel in degrees."""
        now = _now(time)
        acc = max(accuracy or 20.0, 1.0)
        if not self._live or direction(*self.position, lat, lon).distance_m > max(acc, 5.0):
            self._fixes = []
        self._fixes.append((lat, lon, acc, now))
        self._fixes = [f for f in self._fixes if now - f[3] < self.smoothing_window_s][-self.smoothing_count:]
        sw = slat = slon = 0.0
        for flat, flon, facc, _ in self._fixes:
            w = 1.0 / (facc * facc)
            sw += w
            slat += flat * w
            slon += flon * w
        self.position = (slat / sw, slon / sw)
        self.accuracy_m = accuracy
        self._live = True
        self._anchor = None

        if speed is not None and speed >= self.moving_mps and _valid(heading):
            self.heading = (heading % 360.0, "gps")
        elif self._compass is not None and now - self._compass[1] < self.compass_fresh_s:
            self.heading = (self._compass[0], "compass")
        else:
            self.heading = None
        return self.state

    def add_compass(self, heading, time=None):
        """A compass reading in degrees from north (already corrected for screen rotation).

        It is used straight away unless the GPS direction of travel is in charge, and it is
        ignored while the position comes from set_position.
        """
        if not _valid(heading):
            return self.state
        self._compass = (heading % 360.0, _now(time))
        if self._live and not (self.heading and self.heading[1] == "gps"):
            self.heading = (self._compass[0], "compass")
        return self.state

    def set_position(self, lat, lon, heading=None):
        """Place the rider directly, e.g. a manual pick or a simulation; no smoothing.

        With a heading, that heading is used ('manual'). Without one, once the rider has moved
        2 m or more since the last change, the direction of that movement is used ('movement').
        """
        if self._anchor is not None:
            moved = direction(*self._anchor, lat, lon)
        else:
            self.heading = None
            moved = None
            self._anchor = (lat, lon)
        if _valid(heading):
            self.heading = (heading % 360.0, "manual")
            self._anchor = (lat, lon)
        elif moved is not None and moved.distance_m >= 2.0:
            self.heading = (moved.bearing_deg, "movement")
            self._anchor = (lat, lon)
        self.position = (lat, lon)
        self.accuracy_m = None
        self._fixes = []
        self._live = False
        return self.state

    @property
    def state(self):
        tlat, tlon = self.target
        if self.position is None:
            return NavState("waiting", tlat, tlon)
        hdg, src = self.heading if self.heading else (None, None)
        d = direction(*self.position, tlat, tlon, heading=hdg)
        if d.distance_m < self.arrived_m:
            status = "arrived"
        elif d.distance_m < self.almost_m:
            status = "almost"
        else:
            status = "navigating"
        return NavState(status, tlat, tlon, self.position[0], self.position[1], self.accuracy_m, hdg, src,
                        d.distance_m, d.distance_ft, d.bearing_deg, d.compass, d.compass_short, d.turn_deg,
                        d.turn, d.turn_deg if hdg is not None else d.bearing_deg,
                        "relative" if hdg is not None else "north-up")


def _now(t):
    return _time.time() if t is None else float(t)


def _valid(heading):
    return heading is not None and math.isfinite(heading)
