"""Place names: province, district and tehsil tags on every cell and sub-cell.

Places are a *layer* on the grid. The grid (rectangle, quadrants, cells,
sub-cells) is frozen: its IDs never change. Place names do change (new
districts, renamed tehsils), so the places layer has its own version and can be
rebuilt or replaced without touching a single grid ID. Apps store grid IDs and
look up the current names.

How the tags are made (places version 1):

1. Every sub-cell that touches Pakistan (border flag inside or border) is
   tagged with the **tehsil** whose boundary contains the sub-cell's centroid.
   Tehsils come from the detailed 2017 census boundaries (geoBoundaries
   PAK-ADM3). Sub-cells along the edges that no tehsil polygon reaches (the
   source's coastline and border run slightly inside ours) take the tag of
   their nearest tagged neighbours.
2. Each tehsil's **district** is the district (geoBoundaries PAK-ADM2) that
   covers most of it, and each district's **province** is the province
   (PAK-ADM1) that covers most of it. The district outlines are much coarser
   than the tehsils, so a tehsil whose main district covers under 70% of it is
   marked *uncertain*. Deriving the hierarchy this way keeps it consistent: a
   sub-cell's district always contains its tehsil.
3. A **cell** takes the tehsil that most of its 25 sub-cells have; where a
   tehsil line runs through a cell, its sub-cells keep their own tags.

Everything uses exact arithmetic on the grid's 1e-7 degree coordinates (no
trigonometry), so the layer is identical on every platform.
"""

import functools
import gzip
import hashlib
import json
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

PLACES_VERSION = 1
LEVELS = ("province", "district", "tehsil")
UNCERTAIN_BELOW = 0.70      # a tehsil whose main district covers less than this is marked uncertain

SOURCES = {
    "province": ("geoBoundaries-PAK-ADM1.geojson.gz", "geoBoundaries gbOpen PAK-ADM1-70912109", "public domain"),
    "district": ("geoBoundaries-PAK-ADM2.geojson.gz", "geoBoundaries gbOpen PAK-ADM2-60131773", "public domain"),
    "tehsil": ("geoBoundaries-PAK-ADM3.geojson.gz", "geoBoundaries gbOpen PAK-ADM3-9618217", "ODbL-1.0"),
}
ATTRIBUTION = ("Place boundaries via geoBoundaries (www.geoboundaries.org): provinces and districts "
               "(public domain, from Wikimedia Commons); tehsils © Pathways Data / Pakistan Bureau of "
               "Statistics 2017 census, available under the ODbL")

SCHEMA = """
CREATE TABLE places (
    id INTEGER PRIMARY KEY, level INTEGER NOT NULL, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    parent INTEGER, parent_share INTEGER, source_name TEXT NOT NULL, source_id TEXT NOT NULL);
CREATE TABLE cell_places (id INTEGER PRIMARY KEY, tehsil INTEGER NOT NULL, subs BLOB);
CREATE INDEX cell_places_tehsil ON cell_places (tehsil);
CREATE INDEX cell_places_split ON cell_places (id) WHERE subs IS NOT NULL;
"""
_SUBS = struct.Struct("<25H")
_ZERO5 = array("H", bytes(2 * SUB))
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


def _load_units(level):
    data = resources.files("pakgrid").joinpath("data", SOURCES[level][0]).read_bytes()
    units = []
    for i, f in enumerate(json.loads(gzip.decompress(data))["features"]):
        geom = f["geometry"]
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings, area = [], 0
        for poly in polys:
            for k, ring in enumerate(poly):
                pts = [(round(x * SCALE), round(y * SCALE)) for x, y, *_ in ring]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                if len(pts) >= 4:
                    rings.append(pts)
                    a = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:])))
                    area += -a if k else a                     # holes subtract
        p = f["properties"]
        units.append(_Unit(i, clean_name(p["shapeName"]), p["shapeName"], p["shapeID"], rings, area))
    return units


