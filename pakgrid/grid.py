"""The public `Grid` class: lookups against the grid database."""

import math
from bisect import bisect_right
from dataclasses import dataclass

from . import geodesy, ids, navigate, spec
from .boundary import FLAG_NAMES, OUTSIDE, Boundary, sub_flag
from .errors import GridMismatchError, IntegrityError, InvalidIdError, NotInDatabaseError, OutsideGridError
from .layout import SCALE, SUB, GridLayout, QuadrantLayout, to_e7
from .store import SCHEMA_VERSION, GridStore

MAX_CANDIDATE_RADIUS_M = 5000.0


@dataclass(frozen=True)
class Bounds:
    south: float
    west: float
    north: float
    east: float

    @classmethod
    def from_e7(cls, s, w, n, e):
        return cls(s / SCALE, w / SCALE, n / SCALE, e / SCALE)

    def contains(self, lat, lon):
        return self.south <= lat <= self.north and self.west <= lon <= self.east

    def to_dict(self):
        return dict(self.__dict__)


@dataclass(frozen=True)
class Cell:
    id: int
    code: str
    quadrant: str
    row: int
    column: int
    bounds: Bounds
    centroid: tuple
    pakistan: str        # "inside", "border" or "outside"

    def to_dict(self):
        return {"id": self.id, "code": self.code, "quadrant": self.quadrant, "row": self.row,
                "column": self.column, "centroid": list(self.centroid), "bounds": self.bounds.to_dict(),
                "pakistan": self.pakistan}


@dataclass(frozen=True)
class SubCell:
    id: int
    code: str
    cell_id: int
    cell_code: str
    index: int
    bounds: Bounds
    centroid: tuple
    pakistan: str        # "inside", "border" or "outside"

    def to_dict(self):
        return {"id": self.id, "code": self.code, "cell_id": self.cell_id, "cell_code": self.cell_code,
                "index": self.index, "centroid": list(self.centroid), "bounds": self.bounds.to_dict(),
                "pakistan": self.pakistan}


@dataclass(frozen=True)
class Offset:
    """Vector from a centroid to a point."""
    east_m: float
    north_m: float

    @property
    def distance_m(self):
        return math.hypot(self.east_m, self.north_m)

    @property
    def bearing_deg(self):
        return geodesy.bearing(self.east_m, self.north_m)

    @property
    def text(self):
        """Short form such as 'W23 S18' (metres west/east, south/north)."""
        ew = "E" if self.east_m >= 0 else "W"
        ns = "N" if self.north_m >= 0 else "S"
        return f"{ew}{round(abs(self.east_m))} {ns}{round(abs(self.north_m))}"

    def to_dict(self):
        return {"east_m": round(self.east_m, 1), "north_m": round(self.north_m, 1),
                "distance_m": round(self.distance_m, 1), "bearing_deg": round(self.bearing_deg, 1),
                "text": self.text}


@dataclass(frozen=True)
class Location:
    """Result of `Grid.locate`."""
    lat: float
    lon: float
    sub_cell: SubCell
    offset: Offset
    edge_distance_m: float
    accuracy_m: float | None
    confident: bool | None   # None when no accuracy was given
    candidates: tuple        # other sub-cell IDs within the accuracy radius, nearest first
    in_pakistan: bool        # the point itself, checked against the border line in border blocks

    @property
    def id(self):
        return self.sub_cell.id

    @property
    def code(self):
        return self.sub_cell.code

    @property
    def cell_id(self):
        return self.sub_cell.cell_id

    @property
    def cell_code(self):
        return self.sub_cell.cell_code

    def to_dict(self):
        return {
            "grid_version": spec.GRID_VERSION,
            "point": [self.lat, self.lon],
            "sub_cell": self.sub_cell.to_dict(),
            "cell": {"id": self.cell_id, "code": self.cell_code},
            "offset": self.offset.to_dict(),
            "accuracy_m": self.accuracy_m,
            "confident": self.confident,
            "edge_distance_m": round(self.edge_distance_m, 1),
            "candidates": [ids.sub_cell_code(c) for c in self.candidates],
            "in_pakistan": self.in_pakistan,
        }


