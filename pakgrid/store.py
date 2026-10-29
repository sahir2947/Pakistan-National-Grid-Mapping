"""Read-only access to the grid database (SQLite).

Coordinates are integers in units of 1e-7 degree. Each cell's 25 sub-cells
are packed into one record of 62 int32 values (248 bytes):

    6 latitude lines (south edge .. north edge)
    6 longitude lines (west edge .. east edge)
    25 centroids as (lat, lon), in sub-cell order 0-24

Border flags: `cells.pakistan` is 0 (outside), 1 (border) or 2 (inside).
For sub-cells, bit k of `inside_mask` / `border_mask` is set when sub-cell k
is inside / on the border; neither bit means outside. The `boundary` table
holds the border itself as packed int32 (lon, lat) rings.

Thread and process safety: one GridStore can be shared by every thread of a
web server. Each thread gets its own read-only SQLite connection, opened on
first use, and a process created by fork (e.g. a gunicorn worker started
after the app was loaded) opens fresh connections rather than reusing the
parent's, as SQLite requires.
"""

import os
import sqlite3
import struct
import threading
import weakref
from pathlib import Path

from .errors import NotInDatabaseError

SCHEMA_VERSION = 2
SUB_RECORD = struct.Struct("<62i")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE quadrants (
    q INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
    south INTEGER NOT NULL, west INTEGER NOT NULL, north INTEGER NOT NULL, east INTEGER NOT NULL,
    nrows INTEGER NOT NULL, ncells INTEGER NOT NULL);
CREATE TABLE rows (
    q INTEGER NOT NULL, row INTEGER NOT NULL,
    south INTEGER NOT NULL, north INTEGER NOT NULL, ncols INTEGER NOT NULL,
    PRIMARY KEY (q, row)) WITHOUT ROWID;
CREATE TABLE cells (
    id INTEGER PRIMARY KEY,
    south INTEGER NOT NULL, west INTEGER NOT NULL, north INTEGER NOT NULL, east INTEGER NOT NULL,
    centroid_lat INTEGER NOT NULL, centroid_lon INTEGER NOT NULL, pakistan INTEGER NOT NULL);
CREATE TABLE sub_cells (
    id INTEGER PRIMARY KEY, record BLOB NOT NULL,
    inside_mask INTEGER NOT NULL, border_mask INTEGER NOT NULL);
CREATE TABLE boundary (ring INTEGER PRIMARY KEY, coords BLOB NOT NULL);
"""


def unpack_sub_record(blob, inside_mask=0, border_mask=0):
    """(lat_lines, lon_lines, centroids, inside_mask, border_mask) from a sub-cell row."""
    v = SUB_RECORD.unpack(blob)
    return v[0:6], v[6:12], tuple(zip(v[12::2], v[13::2])), inside_mask, border_mask


class _Connection:
    """One thread's connection; closed when its thread ends (and the holder is collected)."""

    def __init__(self, uri):
        self.pid = os.getpid()
        self.db = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def close(self):
        if self.pid == os.getpid():     # never close a connection inherited through fork
            self.db.close()

    def __del__(self):
        try:
            self.close()
        except Exception:               # e.g. at interpreter shutdown
            pass