class PolygonLayer:
    """Non-overlapping units (polygons with holes, even-odd rule), queried one latitude at a time."""

    def __init__(self, units, tags):
        # Smaller units first: where two source polygons overlap slightly, the smaller (inner) one wins.
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
        tags = array("H", bytes(2 * len(centres)))
        hit = self.crossings(lat)
        for tag in sorted(hit, key=self._rank.__getitem__):
            xs = hit[tag]
            for k in range(0, len(xs) - 1, 2):
                a, b = bisect_left(centres, xs[k]), bisect_left(centres, xs[k + 1])
                if b <= a:
                    continue
                if tags[a:b].count(0) == b - a:
                    tags[a:b] = array("H", [tag]) * (b - a)
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
    places: list            # (id, level, key, name, parent, parent_share, source_name, source_id)
    cells: list             # (cell ID, tehsil, subs blob or None), in ID order
    filled_sub_cells: int   # sub-cells tagged from their neighbours (no tehsil polygon at the centroid)
    nearest_sub_cells: int  # sub-cells tagged with the nearest tehsil (islands the tehsils leave out)
    untagged_sub_cells: int  # sub-cells touching Pakistan that could not be tagged (none in a full build)


def _assign_ids(provinces, districts, tehsils):
    """Place IDs: provinces, then districts, then tehsils, each sorted by name then source ID."""
    pid = {}
    for level, units in ((1, provinces), (2, districts), (3, tehsils)):
        for u in sorted(units, key=lambda u: (u.name.lower(), u.source_id)):
            pid[(level, u.index)] = len(pid) + 1
    return pid


@functools.lru_cache(maxsize=1)
def _sources():
    """The packaged source units and their polygon layers (loaded once per process)."""
    provinces, districts, tehsils = (_load_units(level) for level in LEVELS)
    pid = _assign_ids(provinces, districts, tehsils)
    layers = tuple(PolygonLayer(units, [pid[(level, u.index)] for u in units])
                   for level, units in ((1, provinces), (2, districts), (3, tehsils)))
    return provinces, districts, tehsils, pid, layers


def build_places_data(layout, boundary=None, quadrants=None, rows=None, progress=None, on_rows=None):
    """Compute the places layer for the given part of the grid (all of it by default).

    on_rows(q, tags), if given, is called once per quadrant with {fine row: array of sub-cell tags}
    (0 = no tag), the final tags of every sub-cell; the map viewer's data is made from it.
    """
    from .builder import _parse_selection
    boundary = boundary or Boundary.default()
    qs, row_sel = _parse_selection(quadrants, rows)
    t_layer = _sources()[4][2]

    cells, filled, nearest, untagged = [], 0, 0, 0
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
            clon = array("d", (to_e7(c) / SCALE for c in centres))       # the stored centroid longitudes
            for i in range(row * SUB, (row + 1) * SUB):
                clat = to_e7(geodesy.band_centroid_lat(lats[i], lats[i + 1])) / SCALE
                clats[i], clons[i] = clat, clon
                f = row_flags(*boundary.band(lats[i], lats[i + 1]), lons, centres)
                t = t_layer.scan(clat, clon)
                for m in _OUTSIDE_RUN.finditer(f):
                    t[m.start():m.end()] = array("H", bytes(2 * (m.end() - m.start())))
                tags[i], flags[i], edges[i] = t, f, lons
        n_filled, left = _fill_gaps(tags, flags, edges)
        filled += n_filled
        for i, j in sorted(left):
            tags[i][j] = t_layer.nearest(clats[i], clons[i][j])
            if tags[i][j]:
                nearest += 1
            else:
                untagged += 1
        if on_rows:
            on_rows(q, tags)
        for row in row_range:
            rt = [tags[row * SUB + sr] for sr in range(SUB)]
            for col in range(ql.ncols[row]):
                c0 = col * SUB
                segs = [t[c0:c0 + SUB] for t in rt]
                if all(sg == _ZERO5 for sg in segs):
                    continue
                first = segs[0][0]
                if first and all(sg == array("H", [first]) * SUB for sg in segs):
                    cells.append((ids.cell_id(q, row, col), first, None))
                    continue
                block = [x for sg in segs for x in sg]
                main = _main_tag(block)
                cells.append((ids.cell_id(q, row, col), main,
                              None if all(b == main for b in block) else _SUBS.pack(*block)))

    places = _places_table()
    cells.sort()
    return PlacesData(places, cells, filled, nearest, untagged)


