"""Custom zones: delivery areas and other regions made of grid sub-cells.

A zone is a set of sub-cells, made from a drawn polygon, a circle, a list of cells, a place
(province, district, tehsil or locality), or other zones (union, intersection, difference).
Because it is made of grid blocks, checking a pin is one grid lookup and a bit test, and a
zone stores compactly and identically everywhere:

    with Grid.open("pakgrid_v1_pakistan.sqlite") as grid:
        zone = grid.zone(polygon=[(31.50, 74.33), (31.50, 74.38), (31.54, 74.38), (31.54, 74.33)])
        zone.contains(31.5204, 74.3587)      # True: is this pin in my delivery area? (~3 µs)
        zone.sub_cell_count, zone.area_km2   # 506 sub-cells, ~20.2 km²
        data = zone.to_dict()                # store it (e.g. in a JSONField) ...
        zone = Zone.from_dict(data)          # ... and load it back

Rules:

- **Polygon:** a sub-cell is in the zone when its centroid is inside the polygon (even-odd rule,
  so holes work), in exact integer arithmetic on 1e-7 degree. The edge of the zone therefore
  follows the ~200 m sub-cells, not the drawn line.
- **Circle:** the sub-cells whose centroid (or with touching=True, any part) is within the radius,
  as Grid.cells_within.
- **Cells:** cell codes (all 25 sub-cells) and sub-cell codes or IDs.
- **Place:** every sub-cell tagged with the place (for a province or district, with any of its
  tehsils), exactly as Grid.cells_in_place(level="sub").
- pakistan_only=True keeps only the sub-cells inside Pakistan or on its border.
- A pin is in a zone when the sub-cell containing it is (Grid.locate's edge rule).

Internally a zone is {cell ID: 25-bit mask of its sub-cells}, which also is its stored form.
"""

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass

from . import geodesy, ids, spec
from .errors import OutsideGridError, PakgridError
from .layout import SCALE, SUB, to_e7

ZONE_VERSION = 1
FULL = (1 << (SUB * SUB)) - 1        # mask of a cell with all 25 sub-cells
MAX_ZONE_SUB_CELLS = 6_000_000       # ~240,000 km²: more than Punjab (5.1 million sub-cells)
_BAND = 100_000                      # polygon edge index band, in 1e-7 degree (0.01 degree)


class ZoneError(PakgridError, ValueError):
    """A zone cannot be made as asked (bad polygon, too large, mismatched data, ...)."""


# -- Polygons ------------------------------------------------------------------------------

def rings_e7(polygon):
    """Closed rings of (lat_e7, lon_e7) from [(lat, lon), ...], [[ring], [hole], ...] or a GeoJSON
    Polygon or MultiPolygon ({"type": "Polygon", "coordinates": [[[lon, lat], ...], ...]})."""
    if isinstance(polygon, dict):
        kind = polygon.get("type")
        if kind == "Feature":
            return rings_e7(polygon.get("geometry") or {})
        if kind == "Polygon":
            rings = [[(p[1], p[0]) for p in ring] for ring in polygon["coordinates"]]
        elif kind == "MultiPolygon":
            rings = [[(p[1], p[0]) for p in ring] for poly in polygon["coordinates"] for ring in poly]
        else:
            raise ZoneError("a GeoJSON shape must be a Polygon or MultiPolygon")
    else:
        polygon = list(polygon)
        if polygon and _is_point(polygon[0]):
            rings = [polygon]
        else:
            rings = [list(r) for r in polygon]
    out = []
    for ring in rings:
        pts = []
        for p in ring:
            if not _is_point(p):
                raise ZoneError(f"a polygon point must be (lat, lon), got {p!r}")
            lat, lon = float(p[0]), float(p[1])
            if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ZoneError(f"not a (lat, lon) point: {p!r}")
            pts.append((to_e7(lat), to_e7(lon)))
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        if len(pts) < 4:
            raise ZoneError("a polygon ring needs at least 3 distinct points")
        out.append(pts)
    if not out:
        raise ZoneError("the polygon is empty")
    return out


def _is_point(p):
    return isinstance(p, (list, tuple)) and len(p) == 2 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)


