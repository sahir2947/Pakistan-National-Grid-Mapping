"""The shape of the grid: rows, column counts and edges, plus topology.

Everything is expressed on the *fine* grid of sub-cell lines. Within a
quadrant, fine latitude line `i` (0 .. 5 * rows) and, in cell row `r`, fine
longitude line `j` (0 .. 5 * columns) are

    lat(i) = south + (north - south) * i / (5 * rows)
    lon(r, j) = west + (east - west) * j / (5 * columns[r])

with the last line pinned to the quadrant's edge. Cell edges are every fifth
fine line. Edges are compared as integers in units of 1e-7 degree, so a
point always falls in exactly one cell and one sub-cell.

`level` arguments are "cell" or "sub". At either level an item is addressed
as (quadrant, i, j): its row and column counted at that level within the
quadrant.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass, field

from . import geodesy, ids, spec
from .errors import InvalidIdError, OutsideGridError

SCALE = spec.COORD_SCALE
SUB = spec.SUBDIVISIONS
LEVELS = ("cell", "sub")


def to_e7(deg):
    """Degrees -> integer units of 1e-7 degree."""
    return round(deg * SCALE)


SOUTH_E7, NORTH_E7 = to_e7(spec.SOUTH), to_e7(spec.NORTH)
WEST_E7, EAST_E7 = to_e7(spec.WEST), to_e7(spec.EAST)
MID_LAT_E7, MID_LON_E7 = to_e7(spec.MID_LAT), to_e7(spec.MID_LON)


def _unit(level):
    if level == "cell":
        return SUB
    if level == "sub":
        return 1
    raise ValueError(f"level must be 'cell' or 'sub', got {level!r}")


@dataclass(frozen=True)
class QuadrantLayout:
    q: int
    south: float
    north: float
    west: float
    east: float
    nrows: int
    ncols: tuple
    lat_e7: tuple = field(repr=False)  # fine latitude lines, 5 * nrows + 1 values
    west_e7: int = field(repr=False)
    east_e7: int = field(repr=False)

    @classmethod
    def build(cls, q, nrows, ncols):
        south, north, west, east = spec.quadrant_bounds(q)
        n5 = SUB * nrows
        lats = tuple(to_e7(north if i == n5 else south + (north - south) * i / n5)
                     for i in range(n5 + 1))
        return cls(q, south, north, west, east, nrows, tuple(ncols), lats, to_e7(west), to_e7(east))

    @property
    def code(self):
        return spec.QUADRANTS[self.q][0]

    @property
    def name(self):
        return spec.QUADRANTS[self.q][1]

    @property
    def ncells(self):
        return sum(self.ncols)

    def fine_lat(self, i):
        n5 = SUB * self.nrows
        return self.north if i == n5 else self.south + (self.north - self.south) * i / n5

    def fine_lon(self, row, j):
        m5 = SUB * self.ncols[row]
        return self.east if j == m5 else self.west + (self.east - self.west) * j / m5

    def fine_lon_e7(self, row, j):
        return to_e7(self.fine_lon(row, j))

    def level_rows(self, level):
        return self.nrows * SUB // _unit(level)

    def level_cols(self, i, level):
        u = _unit(level)
        return self.ncols[i * u // SUB] * SUB // u

    def item_bounds_e7(self, i, j, level):
        """(south, west, north, east) of item (i, j) in units of 1e-7 degree."""
        u = _unit(level)
        row = i * u // SUB
        return (self.lat_e7[i * u], self.fine_lon_e7(row, j * u),
                self.lat_e7[(i + 1) * u], self.fine_lon_e7(row, (j + 1) * u))

    def item_centroid_e7(self, i, j, level):
        """(lat, lon) of item (i, j)'s area-weighted centroid, in 1e-7 degree.

        The same formulas the builder stores, so no database is needed to get them.
        """
        u = _unit(level)
        row = i * u // SUB
        lat = geodesy.band_centroid_lat(self.fine_lat(i * u), self.fine_lat((i + 1) * u))
        return to_e7(lat), to_e7((self.fine_lon(row, j * u) + self.fine_lon(row, (j + 1) * u)) / 2)


class GridLayout:
    """Row and column structure of all four quadrants."""

    def __init__(self, quadrants):
        self.quadrants = tuple(sorted(quadrants, key=lambda ql: ql.q))
        if [ql.q for ql in self.quadrants] != [0, 1, 2, 3]:
            raise ValueError("a layout needs exactly quadrants 0-3")

    @classmethod
    def from_spec(cls):
        """Compute the layout from the grid rules."""
        return cls(QuadrantLayout.build(q, *compute_quadrant_rows(q)) for q in spec.QUADRANTS)

    @property
    def total_cells(self):
        return sum(ql.ncells for ql in self.quadrants)

    @property
    def total_sub_cells(self):
        return self.total_cells * SUB * SUB

    def same_shape(self, other):
        return all(a.nrows == b.nrows and a.ncols == b.ncols
                   for a, b in zip(self.quadrants, other.quadrants))

    # -- IDs -------------------------------------------------------------

    def check_cell(self, q, row, col):
        ql = self.quadrants[q]
        if row >= ql.nrows or col >= ql.ncols[row]:
            raise InvalidIdError(
                f"cell {q:02b}-{row:04d}-{col:04d} does not exist: quadrant {ql.code} has "
                f"{ql.nrows} rows" + (f" and row {row} has {ql.ncols[row]} columns" if row < ql.nrows else ""))

    def item_from_id(self, item_id, level):
        """(q, i, j) of a cell or sub-cell ID, checked against the layout."""
        if level == "cell":
            q, row, col = ids.split_cell_id(item_id)
            self.check_cell(q, row, col)
            return q, row, col
        cid, sub = ids.split_sub_cell_id(item_id)
        q, row, col = ids.split_cell_id(cid)
        self.check_cell(q, row, col)
        sr, sc = ids.split_sub_index(sub)
        return q, row * SUB + sr, col * SUB + sc

    @staticmethod
    def item_id(q, i, j, level):
        if level == "cell":
            return ids.cell_id(q, i, j)
        return ids.sub_cell_id(ids.cell_id(q, i // SUB, j // SUB),
                               ids.sub_index(i % SUB, j % SUB))

    # -- Point location --------------------------------------------------

    def locate_e7(self, lat_e7, lon_e7):
        """(quadrant, row, column, sub_row, sub_col) containing a point given in 1e-7 degree.

        Edge rule: a point on a line belongs to the item north or east of it;
        points on the outer north and east edges belong to the last row or column.
        """
        if not (SOUTH_E7 <= lat_e7 <= NORTH_E7 and WEST_E7 <= lon_e7 <= EAST_E7):
            raise OutsideGridError(
                f"point ({lat_e7 / SCALE}, {lon_e7 / SCALE}) is outside the grid "
                f"({spec.SOUTH}-{spec.NORTH} N, {spec.WEST}-{spec.EAST} E)")
        q = ((lon_e7 >= MID_LON_E7) << 1) | (lat_e7 >= MID_LAT_E7)
        ql = self.quadrants[q]

        lats = ql.lat_e7
        n5 = len(lats) - 1
        i = min(max(bisect_right(lats, lat_e7) - 1, 0), n5 - 1)
        row = i // SUB

        m5 = SUB * ql.ncols[row]
        west, east = ql.west_e7, ql.east_e7
        j = min(max((lon_e7 - west) * m5 // (east - west), 0), m5 - 1)
        while j > 0 and lon_e7 < ql.fine_lon_e7(row, j):
            j -= 1
        while j < m5 - 1 and lon_e7 >= ql.fine_lon_e7(row, j + 1):
            j += 1
        return q, row, j // SUB, i % SUB, j % SUB

    # -- Topology --------------------------------------------------------

    def _row_after(self, q, i, step, level):
        """The level row next to row i of quadrant q, as (half, i), or None at the outer edge."""
        half = q & 1
        ni = i + step
        if 0 <= ni < self.quadrants[q].level_rows(level):
            return half, ni
        if step > 0 and half == 0:
            return 1, 0
        if step < 0 and half == 1:
            return 0, self.quadrants[0].level_rows(level) - 1
        return None

    def _cols_touching(self, q, i, level, w_e7, e_e7, strict_west=False):
        """Columns j of level row i in quadrant q whose span touches [w_e7, e_e7].

        With strict_west, an item that only ends exactly at w_e7 is excluded
        (used for half-open items against a closed query box).
        """
        ql = self.quadrants[q]
        if e_e7 < ql.west_e7 or w_e7 > ql.east_e7:
            return []
        m = ql.level_cols(i, level)
        span = ql.east_e7 - ql.west_e7
        j0 = max((w_e7 - ql.west_e7) * m // span - 1, 0)
        j1 = min((e_e7 - ql.west_e7) * m // span + 1, m - 1)
        out = []
        for j in range(j0, j1 + 1):
            _, a, _, b = ql.item_bounds_e7(i, j, level)
            if a <= e_e7 and (b > w_e7 if strict_west else b >= w_e7):
                out.append(j)
        return out

    def neighbours(self, q, i, j, level):
        """Items sharing an edge or a corner with (q, i, j), across quadrant lines too."""
        ql = self.quadrants[q]
        found = []
        if j > 0:
            found.append((q, i, j - 1))
        elif q >= 2:
            west_q = q - 2
            found.append((west_q, i, self.quadrants[west_q].level_cols(i, level) - 1))
        if j < ql.level_cols(i, level) - 1:
            found.append((q, i, j + 1))
        elif q < 2:
            found.append((q + 2, i, 0))

        _, w, _, e = ql.item_bounds_e7(i, j, level)
        for step in (-1, 1):
            nxt = self._row_after(q, i, step, level)
            if nxt is None:
                continue
            half, ni = nxt
            for nq in (half, half + 2):
                found.extend((nq, ni, nj) for nj in self._cols_touching(nq, ni, level, w, e))
        return found

    def items_in_area(self, south_e7, west_e7, north_e7, east_e7, level):
        """All items whose area overlaps the closed box, in ID order."""
        south_e7, north_e7 = max(south_e7, SOUTH_E7), min(north_e7, NORTH_E7)
        west_e7, east_e7 = max(west_e7, WEST_E7), min(east_e7, EAST_E7)
        if south_e7 > north_e7 or west_e7 > east_e7:
            return []
        u = _unit(level)
        out = []
        for ql in self.quadrants:
            lines = ql.lat_e7
            if ql.west_e7 > east_e7 or ql.east_e7 < west_e7 or lines[0] > north_e7 or lines[-1] < south_e7:
                continue
            if south_e7 == lines[-1] and lines[-1] != NORTH_E7:
                continue  # the box only touches the shared edge with the quadrant to the north
            last = len(lines) - 2
            first_row = min(max(bisect_right(lines, south_e7) - 1, 0), last) // u
            last_row = min(max(bisect_right(lines, north_e7) - 1, 0), last) // u
            for i in range(first_row, last_row + 1):
                cols = self._cols_touching(ql.q, i, level, west_e7, east_e7, strict_west=True)
                if not cols and ql.east == spec.EAST and west_e7 == ql.east_e7:
                    cols = [ql.level_cols(i, level) - 1]
                out.extend((ql.q, i, j) for j in cols)
        return out


def compute_quadrant_rows(q):
    """(number of rows, column count per row) of quadrant q, from the grid rules."""
    south, north, west, east = spec.quadrant_bounds(q)
    nrows = round(geodesy.meridian_arc(south, north) / spec.CELL_SIZE_M)
    height = (north - south) / nrows
    ncols = []
    for row in range(nrows):
        mid = south + (row + 0.5) * height
        width = geodesy.parallel_length(mid, west, east)
        ncols.append(round(width / spec.CELL_SIZE_M))
    return nrows, ncols


def rounding_margins(q):
    """How far each row/column count is from a rounding boundary (0.5 = safest).

    Used by tests to prove the layout cannot change through last-digit
    floating-point differences between platforms.
    """
    south, north, west, east = spec.quadrant_bounds(q)
    raw_rows = geodesy.meridian_arc(south, north) / spec.CELL_SIZE_M
    nrows = round(raw_rows)
    height = (north - south) / nrows
    margins = [abs(abs(raw_rows - math.floor(raw_rows)) - 0.5)]
    for row in range(nrows):
        raw = geodesy.parallel_length(south + (row + 0.5) * height, west, east) / spec.CELL_SIZE_M
        margins.append(abs((raw - math.floor(raw)) - 0.5))
    return margins
