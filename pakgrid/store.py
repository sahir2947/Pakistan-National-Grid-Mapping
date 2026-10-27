"""Read-only access to the grid database (SQLite).

Coordinates are integers in units of 1e-7 degree. Each cell's 25 sub-cells
are packed into one record of 62 int32 values (248 bytes):

    6 latitude lines (south edge .. north edge)
    6 longitude lines (west edge .. east edge)
    25 centroids as (lat, lon), in sub-cell order 0-24
"""

import sqlite3
import struct
from pathlib import Path

from .errors import NotInDatabaseError

SCHEMA_VERSION = 1
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
    centroid_lat INTEGER NOT NULL, centroid_lon INTEGER NOT NULL);
CREATE TABLE sub_cells (id INTEGER PRIMARY KEY, record BLOB NOT NULL);
"""


def unpack_sub_record(blob):
    """(lat_lines, lon_lines, centroids) from a packed sub-cell record."""
    v = SUB_RECORD.unpack(blob)
    return v[0:6], v[6:12], tuple(zip(v[12::2], v[13::2]))


class GridStore:
    """A read-only connection to a grid database file."""

    def __init__(self, path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"grid database not found: {path}")
        self.path = path
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def close(self):
        self._db.close()

    def meta(self):
        return dict(self._db.execute("SELECT key, value FROM meta"))

    def quadrants(self):
        return self._db.execute(
            "SELECT q, code, name, south, west, north, east, nrows, ncells FROM quadrants ORDER BY q").fetchall()

    def rows(self):
        return self._db.execute("SELECT q, row, south, north, ncols FROM rows ORDER BY q, row").fetchall()

    def cell(self, cid):
        row = self._db.execute(
            "SELECT south, west, north, east, centroid_lat, centroid_lon FROM cells WHERE id = ?",
            (cid,)).fetchone()
        if row is None:
            raise NotInDatabaseError(f"cell {cid} is not in this database ({self.path.name})")
        return row

    def sub_record(self, cid):
        row = self._db.execute("SELECT record FROM sub_cells WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise NotInDatabaseError(f"cell {cid} is not in this database ({self.path.name})")
        return unpack_sub_record(row[0])

    def iter_cells(self):
        """All (id, south, west, north, east, centroid_lat, centroid_lon) in ID order."""
        return self._db.execute(
            "SELECT id, south, west, north, east, centroid_lat, centroid_lon FROM cells ORDER BY id")

    def iter_sub_blobs(self):
        """All (cell ID, packed record) in ID order."""
        return self._db.execute("SELECT id, record FROM sub_cells ORDER BY id")
