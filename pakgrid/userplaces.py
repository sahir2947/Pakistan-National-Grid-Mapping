"""User places: localities that an app's own users draw and name, kept beside the grid.

The official places layer (pakgrid.places) comes from published sources and is checked against an
official checksum, so it is never edited. Localities that people know but the sources miss (a
gali, a market, a new housing scheme) are added here instead, in a separate SQLite file:

    with Grid.open("pakgrid_v1_pakistan.sqlite") as grid:
        user = grid.attach_user_places("user_places.sqlite")      # created if missing
        gali = user.add("Gali 7", polygon=[(31.5210, 74.3580), (31.5210, 74.3600),
                                          (31.5195, 74.3600), (31.5195, 74.3580)],
                        author="ali@shop-42", kind="street", nearest=2)
        gali.references[0].text          # '500 m SW of Askari 2'
        grid.place(sub_cell_id).label    # 'Gali 7 (500 m SW of Askari 2), Lahore Cantt, Lahore, Punjab'

How a user locality is made:

- Its **sub-cells** are those whose centroid lies inside the drawn polygon (even-odd rule, exact
  integer arithmetic), or the cells and sub-cells given by ID or code; only sub-cells in
  Pakistan (with a tehsil) count.
- Its **tehsil** is the one most of those sub-cells have, and its key is that tehsil's key plus
  the name: 'punjab/lahore/lahore-cantt/gali-7'.
- Its **centre** is the covered sub-cell centroid nearest their average, so it always lies in
  the locality.
- Its **references** say where it is from localities already known: for each, the distance,
  compass bearing and east/north vector from that locality's point to the centre ('500 m SW of
  Askari 2'). The user names them (official or other user localities), or asks for the
  `nearest` official ones, or both. References must be within MAX_REFERENCE_KM.
- Inside it, `Grid.place` gives the user locality instead of the OpenStreetMap one (which is kept
  as `PlaceTags.osm_locality`). Where user localities overlap, the smallest wins (ties: the
  newest).

Who may edit is the app's decision (for example a Django permission); the library requires an
`author` for every change and keeps a history of all changes, so each entry can be traced.
Removing a locality hides it but keeps its history.

The file is safe to share between the threads of a web server: each thread gets its own SQLite
connection, writes are transactions, and readers see each committed change at once.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import struct
import threading
import weakref
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from . import ids, navigate
from . import zones as _zones
from .access import AccessMixin
from .errors import InvalidIdError, NotInDatabaseError, PakgridError
from .layout import SCALE, SUB, to_e7
from .roads import RoadsMixin
from .zones import RingE7, ZoneError, polygon_masks, rings_e7

if TYPE_CHECKING:
    from .access import StreetAccess
    from .grid import Grid, Place
    from .layout import GridLayout
    from .roads import UserRoad

USER_PLACES_VERSION = 1
USER_ID_BASE = 1_000_000_000     # user locality place IDs start above every official place ID
MAX_REFERENCE_KM = 50.0          # a reference locality must be at most this far from the centre
MAX_SUB_CELLS = 25_000           # ~1,000 km²: a larger locality is almost certainly a drawing mistake
MAX_REFERENCES = 10

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE localities (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, local_name TEXT,
    kind TEXT NOT NULL, tehsil_key TEXT NOT NULL, lat INTEGER NOT NULL, lon INTEGER NOT NULL,
    sub_cells INTEGER NOT NULL, shape TEXT NOT NULL,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    removed_by TEXT, removed_at TEXT, places_version INTEGER NOT NULL);
CREATE TABLE locality_cells (locality INTEGER NOT NULL, sub_cell INTEGER NOT NULL,
    PRIMARY KEY (locality, sub_cell)) WITHOUT ROWID;
CREATE INDEX locality_cells_sub_cell ON locality_cells (sub_cell);
CREATE TABLE refs (
    locality INTEGER NOT NULL, ord INTEGER NOT NULL, place_key TEXT NOT NULL, place_name TEXT NOT NULL,
    distance_m REAL NOT NULL, bearing_deg REAL NOT NULL, direction TEXT NOT NULL,
    east_m REAL NOT NULL, north_m REAL NOT NULL, PRIMARY KEY (locality, ord)) WITHOUT ROWID;
CREATE TABLE history (
    id INTEGER PRIMARY KEY, locality INTEGER NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL,
    action TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX history_locality ON history (locality);
"""

