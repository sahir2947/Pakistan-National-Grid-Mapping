"""Place names: province, district, tehsil and locality tags on every cell and sub-cell.

Places are a *layer* on the grid. The grid (rectangle, quadrants, cells,
sub-cells) is frozen: its IDs never change. Place names do change (new
districts, renamed tehsils, new neighbourhoods), so the places layer has its
own version and can be rebuilt or replaced without touching a single grid ID.
Apps store grid IDs and look up the current names.

How the tags are made (places version 2; sources in pakgrid/data/PLACES_SOURCE.md):

1. Every sub-cell that touches Pakistan (border flag inside or border) is
   tagged with the **tehsil** whose boundary contains the sub-cell's centroid
   (UN OCHA administrative boundaries). Sub-cells along the edges that no tehsil
   polygon reaches take the tag of their nearest tagged neighbours; island
   sub-cells that no tehsil includes take the nearest tehsil.
2. Each tehsil's **district** and **province** are the ones the source names
   for it (by P-code), so the hierarchy is exactly the official one.
3. Each of those sub-cells is also tagged with a **locality** (OpenStreetMap):
   the named area (neighbourhood, suburb, residential area, village or town
   outline) containing its centroid, the smallest if several do; otherwise the
   named place point (city, town, village, hamlet, ...) that is relatively
   nearest, i.e. with the smallest distance / reach, among those within their
   reach (LOCALITY_REACH_M: 10 km for a city, 4 km for a town, 2 km for a village,
   1 km for a hamlet ...); otherwise none.
4. A **cell** takes the tehsil, and the locality, that most of its 25 sub-cells
   have; where a boundary runs through a cell, its sub-cells keep their own tags.

Everything uses exact arithmetic on the grid's 1e-7 degree coordinates (no
trigonometry), so the layer is identical on every platform.
"""

import functools
import gzip
import hashlib
import json
import math
import re
import struct
from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from importlib import resources

from . import geodesy, ids, spec
from .boundary import Boundary, row_flags
from .layout import SCALE, SUB, to_e7

PLACES_VERSION = 2
LEVELS = ("province", "district", "tehsil", "locality")
DATA_FILE = "places-v2.json.gz"

# How far a locality point reaches, in metres, by OpenStreetMap place kind.
LOCALITY_REACH_M = {
    "city": 10_000, "town": 4_000, "village": 2_000, "hamlet": 1_000, "isolated_dwelling": 500,
    "suburb": 1_000, "quarter": 1_000, "neighbourhood": 1_000, "city_block": 300, "locality": 1_000,
    "residential": 1_000,
}
# Metres per degree used for locality distances: fixed values (latitude, and longitude at 31.8 N)
# so the tags need no trigonometry and are the same on every platform. Within about 7% of the
# true distance anywhere in Pakistan.
_M_PER_DEG_LAT = 111_000.0
_M_PER_DEG_LON = 111_000.0 * 0.85

SOURCES = {
    "tehsil": "UN OCHA COD-AB Pakistan, admin level 3 (valid from 2022-09-09)",
    "locality": "OpenStreetMap populated places of Pakistan, HOT export (snapshot 2026-10-12)",
}
LICENSES = {"tehsil": "CC BY-IGO 3.0", "locality": "ODbL-1.0"}
ATTRIBUTION = ("Administrative boundaries: UN OCHA, Common Operational Dataset for Pakistan "
               "(CC BY-IGO 3.0); localities © OpenStreetMap contributors (ODbL), via the Humanitarian "
               "OpenStreetMap Team")

SCHEMA = """
CREATE TABLE places (
    id INTEGER PRIMARY KEY, level INTEGER NOT NULL, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    parent INTEGER, source_name TEXT NOT NULL, source_id TEXT NOT NULL,
    kind TEXT, local_name TEXT, lat INTEGER, lon INTEGER, area INTEGER);
CREATE TABLE cell_places (id INTEGER PRIMARY KEY, tehsil INTEGER NOT NULL, subs BLOB);
CREATE INDEX cell_places_tehsil ON cell_places (tehsil);
CREATE INDEX cell_places_split ON cell_places (id) WHERE subs IS NOT NULL;
CREATE TABLE cell_localities (id INTEGER PRIMARY KEY, locality INTEGER NOT NULL, subs BLOB);
CREATE INDEX cell_localities_locality ON cell_localities (locality);
CREATE INDEX cell_localities_split ON cell_localities (id) WHERE subs IS NOT NULL;
"""
_SUBS = struct.Struct("<25H")
_LSUBS = struct.Struct("<25I")
_OUTSIDE_RUN = re.compile(rb"\x00+")
_INSIDE_RUN = re.compile(rb"[^\x00]+")
_BAND = 100_000             # edge index band height, in 1e-7 degree (0.01 degree)
_LON_SCALE = 0.85           # longitude scale for nearest-boundary distances (see PolygonLayer.nearest)


