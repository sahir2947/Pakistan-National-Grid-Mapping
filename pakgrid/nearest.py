"""Nearest lookup: the nearest shops, riders or places to a pin.

Two ways, for two kinds of data:

- **PointIndex**: points kept in memory, bucketed by grid cell: riders whose positions change
  every few seconds, or a list of shops loaded once. Add, move and remove points at any time.

      riders = grid.point_index()
      riders.add("rider-7", 31.5210, 74.3570)        # again to move it
      riders.nearest(31.5204, 74.3587, k=3)          # [Neighbour(id='rider-7', distance_m=174.6, ...)]

- **Grid.nearest(lat, lon, fetch)**: points stored in your own database with their cell ID
  (e.g. a Shop model with an indexed cell_id column). The grid asks `fetch` for the points in
  a few cells at a time, nearest cells first, so the database only reads the cells around the pin:

      def fetch(cell_ids):
          return Shop.objects.filter(cell_id__in=cell_ids).values_list("id", "lat", "lon")
      grid.nearest(31.5204, 74.3587, fetch, k=5)

How the search works: it looks in the cells within a radius of the pin, starting at 1 km and
doubling, until the k-th nearest point found is nearer than any point in a cell not yet looked in
could be. The answer is therefore exact: the k nearest points by straight-line distance (as
direction() measures it), nearest first, ties in the order the points were added (or fetched).
max_m limits how far to look (at most MAX_NEAREST_M); fewer than k come back if there are not
enough within it. `where` keeps only the points it accepts (open shops, free riders).
"""

import math
import threading
from dataclasses import dataclass

from . import geodesy, ids, navigate
from .errors import OutsideGridError
from .layout import SCALE, to_e7

MAX_NEAREST_M = 49_000.0        # the furthest a search looks (Grid.cells_within reaches 50 km)
_FIRST_RADIUS_M = 1_000.0
# Cells are reached by their distance from the pin at the pin's latitude; points are measured as
# direction() does, at the mid-latitude. Within 50 km the two scales differ by under 1%, so a point
# in a cell not yet searched is at least radius / _MARGIN away.
_MARGIN = 1.01


@dataclass(frozen=True)
class Neighbour:
    """A point found by a nearest search."""
    id: object                  # the point's ID (PointIndex) or the item fetch returned (Grid.nearest)
    lat: float
    lon: float
    distance_m: float           # straight line from the pin, as direction() measures it
    bearing_deg: float          # compass bearing from the pin to the point

    @property
    def direction(self):
        """16-point compass direction from the pin, e.g. 'NE'."""
        return navigate._COMPASS_16[round(self.bearing_deg / 22.5) % 16]

    @property
    def text(self):
        """'170 m WNW'"""
        return f"{navigate.format_distance(self.distance_m)} {self.direction}"

    def to_dict(self):
        return {"id": self.id, "lat": self.lat, "lon": self.lon, "distance_m": round(self.distance_m, 1),
                "bearing_deg": round(self.bearing_deg, 1), "direction": self.direction, "text": self.text}