_LOCALITY_COLUMNS = ("id, key, name, local_name, kind, tehsil_key, lat, lon, sub_cells, shape, created_by, "
                     "created_at, updated_by, updated_at, removed_by, removed_at, places_version")


class UserPlacesError(PakgridError, ValueError):
    """A user locality cannot be added or changed as asked (bad polygon, unknown reference, ...)."""


@dataclass(frozen=True)
class Reference:
    """Where a user locality is from a known locality: the vector from that locality's point to
    the user locality's centre."""
    place_key: str
    place_name: str
    distance_m: float
    bearing_deg: float          # compass bearing, clockwise from north
    direction: str              # 16-point compass, e.g. 'NE'
    east_m: float
    north_m: float

    @property
    def text(self) -> str:
        """'500 m SW of Askari 2'"""
        return f"{navigate.format_distance(self.distance_m)} {self.direction} of {self.place_name}"

    def to_dict(self) -> dict[str, Any]:
        return {"place_key": self.place_key, "place_name": self.place_name, "distance_m": self.distance_m,
                "bearing_deg": self.bearing_deg, "direction": self.direction, "east_m": self.east_m,
                "north_m": self.north_m, "text": self.text}


@dataclass(frozen=True)
class UserLocality:
    """A locality added by a user (see the module docstring)."""
    id: int                     # its place ID: USER_ID_BASE + its number in the file
    key: str                    # e.g. 'punjab/lahore/lahore-cantt/gali-7'; never changes
    name: str
    local_name: str | None
    kind: str                   # e.g. 'street', 'neighbourhood', 'market'; free text
    tehsil_key: str
    lat: float                  # its centre: a covered sub-cell's centroid
    lon: float
    sub_cell_count: int
    shape: dict[str, Any]       # how it was drawn: {"polygon": [[lat, lon], ...] rings} or {"sub_cells": [...]}
    references: tuple[Reference, ...]   # in the order given
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    removed_by: str | None = None
    removed_at: str | None = None

    @property
    def removed(self) -> bool:
        return self.removed_at is not None

    @property
    def text(self) -> str:
        """'Gali 7 (500 m SW of Askari 2)': the name and its first reference."""
        return f"{self.name} ({self.references[0].text})" if self.references else self.name

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "name": self.name, "local_name": self.local_name,
                "kind": self.kind, "tehsil_key": self.tehsil_key, "centre": [self.lat, self.lon],
                "sub_cells": self.sub_cell_count, "shape": self.shape, "text": self.text,
                "references": [r.to_dict() for r in self.references],
                "created_by": self.created_by, "created_at": self.created_at,
                "updated_by": self.updated_by, "updated_at": self.updated_at,
                "removed_by": self.removed_by, "removed_at": self.removed_at}


class _Connection:
    def __init__(self, path: Path) -> None:
        self.pid = os.getpid()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        if self.pid == os.getpid():
            self.db.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@overload
def _check_text(value: object, what: str) -> str: ...
@overload
def _check_text(value: object, what: str, required: Literal[True]) -> str: ...
@overload
def _check_text(value: object, what: str, required: bool) -> str | None: ...