# -- Names ---------------------------------------------------------------------------

_KEEP_UPPER = {"I", "II", "III", "IV", "V", "FR"}


def clean_name(raw):
    """Display name from a source name: 'D.I.KHAN' -> 'D.I. Khan', 'BABA_KOT' -> 'Baba Kot'."""
    name = re.sub(r"_+", " ", raw).strip()
    name = re.sub(r"\s*\bSUB-$", " SUB-TEHSIL", name)         # the source cuts 'SUB-TEHSIL' short
    name = re.sub(r"(\w)\(", r"\1 (", name)                    # 'SAMARBAGH(BARWA)'
    name = re.sub(r"((?:\b[A-Za-z]\.)+)(?=[A-Za-z]{2})", r"\1 ", name)   # 'D.I.KHAN' -> 'D.I. KHAN'
    name = re.sub(r"\s+", " ", name)
    if any(c.islower() for c in name):
        return name                                            # already in mixed case
    out = []
    for word in name.split(" "):
        parts = []
        for part in re.split(r"(-|\(|\))", word):
            if part in _KEEP_UPPER or re.fullmatch(r"(?:[A-Z]\.)+", part) or not part.isalpha():
                parts.append(part)
            else:
                parts.append(part.capitalize())
        out.append("".join(parts))
    return " ".join(out)


def _tidy(name):
    """A locality name for display: 'QUID_e-AZAM  COLONY' -> 'QUID e-AZAM COLONY'."""
    return re.sub(r"[_\s]+", " ", name).strip() or name


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# -- Source polygons -------------------------------------------------------------------

@dataclass
class _Unit:
    index: int              # position in the source file
    name: str
    source_name: str
    source_id: str
    rings: list             # rings of (lon_e7, lat_e7)
    area: int               # twice the absolute shoelace area, in (1e-7 degree)^2, for ordering


def _decode_ring(flat):
    pts, x, y = [], 0, 0
    for k in range(0, len(flat), 2):
        x += flat[k]
        y += flat[k + 1]
        pts.append((x, y))
    return pts


def _ring_area(rings):
    """Twice the absolute shoelace area (outer rings minus holes, by orientation), for ordering."""
    total = 0
    for pts in rings:
        total += sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:]))
    return abs(total)


@dataclass
class _Locality:
    index: int
    source_id: str          # OpenStreetMap ID, e.g. 'node/123'
    kind: str               # OpenStreetMap place kind ('residential' for named residential areas)
    name: str               # Latin-script name, for display (underscores and extra spaces removed)
    source_name: str        # the name as in the source
    local_name: str | None  # name in Urdu or another local script, where given
    point: tuple | None     # (lon_e7, lat_e7) for a point locality
    rings: list | None      # rings of (lon_e7, lat_e7) for an area locality


def _load_sources():
    raw = json.loads(gzip.decompress(resources.files("pakgrid").joinpath("data", DATA_FILE).read_bytes()))
    tehsils = []
    for i, (pcode, name, dpcode, dname, ppcode, pname, rings) in enumerate(raw["tehsils"]):
        pts = [_decode_ring(r) for r in rings]
        tehsils.append((_Unit(i, clean_name(name), name, pcode, pts, _ring_area(pts)), dpcode, dname, ppcode, pname))
    localities = []
    for i, (osm_id, kind, name, local, shape, coords) in enumerate(raw["localities"]):
        if shape == "point":
            localities.append(_Locality(i, osm_id, kind, _tidy(name), name, local, tuple(coords), None))
        else:
            localities.append(_Locality(i, osm_id, kind, _tidy(name), name, local, None,
                                        [_decode_ring(r) for r in coords]))
    return raw["sources"], tehsils, localities


