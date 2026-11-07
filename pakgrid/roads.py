"""User roads: roads and streets that an app's authorised users draw and name.

OpenStreetMap misses many streets people use every day, above all in new housing schemes and
villages. Users add them by drawing a line along the road and naming it; the library keeps them in
the user places file (pakgrid.userplaces), beside user localities, with the same author and history
rules:

    user = grid.attach_user_places("user_places.sqlite")
    street = user.add_road("Street 7", author="ali", kind="street", local_name="گلی ۷",
                           line=[(31.5201, 74.3571), (31.5203, 74.3590), (31.5210, 74.3602)],
                           details={"oneway": False, "surface": "paved"})
    street.length_m                         # 310.4
    user.road_sub_cells(street)             # every sub-cell the line passes through
    user.describe_road(street)              # length, cells, places, junctions with other roads
    grid.locate(31.5204, 74.3587).place.street_text    # 'Street 7' (or 'near Street 7, 40 m')

How a road is made:

- Its **line** is two or more (lat, lon) points (or a GeoJSON LineString), rounded to 1e-7
  degree; repeated points are dropped. At most MAX_POINTS points and MAX_ROAD_KM long.
- Its **sub-cells** are every sub-cell whose box the line touches (edges and corners included),
  found exactly, segment by segment. At least one must be in Pakistan.
- Its **tehsil** is the one most of those sub-cells have; its key is that tehsil's key, "roads",
  and its name: 'punjab/lahore/lahore-cantt/roads/street-7'. The key never changes.
- Its **length** is the sum of its segments, as direction() measures them.
- **Details** are optional: oneway (bool), surface, vehicles (allowed: car, motorbike,
  bicycle, foot), width_m, speed_kmh, and any other fields you like.
- **Junctions:** where it crosses another road, or one of the two ends within JUNCTION_SNAP_M of
  the other (a T-junction drawn by hand rarely touches exactly).
- **Streets on lookups:** with the file attached, Grid.locate and Grid.place give the nearest road
  within STREET_MAX_M of the point ('on' it within STREET_ON_M).

Existing roads (from OpenStreetMap) are not part of this; renaming them is not possible yet.
"""

import json
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass

from . import geodesy, ids, navigate
from .errors import InvalidIdError
from .layout import SCALE, SUB, to_e7
from .zones import _set_run, make_zone

MAX_POINTS = 5_000
MAX_ROAD_KM = 300.0
JUNCTION_SNAP_M = 12.0       # a road's end this close to another road meets it
STREET_ON_M = 25.0           # a point this close to a road is 'on' it
STREET_MAX_M = 100.0         # the furthest a lookup looks for a street
ROAD_KINDS = ("street", "gali", "road", "main road", "highway", "track", "path")
VEHICLES = ("car", "motorbike", "bicycle", "foot")

ROADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS roads (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, local_name TEXT,
    kind TEXT NOT NULL, tehsil_key TEXT NOT NULL, line TEXT NOT NULL, length_m REAL NOT NULL,
    sub_cells INTEGER NOT NULL, details TEXT NOT NULL,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    removed_by TEXT, removed_at TEXT, places_version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS road_cells (road INTEGER NOT NULL, sub_cell INTEGER NOT NULL,
    PRIMARY KEY (road, sub_cell)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS road_cells_sub_cell ON road_cells (sub_cell);
CREATE TABLE IF NOT EXISTS road_history (
    id INTEGER PRIMARY KEY, road INTEGER NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL,
    action TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS road_history_road ON road_history (road);
"""
_ROAD_COLUMNS = ("id, key, name, local_name, kind, tehsil_key, line, length_m, sub_cells, details, created_by, "
                 "created_at, updated_by, updated_at, removed_by, removed_at")


@dataclass(frozen=True)
class UserRoad:
    """A road or street added by a user (see the module docstring)."""
    id: int
    key: str                    # e.g. 'punjab/lahore/lahore-cantt/roads/street-7'; never changes
    name: str
    local_name: str | None
    kind: str
    tehsil_key: str
    line: tuple                 # ((lat, lon), ...)
    length_m: float
    sub_cell_count: int
    details: dict               # oneway, surface, vehicles, width_m, speed_kmh, ... (all optional)
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    removed_by: str | None = None
    removed_at: str | None = None

    @property
    def removed(self):
        return self.removed_at is not None

    def to_dict(self):
        return {"id": self.id, "key": self.key, "name": self.name, "local_name": self.local_name, "kind": self.kind,
                "tehsil_key": self.tehsil_key, "line": [list(p) for p in self.line], "length_m": self.length_m,
                "sub_cells": self.sub_cell_count, "details": self.details,
                "created_by": self.created_by, "created_at": self.created_at,
                "updated_by": self.updated_by, "updated_at": self.updated_at,
                "removed_by": self.removed_by, "removed_at": self.removed_at}

    def to_geojson(self):
        """The road as a GeoJSON LineString Feature ([lon, lat]), with its key, name, kind and details."""
        return {"type": "Feature", "id": self.key,
                "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in self.line]},
                "properties": {"key": self.key, "name": self.name, "local_name": self.local_name, "kind": self.kind,
                               "length_m": self.length_m, **self.details}}


@dataclass(frozen=True)
class RoadMatch:
    """The nearest point of a road to a pin (UserPlaces.nearest_road)."""
    road: UserRoad
    distance_m: float           # from the pin to the road
    lat: float                  # the nearest point on the road
    lon: float

    @property
    def on(self):
        return self.distance_m <= STREET_ON_M

    @property
    def text(self):
        """'Street 7', or 'near Street 7, 40 m'"""
        if self.on:
            return self.road.name
        return f"near {self.road.name}, {navigate.format_distance(self.distance_m)}"

    def to_dict(self):
        return {"key": self.road.key, "name": self.road.name, "local_name": self.road.local_name,
                "kind": self.road.kind, "distance_m": round(self.distance_m, 1), "on": self.on,
                "point": [self.lat, self.lon], "text": self.text}


# -- Geometry ------------------------------------------------------------------------------

def line_e7(line, error):
    """[(lat_e7, lon_e7), ...] from [(lat, lon), ...] or a GeoJSON LineString / Feature, without
    repeated points. `error` is the exception class to raise."""
    if isinstance(line, dict):
        if line.get("type") == "Feature":
            line = line.get("geometry") or {}
        if line.get("type") != "LineString":
            raise error("a GeoJSON road must be a LineString")
        line = [(p[1], p[0]) for p in line["coordinates"]]
    pts = []
    for p in line:
        if not (isinstance(p, (list, tuple)) and len(p) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)):
            raise error(f"a road point must be (lat, lon), got {p!r}")
        if not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180):
            raise error(f"not a (lat, lon) point: {p!r}")
        q = (to_e7(p[0]), to_e7(p[1]))
        if not pts or pts[-1] != q:
            pts.append(q)
    if len(pts) < 2:
        raise error("a road needs at least 2 different points")
    if len(pts) > MAX_POINTS:
        raise error(f"a road can have at most {MAX_POINTS:,} points")
    return pts


def line_length_m(pts):
    return sum(navigate.direction(a[0] / SCALE, a[1] / SCALE, b[0] / SCALE, b[1] / SCALE).distance_m
               for a, b in zip(pts, pts[1:]))


def line_masks(layout, pts):
    """{cell ID: mask} of every sub-cell whose (closed) box the line [(lat_e7, lon_e7), ...] touches.

    Each segment is clipped to each row of sub-cells it crosses, and the columns its stretch of
    longitude covers in that row are taken: exact, and in proportion to the sub-cells touched.
    """
    masks = defaultdict(int)
    for (y1, x1), (y2, x2) in zip(pts, pts[1:]):
        ymin, ymax, xmin, xmax = min(y1, y2), max(y1, y2), min(x1, x2), max(x1, x2)
        for ql in layout.quadrants:
            lats = ql.lat_e7
            if ql.west_e7 > xmax or ql.east_e7 < xmin or lats[0] > ymax or lats[-1] < ymin:
                continue
            n5 = len(lats) - 1
            i0 = max(bisect_left(lats, ymin) - 1, 0)
            i1 = min(bisect_right(lats, ymax) - 1, n5 - 1)
            for i in range(i0, i1 + 1):
                lo, hi = max(lats[i], ymin), min(lats[i + 1], ymax)
                if lo > hi:
                    continue
                if y1 == y2:
                    a, b = xmin, xmax
                else:
                    xa = x1 + (lo - y1) * (x2 - x1) / (y2 - y1)
                    xb = x1 + (hi - y1) * (x2 - x1) / (y2 - y1)
                    a, b = min(xa, xb), max(xa, xb)
                a, b = max(a, ql.west_e7), min(b, ql.east_e7)
                if a > b:
                    continue
                row, sr = divmod(i, SUB)
                j0, j1 = _cols(ql, row, a, b)
                if j0 <= j1:
                    _set_run(masks, ql.q, row, sr, j0, j1 + 1)
    return dict(masks)


def _cols(ql, row, a, b):
    """First and last sub-cell column of the row whose closed span [west, east] meets [a, b]."""
    m5 = SUB * ql.ncols[row]
    w, e = ql.west_e7, ql.east_e7
    edge = lambda j: ql.fine_lon_e7(row, j)            # noqa: E731
    j0 = min(max(int((a - w) * m5 // (e - w)), 0), m5 - 1)
    while j0 > 0 and edge(j0) >= a:                    # the column's east edge must reach a
        j0 -= 1
    while j0 < m5 - 1 and edge(j0 + 1) < a:
        j0 += 1
    j1 = min(max(int((b - w) * m5 // (e - w)), 0), m5 - 1)
    while j1 < m5 - 1 and edge(j1 + 1) <= b:           # the column's west edge must not pass b
        j1 += 1
    while j1 > 0 and edge(j1) > b:
        j1 -= 1
    return j0, j1


def _subs_near(layout, lat, lon, r):
    """IDs of the sub-cells with any part within r metres of the pin (no centroids worked out)."""
    m_lat, m_lon = geodesy.metres_per_degree(lat)
    pad = 1.02 * r + 300
    out = []
    for q, i, j in layout.items_in_area(to_e7(lat - pad / m_lat), to_e7(lon - pad / m_lon),
                                        to_e7(lat + pad / m_lat), to_e7(lon + pad / m_lon), "sub"):
        s, w, n, e = (v / SCALE for v in layout.quadrants[q].item_bounds_e7(i, j, "sub"))
        if math.hypot(max(w - lon, 0.0, lon - e) * m_lon, max(s - lat, 0.0, lat - n) * m_lat) <= r:
            row, col = i // SUB, j // SUB
            out.append(ids.sub_cell_id(ids.cell_id(q, row, col), (i % SUB) * SUB + j % SUB))
    return out


def nearest_on_line(line, lat, lon):
    """(distance_m, nearest point (lat, lon), distance along the line in metres) from a pin to a line."""
    m_lat, m_lon = geodesy.metres_per_degree(lat)
    px, py = lon * m_lon, lat * m_lat
    best, along = None, 0.0
    for (a_lat, a_lon), (b_lat, b_lon) in zip(line, line[1:]):
        ax, ay, bx, by = a_lon * m_lon, a_lat * m_lat, b_lon * m_lon, b_lat * m_lat
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (seg * seg)))
        qx, qy = ax + t * dx, ay + t * dy
        d = math.hypot(px - qx, py - qy)
        if best is None or d < best[0]:
            best = (d, (qy / m_lat, qx / m_lon), along + t * seg)
        along += seg
    return best


def crossings(line_a, line_b):
    """[(along_a_m, (lat, lon))] where two lines cross (proper or touching), in planar metres."""
    ref = line_a[0][0]
    m_lat, m_lon = geodesy.metres_per_degree(ref)
    pa = [(lon * m_lon, lat * m_lat) for lat, lon in line_a]
    pb = [(lon * m_lon, lat * m_lat) for lat, lon in line_b]
    out, along = [], 0.0
    for (ax, ay), (bx, by) in zip(pa, pa[1:]):
        rx, ry = bx - ax, by - ay
        seg = math.hypot(rx, ry)
        for (cx, cy), (dx, dy) in zip(pb, pb[1:]):
            sx, sy = dx - cx, dy - cy
            den = rx * sy - ry * sx
            if den == 0:
                continue                               # parallel: no single crossing point
            t = ((cx - ax) * sy - (cy - ay) * sx) / den
            u = ((cx - ax) * ry - (cy - ay) * rx) / den
            if 0 <= t <= 1 and 0 <= u <= 1:
                x, y = ax + t * rx, ay + t * ry
                out.append((along + t * seg, (round(y / m_lat, 7), round(x / m_lon, 7))))
        along += seg
    return out


# -- Storage (mixed into UserPlaces) ------------------------------------------------------

class RoadsMixin:
    """The road methods of UserPlaces (see the module docstring)."""

    def add_road(self, name, *, author, line, kind="street", local_name=None, details=None):
        """Add a road drawn as `line`. Returns the new UserRoad."""
        from .userplaces import _check_text, _now, _slug
        author = _check_text(author, "author")
        name = _check_text(name, "name")
        kind = self._road_kind(kind)
        local_name = _check_text(local_name, "local_name", required=False)
        details = self._road_details(details)
        pts, subs, tehsil, length = self._road_shape(line)
        now = _now()
        db = self._db
        with self._writing(db):
            key = self._free_road_key(f"{tehsil.key}/roads/{_slug(name) or 'road'}")
            n = db.execute(f"INSERT INTO roads ({_ROAD_COLUMNS}, places_version) VALUES "
                           "(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,?)",
                           (key, name, local_name, kind, tehsil.key, json.dumps([[y / SCALE, x / SCALE] for y, x in pts]),
                            round(length, 1), len(subs), json.dumps(details, ensure_ascii=False),
                            author, now, author, now, self._places_version())).lastrowid
            db.executemany("INSERT INTO road_cells VALUES (?, ?)", [(n, s) for s in subs])
            road = self._get_road(n)
            self._log_road(n, author, "add", road)
        self._has_roads = True
        return road

    def update_road(self, road, *, author, name=None, local_name=None, kind=None, line=None, details=None):
        """Change a road's name, local name, kind, line or details (replaced). Its key stays the same."""
        from .userplaces import UserPlacesError, _check_text, _now
        author = _check_text(author, "author")
        old = self.get_road(road)
        if old.removed:
            raise UserPlacesError(f"{old.key} was removed; add it again instead")
        fields = {}
        if name is not None:
            fields["name"] = _check_text(name, "name")
        if local_name is not None:
            fields["local_name"] = _check_text(local_name, "local_name") if local_name else None
        if kind is not None:
            fields["kind"] = self._road_kind(kind)
        if details is not None:
            fields["details"] = json.dumps(self._road_details(details), ensure_ascii=False)
        db = self._db
        with self._writing(db):
            if line is not None:
                pts, subs, tehsil, length = self._road_shape(line)
                fields.update(line=json.dumps([[y / SCALE, x / SCALE] for y, x in pts]), length_m=round(length, 1),
                              sub_cells=len(subs), tehsil_key=tehsil.key)
                db.execute("DELETE FROM road_cells WHERE road = ?", (old.id,))
                db.executemany("INSERT INTO road_cells VALUES (?, ?)", [(old.id, s) for s in subs])
            fields.update(updated_by=author, updated_at=_now(), places_version=self._places_version())
            db.execute(f"UPDATE roads SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), old.id))
            road = self._get_road(old.id)
            self._log_road(old.id, author, "update", road)
        return road

    def remove_road(self, road, *, author):
        """Remove a road: lookups stop showing it; its record and history are kept."""
        from .userplaces import _check_text, _now
        author = _check_text(author, "author")
        r = self.get_road(road)
        db = self._db
        with self._writing(db):
            db.execute("UPDATE roads SET removed_by = ?, removed_at = ? WHERE id = ?", (author, _now(), r.id))
            r = self._get_road(r.id)
            self._log_road(r.id, author, "remove", r)
        return r

    def get_road(self, road):
        """A UserRoad by key, ID or UserRoad (removed ones too)."""
        if isinstance(road, UserRoad):
            road = road.id
        if isinstance(road, int) and not isinstance(road, bool):
            row = self._one(f"SELECT {_ROAD_COLUMNS} FROM roads WHERE id = ?", (road,))
        elif isinstance(road, str):
            row = self._one(f"SELECT {_ROAD_COLUMNS} FROM roads WHERE key = ?", (road.strip().lower(),))
        else:
            row = None
        if row is None:
            raise InvalidIdError(f"no such user road: {road!r}")
        return self._road_from_row(row)

    def roads(self, include_removed=False):
        """All user roads, in the order they were added."""
        rows = self._db.execute(f"SELECT {_ROAD_COLUMNS} FROM roads"
                                + ("" if include_removed else " WHERE removed_at IS NULL") + " ORDER BY id").fetchall()
        return [self._road_from_row(r) for r in rows]

    def find_roads(self, text):
        """Roads whose name or local name contains `text`, or whose key is `text`."""
        t = text.strip().lower()
        return [r for r in self.roads() if t in r.name.lower() or t == r.key or (r.local_name and t in r.local_name)]

    def road_sub_cells(self, road):
        """IDs of the sub-cells a road passes through, in ID order."""
        r = self.get_road(road)
        return [s for (s,) in self._db.execute("SELECT sub_cell FROM road_cells WHERE road = ? ORDER BY sub_cell", (r.id,))]

    def road_history(self, road=None):
        """Every change to roads, oldest first: dicts of at, author, action, key and the road after it."""
        sql, args = ("SELECT h.at, h.author, h.action, r.key, h.data FROM road_history h JOIN roads r ON r.id = h.road", ())
        if road is not None:
            sql, args = sql + " WHERE h.road = ?", (self.get_road(road).id,)
        return [{"at": at, "author": who, "action": action, "key": key, "road": json.loads(data)}
                for at, who, action, key, data in self._db.execute(sql + " ORDER BY h.id", args).fetchall()]

    def roads_in(self, sub_cell_ids):
        """The roads passing through any of these sub-cells, in the order they were added."""
        found = set()
        sub_cell_ids = list(sub_cell_ids)
        for k in range(0, len(sub_cell_ids), 900):
            chunk = sub_cell_ids[k:k + 900]
            found.update(n for (n,) in self._db.execute(
                "SELECT DISTINCT c.road FROM road_cells c JOIN roads r ON r.id = c.road WHERE r.removed_at IS NULL "
                f"AND c.sub_cell IN ({','.join('?' * len(chunk))})", chunk).fetchall())
        return [self._get_road(n) for n in sorted(found)]

    def nearest_road(self, lat, lon, max_m=STREET_MAX_M):
        """The road nearest the pin within max_m (at most 1 km), as a RoadMatch, or None."""
        if not self.has_roads:
            return None
        max_m = min(float(max_m), 1000.0)
        subs = _subs_near(self.grid.layout, lat, lon, max_m)
        best = None
        for road in self.roads_in(subs):
            d, (plat, plon), _ = nearest_on_line(road.line, lat, lon)
            if d <= max_m and (best is None or (d, road.id) < (best.distance_m, best.road.id)):
                best = RoadMatch(road, d, round(plat, 7), round(plon, 7))
        return best

    @property
    def has_roads(self):
        if getattr(self, "_has_roads", None) is None:
            self._has_roads = self._one("SELECT 1 FROM roads WHERE removed_at IS NULL LIMIT 1", ()) is not None
        return self._has_roads

    def junctions(self, road):
        """Where a road meets other user roads, in order along it: dicts of the other road's key and
        name, the point, how far along this road, and 'crosses' or 'meets' (an end within
        JUNCTION_SNAP_M)."""
        r = self.get_road(road)
        out = {}
        for other in self.roads_in(self._near_subs(r)):
            if other.id == r.id:
                continue
            found = [(a, p, "crosses") for a, p in crossings(r.line, other.line)]
            for end in (r.line[0], r.line[-1]):            # this road's ends near the other road
                d, p, _ = nearest_on_line(other.line, *end)
                if d <= JUNCTION_SNAP_M:
                    found.append((nearest_on_line(r.line, *end)[2], (round(end[0], 7), round(end[1], 7)), "meets"))
            for end in (other.line[0], other.line[-1]):    # the other road's ends near this road
                d, p, a = nearest_on_line(r.line, *end)
                if d <= JUNCTION_SNAP_M:
                    found.append((a, (round(p[0], 7), round(p[1], 7)), "meets"))
            for along, point, how in found:
                key = (other.id, round(along))
                if key not in out or (how == "crosses" and out[key]["how"] == "meets"):
                    out[key] = {"key": other.key, "name": other.name, "point": list(point),
                                "along_m": round(along, 1), "how": how}
        merged = []
        for j in sorted(out.values(), key=lambda j: (j["along_m"], j["key"])):
            if merged and merged[-1]["key"] == j["key"] and abs(merged[-1]["along_m"] - j["along_m"]) <= JUNCTION_SNAP_M:
                continue                                    # the same junction found twice
            merged.append(j)
        return merged

    def describe_road(self, road, references=()):
        """A road's full description: name, kind, details, length, ends, sub-cells, places (as
        Grid.describe_zone), junctions with other user roads, and a label."""
        r = self.get_road(road)
        subs = self.road_sub_cells(r)
        masks = {}
        for s in subs:
            c, k = ids.split_sub_cell_id(s)
            masks[c] = masks.get(c, 0) | 1 << k
        area = self.grid.describe_zone(make_zone(masks, self.grid.layout), references)
        names = [r.name] + ([area["label"]] if area["label"] else [])
        return {"key": r.key, "name": r.name, "local_name": r.local_name, "kind": r.kind, "details": r.details,
                "length_m": r.length_m, "start": list(r.line[0]), "end": list(r.line[-1]), "points": len(r.line),
                "sub_cells": len(subs), "cells": len(masks),
                "sub_cell_codes": [ids.sub_cell_code(s) for s in subs],
                "places": area["places"], "city": area["city"], "nearest_locality": area["nearest_locality"],
                "references": area["references"], "outside_pakistan_sub_cells": area["outside_pakistan_sub_cells"],
                "junctions": self.junctions(r), "label": ", ".join(names)}

    def roads_geojson(self):
        """Every road as a GeoJSON FeatureCollection of LineStrings."""
        return {"type": "FeatureCollection", "features": [r.to_geojson() for r in self.roads()]}

    # -- Internals --------------------------------------------------------------------

    def _ensure_road_tables(self, db):
        with self._writing(db):
            db.executescript(ROADS_SCHEMA)

    def _road_kind(self, kind):
        from .userplaces import _check_text
        return _check_text(kind, "kind").lower()

    def _road_details(self, details):
        from .userplaces import UserPlacesError
        if details is None:
            return {}
        if not isinstance(details, dict):
            raise UserPlacesError("details must be a dict, e.g. {'oneway': True, 'surface': 'paved'}")
        out = dict(details)
        checks = {"oneway": lambda v: isinstance(v, bool),
                  "surface": lambda v: isinstance(v, str) and v.strip(),
                  "vehicles": lambda v: isinstance(v, (list, tuple)) and all(x in VEHICLES for x in v),
                  "width_m": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 200,
                  "speed_kmh": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 200}
        for k, ok in checks.items():
            if k in out and out[k] is not None and not ok(out[k]):
                raise UserPlacesError(f"bad {k}: {out[k]!r}" + (f" (vehicles are {', '.join(VEHICLES)})" if k == "vehicles" else ""))
        if "vehicles" in out and out["vehicles"] is not None:
            out["vehicles"] = sorted(set(out["vehicles"]), key=VEHICLES.index)
        try:
            json.dumps(out)
        except (TypeError, ValueError):
            raise UserPlacesError("details must be JSON-ready (text, numbers, true/false, lists)") from None
        return {k: v for k, v in out.items() if v is not None}

    def _road_shape(self, line):
        """(points e7, sub-cell IDs, tehsil Place, length in metres) of a drawn line."""
        from .userplaces import UserPlacesError
        pts = line_e7(line, UserPlacesError)
        length = line_length_m(pts)
        if length > MAX_ROAD_KM * 1000:
            raise UserPlacesError(f"the road is {length / 1000:,.1f} km long; at most {MAX_ROAD_KM:g} km is allowed")
        masks = line_masks(self.grid.layout, pts)
        subs = sorted(ids.sub_cell_id(c, k) for c, m in masks.items() for k in range(SUB * SUB) if m >> k & 1)
        in_pk = self._in_pakistan(subs)
        if not in_pk:
            raise UserPlacesError("the road does not pass through any sub-cell in Pakistan")
        tehsil, _ = self._tehsil_and_centre(in_pk)
        return pts, subs, tehsil, length

    def _near_subs(self, road):
        """The road's sub-cells and their neighbours (for junctions drawn a little apart)."""
        subs = set(self.road_sub_cells(road))
        for s in list(subs):
            subs.update(self.grid.neighbours(s, level="sub"))
        return sorted(subs)

    def _free_road_key(self, base):
        key, n = base, 2
        while self._one("SELECT 1 FROM roads WHERE key = ?", (key,)) is not None:
            key, n = f"{base}-{n}", n + 1
        return key

    def _log_road(self, n, author, action, road):
        from .userplaces import _now
        self._db.execute("INSERT INTO road_history (road, at, author, action, data) VALUES (?,?,?,?,?)",
                         (n, _now(), author, action, json.dumps(road.to_dict(), ensure_ascii=False)))
        if action == "remove":
            self._has_roads = None

    def _get_road(self, n):
        return self._road_from_row(self._one(f"SELECT {_ROAD_COLUMNS} FROM roads WHERE id = ?", (n,)))

    @staticmethod
    def _road_from_row(r):
        return UserRoad(r[0], r[1], r[2], r[3], r[4], r[5], tuple(tuple(p) for p in json.loads(r[6])), r[7], r[8],
                        json.loads(r[9]), r[10], r[11], r[12], r[13], r[14], r[15])
