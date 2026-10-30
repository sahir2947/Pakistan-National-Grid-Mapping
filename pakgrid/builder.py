"""Builds the grid database: every cell and sub-cell with its boundaries, centroid and border flag."""

import hashlib
import os
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__, geodesy, ids, spec
from .boundary import FLAG_NAMES, OUTSIDE, Boundary, cell_flags, row_flags
from .layout import SCALE, SUB, GridLayout, to_e7
from .store import SCHEMA, SCHEMA_VERSION, SUB_RECORD

_QUADRANT_REC = struct.Struct("<7i")
_ROW_REC = struct.Struct("<5i")
_CELL_REC = struct.Struct("<8i")
_MASKS = struct.Struct("<2i")


@dataclass(frozen=True)
class BuildReport:
    path: Path
    cells: int
    sub_cells: int
    content_sha256: str
    seconds: float
    size_bytes: int


def _parse_selection(quadrants, rows):
    qs = sorted({_quadrant_number(q) for q in quadrants}) if quadrants else [0, 1, 2, 3]
    if rows is not None:
        rows = range(rows.start, rows.stop) if isinstance(rows, range) else range(*rows)
    return qs, rows


def _quadrant_number(q):
    if isinstance(q, int):
        return q
    for number, (code, _) in spec.QUADRANTS.items():
        if q == code:
            return number
    raise ValueError(f"unknown quadrant {q!r}; use 0-3 or a code such as '11'")


def iter_cell_records(layout, boundary, quadrants=None, rows=None, pakistan_only=False):
    """Yield (cell ID, cell values, sub-record values, (inside_mask, border_mask)) in ID order.

    cell values: (south, west, north, east, centroid_lat, centroid_lon, border flag).
    With pakistan_only, cells wholly outside Pakistan are skipped.
    """
    qs, rows = _parse_selection(quadrants, rows)
    for q in qs:
        ql = layout.quadrants[q]
        lats = [ql.fine_lat(i) for i in range(SUB * ql.nrows + 1)]
        fine_centroid_lat = [to_e7(geodesy.band_centroid_lat(lats[i], lats[i + 1]))
                             for i in range(len(lats) - 1)]
        for row in (rows if rows is not None else range(ql.nrows)):
            if not 0 <= row < ql.nrows:
                continue
            lat_lines = ql.lat_e7[row * SUB:(row + 1) * SUB + 1]
            cell_clat = to_e7(geodesy.band_centroid_lat(lats[row * SUB], lats[(row + 1) * SUB]))
            sub_clats = fine_centroid_lat[row * SUB:(row + 1) * SUB]
            lons = [ql.fine_lon(row, j) for j in range(SUB * ql.ncols[row] + 1)]
            lons_e7 = [to_e7(x) for x in lons]
            fine_clon = [to_e7((lons[j] + lons[j + 1]) / 2) for j in range(len(lons) - 1)]
            centres = [(lons[j] + lons[j + 1]) / 2 for j in range(len(lons) - 1)]
            sub_rows = [row_flags(*boundary.band(lats[i], lats[i + 1]), lons, centres)
                        for i in range(row * SUB, (row + 1) * SUB)]
            for col in range(ql.ncols[row]):
                flag, inside_mask, border_mask = cell_flags(sub_rows, col)
                if pakistan_only and flag == OUTSIDE:
                    continue
                j0 = col * SUB
                lon_lines = lons_e7[j0:j0 + SUB + 1]
                cell = (lat_lines[0], lon_lines[0], lat_lines[SUB], lon_lines[SUB],
                        cell_clat, to_e7((lons[j0] + lons[j0 + SUB]) / 2), flag)
                centroids = []
                for sr in range(SUB):
                    for sc in range(SUB):
                        centroids += (sub_clats[sr], fine_clon[j0 + sc])
                yield (ids.cell_id(q, row, col), cell, (*lat_lines, *lon_lines, *centroids),
                       (inside_mask, border_mask))


def _quadrant_rows(layout):
    return [(ql.q, ql.code, ql.name, to_e7(ql.south), ql.west_e7, to_e7(ql.north), ql.east_e7,
             ql.nrows, ql.ncells) for ql in layout.quadrants]


def _row_rows(layout):
    return [(ql.q, row, ql.lat_e7[row * SUB], ql.lat_e7[(row + 1) * SUB], ql.ncols[row])
            for ql in layout.quadrants for row in range(ql.nrows)]


def content_hash():
    return hashlib.sha256(b"pakgrid-content-v2")


def hash_cell(h, cid, cell, blob, masks):
    h.update(_CELL_REC.pack(cid, *cell))
    h.update(blob)
    h.update(_MASKS.pack(*masks))


def hash_layout(h, quadrant_rows, row_rows):
    for q, _code, _name, s, w, n, e, nrows, ncells in quadrant_rows:
        h.update(_QUADRANT_REC.pack(q, s, w, n, e, nrows, ncells))
    for row in row_rows:
        h.update(_ROW_REC.pack(*row))