class GridStore:
    """Read-only access to a grid database file, safe to share between threads and forked processes."""

    def __init__(self, path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"grid database not found: {path}")
        self.path = path
        self._uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        self._local = threading.local()
        self._open = weakref.WeakSet()
        self._lock = threading.Lock()
        self._closed = False
        self.pakistan_edition = self.meta().get("edition") == "pakistan"

    @property
    def _db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.pid != os.getpid():
            if self._closed:
                raise sqlite3.ProgrammingError("Cannot operate on a closed grid database.")
            conn = self._local.conn = _Connection(self._uri)
            with self._lock:
                self._open.add(conn)
        return conn.db

    def close(self):
        """Close every thread's connection; the store cannot be used afterwards."""
        self._closed = True
        with self._lock:
            conns = list(self._open)
        for conn in conns:
            conn.close()

    def meta(self):
        return dict(self._db.execute("SELECT key, value FROM meta"))

    def quadrants(self):
        return self._db.execute(
            "SELECT q, code, name, south, west, north, east, nrows, ncells FROM quadrants ORDER BY q").fetchall()

    def rows(self):
        return self._db.execute("SELECT q, row, south, north, ncols FROM rows ORDER BY q, row").fetchall()

    def boundary_rings(self):
        return [blob for (blob,) in self._db.execute("SELECT coords FROM boundary ORDER BY ring")]

    def _missing(self, cid):
        why = ("it lies wholly outside Pakistan and this is the Pakistan edition"
               if self.pakistan_edition else "it was not included in this build")
        return NotInDatabaseError(f"cell {cid} is not in this database ({self.path.name}): {why}")

    def cell(self, cid):
        """(south, west, north, east, centroid_lat, centroid_lon, pakistan flag)."""
        row = self._db.execute(
            "SELECT south, west, north, east, centroid_lat, centroid_lon, pakistan FROM cells WHERE id = ?",
            (cid,)).fetchone()
        if row is None:
            raise self._missing(cid)
        return row

    def sub_record(self, cid):
        row = self._db.execute(
            "SELECT record, inside_mask, border_mask FROM sub_cells WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise self._missing(cid)
        return unpack_sub_record(*row)

    def cell_flags(self, cids):
        """{cell ID: pakistan flag} for the given cells that are stored."""
        out, cids = {}, list(cids)
        for k in range(0, len(cids), 900):
            chunk = cids[k:k + 900]
            out.update(self._db.execute(
                f"SELECT id, pakistan FROM cells WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        return out

    def sub_masks(self, cids):
        """{cell ID: (inside_mask, border_mask)} for the given cells that are stored."""
        out, cids = {}, list(cids)
        for k in range(0, len(cids), 900):
            chunk = cids[k:k + 900]
            for cid, inside, border in self._db.execute(
                    f"SELECT id, inside_mask, border_mask FROM sub_cells WHERE id IN "
                    f"({','.join('?' * len(chunk))})", chunk):
                out[cid] = (inside, border)
        return out

    # -- Places layer (see pakgrid.places) ----------------------------------------

    def has_places(self):
        return self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cell_places'").fetchone() is not None

    def places_rows(self):
        """All (id, level, key, name, parent, parent_share, source_name, source_id), in ID order."""
        return self._db.execute(
            "SELECT id, level, key, name, parent, parent_share, source_name, source_id FROM places ORDER BY id"
        ).fetchall()

    def cell_place(self, cid):
        """(tehsil, subs blob or None) for a cell, or None if it has no places tags."""
        return self._db.execute("SELECT tehsil, subs FROM cell_places WHERE id = ?", (cid,)).fetchone()

    def cells_with_tehsils(self, tehsils):
        """(cell ID, tehsil, subs) for cells whose main tehsil is one of `tehsils`."""
        out, tehsils = [], list(tehsils)
        for k in range(0, len(tehsils), 900):
            chunk = tehsils[k:k + 900]
            out += self._db.execute(f"SELECT id, tehsil, subs FROM cell_places WHERE tehsil IN "
                                    f"({','.join('?' * len(chunk))})", chunk).fetchall()
        return out

    def split_cells(self):
        """(cell ID, tehsil, subs) for cells whose sub-cells have different tehsils."""
        return self._db.execute("SELECT id, tehsil, subs FROM cell_places WHERE subs IS NOT NULL").fetchall()

    def iter_cell_places(self):
        """All (cell ID, tehsil, subs) in ID order."""
        return self._db.execute("SELECT id, tehsil, subs FROM cell_places ORDER BY id")

    def has_cell(self, cid):
        return self._db.execute("SELECT 1 FROM cells WHERE id = ?", (cid,)).fetchone() is not None

    def iter_cells(self):
        """All (id, south, west, north, east, centroid_lat, centroid_lon, pakistan) in ID order."""
        return self._db.execute(
            "SELECT id, south, west, north, east, centroid_lat, centroid_lon, pakistan FROM cells ORDER BY id")

    def iter_sub_blobs(self):
        """All (cell ID, packed record, inside_mask, border_mask) in ID order."""
        return self._db.execute("SELECT id, record, inside_mask, border_mask FROM sub_cells ORDER BY id")
