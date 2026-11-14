"""Street access: what can drive through a street, as the people who live and work there say.

Riders lose time when a navigation app sends a car down a gali only a motorbike gets through. The
people who know are the shop owners and residents, so the app asks them one question with a few
buttons, "What can drive through this street?", and the library keeps the answer on that street:

    user = grid.attach_user_places("user_places.sqlite")
    user.set_access(largest="rickshaw", author="ali", road="punjab/lahore/lahore-cantt/roads/street-7")
    user.set_access(largest="motorbike", author="ali", line=[(31.5201, 74.3571), (31.5203, 74.3590)],
                    source_id="way/123456789", name="Gali 3")     # a street the library has no road for
    a = user.access_at(31.5202, 74.3580)          # the answer for the street at this pin, or None
    a.largest, a.text, a.allows("car")            # 'motorbike', 'Up to motorbike size', False

Rules:

- The vehicles are VEHICLES, smallest first: foot, bicycle, motorbike, rickshaw (including
  Qingqi / chingchi), car, pickup (pickups and loaders: Suzuki, Shehzore), truck.
- An answer is a set of them. Most streets are "up to X": everything up to the largest that fits
  (up_to), which is what `largest=` gives. A list can say otherwise (a barrier that only lets
  motorbikes and people through: ["foot", "motorbike"]).
- On a **user road** (pakgrid.roads) the answer is the road's `vehicles` detail, changed with the
  road's own history. On any **other street** (one in OpenStreetMap or the app's own map, which the
  library has no road for), it is an **access note**: the stretch of street as a line (2 or more
  points, at most MAX_NOTE_KM long), the street's ID in that map (`source_id`, e.g. 'way/123'),
  and its name. A note with the same source_id is updated in place, keeping its history.
- The latest answer counts. Every answer keeps who gave it and when; removed notes keep their
  history.
- access_at(lat, lon) gives the answer for the nearest street with one within STREET_ON_M (25 m):
  user roads with a `vehicles` detail and access notes alike. Ties go to the newest answer.
  Lookups (Grid.locate, Grid.place) carry it as PlaceTags.access.
"""

import json
import math
from dataclasses import dataclass

from . import ids
from .errors import InvalidIdError
from .layout import SCALE, SUB

VEHICLES = ("foot", "bicycle", "motorbike", "rickshaw", "car", "pickup", "truck")
LABELS = {"foot": "on foot", "bicycle": "bicycle", "motorbike": "motorbike", "rickshaw": "rickshaw",
          "car": "car", "pickup": "pickup", "truck": "truck"}
LABELS_UR = {"foot": "پیدل", "bicycle": "سائیکل", "motorbike": "موٹر سائیکل", "rickshaw": "رکشہ / چنگچی",
             "car": "کار", "pickup": "پک اپ / لوڈر", "truck": "ٹرک"}
MAX_NOTE_KM = 5.0

ACCESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_notes (
    id INTEGER PRIMARY KEY, source_id TEXT, name TEXT, line TEXT NOT NULL, length_m REAL NOT NULL,
    vehicles TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL, removed_by TEXT, removed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS access_notes_source ON access_notes (source_id)
    WHERE source_id IS NOT NULL AND removed_at IS NULL;
CREATE TABLE IF NOT EXISTS access_cells (note INTEGER NOT NULL, sub_cell INTEGER NOT NULL,
    PRIMARY KEY (note, sub_cell)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS access_cells_sub_cell ON access_cells (sub_cell);
CREATE TABLE IF NOT EXISTS access_history (
    id INTEGER PRIMARY KEY, note INTEGER NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL,
    action TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS access_history_note ON access_history (note);
"""
_NOTE_COLUMNS = ("id, source_id, name, line, length_m, vehicles, created_by, created_at, updated_by, updated_at, "
                 "removed_by, removed_at")


def up_to(largest):
    """Every vehicle up to and including `largest`, smallest first: up_to('rickshaw') is
    ('foot', 'bicycle', 'motorbike', 'rickshaw')."""
    if largest not in VEHICLES:
        raise ValueError(f"unknown vehicle {largest!r}; the vehicles are {', '.join(VEHICLES)}")
    return VEHICLES[:VEHICLES.index(largest) + 1]


def vehicles_list(vehicles, error=ValueError):
    """The vehicles, checked, without repeats, smallest first. At least one is needed."""
    if isinstance(vehicles, str) or not isinstance(vehicles, (list, tuple, set, frozenset)):
        raise error(f"vehicles must be a list, e.g. ['foot', 'motorbike'], got {vehicles!r}")
    bad = [v for v in vehicles if v not in VEHICLES]
    if bad:
        raise error(f"unknown vehicle {bad[0]!r}; the vehicles are {', '.join(VEHICLES)}")
    if not vehicles:
        raise error("give at least one vehicle (['foot'] when only people on foot get through)")
    return tuple(v for v in VEHICLES if v in vehicles)


def access_text(vehicles):
    """'On foot only', 'Up to rickshaw size', 'All vehicles', or 'Only on foot, motorbike'."""
    vehicles = vehicles_list(vehicles)
    if vehicles == ("foot",):
        return "On foot only"
    if vehicles == VEHICLES:
        return "All vehicles"
    if vehicles == up_to(vehicles[-1]):
        return f"Up to {LABELS[vehicles[-1]]} size"
    return "Only " + ", ".join(LABELS[v] for v in vehicles)


@dataclass(frozen=True)
class StreetAccess:
    """What can drive through a street (see the module docstring)."""
    vehicles: tuple             # smallest first
    source: str                 # 'road' (a user road's detail) or 'note' (an access note)
    key: str                    # the road's key, or 'note/<id>'
    name: str | None            # the street's name, if known
    source_id: str | None       # a note's street ID in another map, e.g. 'way/123'
    set_by: str
    set_at: str
    distance_m: float | None = None     # from the pin, for access_at

    @property
    def largest(self):
        return self.vehicles[-1]

    @property
    def nested(self):
        """True when it is 'up to the largest': every smaller vehicle gets through too."""
        return self.vehicles == up_to(self.largest)

    @property
    def text(self):
        return access_text(self.vehicles)

    def allows(self, vehicle):
        """Whether this vehicle gets through."""
        if vehicle not in VEHICLES:
            raise ValueError(f"unknown vehicle {vehicle!r}; the vehicles are {', '.join(VEHICLES)}")
        return vehicle in self.vehicles

    def to_dict(self):
        return {"vehicles": list(self.vehicles), "largest": self.largest, "nested": self.nested, "text": self.text,
                "source": self.source, "key": self.key, "name": self.name, "source_id": self.source_id,
                "set_by": self.set_by, "set_at": self.set_at,
                "distance_m": None if self.distance_m is None else round(self.distance_m, 1)}


@dataclass(frozen=True)
class AccessNote:
    """The answer for a street the library has no user road for: a stretch of street and its vehicles."""
    id: int
    source_id: str | None
    name: str | None
    line: tuple                 # ((lat, lon), ...)
    length_m: float
    vehicles: tuple
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str
    removed_by: str | None = None
    removed_at: str | None = None

    @property
    def key(self):
        return f"note/{self.id}"

    @property
    def removed(self):
        return self.removed_at is not None

    def access(self, distance_m=None):
        return StreetAccess(self.vehicles, "note", self.key, self.name, self.source_id, self.updated_by,
                            self.updated_at, distance_m)

    def to_dict(self):
        return {"id": self.id, "key": self.key, "source_id": self.source_id, "name": self.name,
                "line": [list(p) for p in self.line], "length_m": self.length_m, "vehicles": list(self.vehicles),
                "text": access_text(self.vehicles), "created_by": self.created_by, "created_at": self.created_at,
                "updated_by": self.updated_by, "updated_at": self.updated_at,
                "removed_by": self.removed_by, "removed_at": self.removed_at}

    def to_geojson(self):
        return {"type": "Feature", "id": self.key,
                "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in self.line]},
                "properties": {"key": self.key, "source_id": self.source_id, "name": self.name,
                               "vehicles": list(self.vehicles), "text": access_text(self.vehicles)}}


def road_access(road, distance_m=None):
    """A user road's StreetAccess, or None when its `vehicles` detail is not set."""
    v = road.details.get("vehicles")
    if not v:
        return None
    return StreetAccess(vehicles_list(v), "road", road.key, road.name, None, road.updated_by, road.updated_at,
                        distance_m)


# -- Storage (mixed into UserPlaces) ------------------------------------------------------

class AccessMixin:
    """The street access methods of UserPlaces (see the module docstring)."""

    def set_access(self, vehicles=None, *, author, largest=None, road=None, line=None, source_id=None, name=None):
        """Record what can drive through a street: `vehicles` (a list) or `largest` (everything up
        to it). The street is a user road (`road`: key, ID or UserRoad), or any other street as an
        access note: its `source_id` in another map (a note with it is updated), and/or its `line`
        (needed for a new note), and its `name`. Returns the StreetAccess."""
        from .userplaces import UserPlacesError, _check_text, _now
        author = _check_text(author, "author")
        if (vehicles is None) == (largest is None):
            raise UserPlacesError("give exactly one of vehicles (a list) or largest (a vehicle)")
        try:
            vehicles = vehicles_list(vehicles) if vehicles is not None else up_to(largest)
        except ValueError as e:
            raise UserPlacesError(str(e)) from None
        if road is not None:
            if line is not None or source_id is not None or name is not None:
                raise UserPlacesError("for a user road, give only road (its line and name are the road's)")
            r = self.get_road(road)
            r = self.update_road(r, author=author, details={**r.details, "vehicles": list(vehicles)})
            return road_access(r)
        source_id = _check_text(source_id, "source_id", required=False)
        name = _check_text(name, "name", required=False)
        old = None
        if source_id is not None:
            row = self._one(f"SELECT {_NOTE_COLUMNS} FROM access_notes WHERE source_id = ? AND removed_at IS NULL",
                            (source_id,))
            old = self._note_from_row(row) if row else None
        if old is None and line is None:
            raise UserPlacesError("give the street's line (2 or more points along it), or a road"
                                  + (f"; there is no note for {source_id!r} yet" if source_id else ""))
        db, now = self._db, _now()
        with self._writing(db):
            fields = {"vehicles": json.dumps(list(vehicles)), "updated_by": author, "updated_at": now}
            if name is not None or old is None:
                fields["name"] = name
            if line is not None:
                pts, subs, length = self._note_shape(line)
                fields.update(line=json.dumps([[y / SCALE, x / SCALE] for y, x in pts]), length_m=round(length, 1))
            if old is None:
                fields.update(source_id=source_id, created_by=author, created_at=now)
                n = db.execute(f"INSERT INTO access_notes ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                               tuple(fields.values())).lastrowid
                action = "add"
            else:
                n = old.id
                db.execute(f"UPDATE access_notes SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                           (*fields.values(), n))
                action = "update"
            if line is not None:
                db.execute("DELETE FROM access_cells WHERE note = ?", (n,))
                db.executemany("INSERT INTO access_cells VALUES (?, ?)", [(n, s) for s in subs])
            note = self._get_note(n)
            self._log_note(n, author, action, note)
        self._has_notes = True
        return note.access()

    def remove_access(self, street, *, author):
        """Take back an answer: remove an access note (its ID, 'note/<id>', source_id or AccessNote;
        its history is kept), or clear a user road's `vehicles` detail (its key or UserRoad)."""
        from .userplaces import _check_text, _now
        from .roads import UserRoad
        author = _check_text(author, "author")
        road = street if isinstance(street, UserRoad) else None
        if isinstance(street, str) and not street.startswith("note/"):
            try:
                road = self.get_road(street)
            except InvalidIdError:
                pass                                    # not a road key: a note's source_id
        if road is not None:
            r = self.get_road(road)
            self.update_road(r, author=author, details={k: v for k, v in r.details.items() if k != "vehicles"})
            return None
        note = self.get_access_note(street)
        if note.removed:
            return note
        db = self._db
        with self._writing(db):
            db.execute("UPDATE access_notes SET removed_by = ?, removed_at = ? WHERE id = ?", (author, _now(), note.id))
            note = self._get_note(note.id)
            self._log_note(note.id, author, "remove", note)
        self._has_notes = None
        return note

    def get_access_note(self, note):
        """An AccessNote by ID, 'note/<id>', a live note's source_id, or AccessNote (removed ones too by ID)."""
        if isinstance(note, AccessNote):
            note = note.id
        row = None
        if isinstance(note, int) and not isinstance(note, bool):
            row = self._one(f"SELECT {_NOTE_COLUMNS} FROM access_notes WHERE id = ?", (note,))
        elif isinstance(note, str):
            if note.startswith("note/") and note[5:].isdigit():
                row = self._one(f"SELECT {_NOTE_COLUMNS} FROM access_notes WHERE id = ?", (int(note[5:]),))
            else:
                row = self._one(f"SELECT {_NOTE_COLUMNS} FROM access_notes WHERE source_id = ? AND removed_at IS NULL",
                                (note.strip(),))
        if row is None:
            raise InvalidIdError(f"no such access note: {note!r}")
        return self._note_from_row(row)

    def access_notes(self, include_removed=False):
        """All access notes, in the order they were added."""
        rows = self._db.execute(f"SELECT {_NOTE_COLUMNS} FROM access_notes"
                                + ("" if include_removed else " WHERE removed_at IS NULL") + " ORDER BY id").fetchall()
        return [self._note_from_row(r) for r in rows]

    def access_history(self, note=None):
        """Every change to access notes, oldest first (a user road's answers are in road_history)."""
        sql, args = "SELECT h.at, h.author, h.action, h.note, h.data FROM access_history h", ()
        if note is not None:
            sql, args = sql + " WHERE h.note = ?", (self.get_access_note(note).id,)
        return [{"at": at, "author": who, "action": action, "key": f"note/{n}", "note": json.loads(data)}
                for at, who, action, n, data in self._db.execute(sql + " ORDER BY h.id", args).fetchall()]

    def access_at(self, lat, lon, max_m=None):
        """What can drive through the street at this pin: the answer of the nearest street that has
        one within max_m (default STREET_ON_M, at most 100 m), as a StreetAccess, or None."""
        from .roads import STREET_MAX_M, STREET_ON_M, _subs_near, nearest_on_line
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        if not self.has_access:
            return None
        max_m = STREET_ON_M if max_m is None else min(float(max_m), STREET_MAX_M)
        subs = _subs_near(self.grid.layout, lat, lon, max_m)
        found = []
        if self.has_roads:
            for road in self.roads_in(subs):
                if road.details.get("vehicles"):
                    d = nearest_on_line(road.line, lat, lon)[0]
                    if d <= max_m:
                        found.append(road_access(road, d))
        if self.has_access_notes:
            for note in self._notes_in(subs):
                d = nearest_on_line(note.line, lat, lon)[0]
                if d <= max_m:
                    found.append(note.access(d))
        if not found:
            return None
        # nearest; then the newest answer; then by key, so the choice never depends on order
        found.sort(key=lambda a: a.key)
        found.sort(key=lambda a: a.set_at, reverse=True)
        return min(found, key=lambda a: round(a.distance_m, 6))

    @property
    def has_access_notes(self):
        if getattr(self, "_has_notes", None) is None:
            self._has_notes = self._one("SELECT 1 FROM access_notes WHERE removed_at IS NULL LIMIT 1", ()) is not None
        return self._has_notes

    @property
    def has_access(self):
        """Whether any street may have an answer (any access note or user road)."""
        return self.has_access_notes or self.has_roads

    def access_geojson(self):
        """Every access note as a GeoJSON FeatureCollection of LineStrings (user roads are in roads_geojson)."""
        return {"type": "FeatureCollection", "features": [n.to_geojson() for n in self.access_notes()]}

    # -- Internals --------------------------------------------------------------------

    def _ensure_access_tables(self, db):
        with self._writing(db):
            db.executescript(ACCESS_SCHEMA)

    def _note_shape(self, line):
        from .roads import line_e7, line_length_m, line_masks
        from .userplaces import UserPlacesError
        pts = line_e7(line, UserPlacesError)
        length = line_length_m(pts)
        if length > MAX_NOTE_KM * 1000:
            raise UserPlacesError(f"the stretch of street is {length / 1000:,.1f} km long; at most {MAX_NOTE_KM:g} km "
                                  "is allowed (give the part the answer is for)")
        masks = line_masks(self.grid.layout, pts)
        subs = sorted(ids.sub_cell_id(c, k) for c, m in masks.items() for k in range(SUB * SUB) if m >> k & 1)
        if not self._in_pakistan(subs):
            raise UserPlacesError("the street does not pass through any sub-cell in Pakistan")
        return pts, subs, length

    def _notes_in(self, sub_cell_ids):
        found = set()
        sub_cell_ids = list(sub_cell_ids)
        for k in range(0, len(sub_cell_ids), 900):
            chunk = sub_cell_ids[k:k + 900]
            found.update(n for (n,) in self._db.execute(
                "SELECT DISTINCT c.note FROM access_cells c JOIN access_notes a ON a.id = c.note "
                f"WHERE a.removed_at IS NULL AND c.sub_cell IN ({','.join('?' * len(chunk))})", chunk).fetchall())
        return [self._get_note(n) for n in sorted(found)]

    def _log_note(self, n, author, action, note):
        from .userplaces import _now
        self._db.execute("INSERT INTO access_history (note, at, author, action, data) VALUES (?,?,?,?,?)",
                         (n, _now(), author, action, json.dumps(note.to_dict(), ensure_ascii=False)))

    def _get_note(self, n):
        return self._note_from_row(self._one(f"SELECT {_NOTE_COLUMNS} FROM access_notes WHERE id = ?", (n,)))

    @staticmethod
    def _note_from_row(r):
        return AccessNote(r[0], r[1], r[2], tuple(tuple(p) for p in json.loads(r[3])), r[4],
                          tuple(json.loads(r[5])), r[6], r[7], r[8], r[9], r[10], r[11])