class Grid:
    """Lookups against a grid database built by `pakgrid build`.

    Only the small rows table is kept in memory; cells and sub-cells are read
    from the database on demand.
    """

    def __init__(self, store):
        self.store = store
        self.meta = store.meta()
        version = int(self.meta.get("grid_version", -1))
        if version != spec.GRID_VERSION:
            raise GridMismatchError(
                f"database is grid version {version}; this library implements version {spec.GRID_VERSION}")
        if int(self.meta.get("schema_version", 0)) != SCHEMA_VERSION:
            raise GridMismatchError(
                f"database schema version {self.meta.get('schema_version')} is not {SCHEMA_VERSION}; "
                "rebuild it with `pakgrid build`")
        self._boundary = None
        self.layout = _layout_from_store(store)
        if not self.layout.same_shape(GridLayout.from_spec()):
            raise GridMismatchError("database rows and columns do not match the grid rules of this library")

    @classmethod
    def open(cls, path):
        return cls(GridStore(path))

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- Finding a location ---------------------------------------------

    def locate(self, lat, lon, accuracy_m=None):
        """The sub-cell containing a GPS point, with its offset and confidence.

        The point is rounded to 1e-7 degree (~1.1 cm). With accuracy_m (the
        phone's reported accuracy radius), the result says whether the point
        could belong to a neighbouring block and lists those blocks.
        """
        lat_e7, lon_e7 = _point_e7(lat, lon)
        q, row, col, _, _ = self.layout.locate_e7(lat_e7, lon_e7)
        cid = ids.cell_id(q, row, col)
        return self._locate_in(cid, self.store.sub_record(cid), lat, lon, lat_e7, lon_e7, accuracy_m)

    def locate_many(self, points):
        """`locate` for many points; each point is (lat, lon) or (lat, lon, accuracy_m)."""
        return [self.locate(*p) for p in points]

    def in_pakistan(self, lat, lon):
        """True if the point is inside Pakistan's border; False outside it or outside the grid."""
        try:
            return self.locate(lat, lon).in_pakistan
        except OutsideGridError:
            return False
        except NotInDatabaseError:
            if self.store.pakistan_edition:
                return False   # cells wholly outside Pakistan are left out of this edition
            raise

    @property
    def boundary(self):
        """Pakistan's border, as stored in this database (loaded on first use)."""
        if self._boundary is None:
            self._boundary = Boundary.from_packed(
                self.store.boundary_rings(), self.meta.get("boundary_name", ""),
                self.meta.get("boundary_license", ""), self.meta.get("boundary_attribution", ""))
        return self._boundary

    def _locate_in(self, cid, record, lat, lon, lat_e7, lon_e7, accuracy_m):
        lat_lines, lon_lines = record[0], record[1]
        # The stored boundaries have the final say.
        if not (lat_lines[0] <= lat_e7 <= lat_lines[SUB] and lon_lines[0] <= lon_e7 <= lon_lines[SUB]):
            raise IntegrityError(f"cell {ids.cell_code(cid)} in the database does not contain "
                                 f"({lat}, {lon}); the database may be corrupt")
        sr = min(bisect_right(lat_lines, lat_e7) - 1, SUB - 1)
        sc = min(bisect_right(lon_lines, lon_e7) - 1, SUB - 1)
        sub = _sub_cell_from_record(cid, ids.sub_index(sr, sc), record)

        clat, clon = sub.centroid
        offset = Offset(*geodesy.local_offset(clat, clon, lat, lon))
        m_lat, m_lon = geodesy.metres_per_degree(clat)
        b = sub.bounds
        edge = max(0.0, min((lat - b.south) * m_lat, (b.north - lat) * m_lat,
                            (lon - b.west) * m_lon, (b.east - lon) * m_lon))

        confident, candidates = None, ()
        if accuracy_m is not None:
            if not (accuracy_m >= 0 and math.isfinite(accuracy_m)):
                raise ValueError(f"accuracy_m must be a non-negative number, got {accuracy_m!r}")
            confident = edge >= accuracy_m
            if not confident:
                candidates = self._candidates(lat, lon, min(accuracy_m, MAX_CANDIDATE_RADIUS_M), sub.id)

        in_pk = sub.pakistan == "inside" or (sub.pakistan == "border" and self.boundary.contains(lat, lon))
        return Location(lat, lon, sub, offset, edge, accuracy_m, confident, candidates, in_pk)

    def _candidates(self, lat, lon, radius_m, own_id):
        m_lat, m_lon = geodesy.metres_per_degree(lat)
        dlat, dlon = radius_m / m_lat, radius_m / m_lon
        found = []
        for q, i, j in self.layout.items_in_area(to_e7(lat - dlat), to_e7(lon - dlon),
                                                 to_e7(lat + dlat), to_e7(lon + dlon), "sub"):
            sid = GridLayout.item_id(q, i, j, "sub")
            if sid == own_id:
                continue
            s, w, n, e = (v / SCALE for v in self.layout.quadrants[q].item_bounds_e7(i, j, "sub"))
            dx = max(w - lon, 0.0, lon - e) * m_lon
            dy = max(s - lat, 0.0, lat - n) * m_lat
            d = math.hypot(dx, dy)
            if d <= radius_m:
                found.append((d, sid))
        return tuple(sid for _, sid in sorted(found))

    # -- Lookup by ID ----------------------------------------------------

    def cell(self, cell):
        """A cell's boundaries and centroid, by ID or code such as '11-0140-0471'."""
        cid = self._resolve(cell, "cell")
        q, row, col = ids.split_cell_id(cid)
        s, w, n, e, clat, clon, flag = self.store.cell(cid)
        return Cell(cid, ids.cell_code(cid), spec.QUADRANTS[q][0], row, col,
                    Bounds.from_e7(s, w, n, e), (clat / SCALE, clon / SCALE), FLAG_NAMES[flag])

    def sub_cell(self, sub_cell):
        """A sub-cell's boundaries and centroid, by ID or code such as '11-0140-0471-00'."""
        sid = self._resolve(sub_cell, "sub")
        cid, sub = ids.split_sub_cell_id(sid)
        return _sub_cell_from_record(cid, sub, self.store.sub_record(cid))

    # -- Hierarchy and area ----------------------------------------------

    @staticmethod
    def parent(sub_cell_id):
        """The cell ID containing a sub-cell (no database access)."""
        return ids.parent(sub_cell_id)

    def children(self, cell):
        """The 25 sub-cells of a cell, in order 0-24."""
        cid = self._resolve(cell, "cell")
        record = self.store.sub_record(cid)
        return [_sub_cell_from_record(cid, k, record) for k in range(SUB * SUB)]

    def neighbours(self, item, level="cell"):
        """IDs of the cells (or sub-cells) touching this one, including across quadrant lines.

        `item` is an ID or a code; a code sets the level by itself.
        """
        level, item_id = self._resolve_any(item, level)
        q, i, j = self.layout.item_from_id(item_id, level)
        return sorted(GridLayout.item_id(*n, level) for n in self.layout.neighbours(q, i, j, level))

    def cells_in_area(self, south, west, north, east, level="cell", pakistan_only=False):
        """IDs of all cells (or sub-cells) overlapping a latitude/longitude box, in ID order.

        With pakistan_only, only those inside Pakistan or on its border are returned.
        """
        items = self.layout.items_in_area(to_e7(south), to_e7(west), to_e7(north), to_e7(east), level)
        found = sorted(GridLayout.item_id(q, i, j, level) for q, i, j in items)
        if not pakistan_only:
            return found
        flags = self._flags(found, level)
        return [i for i in found if flags.get(i, OUTSIDE) != OUTSIDE]

    def pakistan_flags(self, items, level="cell"):
        """{ID: "inside" | "border" | "outside"} for many cells (or sub-cells) at once."""
        items = [self._resolve(i, level) for i in items]
        flags = self._flags(items, level)
        return {i: FLAG_NAMES[flags.get(i, OUTSIDE)] for i in items}

    def _flags(self, item_ids, level):
        """{ID: flag} read from the database in bulk; cells missing from it are left out
        (they are wholly outside in the Pakistan edition, otherwise an error)."""
        item_ids = list(item_ids)
        cids = item_ids if level == "cell" else sorted({ids.parent(s) for s in item_ids})
        if level == "cell":
            stored = self.store.cell_flags(cids)
        else:
            masks = self.store.sub_masks(cids)
            stored = {s: sub_flag(s & ids.SUB_MASK, *masks[ids.parent(s)])
                      for s in item_ids if ids.parent(s) in masks}
            cids = [c for c in cids if c not in masks]
        missing = [c for c in cids if c not in stored]
        if missing and not self.store.pakistan_edition:
            raise NotInDatabaseError(f"{len(missing)} cells in that area are not in this database "
                                     f"({self.store.path.name}), e.g. {ids.cell_code(missing[0])}")
        return stored

    # -- Vectors ---------------------------------------------------------

    def offset(self, item, lat, lon, level="sub"):
        """Vector from a sub-cell's (or cell's) centroid to a point."""
        clat, clon = self._centroid(item, level)
        return Offset(*geodesy.local_offset(clat, clon, lat, lon))

    def from_offset(self, item, east_m, north_m, level="sub"):
        """The GPS point `east_m` east and `north_m` north of a sub-cell's (or cell's) centroid."""
        clat, clon = self._centroid(item, level)
        return geodesy.apply_offset(clat, clon, east_m, north_m)

    direction = staticmethod(navigate.direction)

    # -- Readable codes --------------------------------------------------

    @staticmethod
    def to_text(item_id, level="sub"):
        """'11-0140-0471-00' from a sub-cell ID, or '11-0140-0471' from a cell ID with level='cell'."""
        return ids.cell_code(item_id) if level == "cell" else ids.sub_cell_code(item_id)

    def from_text(self, code):
        """Integer ID from a readable code, checked against the grid."""
        level, item_id = ids.parse_code(code)
        self.layout.item_from_id(item_id, level)
        return item_id

    # -- Info ------------------------------------------------------------

    def info(self):
        return {
            "grid_version": spec.GRID_VERSION,
            "datum": spec.DATUM,
            "bounds": {"south": spec.SOUTH, "west": spec.WEST, "north": spec.NORTH, "east": spec.EAST},
            "midlines": {"lat": spec.MID_LAT, "lon": spec.MID_LON},
            "quadrants": {ql.code: {"name": ql.name, "rows": ql.nrows, "cells": ql.ncells}
                          for ql in self.layout.quadrants},
            "total_cells": self.layout.total_cells,
            "total_sub_cells": self.layout.total_sub_cells,
            "database": {
                "path": str(self.store.path),
                "coverage": self.meta.get("coverage"),
                "edition": self.meta.get("edition"),
                "cells": int(self.meta.get("cells", 0)),
                "content_sha256": self.meta.get("content_sha256"),
                "library_version": self.meta.get("library_version"),
            },
            "pakistan": {
                "cells_inside": int(self.meta.get("cells_inside", 0)),
                "cells_border": int(self.meta.get("cells_border", 0)),
                "cells_outside": int(self.meta.get("cells_outside", 0)),
                "boundary": self.meta.get("boundary_name"),
                "boundary_license": self.meta.get("boundary_license"),
                "attribution": self.meta.get("boundary_attribution"),
                "boundary_sha256": self.meta.get("boundary_sha256"),
            },
        }

    # -- Helpers ---------------------------------------------------------

    def _resolve(self, item, level):
        got_level, item_id = self._resolve_any(item, level)
        if got_level != level:
            raise InvalidIdError(f"{item!r} is a {got_level} code, expected a {level} code")
        self.layout.item_from_id(item_id, level)
        return item_id

    @staticmethod
    def _resolve_any(item, level):
        if isinstance(item, str):
            return ids.parse_code(item)
        if level not in ("cell", "sub"):
            raise ValueError(f"level must be 'cell' or 'sub', got {level!r}")
        return level, item

    def _centroid(self, item, level):
        level, _ = self._resolve_any(item, level)
        return (self.cell(item) if level == "cell" else self.sub_cell(item)).centroid


