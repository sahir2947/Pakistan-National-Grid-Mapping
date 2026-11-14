"""Walkthroughs: photos of the last stretch, from the road to the door, for riders.

A shop owner records a phone video once, walking from a point on the road where a rider can
park to their door, while the app logs the phone's GPS about once a second. The library turns
that into a **walkthrough**: the cleaned path, the moments of the video to take photos from
(every few metres and at each turn), and a short step for each photo. Riders then see the photo
that matches where they are standing, with the distance to the door and the next turn.

    wt = grid.walkthrough(track, pin=(31.52064, 74.35742), start=(31.5201, 74.3571), author="ali")
    wt.length_m, len(wt.frames)                 # 86.84, 19
    data = wt.to_dict()                         # store it anywhere (JSON); the photos are separate files
    wt.ffmpeg_commands("walk.mp4", "frames/")   # one command per photo, to take them from the video

    guide = Walkthrough.from_dict(data).guide()     # on the rider's phone
    s = guide.update(31.52024, 74.35711, accuracy_m=8)
    s.frame, s.text, s.to_go_m                  # 3, 'In 25 m, turn right', 71.3

The track is a list of readings, each (t, lat, lon) or (t, lat, lon, accuracy_m), or a dict with
those keys; t is seconds from the start of the video. read_gpx() reads a GPX file from a GPS
logger instead.

How a walkthrough is made (build):

1. **Clean:** readings that are not finite or not on Earth, less accurate than MAX_ACCURACY_M,
   at a time already seen, or further from the last kept reading than MAX_SPEED_MS allows plus
   both readings' accuracy (a GPS jump) are dropped (counted in `track`).
2. **Smooth:** each reading becomes the average of the readings within SMOOTH_S seconds of it,
   weighted by 1 / accuracy² (accuracy DEFAULT_ACCURACY_M when not given), in metres.
3. **Anchor:** the first reading is moved to the start (a point on the road; the first reading
   when not given) and the last to the pin. Each must be within ANCHOR_MAX_M of it.
4. **Simplify:** points are rounded to 1e-7 degree and the line simplified (Douglas-Peucker,
   SIMPLIFY_M), so GPS wobble does not become zig-zags. Each point keeps its video time.
5. **Measure:** all distances use one flat frame: metres east and north of the start, at the
   start's scale (geodesy.local_offset). length_m is the path's length; at most MAX_LENGTH_M,
   and the walk at most MAX_DURATION_S long.
6. **Turns:** at a point of the path where the direction changes by TURN_DEG or more, measured
   from TURN_ARM_M before the point to TURN_ARM_M after it; of turns closer than TURN_GAP_M,
   only the sharpest counts. Each has the words of navigate.turn_words ('turn left', 'bear
   right', 'turn around').
7. **Frames:** one every FRAME_EVERY_M along the path, one at each turn and one at the end; a
   regular frame within MIN_FRAME_GAP_M of a turn or the end is left out. Each has its video
   time (the time is interpolated along the path), point, heading, distance done and to go,
   sub-cell, the turn there, the step text and its photo's file name.

The rider rule (WalkthroughGuide) is in conformance/WALKTHROUGH.md, with test cases in
conformance/walkthrough.json for ports (the viewer's JavaScript passes them).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from . import geodesy, ids, navigate, spec
from .errors import PakgridError

if TYPE_CHECKING:
    from .grid import Grid

XY = tuple[float, float]                        # metres east and north of the reference point
LatLon = tuple[float, float]
TimedPoint = tuple[float, float, float]         # (t, lat, lon)

WALKTHROUGH_VERSION = 1

# Making a walkthrough
MAX_ACCURACY_M = 25.0        # readings less accurate than this are dropped
DEFAULT_ACCURACY_M = 10.0    # the accuracy of a reading that gives none
MAX_SPEED_MS = 4.0           # further than this speed allows (plus both readings' accuracy) is a GPS jump
SMOOTH_S = 2.0               # readings within this many seconds are averaged
ANCHOR_MAX_M = 30.0          # the start and pin must be this close to the first and last reading
SIMPLIFY_M = 5.0
TURN_DEG = 30.0
TURN_ARM_M = 8.0             # a turn's angle is measured from this far before it to this far after it
TURN_GAP_M = 12.0            # of two turns closer than this, only the sharper one counts
FRAME_EVERY_M = 5.0
MIN_FRAME_GAP_M = 2.0
MIN_LENGTH_M = 5.0
MAX_LENGTH_M = 500.0
MAX_DURATION_S = 300.0

# The rider rule
WEAK_GPS_M = 30.0            # a reading less accurate than this does not move the rider
OFF_PATH_M = 30.0            # further than this from the path, the rider is off it
BACK_M = 12.0                # a reading this far behind the rider's progress moves it back
ARRIVED_M = 6.0              # this close to the door (along the path), the rider has arrived
TURN_NOW_M = 4.0             # this close to a turn, it says "now"
ASSUMED_ACCURACY_M = 20.0    # the accuracy of a reading that gives none (or a bad one)


class WalkthroughError(PakgridError, ValueError):
    """A walkthrough cannot be made from this track, or the data is not a walkthrough."""


# -- Geometry in the walkthrough's flat frame ------------------------------------------------

def _xy(ref: Sequence[float], lat: float, lon: float) -> XY:
    return geodesy.local_offset(ref[0], ref[1], lat, lon)


def _project(xy: Sequence[XY], along: Sequence[float], x: float, y: float) -> tuple[float, float, int]:
    """(distance, along, segment) of the nearest point of the path [(x, y)] to (x, y); the
    first segment wins a tie."""
    best: tuple[float, float, int] | None = None
    for k in range(len(xy) - 1):
        (ax, ay), (bx, by) = xy[k], xy[k + 1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg2))
        d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
        if best is None or d < best[0]:
            best = (d, along[k] + t * (along[k + 1] - along[k]), k)
    assert best is not None, "a path has at least two points"
    return best


def _simplify(xy: Sequence[XY], tol: float) -> list[int]:
    """Indices kept by Douglas-Peucker (segment distance), first and last always kept."""
    keep = {0, len(xy) - 1}
    stack = [(0, len(xy) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = xy[i], xy[j]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        far, at = -1.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
            d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > far:
                far, at = d, k
        if at >= 0 and far > tol:
            keep.add(at)
            stack += [(i, at), (at, j)]
    return sorted(keep)


def _bearing(a: XY, b: XY) -> float:
    return geodesy.bearing(b[0] - a[0], b[1] - a[1])


def _change(b1: float, b2: float) -> float:
    """Signed turn from bearing b1 to b2, in (-180, 180]; negative is left."""
    c = (b2 - b1 + 180.0) % 360.0 - 180.0
    return 180.0 if c == -180.0 else c


# -- Reading tracks ------------------------------------------------------------------------

def _reading(r: object) -> tuple[Any, Any, Any, Any]:
    if isinstance(r, dict):
        return r.get("t"), r.get("lat"), r.get("lon"), r.get("accuracy", r.get("accuracy_m"))
    if isinstance(r, (list, tuple)) and len(r) in (3, 4):
        t, lat, lon, *acc = r
        return t, lat, lon, acc[0] if acc else None
    raise WalkthroughError(f"a reading must be (t, lat, lon[, accuracy_m]) or a dict, got {r!r}")


def _num(v: object) -> TypeGuard[float]:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def read_gpx(text: str | bytes, video_start: datetime | str | None = None) -> list[dict[str, float]]:
    """Readings [{'t', 'lat', 'lon'}] from a GPX file's track points. t is seconds from
    video_start (an aware datetime or ISO text), or from the first point when not given."""
    root = ET.fromstring(text)
    points: list[tuple[datetime, float, float]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        when = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "time"), None)
        if when is None:
            continue
        points.append((_parse_time(when), float(cast(str, el.get("lat"))), float(cast(str, el.get("lon")))))
    if not points:
        raise WalkthroughError("the GPX file has no track points with times")
    t0 = _parse_time(video_start) if video_start is not None else points[0][0]
    return [{"t": (when - t0).total_seconds(), "lat": lat, "lon": lon} for when, lat, lon in points]


def _parse_time(v: datetime | str) -> datetime:
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(re.sub(r"Z$", "+00:00", str(v).strip()))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


# -- Building ------------------------------------------------------------------------------

def build(track: Iterable[Any], *, pin: Sequence[float], author: str, start: Sequence[float] | None = None,
          grid: Grid | None = None, properties: dict[str, Any] | None = None) -> Walkthrough:
    """Make a Walkthrough from a GPS track recorded with the video (see the module docstring).
    With a grid, frames and the path also get their sub-cells."""
    if not isinstance(author, str) or not author.strip():
        raise WalkthroughError("author must be a non-empty string")
    pin_p = _point(pin, "pin")
    start_p = _point(start, "start") if start is not None else None

    # 1. Clean
    raw = [_reading(r) for r in track]
    dropped = {"invalid": 0, "inaccurate": 0, "repeated_time": 0, "jump": 0}
    good: list[tuple[float, float, float, float]] = []
    for t, lat, lon, acc in sorted((r for r in raw if _num(r[0])), key=lambda r: r[0]):
        if not (_num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180) \
                or (acc is not None and not (_num(acc) and acc > 0)):
            dropped["invalid"] += 1
            continue
        if acc is not None and acc > MAX_ACCURACY_M:
            dropped["inaccurate"] += 1
            continue
        if good and t == good[-1][0]:
            dropped["repeated_time"] += 1
            continue
        if good:
            pt, plat, plon, pacc = good[-1]
            slack = pacc + (acc if acc is not None else DEFAULT_ACCURACY_M)
            if navigate.direction(plat, plon, lat, lon).distance_m > MAX_SPEED_MS * (t - pt) + slack:
                dropped["jump"] += 1
                continue
        good.append((t, lat, lon, acc if acc is not None else DEFAULT_ACCURACY_M))
    dropped["invalid"] += sum(1 for r in raw if not _num(r[0]))
    if len(good) < 2:
        raise WalkthroughError(f"the track needs at least 2 good readings; {len(good)} left after cleaning "
                               f"({_dropped_text(dropped)})")

    # 2. Smooth, in metres from the first reading
    ref0 = good[0][1:3]
    pts = [(t, *_xy(ref0, lat, lon)) for t, lat, lon, _ in good]
    smooth: list[TimedPoint] = []
    for t, _, _ in pts:
        sw = sx = sy = 0.0
        for (u, x, y), r in zip(pts, good):
            if abs(u - t) <= SMOOTH_S:
                w = 1.0 / (r[3] * r[3])
                sw, sx, sy = sw + w, sx + w * x, sy + w * y
        smooth.append((t, sx / sw, sy / sw))
    m_lat, m_lon = geodesy.metres_per_degree(ref0[0])
    as_lat_lon = [(t, ref0[0] + y / m_lat, ref0[1] + x / m_lon) for t, x, y in smooth]

    # 3. Anchor
    first, last = as_lat_lon[0], as_lat_lon[-1]
    if start_p is None:
        start_p = (first[1], first[2])
    for what, end, rd in (("start", start_p, first), ("pin", pin_p, last)):
        gap = navigate.direction(end[0], end[1], rd[1], rd[2]).distance_m
        if gap > ANCHOR_MAX_M:
            raise WalkthroughError(f"the {'first' if what == 'start' else 'last'} reading is {gap:.0f} m from the "
                                   f"{what}; it must be within {ANCHOR_MAX_M:g} m (record from the {what})")
    seq: list[TimedPoint] = [(first[0], *start_p)] + as_lat_lon[1:-1] + [(last[0], *pin_p)]

    # 4. Round, drop repeats, simplify
    seq = [(t, round(lat, 7), round(lon, 7)) for t, lat, lon in seq]
    ref = seq[0][1:]
    path = [seq[0]]
    for p in seq[1:-1]:
        if p[1:] != path[-1][1:] and p[1:] != seq[-1][1:]:
            path.append(p)
    if seq[-1][1:] == path[-1][1:]:
        raise WalkthroughError("the start and the pin are the same point")
    path.append(seq[-1])
    xy = [_xy(ref, lat, lon) for _, lat, lon in path]
    keep = _simplify(xy, SIMPLIFY_M)
    path = [path[k] for k in keep]

    data = _manifest(path, author, properties, grid, dropped, len(raw))
    return Walkthrough(data)


def _point(p: object, what: str) -> LatLon:
    if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(_num(v) for v in p)
            and -90 <= p[0] <= 90 and -180 <= p[1] <= 180):
        raise WalkthroughError(f"{what} must be (lat, lon), got {p!r}")
    assert isinstance(p, (list, tuple))
    return float(p[0]), float(p[1])


def _dropped_text(d: dict[str, int]) -> str:
    return ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in d.items() if n) or "none dropped"


def _manifest(path: list[TimedPoint], author: str, properties: dict[str, Any] | None, grid: Grid | None,
              dropped: dict[str, int], n_readings: int) -> dict[str, Any]:
    from .userplaces import _now
    ref = path[0][1:]
    xy = [_xy(ref, lat, lon) for _, lat, lon in path]
    along = [0.0]
    for a, b in zip(xy, xy[1:]):
        along.append(along[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    length = along[-1]
    duration = path[-1][0] - path[0][0]
    if length < MIN_LENGTH_M:
        raise WalkthroughError(f"the walk is {length:.1f} m long; at least {MIN_LENGTH_M:g} m is needed")
    if length > MAX_LENGTH_M:
        raise WalkthroughError(f"the walk is {length:.0f} m long; at most {MAX_LENGTH_M:g} m is allowed "
                               "(a walkthrough is for the last stretch, from the road to the door)")
    if duration > MAX_DURATION_S:
        raise WalkthroughError(f"the walk takes {duration:.0f} s; at most {MAX_DURATION_S:g} s is allowed")

    # Turns: measured over TURN_ARM_M either side, the sharper of two close ones kept
    def at(s: float) -> XY:
        s = min(max(s, 0.0), length)
        k = _segment_at(along, s)
        f = 0.0 if along[k + 1] == along[k] else (s - along[k]) / (along[k + 1] - along[k])
        return xy[k][0] + f * (xy[k + 1][0] - xy[k][0]), xy[k][1] + f * (xy[k + 1][1] - xy[k][1])
    found: list[tuple[int, float]] = []
    for k in range(1, len(xy) - 1):
        c = _change(_bearing(at(along[k] - TURN_ARM_M), xy[k]), _bearing(xy[k], at(along[k] + TURN_ARM_M)))
        if abs(c) >= TURN_DEG:
            found.append((k, c))
    kept: list[tuple[int, float]] = []
    for k, c in sorted(found, key=lambda kc: (-abs(kc[1]), kc[0])):
        if all(abs(along[k] - along[j]) >= TURN_GAP_M for j, _ in kept):
            kept.append((k, c))
    turns: list[dict[str, Any]] = [{"along_m": round(along[k], 2), "turn_deg": round(c, 1), "turn": navigate.turn_words(c),
              "point": [path[k][1], path[k][2]]} for k, c in sorted(kept)]

    # Frames
    marks = {round(t["along_m"], 2): t["turn"] for t in turns}
    stops = sorted(set(marks) | {round(length, 2)})
    regular = [round(k * FRAME_EVERY_M, 2) for k in range(int(length // FRAME_EVERY_M) + 1)]
    regular = [s for s in regular if all(abs(s - m) >= MIN_FRAME_GAP_M for m in stops)]
    m_lat, m_lon = geodesy.metres_per_degree(ref[0])
    frames: list[dict[str, Any]] = []
    vertex = {round(a, 2): k for k, a in enumerate(along[:-1])}
    for s in sorted(set(regular) | set(stops)):
        k = vertex[s] if s in marks else _segment_at(along, s)        # at a turn: the way out of it
        f = 0.0 if along[k + 1] == along[k] else (s - along[k]) / (along[k + 1] - along[k])
        f = min(max(f, 0.0), 1.0)
        x = xy[k][0] + f * (xy[k + 1][0] - xy[k][0])
        y = xy[k][1] + f * (xy[k + 1][1] - xy[k][1])
        t = path[k][0] + f * (path[k + 1][0] - path[k][0])
        lat, lon = round(ref[0] + y / m_lat, 7), round(ref[1] + x / m_lon, 7)
        frames.append({"index": len(frames), "t_s": round(t, 2), "lat": lat, "lon": lon,
                       "heading_deg": round(_bearing(xy[k], xy[k + 1]), 1), "along_m": s,
                       "to_go_m": round(length - s, 2), "turn": marks.get(s),
                       "image": f"frame-{len(frames):03d}.jpg"})

    def sub_code(lat: float, lon: float) -> str | None:
        if grid is None:
            return None
        from .layout import SUB, to_e7
        try:
            q, row, col, sr, sc = grid.layout.locate_e7(to_e7(lat), to_e7(lon))
        except PakgridError:
            return None
        return ids.sub_cell_code(ids.sub_cell_id(ids.cell_id(q, row, col), sr * SUB + sc))

    for f in frames:
        f["sub_cell"] = sub_code(f["lat"], f["lon"])
    sub_cells: list[str] = []
    if grid is not None:
        from .layout import SUB, to_e7
        from .roads import line_masks
        masks = line_masks(grid.layout, [(to_e7(lat), to_e7(lon)) for _, lat, lon in path])
        sub_cells = sorted(ids.sub_cell_code(ids.sub_cell_id(c, b))
                           for c, m in masks.items() for b in range(SUB * SUB) if m >> b & 1)
    data = {
        "walkthrough_version": WALKTHROUGH_VERSION, "grid_version": spec.GRID_VERSION,
        "start": [path[0][1], path[0][2]], "pin": [path[-1][1], path[-1][2]],
        "start_sub_cell": sub_code(*path[0][1:]), "pin_sub_cell": sub_code(*path[-1][1:]),
        "length_m": round(length, 2), "duration_s": round(duration, 2),
        "video_start_s": round(path[0][0], 2), "video_end_s": round(path[-1][0], 2),
        "path": [[lat, lon, round(t, 2), round(a, 2)] for (t, lat, lon), a in zip(path, along)],
        "sub_cells": sub_cells, "turns": turns, "frames": frames,
        "track": {"readings": n_readings, "kept": n_readings - sum(dropped.values()), "dropped": dropped},
        "author": author.strip(), "created_at": _now(), "properties": dict(properties or {}),
    }
    wt = Walkthrough(data)
    for f in frames:                                # each photo's step, as the rider rule says it
        f["text"] = wt._guidance(f["along_m"], None, "ok")["text"]
    return data


def _segment_at(along: Sequence[float], s: float) -> int:
    """The segment k with along[k] <= s <= along[k + 1] (the last one for s past the end)."""
    for k in range(len(along) - 1):
        if s <= along[k + 1]:
            return k
    return len(along) - 2


# -- A walkthrough -------------------------------------------------------------------------

class Walkthrough:
    """A walkthrough's data (see the module docstring); to_dict() is its stored form."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        p = data["path"]
        self.ref = (p[0][0], p[0][1])
        self.xy = [_xy(self.ref, lat, lon) for lat, lon, *_ in p]
        self.along = [a for *_, a in p]

    @classmethod
    def from_dict(cls, data: object) -> Walkthrough:
        if not isinstance(data, dict) or data.get("walkthrough_version") != WALKTHROUGH_VERSION:
            raise WalkthroughError(f"not walkthrough version {WALKTHROUGH_VERSION} data")
        try:
            wt = cls(data)
            if len(wt.xy) < 2 or not data["frames"]:
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise WalkthroughError("the walkthrough data is incomplete") from None
        return wt

    def to_dict(self) -> dict[str, Any]:
        return self.data

    @property
    def length_m(self) -> float:
        return self.data["length_m"]

    @property
    def frames(self) -> list[dict[str, Any]]:
        return self.data["frames"]

    @property
    def turns(self) -> list[dict[str, Any]]:
        return self.data["turns"]

    def guide(self) -> WalkthroughGuide:
        """A WalkthroughGuide for a rider following this walkthrough."""
        return WalkthroughGuide(self)

    def frame_times(self) -> list[float]:
        """The video time of each photo, in seconds."""
        return [f["t_s"] for f in self.frames]

    def ffmpeg_commands(self, video: object, out_dir: object, width: int = 1280, quality: int = 3) -> list[list[str]]:
        """One ffmpeg command (a list of arguments) per photo, taking it from the video at its time
        into out_dir/<image>, `width` pixels wide (JPEG quality 2-31, lower is better)."""
        import os
        return [["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{f['t_s']:.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", str(int(quality)),
                 os.path.join(str(out_dir), f["image"])] for f in self.frames]

    def to_geojson(self) -> dict[str, Any]:
        """A FeatureCollection: the path as a LineString, then each photo's point."""
        from .geojson import feature_collection
        d = self.data
        feats = [{"type": "Feature", "id": "path",
                  "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon, *_ in d["path"]]},
                  "properties": {"kind": "walkthrough", "length_m": d["length_m"], "duration_s": d["duration_s"],
                                 "pin_sub_cell": d["pin_sub_cell"], "author": d["author"]}}]
        for f in self.frames:
            feats.append({"type": "Feature", "id": f"frame-{f['index']}",
                          "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
                          "properties": {k: f[k] for k in ("index", "t_s", "heading_deg", "along_m", "to_go_m",
                                                            "turn", "text", "image")}})
        return feature_collection(feats)

    # The rider rule's guidance at a point of progress (also each frame's text)
    def _guidance(self, progress: float, off: float | None, gps: str) -> dict[str, Any]:
        frames, turns, length = self.frames, self.turns, self.length_m
        best = 0
        for i, f in enumerate(frames):
            if abs(f["along_m"] - progress) <= abs(frames[best]["along_m"] - progress):
                best = i                                # the nearest photo; a tie goes to the later one
        to_go = max(length - progress, 0.0)
        nxt = next((t for t in turns if t["along_m"] >= progress), None)
        in_m = None if nxt is None else nxt["along_m"] - progress
        arrived = to_go <= ARRIVED_M
        if gps == "off_path":
            text = f"Go back to the path: it is {navigate.format_distance(off or 0.0)} away"
        elif arrived:
            text = "You have arrived"
        elif nxt is not None and in_m is not None and in_m <= TURN_NOW_M:
            text = f"{nxt['turn'][0].upper()}{nxt['turn'][1:]} now"
        elif nxt is not None and in_m is not None:
            text = f"In {navigate.format_distance(in_m)}, {nxt['turn']}"
        else:
            text = f"The door is {navigate.format_distance(to_go)} ahead"
        return {"frame": best, "progress_m": progress, "to_go_m": to_go, "off_path_m": off,
                "next_turn": None if nxt is None else nxt["turn"], "next_turn_in_m": in_m,
                "arrived": arrived, "gps": gps, "text": text}