def _check_text(value: object, what: str, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise UserPlacesError(f"{what} must be a non-empty string, got {value!r}")
    return re.sub(r"\s+", " ", value).strip()


class UserPlaces(RoadsMixin, AccessMixin):
    """The user localities file. Get one with `Grid.attach_user_places(path)`, which creates it if
    needed; it then answers `Grid.place` and `Grid.locate` together with the official layer."""

    def __init__(self, path: str | os.PathLike[str], grid: Grid) -> None:
        self.path = Path(path)
        self.grid = grid
        self._local = threading.local()
        self._open: weakref.WeakSet[_Connection] = weakref.WeakSet()
        self._lock = threading.Lock()
        self._write_lock = threading.RLock()
        self._closed = False
        new = not self.path.exists()
        db = self._db
        if new:
            with db:
                db.execute("PRAGMA journal_mode = WAL")
                db.executescript(SCHEMA)
                db.executemany("INSERT INTO meta VALUES (?, ?)", [
                    ("user_places_version", str(USER_PLACES_VERSION)), ("grid_version", grid.meta["grid_version"]),
                    ("created_at", _now())])
        meta = dict(db.execute("SELECT key, value FROM meta"))
        if meta.get("user_places_version") != str(USER_PLACES_VERSION):
            raise UserPlacesError(f"{self.path.name} is user places version {meta.get('user_places_version')}, "
                                  f"not {USER_PLACES_VERSION}")
        if meta.get("grid_version") != grid.meta["grid_version"]:
            raise UserPlacesError(f"{self.path.name} was made for grid version {meta.get('grid_version')}")
        self._ensure_road_tables(db)            # user roads (pakgrid.roads), added to older files too
        self._ensure_access_tables(db)          # street access notes (pakgrid.access), likewise

    @property
    def _db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.pid != os.getpid():
            if self._closed:
                raise sqlite3.ProgrammingError("Cannot operate on a closed user places file.")
            conn = self._local.conn = _Connection(self.path)
            with self._lock:
                self._open.add(conn)
        return conn.db

    @contextlib.contextmanager
    def _writing(self, db: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        """One write transaction at a time in this process (SQLite allows one writer anyway; waiting
        on a Python lock is much quicker than SQLite's busy retries), committed or rolled back."""
        with self._write_lock, db:
            yield db

    def close(self) -> None:
        self._closed = True
        with self._lock:
            conns = list(self._open)
        for conn in conns:
            conn.close()

    # -- Adding and changing -----------------------------------------------------

    def add(self, name: str, *, author: str, polygon: Any = None, cells: Iterable[str | int] | None = None,
            kind: str = "neighbourhood", local_name: str | None = None, references: Iterable[str | int] = (),
            nearest: int | None = None) -> UserLocality:
        """Add a locality drawn as `polygon` (rings of (lat, lon), or a GeoJSON Polygon), or made of
        `cells` (cell and sub-cell codes, or sub-cell IDs; a cell means all 25 of its sub-cells).

        references: keys (or IDs) of known localities, official or user, to locate it from.
        nearest: also add this many of the nearest official localities as references. With
        neither, the nearest one is used. Returns the new UserLocality.
        """
        author = _check_text(author, "author")
        name = _check_text(name, "name")
        kind = _check_text(kind, "kind").lower()
        local_name = _check_text(local_name, "local_name", required=False)
        shape, subs = self._shape(polygon, cells)
        tehsil, (lat, lon) = self._tehsil_and_centre(subs)
        if nearest is None and not references:
            nearest = 1
        refs = self._references(lat, lon, references, nearest or 0)
        now = _now()
        db = self._db
        with self._writing(db):
            key = self._free_key(f"{tehsil.key}/{_slug(name) or 'locality'}")
            n = db.execute(f"INSERT INTO localities ({_LOCALITY_COLUMNS}) VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                           "NULL,NULL,?)",
                           (key, name, local_name, kind, tehsil.key, lat, lon, len(subs), json.dumps(shape),
                            author, now, author, now, self._places_version())).lastrowid
            assert n is not None                    # an INSERT always gives its row ID
            db.executemany("INSERT INTO locality_cells VALUES (?, ?)", [(n, s) for s in subs])
            self._write_refs(n, refs)
            loc = self._get(n)
            self._log(n, author, "add", loc)
        return loc

    def update(self, locality: str | int | UserLocality, *, author: str, name: str | None = None,
               local_name: str | None = None, kind: str | None = None, polygon: Any = None,
               cells: Iterable[str | int] | None = None, references: Iterable[str | int] | None = None,
               nearest: int | None = None) -> UserLocality:
        """Change a locality: any of its name, local name, kind, shape (polygon or cells) and
        references (references and nearest replace the old ones). Its key stays the same."""
        author = _check_text(author, "author")
        old = self.get(locality)
        if old.removed:
            raise UserPlacesError(f"{old.key} was removed; add it again instead")
        n = old.id - USER_ID_BASE
        db = self._db
        with self._writing(db):
            fields: dict[str, Any] = {}
            if name is not None:
                fields["name"] = _check_text(name, "name")
            if local_name is not None:
                fields["local_name"] = _check_text(local_name, "local_name") if local_name else None
            if kind is not None:
                fields["kind"] = _check_text(kind, "kind").lower()
            lat, lon = to_e7(old.lat), to_e7(old.lon)
            if polygon is not None or cells is not None:
                shape, subs = self._shape(polygon, cells)
                tehsil, (lat, lon) = self._tehsil_and_centre(subs)
                fields.update(shape=json.dumps(shape), tehsil_key=tehsil.key, lat=lat, lon=lon, sub_cells=len(subs))
                db.execute("DELETE FROM locality_cells WHERE locality = ?", (n,))
                db.executemany("INSERT INTO locality_cells VALUES (?, ?)", [(n, s) for s in subs])
            if references is not None or nearest is not None or "lat" in fields:
                keep = [r.place_key for r in old.references] if references is None and nearest is None else []
                refs = self._references(lat, lon, list(references or keep), nearest or 0, own=n)
                db.execute("DELETE FROM refs WHERE locality = ?", (n,))
                self._write_refs(n, refs)
            fields.update(updated_by=author, updated_at=_now(), places_version=self._places_version())
            db.execute(f"UPDATE localities SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                       (*fields.values(), n))
            loc = self._get(n)
            self._log(n, author, "update", loc)
        return loc

    def remove(self, locality: str | int | UserLocality, *, author: str) -> UserLocality:
        """Remove a locality: lookups stop showing it; its record and history are kept."""
        author = _check_text(author, "author")
        loc = self.get(locality)
        n = loc.id - USER_ID_BASE
        db = self._db
        with self._writing(db):
            db.execute("UPDATE localities SET removed_by = ?, removed_at = ? WHERE id = ?", (author, _now(), n))
            loc = self._get(n)
            self._log(n, author, "remove", loc)
        return loc

    def refresh(self, *, author: str) -> list[UserLocality]:
        """Recompute every locality's tehsil, centre and reference vectors against the grid's
        current places layer (after a new places version). Returns the localities that changed."""
        changed = []
        for loc in self.localities():
            subs = self.sub_cells(loc)
            tehsil, (lat, lon) = self._tehsil_and_centre(subs)
            refs = self._references(lat, lon, [r.place_key for r in loc.references], 0,
                                    own=loc.id - USER_ID_BASE, missing_ok=True)
            n = loc.id - USER_ID_BASE
            db = self._db
            with self._writing(db):
                db.execute("UPDATE localities SET tehsil_key = ?, lat = ?, lon = ?, places_version = ? WHERE id = ?",
                           (tehsil.key, lat, lon, self._places_version(), n))
                db.execute("DELETE FROM refs WHERE locality = ?", (n,))
                self._write_refs(n, refs)
                new = self._get(n)
                if new.tehsil_key != loc.tehsil_key or new.references != loc.references or \
                        (new.lat, new.lon) != (loc.lat, loc.lon):
                    db.execute("UPDATE localities SET updated_by = ?, updated_at = ? WHERE id = ?",
                               (author, _now(), n))
                    new = self._get(n)
                    self._log(n, author, "refresh", new)
                    changed.append(new)
        return changed

    # -- Reading -------------------------------------------------------------------

    def get(self, locality: str | int | UserLocality) -> UserLocality:
        """A UserLocality by key, place ID or UserLocality (removed ones too)."""
        if isinstance(locality, UserLocality):
            locality = locality.id
        if isinstance(locality, int) and not isinstance(locality, bool):
            row = self._one(f"SELECT {_LOCALITY_COLUMNS} FROM localities WHERE id = ?", (locality - USER_ID_BASE,))
        elif isinstance(locality, str):
            row = self._one(f"SELECT {_LOCALITY_COLUMNS} FROM localities WHERE key = ?", (locality.strip().lower(),))
        else:
            row = None
        if row is None:
            raise InvalidIdError(f"no such user locality: {locality!r}")
        return self._from_row(row)

    def localities(self, include_removed: bool = False) -> list[UserLocality]:
        """All user localities, in the order they were added."""
        rows = self._db.execute(f"SELECT {_LOCALITY_COLUMNS} FROM localities"
                                + ("" if include_removed else " WHERE removed_at IS NULL") + " ORDER BY id")
        return [self._from_row(r) for r in rows.fetchall()]

    def find(self, text: str) -> list[UserLocality]:
        """User localities whose name or local name contains `text`, or whose key is `text`."""
        t = text.strip().lower()
        return [loc for loc in self.localities()
                if t in loc.name.lower() or t == loc.key or (loc.local_name and t in loc.local_name)]

    def sub_cells(self, locality: str | int | UserLocality) -> list[int]:
        """IDs of a user locality's sub-cells, in ID order."""
        n = self.get(locality).id - USER_ID_BASE
        return [s for (s,) in self._db.execute(
            "SELECT sub_cell FROM locality_cells WHERE locality = ? ORDER BY sub_cell", (n,))]

    def history(self, locality: str | int | UserLocality | None = None) -> list[dict[str, Any]]:
        """Every change, oldest first: dicts of at, author, action, key and the locality after it."""
        sql = "SELECT h.at, h.author, h.action, l.key, h.data FROM history h JOIN localities l ON l.id = h.locality"
        args: tuple[Any, ...] = ()
        if locality is not None:
            sql, args = sql + " WHERE h.locality = ?", (self.get(locality).id - USER_ID_BASE,)
        return [{"at": at, "author": who, "action": action, "key": key, "locality": json.loads(data)}
                for at, who, action, key, data in self._db.execute(sql + " ORDER BY h.id", args)]

    def at_sub_cells(self, sub_cell_ids: Iterable[int]) -> dict[int, UserLocality]:
        """{sub-cell ID: UserLocality} for those of the given sub-cells inside a user locality
        (the smallest, else the newest, where several overlap)."""
        wanted = list(sub_cell_ids)
        best: dict[int, tuple[int, int]] = {}
        for k in range(0, len(wanted), 900):
            chunk = wanted[k:k + 900]
            for sid, n, size in self._db.execute(
                    "SELECT c.sub_cell, l.id, l.sub_cells FROM locality_cells c JOIN localities l ON l.id = c.locality "
                    f"WHERE l.removed_at IS NULL AND c.sub_cell IN ({','.join('?' * len(chunk))})", chunk):
                if sid not in best or (size, -n) < best[sid]:
                    best[sid] = (size, -n)
        got: dict[int, UserLocality] = {}
        for _sid, (_, neg) in best.items():
            if -neg not in got:
                got[-neg] = self._get(-neg)
        return {sid: got[-neg] for sid, (_, neg) in best.items()}

    def at_cell(self, cell_id: int) -> UserLocality | None:
        """The user locality of a cell: the one most of its 25 sub-cells have (sub-cells in none
        count too, as for OpenStreetMap localities), or None."""
        first = ids.sub_cell_id(cell_id, 0)
        found = self.at_sub_cells(range(first, first + SUB * SUB))
        block = [found[first + k].id if first + k in found else 0 for k in range(SUB * SUB)]
        counts = Counter(block)
        top = max(counts.values())
        tied = [t for t, c in counts.items() if c == top]
        main = block[12] if block[12] in tied else min(tied)
        return next((loc for loc in found.values() if loc.id == main), None) if main else None

    # -- Backup ----------------------------------------------------------------------

    def export_data(self, include_removed: bool = False) -> dict[str, Any]:
        """Every locality, road and street access note as JSON-ready data, to back up or move to
        another server (see import_data)."""
        return {"user_places_version": USER_PLACES_VERSION, "exported_at": _now(),
                "access_notes": [{"source_id": n.source_id, "name": n.name, "line": [list(p) for p in n.line],
                                  "vehicles": list(n.vehicles), "updated_by": n.updated_by, "updated_at": n.updated_at,
                                  "removed": n.removed} for n in self.access_notes(include_removed)],
                "roads": [{"key": r.key, "name": r.name, "local_name": r.local_name, "kind": r.kind,
                           "line": [list(p) for p in r.line], "details": r.details, "created_by": r.created_by,
                           "created_at": r.created_at, "removed": r.removed} for r in self.roads(include_removed)],
                "localities": [{"key": loc.key, "name": loc.name, "local_name": loc.local_name, "kind": loc.kind,
                                "shape": loc.shape, "references": [r.place_key for r in loc.references],
                                "created_by": loc.created_by, "created_at": loc.created_at,
                                "removed": loc.removed}
                               for loc in self.localities(include_removed)]}

    def import_data(self, data: Mapping[str, Any], *, author: str) -> list[UserLocality | UserRoad | StreetAccess]:
        """Add the localities, roads and access notes of export_data() output (skipping removed
        ones, keys already here, and notes for a source_id or line already here). Returns the
        localities, roads and StreetAccess answers added."""
        if data.get("user_places_version") != USER_PLACES_VERSION:
            raise UserPlacesError(f"not user places version {USER_PLACES_VERSION} data")
        added: list[UserLocality | UserRoad | StreetAccess] = []
        for item in data["localities"]:
            if item.get("removed") or self._key_exists(item["key"]):
                continue
            shape = item["shape"]
            added.append(self.add(item["name"], author=author, kind=item["kind"], local_name=item.get("local_name"),
                                  polygon=shape.get("polygon"), cells=shape.get("sub_cells"),
                                  references=item["references"], nearest=0 if item["references"] else None))
        for item in data.get("roads", []):
            if item.get("removed") or self._one("SELECT 1 FROM roads WHERE key = ?", (item["key"],)):
                continue
            added.append(self.add_road(item["name"], author=author, line=item["line"], kind=item["kind"],
                                       local_name=item.get("local_name"), details=item.get("details")))
        lines = {json.dumps([list(p) for p in n.line]) for n in self.access_notes()}
        for item in data.get("access_notes", []):
            if item.get("removed") or json.dumps(item["line"]) in lines:
                continue
            if item.get("source_id") and self._one(
                    "SELECT 1 FROM access_notes WHERE source_id = ? AND removed_at IS NULL", (item["source_id"],)):
                continue
            added.append(self.set_access(item["vehicles"], author=author, line=item["line"],
                                         source_id=item.get("source_id"), name=item.get("name")))
        return added

    # -- Internals ------------------------------------------------------------------

    def _one(self, sql: str, args: Sequence[Any]) -> tuple[Any, ...] | None:
        """The first row of a query, with the statement finished at once: an unfinished SELECT keeps
        its read snapshot open, which makes this connection's next write wait on SQLite's locks."""
        rows = self._db.execute(sql, args).fetchall()
        return tuple(rows[0]) if rows else None

    def _places_version(self) -> int:
        return int(self.grid.meta.get("places_version", 0))

    def _key_exists(self, key: str) -> bool:
        return self._one("SELECT 1 FROM localities WHERE key = ?", (key,)) is not None

    def _free_key(self, base: str) -> str:
        key, n = base, 2
        keys = self.grid._key_map()
        while key in keys or self._key_exists(key):
            key, n = f"{base}-{n}", n + 1
        return key

    def _shape(self, polygon: Any, cells: Iterable[str | int] | None) -> tuple[dict[str, Any], dict[int, int]]:
        """(shape to store, sorted sub-cell IDs in Pakistan) from a polygon or cells."""
        if (polygon is None) == (cells is None):
            raise UserPlacesError("give exactly one of polygon or cells")
        if polygon is not None:
            rings = _rings_e7(polygon)
            shape: dict[str, Any] = {"polygon": [[[y / SCALE, x / SCALE] for y, x in ring] for ring in rings]}
            found: Iterable[int] = _sub_cells_in_polygon(self.grid.layout, rings)
        else:
            subs: set[int] = set()
            for item in cells or ():
                if isinstance(item, str):
                    level, item_id = ids.parse_code(item)
                elif isinstance(item, int) and not isinstance(item, bool):
                    level, item_id = "sub", item      # integer IDs are sub-cell IDs; give cells as codes
                else:
                    raise UserPlacesError(f"cells must be codes or sub-cell IDs, got {item!r}")
                self.grid.layout.item_from_id(item_id, level)
                if level == "cell":
                    first = ids.sub_cell_id(item_id, 0)
                    subs.update(range(first, first + SUB * SUB))
                else:
                    subs.add(item_id)
            shape = {"sub_cells": sorted(ids.sub_cell_code(s) for s in subs)}
            found = subs
        tagged = self._in_pakistan(sorted(found))
        if not tagged:
            raise UserPlacesError("the locality covers no sub-cell in Pakistan: a sub-cell counts when its "
                                  "centre is inside the polygon (sub-cells are ~200 m)")
        if len(tagged) > MAX_SUB_CELLS:
            raise UserPlacesError(f"the locality covers {len(tagged):,} sub-cells (~{len(tagged) * 0.04:,.0f} km²); "
                                  f"at most {MAX_SUB_CELLS:,} are allowed")
        return shape, tagged

    def _in_pakistan(self, subs: Iterable[int]) -> dict[int, int]:
        """The sub-cells with a tehsil, as {sub-cell ID: tehsil place ID} in ID order."""
        store = self.grid.store
        out: dict[int, int] = {}
        by_cell: dict[int, list[int]] = {}
        for s in subs:
            by_cell.setdefault(ids.parent(s), []).append(s)
        for cid, members in by_cell.items():
            row = store.cell_place(cid)
            if row is None:
                if not store.has_cell(cid) and not store.pakistan_edition:
                    raise NotInDatabaseError(f"cell {ids.cell_code(cid)} is not in this database "
                                             f"({store.path.name}): it was not included in this build")
                continue
            tehsil, blob = row
            tags = struct.unpack("<25H", blob) if blob else (tehsil,) * (SUB * SUB)
            for s in members:
                if tags[s & ids.SUB_MASK]:
                    out[s] = tags[s & ids.SUB_MASK]
        return out

    def _tehsil_and_centre(self, subs: Iterable[int] | dict[int, int]) -> tuple[Place, tuple[int, int]]:
        """(tehsil Place most of the sub-cells have, centre (lat_e7, lon_e7))."""
        tagged = subs if isinstance(subs, dict) else self._in_pakistan(subs)
        counts = Counter(tagged.values())
        top = max(counts.values())
        tehsil = self.grid._place_table()[min(t for t, c in counts.items() if c == top)]
        layout = self.grid.layout
        cents: list[tuple[int, int]] = []
        for s in tagged:
            q, i, j = layout.item_from_id(s, "sub")
            cents.append(layout.quadrants[q].item_centroid_e7(i, j, "sub"))
        my = sum(c[0] for c in cents) / len(cents)
        mx = sum(c[1] for c in cents) / len(cents)
        centre = min(cents, key=lambda c: ((c[0] - my) ** 2 + (0.85 * (c[1] - mx)) ** 2, c))
        return tehsil, centre

    def _references(self, lat_e7: int, lon_e7: int, keys: Iterable[str | int], nearest: int | None,
                    own: int | None = None, missing_ok: bool = False) -> list[Reference]:
        """[Reference] from the named localities, then the `nearest` official ones not named."""
        lat, lon = lat_e7 / SCALE, lon_e7 / SCALE
        points: list[tuple[str, str, float, float]] = []
        for key in keys:
            try:
                points.append(self._ref_point(key, own))
            except InvalidIdError:
                if not missing_ok:
                    raise
        named = {p[0] for p in points}
        if nearest:
            if not isinstance(nearest, int) or nearest < 0:
                raise UserPlacesError(f"nearest must be a whole number, got {nearest!r}")
            found: list[tuple[float, int, Place]] = []
            for p in self.grid._place_table().values():
                if p.level == "locality" and p.key not in named and p.lat is not None and p.lon is not None:
                    e, n = (p.lon - lon) * 94_350.0, (p.lat - lat) * 111_000.0
                    found.append((e * e + n * n, p.id, p))
            found.sort()
            points += [(p.key, p.name, p.lat or 0.0, p.lon or 0.0) for _, _, p in found[:nearest]]
        if len(points) > MAX_REFERENCES:
            raise UserPlacesError(f"at most {MAX_REFERENCES} references are allowed")
        if len({p[0] for p in points}) != len(points):
            raise UserPlacesError("a reference is given twice")
        refs: list[Reference] = []
        for key, name, plat, plon in points:
            way = navigate.direction(plat, plon, lat, lon)
            if way.distance_m > MAX_REFERENCE_KM * 1000:
                raise UserPlacesError(f"{name} ({key}) is {way.distance_m / 1000:.1f} km away; a reference must "
                                      f"be within {MAX_REFERENCE_KM:g} km")
            east, north = navigate.geodesy.local_offset(plat, plon, lat, lon)
            refs.append(Reference(key, name, round(way.distance_m, 1), round(way.bearing_deg, 1),
                                  way.compass_short, round(east, 1), round(north, 1)))
        return refs

    def _ref_point(self, ref: str | int, own: int | None) -> tuple[str, str, float, float]:
        """(key, name, lat, lon) of a reference: an official locality or another user locality."""
        p = None
        if isinstance(ref, int) and not isinstance(ref, bool) and ref < USER_ID_BASE:
            p = self.grid._place_table().get(ref)
            if p is None:
                raise InvalidIdError(f"no such place: {ref!r}")
        elif isinstance(ref, str):
            p = self.grid._key_map().get(ref.strip().lower())
        if p is not None:
            if p.level != "locality":
                raise UserPlacesError(f"{p.key} is a {p.level}; a reference must be a locality")
            return p.key, p.name, cast(float, p.lat), cast(float, p.lon)
        loc = self.get(ref)
        if loc.removed or loc.id - USER_ID_BASE == own:
            raise InvalidIdError(f"no such reference locality: {ref!r}")
        return loc.key, loc.name, loc.lat, loc.lon

    def _write_refs(self, n: int, refs: Iterable[Reference]) -> None:
        self._db.executemany("INSERT INTO refs VALUES (?,?,?,?,?,?,?,?,?)", [
            (n, k, r.place_key, r.place_name, r.distance_m, r.bearing_deg, r.direction, r.east_m, r.north_m)
            for k, r in enumerate(refs)])

    def _log(self, n: int, author: str, action: str, loc: UserLocality) -> None:
        self._db.execute("INSERT INTO history (locality, at, author, action, data) VALUES (?,?,?,?,?)",
                         (n, _now(), author, action, json.dumps(loc.to_dict(), ensure_ascii=False)))

    def _get(self, n: int) -> UserLocality:
        row = self._one(f"SELECT {_LOCALITY_COLUMNS} FROM localities WHERE id = ?", (n,))
        assert row is not None, f"no user locality {n}"
        return self._from_row(row)

    def _from_row(self, r: Sequence[Any]) -> UserLocality:
        refs = tuple(Reference(*x) for x in self._db.execute(
            "SELECT place_key, place_name, distance_m, bearing_deg, direction, east_m, north_m FROM refs "
            "WHERE locality = ? ORDER BY ord", (r[0],)))
        return UserLocality(USER_ID_BASE + r[0], r[1], r[2], r[3], r[4], r[5], r[6] / SCALE, r[7] / SCALE, r[8],
                            json.loads(r[9]), refs, r[10], r[11], r[12], r[13], r[14], r[15])


# -- Polygons ------------------------------------------------------------------------------
# (the polygon rules are those of custom zones, pakgrid.zones)

point_in_rings = _zones.point_in_rings


def _rings_e7(polygon: Any) -> list[RingE7]:
    try:
        return rings_e7(polygon)
    except ZoneError as exc:
        raise UserPlacesError(str(exc)) from None


def _sub_cells_in_polygon(layout: GridLayout, rings: Sequence[RingE7]) -> list[int]:
    """Sub-cell IDs whose centroid is inside the rings."""
    ys = [y for r in rings for y, _ in r]
    xs = [x for r in rings for _, x in r]
    # a sub-cell is ~0.0018 x 0.0021 degree: refuse boxes that could hold far too many before looking
    if (max(ys) - min(ys)) * (max(xs) - min(xs)) > MAX_SUB_CELLS * 16 * 18_000 * 21_000:
        raise UserPlacesError("the polygon is far too large for a locality")
    out: list[int] = []
    for c, m in sorted(polygon_masks(layout, rings).items()):
        base = ids.sub_cell_id(c, 0)
        out.extend(base + k for k in range(SUB * SUB) if m >> k & 1)
    return out