class PolygonLayer:
    """Non-overlapping units (polygons with holes, even-odd rule), queried one latitude at a time."""

    def __init__(self, units, tags, typecode="H"):
        # Smaller units first: where two source polygons overlap slightly, the smaller (inner) one wins.
        self.typecode = typecode
        order = sorted(range(len(units)), key=lambda k: (units[k].area, units[k].source_id))
        self._edges = []                  # (x1, y1, x2, y2, tag) in degrees
        self._rank = {}
        self._bands = {}
        for rank, k in enumerate(order):
            self._rank[tags[k]] = rank
            for ring in units[k].rings:
                for (ax, ay), (bx, by) in zip(ring, ring[1:]):
                    if ay == by:
                        continue
                    e = len(self._edges)
                    self._edges.append((ax / SCALE, ay / SCALE, bx / SCALE, by / SCALE, tags[k]))
                    for b in range((min(ay, by) - 1) // _BAND, (max(ay, by) + 1) // _BAND + 1):
                        self._bands.setdefault(b, []).append(e)

    def crossings(self, lat):
        """{tag: sorted longitudes where the unit's boundary crosses latitude `lat`}."""
        out = {}
        for e in self._bands.get(int(lat * SCALE) // _BAND, ()):
            x1, y1, x2, y2, tag = self._edges[e]
            if (y1 <= lat) != (y2 <= lat):
                out.setdefault(tag, []).append(x1 + (lat - y1) * (x2 - x1) / (y2 - y1))
        for xs in out.values():
            xs.sort()
        return out

    def intervals(self, lat):
        """[(west, east, tag)] stretches of latitude `lat` inside each unit, sorted by west."""
        out = []
        for tag, xs in self.crossings(lat).items():
            out += [(xs[k], xs[k + 1], tag) for k in range(0, len(xs) - 1, 2)]
        out.sort()
        return out

    def scan(self, lat, centres):
        """Tag of the unit containing each point (lat, centres[j]); 0 where none. `centres` is sorted."""
        tags = array(self.typecode, bytes(array(self.typecode).itemsize * len(centres)))
        hit = self.crossings(lat)
        for tag in sorted(hit, key=self._rank.__getitem__):
            xs = hit[tag]
            for k in range(0, len(xs) - 1, 2):
                a, b = bisect_left(centres, xs[k]), bisect_left(centres, xs[k + 1])
                if b <= a:
                    continue
                if tags[a:b].count(0) == b - a:
                    tags[a:b] = array(self.typecode, [tag]) * (b - a)
                else:
                    for j in range(a, b):
                        if not tags[j]:
                            tags[j] = tag
        return tags

    def at(self, lat, lon):
        return self.scan(lat, [lon])[0]

    def nearest(self, lat, lon, max_deg=0.5):
        """Tag of the unit whose boundary is nearest the point (within `max_deg`), or 0.

        Distances use degrees with longitude scaled by a fixed 0.85 (cos 31.8°, mid-Pakistan): exact
        arithmetic, so the answer is the same on every platform. Ties: the smallest tag.
        """
        # Search a small radius first; a hit within radius r is the nearest overall, because every
        # edge that comes within r of the point is indexed in the bands searched.
        r = 0.02
        while True:
            r = min(r, max_deg)
            tag = self._nearest_within(lat, lon, r)
            if tag or r >= max_deg:
                return tag
            r *= 4

    def _nearest_within(self, lat, lon, max_deg):
        best = None
        seen = set()
        k = _LON_SCALE
        for b in range(int((lat - max_deg) * SCALE) // _BAND, int((lat + max_deg) * SCALE) // _BAND + 1):
            for e in self._bands.get(b, ()):
                if e in seen:
                    continue
                seen.add(e)
                x1, y1, x2, y2, tag = self._edges[e]
                ax, ay, bx, by, px, py = x1 * k, y1, x2 * k, y2, lon * k, lat
                dx, dy = bx - ax, by - ay
                t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                ex, ey = ax + t * dx - px, ay + t * dy - py
                d2 = ex * ex + ey * ey
                if d2 <= max_deg * max_deg and (best is None or (d2, tag) < best):
                    best = (d2, tag)
        return best[1] if best else 0


# -- Building the layer ---------------------------------------------------------------------

@dataclass
class PlacesData:
    places: list            # (id, level, key, name, parent, source_name, source_id, kind, local_name,
                            #  lat_e7, lon_e7, area)
    cells: list             # (cell ID, tehsil, subs blob or None), in ID order
    localities: list        # (cell ID, locality or 0, subs blob or None), in ID order
    filled_sub_cells: int   # sub-cells tagged from their neighbours (no tehsil polygon at the centroid)
    nearest_sub_cells: int  # sub-cells tagged with the nearest tehsil (islands the tehsils leave out)
    untagged_sub_cells: int  # sub-cells touching Pakistan that could not be tagged (none in a full build)
    locality_sub_cells: int  # sub-cells with a locality tag


# Area kinds that are whole towns or cities: a sub-cell inside one takes a locality point within
# reach (a neighbourhood, a village the town has grown over) before the town's own outline.
_TOWN_AREAS = {"city", "town"}


@dataclass
class _Sources:
    units: dict             # place ID -> (level, name, source_name, source_id, parent ID)
    tehsils: PolygonLayer
    areas: PolygonLayer     # named areas other than whole towns and cities
    towns: PolygonLayer     # town and city outlines
    localities: dict        # place ID -> _Locality
    points: dict            # latitude bucket -> [(lat_e7, lon_e7, reach in m, limit, place ID)]


_BUCKET = 1_000_000         # point locality latitude buckets, in 1e-7 degree (0.1 degree; > any reach)


def _limit(reach_m):
    """Largest D2 = (85 dx)^2 + (100 dy)^2 (dx, dy in 1e-7 degree) within `reach_m` metres.

    That distance in metres is sqrt(D2) * 111000 / 1e9 (_M_PER_DEG_LAT, _M_PER_DEG_LON), so the
    reach test is exact in integers.
    """
    return (reach_m * 10 ** 9) ** 2 // 111_000 ** 2


def _in_limit(kind):
    """Largest D2 (as in _limit) at which a sub-cell is *in* a point locality rather than near it."""
    k = 1 if kind in _TOWN_AREAS else 4
    return (LOCALITY_REACH_M[kind] * 10 ** 9) ** 2 // (k * 111_000 ** 2)


def locality_is_near(kind, area, lat_e7, lon_e7, point_lat_e7, point_lon_e7):
    """True when a sub-cell (centroid at lat_e7, lon_e7) tagged with a locality is *near* it, not in it.

    A sub-cell is in an area locality; in a city or town point locality anywhere within its
    reach (they are large); and in any other point locality within half its reach. Beyond that
    it is near it. Exact integer arithmetic, as for the tags.
    """
    if area:
        return False
    d2 = (85 * (lon_e7 - point_lon_e7)) ** 2 + (100 * (lat_e7 - point_lat_e7)) ** 2
    return d2 > _in_limit(kind)


def _inner_point(rings, layer, tag):
    """(lat_e7, lon_e7) inside an area: the middle of its longest stretch at its middle latitude."""
    ys = [y for r in rings for _, y in r]
    lat = (min(ys) + max(ys)) // 2
    stretches = [(e - w, (w + e) / 2) for w, e, t in layer.intervals(lat / SCALE) if t == tag]
    if not stretches:
        x, y = rings[0][0]
        return y, x
    return lat, to_e7(max(stretches)[1])


@functools.lru_cache(maxsize=1)
def _sources():
    """The packaged sources, place IDs and polygon layers (loaded once per process).

    Place IDs: provinces, then districts, then tehsils, then localities, each sorted by name then
    source ID.
    """
    _, tehsils, localities = _load_sources()
    provinces, districts = {}, {}
    for _, dpcode, dname, ppcode, pname in tehsils:
        provinces[ppcode] = pname
        districts[dpcode] = (dname, ppcode)
    order = ([(1, clean_name(n), n, pc) for pc, n in provinces.items()]
             + [(2, clean_name(n), n, pc) for pc, (n, _) in districts.items()]
             + [(3, u.name, u.source_name, u.source_id) for u, *_ in tehsils])
    pid = {}
    for level, name, _, source_id in sorted(order, key=lambda r: (r[0], r[1].lower(), r[3])):
        pid[source_id] = len(pid) + 1
    units = {}
    for level, name, source_name, source_id in order:
        parent = (None if level == 1 else pid[districts[source_id][1]] if level == 2
                  else pid[next(t[1] for t in tehsils if t[0].source_id == source_id)])
        units[pid[source_id]] = (level, name, source_name, source_id, parent)
    t_layer = PolygonLayer([t[0] for t in tehsils], [pid[t[0].source_id] for t in tehsils])

    first = len(pid) + 1
    lid = {loc.index: first + k for k, loc in enumerate(
        sorted(localities, key=lambda loc: (loc.name.lower(), loc.source_id)))}
    by_id = {lid[loc.index]: loc for loc in localities}
    layers = []
    for town in (False, True):
        areas = [loc for loc in localities if loc.rings and (loc.kind in _TOWN_AREAS) == town]
        layers.append(PolygonLayer([_Unit(loc.index, loc.name, loc.source_name, loc.source_id, loc.rings,
                                          _ring_area(loc.rings)) for loc in areas],
                                   [lid[loc.index] for loc in areas], "I"))
    points = {}
    for i, loc in sorted(by_id.items()):
        if loc.point:
            reach = LOCALITY_REACH_M[loc.kind]
            x, y = loc.point
            points.setdefault(y // _BUCKET, []).append((y, x, reach, _limit(reach), i))
    return _Sources(units, t_layer, layers[0], layers[1], by_id, points)


def _locality_point(src, i):
    """(lat_e7, lon_e7) of a locality: its point, or a point inside its area."""
    loc = src.localities[i]
    if loc.point:
        return loc.point[1], loc.point[0]
    return _inner_point(loc.rings, src.towns if loc.kind in _TOWN_AREAS else src.areas, i)


def build_places_data(layout, boundary=None, quadrants=None, rows=None, progress=None, on_rows=None):
    """Compute the places layer for the given part of the grid (all of it by default).

    on_rows(q, tags, localities), if given, is called once per quadrant with {fine row: array of
    sub-cell tags} (0 = no tag) for tehsils and for localities, the final tags of every sub-cell;
    the map viewer's data is made from it.
    """
    from .builder import _parse_selection
    boundary = boundary or Boundary.default()
    qs, row_sel = _parse_selection(quadrants, rows)
    src = _sources()
    t_layer = src.tehsils

    cells, loc_cells, filled, nearest, untagged, located = [], [], 0, 0, 0, 0
    for q in qs:
        if progress:
            progress(f"places, quadrant {spec.QUADRANTS[q][0]}")
        ql = layout.quadrants[q]
        lats = [ql.fine_lat(i) for i in range(SUB * ql.nrows + 1)]
        row_range = [r for r in (row_sel if row_sel is not None else range(ql.nrows)) if 0 <= r < ql.nrows]
        tags, flags, edges, clats, clons = {}, {}, {}, {}, {}      # per fine row i
        for row in row_range:
            lons = array("d", (ql.fine_lon(row, j) for j in range(SUB * ql.ncols[row] + 1)))
            centres = array("d", ((lons[j] + lons[j + 1]) / 2 for j in range(len(lons) - 1)))
            clon = array("q", (to_e7(c) for c in centres))       # the stored centroid longitudes
            for i in range(row * SUB, (row + 1) * SUB):
                clats[i], clons[i] = to_e7(geodesy.band_centroid_lat(lats[i], lats[i + 1])), clon
                f = row_flags(*boundary.band(lats[i], lats[i + 1]), lons, centres)
                t = t_layer.scan(clats[i] / SCALE, [c / SCALE for c in clon])
                for m in _OUTSIDE_RUN.finditer(f):
                    t[m.start():m.end()] = array("H", bytes(2 * (m.end() - m.start())))
                tags[i], flags[i], edges[i] = t, f, lons
        n_filled, left = _fill_gaps(tags, flags, edges)
        filled += n_filled
        for i, j in sorted(left):
            tags[i][j] = t_layer.nearest(clats[i] / SCALE, clons[i][j] / SCALE)
            if tags[i][j]:
                nearest += 1
            else:
                untagged += 1
        locs = {i: _tag_localities(src, clats[i], clons[i], tags[i], flags[i]) for i in tags}
        located += sum(len(t) - t.count(0) for t in locs.values())
        if on_rows:
            on_rows(q, tags, locs)
        for row in row_range:
            for layer, out, typecode, packer in ((tags, cells, "H", _SUBS), (locs, loc_cells, "I", _LSUBS)):
                rt = [layer[row * SUB + sr] for sr in range(SUB)]
                zero = array(typecode, bytes(array(typecode).itemsize * SUB))
                for col in range(ql.ncols[row]):
                    c0 = col * SUB
                    segs = [t[c0:c0 + SUB] for t in rt]
                    if all(sg == zero for sg in segs):
                        continue
                    first = segs[0][0]
                    if first and all(sg == array(typecode, [first]) * SUB for sg in segs):
                        out.append((ids.cell_id(q, row, col), first, None))
                        continue
                    block = [x for sg in segs for x in sg]
                    main = _main_tag(block, skip_zero=layer is tags)
                    out.append((ids.cell_id(q, row, col), main,
                                None if all(b == main for b in block) else packer.pack(*block)))

    cells.sort()
    loc_cells.sort()
    return PlacesData(_places_table(), cells, loc_cells, filled, nearest, untagged, located)


def _tag_localities(src, lat, clon, tehsils, flags):
    """Locality tags of one fine row of sub-cells (centroids at lat_e7, clon[j]); 0 = none.

    Only sub-cells with a tehsil (and a border flag `flags` other than outside) are tagged. In order: the smallest named area containing the
    centroid (other than town and city outlines); else the point locality with the smallest
    distance / reach among those within reach (ties: the smallest place ID); else the smallest
    town or city outline containing it.
    """
    n = len(clon)
    if not _INSIDE_RUN.search(flags):
        return array("I", bytes(4 * n))
    centres = [c / SCALE for c in clon]
    out = src.areas.scan(lat / SCALE, centres)
    best = array("I", bytes(4 * n))
    b0 = lat // _BUCKET
    cands = [p for b in (b0 - 1, b0, b0 + 1) for p in src.points.get(b, ())]
    pd = {}                         # place ID -> (lon_e7, (100 dy)^2, reach^2)
    for py, px, reach, limit, i in cands:
        dy2 = (100 * (lat - py)) ** 2
        if dy2 > limit:
            continue
        dxm = math.isqrt(limit - dy2) // 85
        a, b = bisect_left(clon, px - dxm), bisect_right(clon, px + dxm)
        if b <= a:
            continue
        pd[i] = (px, dy2, reach * reach)
        if best[a:b].count(0) == b - a:
            best[a:b] = array("I", [i]) * (b - a)
            continue
        r2 = reach * reach
        for j in range(a, b):
            cur = best[j]
            if not cur:
                best[j] = i
                continue
            cx, cdy2, cr2 = pd[cur]
            x = clon[j]
            d_new = (85 * (x - px)) ** 2 + dy2
            d_cur = (85 * (x - cx)) ** 2 + cdy2
            lhs, rhs = d_new * cr2, d_cur * r2
            if lhs < rhs or (lhs == rhs and i < cur):
                best[j] = i
    towns = None
    for m in _INSIDE_RUN.finditer(flags):
        for j in range(m.start(), m.end()):
            if not out[j] and tehsils[j]:
                if best[j]:
                    out[j] = best[j]
                else:
                    if towns is None:
                        towns = src.towns.scan(lat / SCALE, centres)
                    out[j] = towns[j]
            elif out[j] and not tehsils[j]:
                out[j] = 0
    for m in _OUTSIDE_RUN.finditer(flags):
        out[m.start():m.end()] = array("I", bytes(4 * (m.end() - m.start())))
    return out


def _main_tag(block, skip_zero=True):
    """The tag most of a cell's sub-cells have (ties: the centre sub-cell's, else the smallest)."""
    counts = Counter(b for b in block if b or not skip_zero)
    best = max(counts.values())
    tied = [t for t, c in counts.items() if c == best]
    return block[12] if block[12] in tied else min(tied)


def _fill_gaps(tags, flags, edges):
    """Tag sub-cells that touch Pakistan but lie outside every tehsil polygon, from their neighbours.

    Repeated in rounds: in each round, every untagged sub-cell with tagged neighbours (left, right,
    and the sub-cells above and below its centre) takes the most common neighbour tag (ties: the
    smallest). The result does not depend on processing order.
    """
    gaps = set()
    for i, f in flags.items():
        t = tags[i]
        for m in _INSIDE_RUN.finditer(f):
            a, b = m.start(), m.end()
            if t[a:b].count(0):
                gaps.update((i, j) for j in range(a, b) if not t[j])
    filled = 0
    while gaps:
        new = {}
        for i, j in gaps:
            t = tags[i]
            near = []
            if j > 0:
                near.append(t[j - 1])
            if j + 1 < len(t):
                near.append(t[j + 1])
            lons = edges[i]
            centre = (lons[j] + lons[j + 1]) / 2
            for k in (i - 1, i + 1):
                if k in tags:
                    m = bisect_right(edges[k], centre) - 1
                    if 0 <= m < len(tags[k]):
                        near.append(tags[k][m])
            counts = Counter(x for x in near if x)
            if counts:
                best = max(counts.values())
                new[(i, j)] = min(x for x, c in counts.items() if c == best)
        if not new:
            break
        for (i, j), tag in new.items():
            tags[i][j] = tag
        gaps -= new.keys()
        filled += len(new)
    return filled, gaps


@functools.lru_cache(maxsize=1)
def _places_table():
    """Rows of the places table, in ID order:

    (id, level, key, name, parent, source_name, source_id, kind, local_name, lat, lon, area)

    Provinces, districts and tehsils have the parents the source names. A locality's parent is the tehsil containing
    its point (or a point inside its area), else the nearest tehsil; localities more than 0.5
    degree from any tehsil are left out. lat and lon (1e-7 degree) are a locality's point, and
    area is 1 for a locality mapped as an outline (0 for a point).
    """
    src = _sources()
    rows = [[i, level, None, name, parent, source_name, source_id, None, None, None, None, None]
            for i, (level, name, source_name, source_id, parent) in sorted(src.units.items())]
    for i, loc in sorted(src.localities.items()):
        lat, lon = _locality_point(src, i)
        parent = src.tehsils.at(lat / SCALE, lon / SCALE) or src.tehsils.nearest(lat / SCALE, lon / SCALE)
        if parent:
            rows.append([i, 4, None, loc.name, parent, loc.source_name, loc.source_id, loc.kind,
                         loc.local_name, lat, lon, 0 if loc.point else 1])
    by_id = {r[0]: r for r in rows}
    used = set()
    for r in rows:                                   # keys: 'punjab/lahore/lahore-city/model-town'
        part = _slug(r[3]) or _slug(r[6])
        key = part if r[4] is None else by_id[r[4]][2] + "/" + part
        base, n = key, 2
        while key in used:
            key, n = f"{base}-{n}", n + 1
        used.add(key)
        r[2] = key
    return [tuple(r) for r in rows]


# -- Map viewer data -----------------------------------------------------------------------

def viewer_data(layout=None, progress=None):
    """The places layer for the map viewer (viewer/places.js): places, and every sub-cell's tags.

    Each fine row of sub-cells that has any tag is stored as runs [tag, count, tag, count, ...]
    from its west end (tag 0 = none), once for tehsils ("rows") and once for localities
    ("locality_rows"). Each place is [id, level, last part of its key, name, parent, extent
    [s, w, n, e]], and a locality adds [kind, local name, lat_e7, lon_e7, area].
    """
    from .layout import GridLayout
    layout = layout or GridLayout.from_spec()
    out_rows = ([dict() for _ in layout.quadrants], [dict() for _ in layout.quadrants])
    extent = {}

    def grow(tag, s, w, n, e):
        b = extent.get(tag)
        extent[tag] = [s, w, n, e] if b is None else [min(b[0], s), min(b[1], w), max(b[2], n), max(b[3], e)]

    def keep(q, tags, localities):
        ql = layout.quadrants[q]
        for layer, rows_out in ((tags, out_rows[0]), (localities, out_rows[1])):
            for i in sorted(layer):
                t = layer[i]
                if not t.count(0) < len(t):
                    continue
                runs, start = [], 0
                for j in range(1, len(t) + 1):
                    if j == len(t) or t[j] != t[start]:
                        runs += [t[start], j - start]
                        if t[start]:
                            row = i // SUB
                            grow(t[start], ql.fine_lat(i), ql.fine_lon(row, start), ql.fine_lat(i + 1),
                                 ql.fine_lon(row, j))
                        start = j
                rows_out[q][i] = runs

    data = build_places_data(layout, progress=progress, on_rows=keep)
    table = {p[0]: list(p) for p in data.places}
    for pid in sorted(table, reverse=True):          # tehsils, then districts: they make their parents'
        p = table[pid]
        if p[1] in (2, 3) and pid in extent:
            grow(p[4], *extent[pid])
    places = []
    for pid, p in sorted(table.items()):
        b = extent.get(pid)
        row = [pid, p[1], p[2].rsplit("/", 1)[-1], p[3], p[4], [round(x, 5) for x in b] if b else None]
        if p[1] == 4:
            row += [p[7], p[8], p[9], p[10], p[11]]
        places.append(row)
    return {
        "places_version": PLACES_VERSION,
        "places_sha256": places_hash(data.places, data.cells, data.localities),
        "attribution": ATTRIBUTION,
        "in_limits": {kind: _in_limit(kind) for kind in sorted(LOCALITY_REACH_M)},
        "places": places,
        "rows": [{str(i): r for i, r in q.items()} for q in out_rows[0]],
        "locality_rows": [{str(i): r for i, r in q.items()} for q in out_rows[1]],
    }


# -- Storing -----------------------------------------------------------------------------

def places_hash(places, cells, localities):
    h = hashlib.sha256(b"pakgrid-places-v2")
    for p in places:
        h.update(json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode())
    for cid, tehsil, subs in cells:
        h.update(struct.pack("<qH", cid, tehsil))
        h.update(subs or b"")
    h.update(b"localities")
    for cid, locality, subs in localities:
        h.update(struct.pack("<qI", cid, locality))
        h.update(subs or b"")
    return h.hexdigest()


def write_places(db, data, coverage):
    """Write (or replace) the places layer in an open, writable grid database."""
    for table in ("cell_localities", "cell_places", "places"):
        db.execute(f"DROP TABLE IF EXISTS {table}")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", data.places)
    db.executemany("INSERT INTO cell_places VALUES (?,?,?)", data.cells)
    db.executemany("INSERT INTO cell_localities VALUES (?,?,?)", data.localities)
    meta = {
        "places_version": PLACES_VERSION,
        "places_coverage": coverage,
        "places_sha256": places_hash(data.places, data.cells, data.localities),
        "places_sources": "; ".join(f"{level}: {SOURCES[level]} ({LICENSES[level]})" for level in SOURCES),
        "places_attribution": ATTRIBUTION,
        "places_count": len(data.places),
        "places_localities": sum(1 for p in data.places if p[1] == 4),
        "places_cells": len(data.cells),
        "places_split_cells": sum(1 for c in data.cells if c[2] is not None),
        "places_filled_sub_cells": data.filled_sub_cells,
        "places_nearest_sub_cells": data.nearest_sub_cells,
        "places_untagged_sub_cells": data.untagged_sub_cells,
        "places_locality_sub_cells": data.locality_sub_cells,
    }
    db.execute("DELETE FROM meta WHERE key LIKE 'places_%'")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in meta.items()])
    return meta


def build_places(path, progress=None):
    """Build (or rebuild) the places layer inside an existing grid database file."""
    import sqlite3
    from .grid import _layout_from_store
    from .store import GridStore
    store = GridStore(path)
    try:
        m = store.meta()
        layout = _layout_from_store(store)
        boundary = Boundary.from_packed(store.boundary_rings())
    finally:
        store.close()
    qs = [int(x, 2) for x in m["quadrants_built"].split(",")]
    rows = None if m["rows_built"] == "all" else tuple(map(int, m["rows_built"].split(":")))
    data = build_places_data(layout, boundary, qs, rows, progress)
    db = sqlite3.connect(path)
    try:
        meta = write_places(db, data, m.get("coverage", "partial"))
        db.commit()
    finally:
        db.close()
    return meta
