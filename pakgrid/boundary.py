"""Pakistan's border, and inside / border / outside flags for grid blocks.

The boundary is a set of rings (outer edges and islands, no holes in the
current data) with even-odd inside rule. Vertices are rounded to 1e-7 degree,
the grid's own precision, so the flags are reproducible on any platform.

A block is
    INSIDE   if it lies wholly inside Pakistan,
    OUTSIDE  if it lies wholly outside,
    BORDER   if the boundary line touches it.
"""

import functools
import gzip
import hashlib
import json
import struct
from bisect import bisect_left, bisect_right
from importlib import resources

from .layout import SCALE

OUTSIDE, BORDER, INSIDE = 0, 1, 2
FLAG_NAMES = ("outside", "border", "inside")

DEFAULT_FILE = "geoBoundaries-PAK-ADM0.geojson.gz"
DEFAULT_NAME = "geoBoundaries gbOpen PAK-ADM0-80972501 (OpenStreetMap)"
DEFAULT_LICENSE = "ODbL-1.0"
DEFAULT_ATTRIBUTION = ("Boundary data © OpenStreetMap contributors, available under the ODbL, "
                       "via geoBoundaries (www.geoboundaries.org)")

_BAND = 100_000  # edge index band height, in 1e-7 degree (0.01 degree)