def _main_tag(block):
    """The tag most of a cell's sub-cells have (ties: the centre sub-cell's, else the smallest)."""
    counts = Counter(b for b in block if b)
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


# Latitudes at which the hierarchy is measured: every 0.01 degree across Pakistan.
_HIERARCHY_LATS = range(236_050_000, 371_000_000, 100_000)


def _overlaps(child, parent):
    """{(child tag, parent tag): length in degrees of longitude} summed over _HIERARCHY_LATS.

    Measuring overlap along evenly spaced lines gives each child's share inside each parent in
    proportion to area, independent of the grid and of which part of it was built.
    """
    total = Counter()
    for lat_e7 in _HIERARCHY_LATS:
        lat = lat_e7 / SCALE
        a, b = child.intervals(lat), parent.intervals(lat)
        k = 0
        for a0, a1, ta in a:
            while k < len(b) and b[k][1] <= a0:
                k += 1
            m = k
            while m < len(b) and b[m][0] < a1:
                lo, hi = max(a0, b[m][0]), min(a1, b[m][1])
                if hi > lo:
                    total[(ta, b[m][2])] += hi - lo
                m += 1
    return total


def _inner_point(unit, layer, tag):
    """A point inside a unit: the middle of its longest stretch at its middle latitude."""
    ys = [y for r in unit.rings for _, y in r]
    lat = (min(ys) + max(ys)) / 2 / SCALE
    stretches = [(e - w, (w + e) / 2) for w, e, t in layer.intervals(lat) if t == tag]
    if not stretches:
        x, y = unit.rings[0][0]
        return y / SCALE, x / SCALE
    return lat, max(stretches)[1]


@functools.lru_cache(maxsize=1)
def _places_table():
    """Rows of the places table: (id, level, key, name, parent, parent_share, source_name, source_id).

    A district's province and a tehsil's district are the parent units that cover most of it.
    parent_share is that parent's share in thousandths of the part covered by any parent.
    """
    provinces, districts, tehsils, pid, (p_layer, d_layer, t_layer) = _sources()
    links = {}
    for level, units, own, parent, counts in ((2, districts, d_layer, p_layer, _overlaps(d_layer, p_layer)),
                                              (3, tehsils, t_layer, d_layer, _overlaps(t_layer, d_layer))):
        for u in units:
            i = pid[(level, u.index)]
            options = {par: c for (ch, par), c in counts.items() if ch == i}
            if options:
                best = max(options.values())
                par = min(p for p, c in options.items() if c == best)
                links[i] = (par, round(1000 * best / sum(options.values())))
            else:               # too small for any measuring line: use a point inside it
                lat, lon = _inner_point(u, own, i)
                links[i] = (parent.at(lat, lon) or parent.nearest(lat, lon) or None, None)

    rows = []
    for level, units in ((1, provinces), (2, districts), (3, tehsils)):
        for u in units:
            i = pid[(level, u.index)]
            parent, share = links.get(i, (None, None))
            rows.append([i, level, None, u.name, parent, share, u.source_name, u.source_id])
    rows.sort()
    by_id = {r[0]: r for r in rows}
    used = set()
    for r in rows:                                   # keys: 'punjab/lahore/raiwind'
        path, p = [_slug(r[3])], r[4]
        while p:
            path.append(_slug(by_id[p][3]))
            p = by_id[p][4]
        key = "/".join(reversed(path))
        base, n = key, 2
        while key in used:
            key, n = f"{base}-{n}", n + 1
        used.add(key)
        r[2] = key
    return [tuple(r) for r in rows]