def build_database(path, quadrants=None, rows=None, progress=None, overwrite=False,
                   pakistan_only=False, boundary=None, places=True):
    """Generate the grid database at `path`.

    quadrants:     optional subset, as numbers 0-3 or codes such as "11".
    rows:          optional row range (range or (start, stop)) applied to each quadrant.
    pakistan_only: leave out cells wholly outside Pakistan (the "Pakistan edition").
                   IDs are unchanged; the cells left out simply are not stored.
    boundary:      the border to flag against; defaults to the packaged Pakistan boundary.
    places:        also build the places layer (province, district, tehsil and locality tags; see
                   pakgrid.places). It can be added or rebuilt later with build_places().

    A subset produces a *partial* database, useful for tests and pilots; the
    quadrant, row and boundary tables are always complete.
    """
    path = Path(path)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} already exists (pass overwrite=True to replace it)")
        path.unlink()
    started = time.perf_counter()
    layout = GridLayout.from_spec()
    boundary = boundary or Boundary.default()
    qs, row_sel = _parse_selection(quadrants, rows)
    partial = qs != [0, 1, 2, 3] or row_sel is not None

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    if tmp.exists():
        tmp.unlink()
    db = sqlite3.connect(tmp)
    try:
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.executescript(SCHEMA)

        h = content_hash()
        quadrant_rows, row_rows = _quadrant_rows(layout), _row_rows(layout)
        db.executemany("INSERT INTO quadrants VALUES (?,?,?,?,?,?,?,?,?)", quadrant_rows)
        db.executemany("INSERT INTO rows VALUES (?,?,?,?,?)", row_rows)
        hash_layout(h, quadrant_rows, row_rows)
        db.executemany("INSERT INTO boundary VALUES (?, ?)", enumerate(boundary.packed_rings()))
        h.update(boundary.sha256.encode())

        n_cells = 0
        counts = [0, 0, 0]
        cells_buf, subs_buf = [], []
        current_q = None
        for cid, cell, sub_values, masks in iter_cell_records(layout, boundary, qs, row_sel, pakistan_only):
            q = cid >> ids.QUADRANT_SHIFT
            if progress and q != current_q:
                progress(f"quadrant {spec.QUADRANTS[q][0]}")
                current_q = q
            blob = SUB_RECORD.pack(*sub_values)
            cells_buf.append((cid, *cell))
            subs_buf.append((cid, blob, *masks))
            hash_cell(h, cid, cell, blob, masks)
            counts[cell[6]] += 1
            n_cells += 1
            if len(cells_buf) >= 50_000:
                _flush(db, cells_buf, subs_buf)
        _flush(db, cells_buf, subs_buf)

        meta = {
            "grid_version": spec.GRID_VERSION,
            "schema_version": SCHEMA_VERSION,
            "library_version": __version__,
            "datum": spec.DATUM,
            "bounds": f"{spec.SOUTH},{spec.WEST},{spec.NORTH},{spec.EAST}",
            "midlines": f"{spec.MID_LAT},{spec.MID_LON}",
            "cell_size_m": spec.CELL_SIZE_M,
            "subdivisions": spec.SUBDIVISIONS,
            "coord_scale": SCALE,
            "coverage": "partial" if partial else "full",
            "edition": "pakistan" if pakistan_only else "full",
            "quadrants_built": ",".join(spec.QUADRANTS[q][0] for q in qs),
            "rows_built": f"{row_sel.start}:{row_sel.stop}" if row_sel is not None else "all",
            "cells": n_cells,
            "sub_cells": n_cells * SUB * SUB,
            **{f"cells_{FLAG_NAMES[f]}": counts[f] for f in range(3)},
            "boundary_name": boundary.name,
            "boundary_license": boundary.license,
            "boundary_attribution": boundary.attribution,
            "boundary_vertices": boundary.vertex_count,
            "boundary_sha256": boundary.sha256,
            "content_sha256": h.hexdigest(),
        }
        db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, str(v)) for k, v in meta.items()])
        if places:
            from .places import build_places_data, write_places
            write_places(db, build_places_data(layout, boundary, qs, row_sel, progress),
                         "partial" if partial else "full")
        db.commit()
    finally:
        db.close()
    os.replace(tmp, path)
    return BuildReport(path, n_cells, n_cells * SUB * SUB, meta["content_sha256"],
                       time.perf_counter() - started, path.stat().st_size)


def _flush(db, cells_buf, subs_buf):
    db.executemany("INSERT INTO cells VALUES (?,?,?,?,?,?,?,?)", cells_buf)
    db.executemany("INSERT INTO sub_cells VALUES (?,?,?,?)", subs_buf)
    cells_buf.clear()
    subs_buf.clear()