class Boundary:
    """A boundary given as rings of (lon_e7, lat_e7) integer vertices."""

    def __init__(self, rings, name="", license="", attribution=""):
        self.rings = [tuple(r) for r in rings if len(r) >= 4]
        self.name, self.license, self.attribution = name, license, attribution
        x1, y1, x2, y2 = [], [], [], []
        self._bands = {}
        for ring in self.rings:
            for (ax, ay), (bx, by) in zip(ring, ring[1:]):
                if (ax, ay) == (bx, by):
                    continue
                k = len(x1)
                x1.append(ax / SCALE); y1.append(ay / SCALE)
                x2.append(bx / SCALE); y2.append(by / SCALE)
                # a 1-unit margin keeps float rounding of query latitudes from missing an edge
                for b in range((min(ay, by) - 1) // _BAND, (max(ay, by) + 1) // _BAND + 1):
                    self._bands.setdefault(b, []).append(k)
        self._x1, self._y1, self._x2, self._y2 = x1, y1, x2, y2
        lats = [v[1] for r in self.rings for v in r]
        self.south_e7, self.north_e7 = (min(lats), max(lats)) if lats else (0, -1)

    # -- Loading and storing -----------------------------------------------

    @classmethod
    def default(cls):
        """The boundary packaged with pakgrid (see pakgrid/data/BOUNDARY_SOURCE.md)."""
        return _default_boundary()

    @classmethod
    def from_geojson(cls, obj, name="", license="", attribution=""):
        """From a GeoJSON FeatureCollection, Feature or (Multi)Polygon geometry."""
        geoms = []
        if obj.get("type") == "FeatureCollection":
            geoms = [f["geometry"] for f in obj["features"]]
        elif obj.get("type") == "Feature":
            geoms = [obj["geometry"]]
        else:
            geoms = [obj]
        rings = []
        for g in geoms:
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            for poly in polys:
                for ring in poly:
                    pts = [(round(x * SCALE), round(y * SCALE)) for x, y, *_ in ring]
                    if pts[0] != pts[-1]:
                        pts.append(pts[0])
                    rings.append(pts)
        return cls(rings, name, license, attribution)

    def packed_rings(self):
        """Rings as bytes: little-endian int32 (lon, lat) pairs, one bytes object per ring."""
        return [struct.pack(f"<{2 * len(r)}i", *(c for v in r for c in v)) for r in self.rings]

    @classmethod
    def from_packed(cls, blobs, name="", license="", attribution=""):
        rings = []
        for blob in blobs:
            vals = struct.unpack(f"<{len(blob) // 4}i", blob)
            rings.append(list(zip(vals[0::2], vals[1::2])))
        return cls(rings, name, license, attribution)

    @property
    def sha256(self):
        h = hashlib.sha256(b"pakgrid-boundary-v1")
        for blob in self.packed_rings():
            h.update(struct.pack("<i", len(blob)))
            h.update(blob)
        return h.hexdigest()

    @property
    def vertex_count(self):
        return sum(len(r) for r in self.rings)

    # -- Queries -------------------------------------------------------------

    def _edges_near(self, south, north):
        seen = set()
        for b in range(int(south * SCALE) // _BAND, int(north * SCALE) // _BAND + 1):
            for k in self._bands.get(b, ()):
                if k not in seen:
                    seen.add(k)
                    yield k

    def contains(self, lat, lon):
        """True if the point is inside Pakistan (even-odd rule, ray towards the east)."""
        inside = False
        x1, y1, x2, y2 = self._x1, self._y1, self._x2, self._y2
        for k in self._bands.get(int(lat * SCALE) // _BAND, ()):
            if (y1[k] <= lat) != (y2[k] <= lat):
                if x1[k] + (lat - y1[k]) * (x2[k] - x1[k]) / (y2[k] - y1[k]) > lon:
                    inside = not inside
        return inside

    def band(self, south, north):
        """What the boundary does inside the latitude band [south, north].

        Returns (touched, crossings):
          touched   - merged longitude intervals where the boundary line lies in the band
          crossings - sorted longitudes where the boundary crosses the band's middle latitude;
                      between crossings 0-1, 2-3, ... the middle latitude is inside
        """
        mid = (south + north) / 2
        x1, y1, x2, y2 = self._x1, self._y1, self._x2, self._y2
        spans, crossings = [], []
        for k in self._edges_near(south, north):
            ya, yb, xa, xb = y1[k], y2[k], x1[k], x2[k]
            lo_y, hi_y = (ya, yb) if ya <= yb else (yb, ya)
            if hi_y < south or lo_y > north:
                continue
            if ya == yb:
                spans.append((min(xa, xb), max(xa, xb)))
            else:
                ys, yn = max(south, lo_y), min(north, hi_y)
                ps = xa + (ys - ya) * (xb - xa) / (yb - ya)
                pn = xa + (yn - ya) * (xb - xa) / (yb - ya)
                spans.append((min(ps, pn), max(ps, pn)))
                if (ya <= mid) != (yb <= mid):
                    crossings.append(xa + (mid - ya) * (xb - xa) / (yb - ya))
        spans.sort()
        touched = []
        for lo, hi in spans:
            if touched and lo <= touched[-1][1]:
                if hi > touched[-1][1]:
                    touched[-1] = (touched[-1][0], hi)
            else:
                touched.append((lo, hi))
        crossings.sort()
        return touched, crossings


@functools.lru_cache(maxsize=1)
def _default_boundary():
    data = resources.files("pakgrid").joinpath("data", DEFAULT_FILE).read_bytes()
    return Boundary.from_geojson(json.loads(gzip.decompress(data)), DEFAULT_NAME, DEFAULT_LICENSE,
                                 DEFAULT_ATTRIBUTION)


def row_flags(touched, crossings, lons, centres):
    """Flags for the blocks of one band, given its block edges `lons` (len n + 1)."""
    n = len(centres)
    flags = bytearray(n)
    for k in range(0, len(crossings) - 1, 2):
        a, b = bisect_left(centres, crossings[k]), bisect_left(centres, crossings[k + 1])
        flags[a:b] = b"\x02" * (b - a)
    for lo, hi in touched:
        a = max(bisect_left(lons, lo) - 1, 0)
        b = min(bisect_right(lons, hi) - 1, n - 1)
        if a <= b:
            flags[a:b + 1] = b"\x01" * (b - a + 1)
    return flags


_ALL_IN = b"\x02" * 5
_ALL_OUT = b"\x00" * 5


def cell_flags(sub_rows, col):
    """(cell flag, inside_mask, border_mask) from the 5 sub-row flag arrays of a cell row."""
    segs = [r[col * 5:col * 5 + 5] for r in sub_rows]
    if all(s == _ALL_IN for s in segs):
        return INSIDE, (1 << 25) - 1, 0
    if all(s == _ALL_OUT for s in segs):
        return OUTSIDE, 0, 0
    inside = border = 0
    for sr, seg in enumerate(segs):
        for sc, f in enumerate(seg):
            bit = 1 << (sr * 5 + sc)
            if f == INSIDE:
                inside |= bit
            elif f == BORDER:
                border |= bit
    return BORDER, inside, border


def sub_flag(sub, inside_mask, border_mask):
    bit = 1 << sub
    return INSIDE if inside_mask & bit else BORDER if border_mask & bit else OUTSIDE