# -- Map viewer data -----------------------------------------------------------------------

def viewer_data(layout=None, progress=None):
    """The places layer for the map viewer (viewer/places.js): places, and every sub-cell's tag.

    Each fine row of sub-cells that has any tag is stored as runs [tag, count, tag, count, ...]
    from its west end (tag 0 = outside Pakistan). Each place has its extent [s, w, n, e].
    """
    from .layout import GridLayout
    layout = layout or GridLayout.from_spec()
    rows_out = [dict() for _ in layout.quadrants]
    extent = {}

    def grow(tag, s, w, n, e):
        b = extent.get(tag)
        extent[tag] = [s, w, n, e] if b is None else [min(b[0], s), min(b[1], w), max(b[2], n), max(b[3], e)]

    def keep(q, tags):
        ql = layout.quadrants[q]
        for i in sorted(tags):
            t = tags[i]
            if not t.count(0) < len(t):
                continue
            runs, start = [], 0
            for j in range(1, len(t) + 1):
                if j == len(t) or t[j] != t[start]:
                    runs += [t[start], j - start]
                    if t[start]:
                        row = i // SUB
                        grow(t[start], ql.fine_lat(i), ql.fine_lon(row, start), ql.fine_lat(i + 1), ql.fine_lon(row, j))
                    start = j
            rows_out[q][i] = runs

    data = build_places_data(layout, progress=progress, on_rows=keep)
    table = {p[0]: list(p) for p in data.places}
    for pid in sorted(table, reverse=True):          # tehsils first: their extents make their parents'
        p = table[pid]
        if pid in extent and p[4]:
            b = extent[pid]
            grow(p[4], *b)
    places = []
    for pid, p in sorted(table.items()):
        b = extent.get(pid)
        places.append([pid, p[1], p[2], p[3], p[4], p[5], [round(x, 5) for x in b] if b else None])
    return {
        "places_version": PLACES_VERSION,
        "places_sha256": places_hash(data.places, data.cells),
        "attribution": ATTRIBUTION,
        "places": places,      # [id, level (1-3), key, name, parent, parent share (thousandths), extent]
        "rows": [{str(i): r for i, r in q.items()} for q in rows_out],
    }


# -- Storing -----------------------------------------------------------------------------

def places_hash(places, cells):
    h = hashlib.sha256(b"pakgrid-places-v1")
    for p in places:
        h.update(json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode())
    for cid, tehsil, subs in cells:
        h.update(struct.pack("<qH", cid, tehsil))
        h.update(subs or b"")
    return h.hexdigest()


def write_places(db, data, coverage):
    """Write (or replace) the places layer in an open, writable grid database."""
    db.execute("DROP TABLE IF EXISTS cell_places")
    db.execute("DROP TABLE IF EXISTS places")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?)", data.places)
    db.executemany("INSERT INTO cell_places VALUES (?,?,?)", data.cells)
    meta = {
        "places_version": PLACES_VERSION,
        "places_coverage": coverage,
        "places_sha256": places_hash(data.places, data.cells),
        "places_sources": "; ".join(f"{level}: {SOURCES[level][1]} ({SOURCES[level][2]})" for level in LEVELS),
        "places_attribution": ATTRIBUTION,
        "places_count": len(data.places),
        "places_cells": len(data.cells),
        "places_split_cells": sum(1 for c in data.cells if c[2] is not None),
        "places_filled_sub_cells": data.filled_sub_cells,
        "places_nearest_sub_cells": data.nearest_sub_cells,
        "places_untagged_sub_cells": data.untagged_sub_cells,
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