def _point_e7(lat, lon):
    for name, v in (("lat", lat), ("lon", lon)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"{name} must be a finite number, got {v!r}")
    return to_e7(lat), to_e7(lon)


def _sub_cell_from_record(cid, sub, record):
    lat_lines, lon_lines, centroids, inside_mask, border_mask = record
    sr, sc = ids.split_sub_index(sub)
    sid = ids.sub_cell_id(cid, sub)
    clat, clon = centroids[sub]
    return SubCell(sid, ids.sub_cell_code(sid), cid, ids.cell_code(cid), sub,
                   Bounds.from_e7(lat_lines[sr], lon_lines[sc], lat_lines[sr + 1], lon_lines[sc + 1]),
                   (clat / SCALE, clon / SCALE), FLAG_NAMES[sub_flag(sub, inside_mask, border_mask)])


def _layout_from_store(store):
    ncols = {q: [] for q in spec.QUADRANTS}
    for q, row, _s, _n, n in store.rows():
        ncols[q].append(n)
    quads = []
    for q, _code, _name, _s, _w, _n, _e, nrows, _ncells in store.quadrants():
        if len(ncols[q]) != nrows:
            raise IntegrityError(f"quadrant {q} lists {nrows} rows but the rows table has {len(ncols[q])}")
        quads.append(QuadrantLayout.build(q, nrows, ncols[q]))
    return GridLayout(quads)