@dataclass(frozen=True)
class GuideState:
    """What a rider's screen shows (see WalkthroughGuide)."""
    frame: int                  # the photo to show (index into frames)
    progress_m: float           # how far along the path the rider is
    to_go_m: float              # along the path to the door
    off_path_m: float | None    # the last reading's distance from the path (None: none, or a manual step)
    next_turn: str | None       # 'turn left', ... (the first turn not yet passed)
    next_turn_in_m: float | None
    arrived: bool
    gps: str                    # 'ok', 'weak', 'off_path', 'ignored', 'manual', or 'start' before any reading
    text: str                   # 'In 15 m, turn left', 'Turn left now', 'The door is 35 m ahead', ...

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class WalkthroughGuide:
    """The rider rule: feed it GPS readings (or manual steps) and read back the photo to show.

    - A reading that is not finite or not on Earth is ignored (gps 'ignored'). An accuracy that
      is not a finite number above 0 counts as ASSUMED_ACCURACY_M.
    - The reading is projected onto the path (nearest point, in the walkthrough's flat frame:
      metres east and north of the path's first point, at its scale). Its distance is off_path_m.
    - Less accurate than WEAK_GPS_M: progress stays (gps 'weak'). Further than OFF_PATH_M from
      the path: progress stays (gps 'off_path'). Otherwise progress moves to the projection when
      that is ahead, or more than BACK_M behind; a smaller step back is GPS wobble and is ignored.
    - show(i), next() and previous() move to a photo by hand (gps 'manual').
    - The photo shown is the one nearest the progress along the path (a tie goes to the later
      one). The next turn is the first with along_m >= progress. Arrived within ARRIVED_M of the
      door. The text: off the path, 'Go back to the path: it is 40 m away'; arrived, 'You have
      arrived'; a turn within TURN_NOW_M, 'Turn left now'; a turn ahead, 'In 15 m, turn left';
      else 'The door is 35 m ahead' (distances as navigate.format_distance).
    """

    def __init__(self, walkthrough: Walkthrough | dict[str, Any]) -> None:
        self.wt = walkthrough if isinstance(walkthrough, Walkthrough) else Walkthrough.from_dict(walkthrough)
        self.progress = 0.0
        self.state = self._state(None, "start")

    def update(self, lat: float, lon: float, accuracy_m: float | None = None) -> GuideState:
        """A GPS reading. Returns the GuideState."""
        if not (_num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            return self._set(None, "ignored")
        acc = accuracy_m if _num(accuracy_m) and accuracy_m > 0 else ASSUMED_ACCURACY_M
        x, y = _xy(self.wt.ref, lat, lon)
        off, raw, _ = _project(self.wt.xy, self.wt.along, x, y)
        if acc > WEAK_GPS_M:
            return self._set(off, "weak")
        if off > OFF_PATH_M:
            return self._set(off, "off_path")
        if raw >= self.progress or self.progress - raw > BACK_M:
            self.progress = raw
        return self._set(off, "ok")

    def show(self, index: int) -> GuideState:
        """Move to a photo by hand (a swipe). Returns the GuideState."""
        frames = self.wt.frames
        index = min(max(int(index), 0), len(frames) - 1)
        self.progress = float(frames[index]["along_m"])
        return self._set(None, "manual")

    def next(self) -> GuideState:
        return self.show(self.state.frame + 1)

    def previous(self) -> GuideState:
        return self.show(self.state.frame - 1)

    def _set(self, off: float | None, gps: str) -> GuideState:
        self.state = self._state(off, gps)
        return self.state

    def _state(self, off: float | None, gps: str) -> GuideState:
        return GuideState(**self.wt._guidance(self.progress, off, gps))