def point_in_rings(lat_e7, lon_e7, rings):
    """True if the point is inside the rings (even-odd rule), in exact integer arithmetic.

    A ray goes east from the point; an edge counts when it straddles the point's latitude
    (one end strictly north, y1 > lat, the other not) and crosses the ray east of the point.
    """
    inside = False
    for ring in rings:
        for (y1, x1), (y2, x2) in zip(ring, ring[1:]):
            if (y1 > lat_e7) != (y2 > lat_e7):
                # crossing longitude x = x1 + (lat - y1) (x2 - x1) / (y2 - y1); count it if lon < x
                lhs = (lon_e7 - x1) * (y2 - y1)
                rhs = (lat_e7 - y1) * (x2 - x1)
                if (lhs < rhs) if y2 > y1 else (lhs > rhs):
                    inside = not inside
    return inside


class _Edges:
    """A polygon's edges, indexed by latitude band, for scanning one latitude at a time."""

    def __init__(self, rings):
        self.bands = defaultdict(list)
        for ring in rings:
            for (y1, x1), (y2, x2) in zip(ring, ring[1:]):
                if y1 == y2:
                    continue
                for b in range(min(y1, y2) // _BAND, max(y1, y2) // _BAND + 1):
                    self.bands[b].append((y1, x1, y2, x2))

    def thresholds(self, lat):
        """Sorted integers T: an integer longitude x is inside the polygon at latitude `lat` exactly
        when an odd number of T are greater than x (the same test as point_in_rings)."""
        out = []
        for y1, x1, y2, x2 in self.bands.get(lat // _BAND, ()):
            if (y1 > lat) != (y2 > lat):
                # x < x1 + (lat - y1)(x2 - x1)/(y2 - y1)  <=>  x < x1 + ceil(that fraction), for integer x
                num, den = (lat - y1) * (x2 - x1), y2 - y1
                if den < 0:
                    num, den = -num, -den
                out.append(x1 - (-num // den))
        out.sort()
        return out


def polygon_masks(layout, rings):
    """{cell ID: mask} of the sub-cells whose centroid is inside the rings.

    Scans the polygon one row of sub-cells at a time: the polygon's crossings of the row's
    centroid latitude give the inside stretches, and the sub-cells whose centroid longitude falls
    in them are found by arithmetic. The result equals testing every sub-cell centroid with
    point_in_rings, but costs time in proportion to rows and stretches, not area.
    """
    ys = [y for r in rings for y, _ in r]
    south, north = min(ys), max(ys)
    edges = _Edges(rings)
    masks = defaultdict(int)
    count = 0
    for ql in layout.quadrants:
        lats = ql.lat_e7
        if lats[0] > north or lats[-1] < south:
            continue
        n5 = len(lats) - 1
        i0 = max(bisect_right(lats, south) - 2, 0)
        i1 = min(bisect_right(lats, north), n5 - 1)
        for i in range(i0, i1 + 1):
            lat = to_e7(geodesy.band_centroid_lat(ql.fine_lat(i), ql.fine_lat(i + 1)))
            if not south <= lat <= north:
                continue
            t = edges.thresholds(lat)
            if not t:
                continue
            row, sr = divmod(i, SUB)
            m5 = SUB * ql.ncols[row]
            k = len(t)
            # inside where an odd number of thresholds lie above x: x in [t[a-1], t[a]) with k - a odd
            for a in range(k - 1, 0, -2):
                lo, hi = t[a - 1], t[a]
                j0 = _first_col_at_or_east(ql, row, m5, lo)
                j1 = _first_col_at_or_east(ql, row, m5, hi)
                if j1 <= j0:
                    continue
                count += j1 - j0
                if count > MAX_ZONE_SUB_CELLS:
                    raise ZoneError(f"the zone would have more than {MAX_ZONE_SUB_CELLS:,} sub-cells")
                _set_run(masks, ql.q, row, sr, j0, j1)
    return dict(masks)


def _centroid_lon(ql, row, j):
    return to_e7((ql.fine_lon(row, j) + ql.fine_lon(row, j + 1)) / 2)


def _first_col_at_or_east(ql, row, m5, x):
    """The first sub-cell column j whose centroid longitude is >= x (m5 if none)."""
    w, e = ql.west_e7, ql.east_e7
    j = min(max(int((x - w) * m5 // (e - w)), 0), m5)
    while j > 0 and _centroid_lon(ql, row, j - 1) >= x:
        j -= 1
    while j < m5 and _centroid_lon(ql, row, j) < x:
        j += 1
    return j


def _set_run(masks, q, row, sr, j0, j1):
    """Set the bits of sub-cell columns j0..j1-1 of sub-row sr in row `row` of quadrant q."""
    while j0 < j1:
        col, sc = divmod(j0, SUB)
        end = min(j1, (col + 1) * SUB)
        bits = ((1 << (end - j0)) - 1) << (sr * SUB + sc)
        masks[ids.cell_id(q, row, col)] |= bits
        j0 = end


# -- Zones ----------------------------------------------------------------------------------

def _rows_of(mask):
    return [k for k in range(SUB * SUB) if mask >> k & 1]


@dataclass(frozen=True, eq=False)
class Zone:
    """A set of grid sub-cells (see the module docstring). Immutable; combine with | & -."""
    masks: dict                 # {cell ID: 25-bit mask of its sub-cells in the zone}; no zero masks

    def __post_init__(self):
        object.__setattr__(self, "masks", {c: m for c, m in self.masks.items() if m})

    # -- Checks ----------------------------------------------------------------------

    def contains(self, lat, lon):
        """True if the pin is in the zone: the sub-cell containing it is. Points outside the grid
        rectangle are in no zone."""
        if self._layout is None:
            raise ZoneError("this zone has no grid layout; make it with Grid.zone or Zone.from_dict(..., grid)")
        try:
            q, row, col, sr, sc = self._layout.locate_e7(to_e7(lat), to_e7(lon))
        except OutsideGridError:
            return False
        return bool(self.masks.get(ids.cell_id(q, row, col), 0) >> (sr * SUB + sc) & 1)

    def __contains__(self, sub_cell_id):
        """`sub_cell_id in zone`."""
        cid, sub = ids.split_sub_cell_id(sub_cell_id)
        return bool(self.masks.get(cid, 0) >> sub & 1)

    def covers_cell(self, cell_id):
        """'all', 'part' or 'none': how much of a cell the zone covers."""
        m = self.masks.get(cell_id, 0)
        return "all" if m == FULL else "part" if m else "none"

    # -- Contents --------------------------------------------------------------------

    @property
    def sub_cell_count(self):
        return sum(bin(m).count("1") for m in self.masks.values())

    @property
    def cell_count(self):
        """Cells with at least one sub-cell in the zone."""
        return len(self.masks)

    @property
    def empty(self):
        return not self.masks

    def cells(self, full_only=False):
        """Cell IDs with any (or, with full_only, all 25) of their sub-cells in the zone, in order."""
        return sorted(c for c, m in self.masks.items() if not full_only or m == FULL)

    def sub_cells(self):
        """Sub-cell IDs in the zone, in order."""
        out = []
        for c in sorted(self.masks):
            m = self.masks[c]
            base = ids.sub_cell_id(c, 0)
            if m == FULL:
                out.extend(range(base, base + SUB * SUB))
            else:
                out.extend(base + k for k in _rows_of(m))
        return out

    @property
    def area_km2(self):
        """The zone's area on the ellipsoid, in km² (sub-cells of one row have the same area)."""
        from .layout import GridLayout
        layout = self._layout or GridLayout.from_spec()
        per_row = defaultdict(int)
        for c, m in self.masks.items():
            q, row, col = ids.split_cell_id(c)
            for sr in range(SUB):
                n = bin(m >> (sr * SUB) & 0x1F).count("1")
                if n:
                    per_row[(q, row, sr, layout.quadrants[q].ncols[row])] += n
        total = 0.0
        for (q, row, sr, ncols), n in per_row.items():
            ql = layout.quadrants[q]
            i = row * SUB + sr
            width = (ql.east - ql.west) / (SUB * ncols)
            total += n * geodesy.rect_area(ql.fine_lat(i), ql.fine_lat(i + 1), 0.0, width)
        return total / 1e6

    def bounds(self):
        """(south, west, north, east) in degrees of the zone's cells, or None if empty."""
        if self.empty:
            return None
        from .layout import GridLayout
        layout = self._layout or GridLayout.from_spec()
        box = None
        for c in self.masks:
            q, row, col = ids.split_cell_id(c)
            s, w, n, e = (v / SCALE for v in layout.quadrants[q].item_bounds_e7(row, col, "cell"))
            box = (s, w, n, e) if box is None else (min(box[0], s), min(box[1], w), max(box[2], n), max(box[3], e))
        return box

    # -- Combining ---------------------------------------------------------------------

    def __or__(self, other):
        out = dict(self.masks)
        for c, m in other.masks.items():
            out[c] = out.get(c, 0) | m
        return self._new(out, other)

    def __and__(self, other):
        return self._new({c: m & other.masks[c] for c, m in self.masks.items() if c in other.masks}, other)

    def __sub__(self, other):
        return self._new({c: m & ~other.masks.get(c, 0) & FULL for c, m in self.masks.items()}, other)

    def __eq__(self, other):
        return isinstance(other, Zone) and self.masks == other.masks

    def __hash__(self):
        return hash(frozenset(self.masks.items()))

    def __len__(self):
        return self.sub_cell_count

    def __repr__(self):
        return f"Zone({self.sub_cell_count:,} sub-cells in {self.cell_count:,} cells)"

    def _new(self, masks, other=None):
        z = Zone(masks)
        object.__setattr__(z, "_layout", self._layout or getattr(other, "_layout", None))
        return z

    # -- Storing ---------------------------------------------------------------------------

    def to_dict(self):
        """JSON-ready: {"zone_version", "grid_version", "full": [cell IDs], "partial": [[cell ID, mask]]},
        both in ID order, so the same zone always gives the same data."""
        return {"zone_version": ZONE_VERSION, "grid_version": spec.GRID_VERSION,
                "sub_cells": self.sub_cell_count,
                "full": sorted(c for c, m in self.masks.items() if m == FULL),
                "partial": [[c, self.masks[c]] for c in sorted(self.masks) if self.masks[c] != FULL]}

    @classmethod
    def from_dict(cls, data, grid=None):
        """A zone from to_dict() data; pass the Grid (or its layout) to check the cells and to use
        contains()."""
        if data.get("zone_version") != ZONE_VERSION or data.get("grid_version") != spec.GRID_VERSION:
            raise ZoneError(f"not zone version {ZONE_VERSION} data for grid version {spec.GRID_VERSION}")
        layout = getattr(grid, "layout", grid)
        masks = {}
        for c in data.get("full", []):
            masks[c] = FULL
        for c, m in data.get("partial", []):
            if not isinstance(m, int) or not 0 < m < FULL:
                raise ZoneError(f"bad sub-cell mask {m!r} for cell {c}")
            masks[c] = m
        if layout is not None:
            for c in masks:
                layout.item_from_id(c, "cell")
        z = cls(masks)
        object.__setattr__(z, "_layout", layout)
        return z

    def to_geojson(self, properties=None):
        """The zone as a GeoJSON Feature: one outline (Polygon or MultiPolygon, with holes) of its
        sub-cells, with sub_cells and area_km2 among the properties (see pakgrid.geojson)."""
        from .geojson import feature, geometry
        from .layout import GridLayout
        props = {"sub_cells": self.sub_cell_count, "area_km2": round(self.area_km2, 6)}
        props.update(properties or {})
        return feature(geometry(self._layout or GridLayout.from_spec(), self.masks), props)

    def codes(self, full_cells=True):
        """Readable codes: whole cells as cell codes (with full_cells) and the rest as sub-cell codes."""
        out = []
        for c in sorted(self.masks):
            m = self.masks[c]
            if full_cells and m == FULL:
                out.append(ids.cell_code(c))
            else:
                base = ids.sub_cell_id(c, 0)
                out.extend(ids.sub_cell_code(base + k) for k in _rows_of(m))
        return out


Zone._layout = None        # zones made without a layout: contains() needs one


def make_zone(masks, layout):
    z = Zone(masks)
    object.__setattr__(z, "_layout", layout)
    return z


class ZoneIndex:
    """Many zones at once (e.g. every shop's delivery area): which zones contain a pin?

        index = ZoneIndex({"shop-42": zone_a, "shop-77": zone_b})
        index.at(31.5204, 74.3587)        # ['shop-42']
    """

    def __init__(self, zones, layout=None):
        self._by_cell = defaultdict(list)
        self._layout = layout
        self.names = []
        for name, zone in zones.items():
            self.names.append(name)
            self._layout = self._layout or zone._layout
            for c, m in zone.masks.items():
                self._by_cell[c].append((name, m))
        if self._layout is None:
            raise ZoneError("the zones have no grid layout; pass layout=grid.layout")

    def at(self, lat, lon):
        """Names of the zones containing the pin, in the order the zones were given."""
        try:
            q, row, col, sr, sc = self._layout.locate_e7(to_e7(lat), to_e7(lon))
        except OutsideGridError:
            return []
        bit = sr * SUB + sc
        return [name for name, m in self._by_cell.get(ids.cell_id(q, row, col), ()) if m >> bit & 1]

    def at_sub_cell(self, sub_cell_id):
        cid, sub = ids.split_sub_cell_id(sub_cell_id)
        return [name for name, m in self._by_cell.get(cid, ()) if m >> sub & 1]