def _check_point(lat, lon):
    for name, v in (("lat", lat), ("lon", lon)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            raise ValueError(f"{name} must be a finite number, got {v!r}")


def _check_query(k, max_m):
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be a whole number of at least 1, got {k!r}")
    if max_m is None:
        return MAX_NEAREST_M
    if isinstance(max_m, bool) or not isinstance(max_m, (int, float)) or not 0 <= max_m <= MAX_NEAREST_M:
        raise ValueError(f"max_m must be a number from 0 to {MAX_NEAREST_M:g}, got {max_m!r}")
    return float(max_m)


def _cells_near(layout, lat, lon, r):
    """IDs of the cells with any part within r metres of the pin: the cells
    Grid.cells_within(..., touching=True) finds, without working out their centroids."""
    lat, lon = to_e7(lat) / SCALE, to_e7(lon) / SCALE
    m_lat, m_lon = geodesy.metres_per_degree(lat)
    pad = 1.02 * r + 1500
    out = []
    for q, i, j in layout.items_in_area(to_e7(lat - pad / m_lat), to_e7(lon - pad / (m_lon * 0.98)),
                                        to_e7(lat + pad / m_lat), to_e7(lon + pad / (m_lon * 0.98)), "cell"):
        s, w, n, e = (v / SCALE for v in layout.quadrants[q].item_bounds_e7(i, j, "cell"))
        if math.hypot(max(w - lon, 0.0, lon - e) * m_lon, max(s - lat, 0.0, lat - n) * m_lat) <= r:
            out.append(ids.cell_id(q, i, j))
    return out


def search(grid, lat, lon, k, max_m, points_in, where=None):
    """The k nearest points to (lat, lon) within max_m, nearest first.

    points_in(cell_ids) returns [(seq, id, lat, lon)] for those cells; seq orders ties.
    """
    _check_point(lat, lon)
    max_m = _check_query(k, max_m)
    seen, found = set(), []                   # found: (distance, seq, Neighbour)
    r = min(_FIRST_RADIUS_M, max_m * _MARGIN)
    while True:
        new = [c for c in _cells_near(grid.layout, lat, lon, r) if c not in seen]
        seen.update(new)
        if new:
            for seq, pid, plat, plon in points_in(new):
                way = navigate.direction(lat, lon, plat, plon)
                if way.distance_m <= max_m and (where is None or where(pid)):
                    found.append((way.distance_m, seq, Neighbour(pid, plat, plon, way.distance_m, way.bearing_deg)))
            found.sort(key=lambda f: (f[0], f[1]))
            del found[k:]
        sure = r / _MARGIN                     # every point within this distance has been looked at
        if (len(found) == k and found[-1][0] <= sure) or sure >= max_m:
            return [f[2] for f in found]
        r = min(r * 2, max_m * _MARGIN)


class PointIndex:
    """Points in memory, bucketed by grid cell, for fast nearest searches (see the module docstring).

    Safe to share between threads: changes and searches take a lock.
    """

    def __init__(self, grid):
        self.grid = grid
        self._points = {}                      # id -> (seq, lat, lon, cell ID)
        self._cells = {}                       # cell ID -> {id, ...}
        self._seq = 0
        self._lock = threading.RLock()

    def add(self, point_id, lat, lon):
        """Add a point, or move it if its ID is already in the index."""
        _check_point(lat, lon)
        try:
            q, row, col, _, _ = self.grid.layout.locate_e7(to_e7(lat), to_e7(lon))
        except OutsideGridError:
            raise ValueError(f"({lat}, {lon}) is outside the grid") from None
        cid = ids.cell_id(q, row, col)
        with self._lock:
            old = self._points.get(point_id)
            if old is not None:
                self._cells[old[3]].discard(point_id)
                seq = old[0]
            else:
                self._seq += 1
                seq = self._seq
            self._points[point_id] = (seq, float(lat), float(lon), cid)
            self._cells.setdefault(cid, set()).add(point_id)

    def add_many(self, points):
        """Add or move many points: an iterable of (id, lat, lon)."""
        for pid, lat, lon in points:
            self.add(pid, lat, lon)

    def remove(self, point_id):
        """Remove a point; a missing ID is ignored."""
        with self._lock:
            old = self._points.pop(point_id, None)
            if old is not None:
                cell = self._cells[old[3]]
                cell.discard(point_id)
                if not cell:
                    del self._cells[old[3]]

    def get(self, point_id):
        """(lat, lon) of a point, or None."""
        p = self._points.get(point_id)
        return (p[1], p[2]) if p else None

    def __len__(self):
        return len(self._points)

    def __contains__(self, point_id):
        return point_id in self._points

    def _points_in(self, cell_ids):
        out = []
        for c in cell_ids:
            for pid in self._cells.get(c, ()):
                seq, lat, lon, _ = self._points[pid]
                out.append((seq, pid, lat, lon))
        return out

    def nearest(self, lat, lon, k=5, max_m=None, where=None):
        """The k nearest points to the pin, nearest first: [Neighbour]."""
        with self._lock:
            return search(self.grid, lat, lon, k, max_m, self._points_in, where)

    def within(self, lat, lon, radius_m, where=None):
        """Every point within radius_m of the pin, nearest first."""
        with self._lock:
            return search(self.grid, lat, lon, max(len(self._points), 1), radius_m, self._points_in, where)
