"""The public `Grid` class: lookups against the grid database."""

import math
import struct
import threading
from bisect import bisect_right
from dataclasses import dataclass

from . import geodesy, ids, navigate, spec
from .address import address_from_point
from .boundary import FLAG_NAMES, OUTSIDE, Boundary, sub_flag
from .checksums import official_checksum, official_places_checksum
from .errors import GridMismatchError, IntegrityError, InvalidIdError, NotInDatabaseError, OutsideGridError
from .layout import SCALE, SUB, GridLayout, QuadrantLayout, to_e7
from .places import PLACES_VERSION, locality_is_near
from .store import SCHEMA_VERSION, GridStore
from .userplaces import USER_ID_BASE

MAX_CANDIDATE_RADIUS_M = 5000.0
MAX_RADIUS_M = 50_000.0          # largest radius for Grid.cells_within
_UNPACK = {"tehsil": "<25H", "locality": "<25I"}


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
class Nearby:
    """A cell or sub-cell found by `Grid.cells_within`."""
    id: int
    code: str
    distance_m: float           # from the point to the block's centroid (to its nearest edge with touching=True)
    bearing_deg: float          # compass bearing from the point to the block's centroid

    def to_dict(self):
        return {"id": self.id, "code": self.code, "distance_m": round(self.distance_m, 1),
                "bearing_deg": round(self.bearing_deg, 1)}


@dataclass(frozen=True)
class Separation:
    """Distance and direction between two cells or sub-cells, centroid to centroid (`Grid.distance`)."""
    from_code: str
    to_code: str
    distance_m: float
    bearing_deg: float          # compass bearing from the first block to the second
    compass: str                # 8-point name, e.g. 'North-East'
    compass_short: str          # 16-point abbreviation, e.g. 'NNE'
    east_m: float               # how far east (negative: west) the second block's centroid is
    north_m: float              # how far north (negative: south)

    @property
    def text(self):
        """'3.2 km NE'"""
        return f"{navigate.format_distance(self.distance_m)} {self.compass_short}"

    def to_dict(self):
        return {"from": self.from_code, "to": self.to_code, "distance_m": round(self.distance_m, 1),
                "bearing_deg": round(self.bearing_deg, 1), "compass": self.compass,
                "compass_short": self.compass_short, "east_m": round(self.east_m, 1),
                "north_m": round(self.north_m, 1), "text": self.text}


PLACE_LEVELS = ("province", "district", "tehsil", "locality")


@dataclass(frozen=True)
class Place:
    """A named place in the places layer: a province, district, tehsil or locality."""
    id: int
    level: str                  # "province", "district", "tehsil" or "locality"
    key: str                    # stable readable key, e.g. 'punjab/rawalpindi/taxila'
    name: str                   # display name, e.g. 'Taxila'
    parent_id: int | None       # a locality's tehsil, a tehsil's district, a district's province
    source_name: str            # the name exactly as in the source data
    source_id: str              # OCHA P-code ('PK60305') or OpenStreetMap ID ('node/123')
    kind: str | None = None     # a locality's OpenStreetMap kind: 'city', 'town', 'village', ...
    local_name: str | None = None   # a locality's name in Urdu or another local script, where known
    lat: float | None = None    # a locality's point (for an area, a point inside it)
    lon: float | None = None
    area: bool = False          # True for a locality mapped as an outline, not a point

    @property
    def user(self):
        """True for a locality added by a user (see pakgrid.userplaces), not from the official sources."""
        return self.source_id.startswith("user/")

    def to_dict(self):
        out = {"id": self.id, "level": self.level, "key": self.key, "name": self.name,
               "parent_id": self.parent_id}
        if self.level == "locality":
            out.update(kind=self.kind, local_name=self.local_name, point=[self.lat, self.lon], user=self.user)
        return out


@dataclass(frozen=True)
class PlaceTags:
    """The places a cell or sub-cell belongs to (see Grid.place)."""
    tehsil: Place
    district: Place | None
    province: Place | None
    locality: Place | None = None
    near: bool = False              # True when the sub-cell is near the locality rather than in it
    locality_distance_m: float | None = None   # from the locality's point to the sub-cell's centroid
    locality_direction: str | None = None      # 16-point compass direction of that, e.g. 'SE'
    osm_locality: Place | None = None          # the OpenStreetMap locality, also when a user one is shown
    user_locality: object = None               # the UserLocality shown as `locality`, if any

    @property
    def locality_text(self):
        """'Model Town', 'near Chak 25 (1.4 km SE)', or for a user locality its name and first
        reference, 'Gali 7 (500 m SW of Askari 2)'; None without a locality."""
        if not self.locality:
            return None
        if self.user_locality is not None:
            return self.user_locality.text
        if not self.near:
            return self.locality.name
        return (f"near {self.locality.name} ({navigate.format_distance(self.locality_distance_m)} "
                f"{self.locality_direction})")

    @property
    def label(self):
        """'Lahore Cant, Lahore Cantt, Lahore, Punjab': locality (where there is one), tehsil, district,
        province. A name the same as the one before it is said once ('Taxila, Rawalpindi, Punjab')."""
        names = ([self.locality_text] if self.locality else []) + [
            p.name for p in (self.tehsil, self.district, self.province) if p]
        return ", ".join(n for k, n in enumerate(names) if not k or n.lower() != names[k - 1].lower())

    def to_dict(self):
        def brief(p):
            return {"id": p.id, "key": p.key, "name": p.name} if p else None
        loc = None
        if self.locality:
            loc = dict(brief(self.locality), kind=self.locality.kind, local_name=self.locality.local_name,
                       near=self.near, text=self.locality_text,
                       distance_m=None if self.locality_distance_m is None else round(self.locality_distance_m),
                       direction=self.locality_direction, user=self.user_locality is not None)
            if self.user_locality is not None:
                loc["references"] = [r.to_dict() for r in self.user_locality.references]
                loc["osm_locality"] = brief(self.osm_locality)
        return {"label": self.label, "locality": loc, "tehsil": brief(self.tehsil),
                "district": brief(self.district), "province": brief(self.province)}


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
    place: PlaceTags | None = None   # the sub-cell's locality, tehsil, district and province (places layer)

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

    @property
    def address(self):
        """The point as a grid address, e.g. '11-0140-0471-00 W23 S18' (see pakgrid.address)."""
        return address_from_point(self.lat, self.lon)

    def to_dict(self):
        return {
            "grid_version": spec.GRID_VERSION,
            "address": self.address.text,
            "point": [self.lat, self.lon],
            "sub_cell": self.sub_cell.to_dict(),
            "cell": {"id": self.cell_id, "code": self.cell_code},
            "offset": self.offset.to_dict(),
            "accuracy_m": self.accuracy_m,
            "confident": self.confident,
            "edge_distance_m": round(self.edge_distance_m, 1),
            "candidates": [ids.sub_cell_code(c) for c in self.candidates],
            "in_pakistan": self.in_pakistan,
            "place": self.place.to_dict() if self.place else None,
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
        self._places = None
        self._keys = None
        self._load_lock = threading.Lock()
        self._user = None
        self._has_places = None
        self.layout = _layout_from_store(store)
        if not self.layout.same_shape(GridLayout.from_spec()):
            raise GridMismatchError("database rows and columns do not match the grid rules of this library")

    @classmethod
    def open(cls, path, user_places=None):
        """Open a grid database; with user_places, also attach that user places file."""
        grid = cls(GridStore(path))
        if user_places is not None:
            grid.attach_user_places(user_places)
        return grid

    def close(self):
        if self._user is not None:
            self._user.close()
        self.store.close()

    # -- User places ---------------------------------------------------------

    def attach_user_places(self, path):
        """Use a user places file (created if missing; see pakgrid.userplaces) for place lookups,
        and return it: add, change and remove localities through it."""
        from .userplaces import UserPlaces
        if not self.has_places:
            self._place_table()                 # raises the "no places layer" error
        if self._user is not None:
            self._user.close()
        self._user = UserPlaces(path, self)
        return self._user

    @property
    def user_places(self):
        """The attached UserPlaces, or None."""
        return self._user

    def _user_place(self, loc):
        """A user locality as a Place (level 'locality', source_id 'user/N')."""
        tehsil = self._key_map().get(loc.tehsil_key)
        return Place(loc.id, "locality", loc.key, loc.name, tehsil.id if tehsil else None, loc.name,
                     f"user/{loc.id - USER_ID_BASE}", loc.kind, loc.local_name, loc.lat, loc.lon, True)

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
        place = (self._tags(cid, sub.index, lambda: (to_e7(sub.centroid[0]), to_e7(sub.centroid[1])))
                 if self.has_places else None)
        return Location(lat, lon, sub, offset, edge, accuracy_m, confident, candidates, in_pk, place)

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

    def cells_within(self, lat, lon, radius_m, level="cell", pakistan_only=False, touching=False):
        """The cells (or sub-cells) within `radius_m` of a point, nearest first: [Nearby].

        A block counts when its centroid is within the radius; with touching=True, when any part
        of it is (distance_m is then to its nearest edge, 0 for the block holding the point).
        Distances are straight lines, as `direction()` measures them. Ties: the smaller ID. With
        pakistan_only, only blocks inside Pakistan or on its border. Radius: up to MAX_RADIUS_M.
        """
        lat_e7, lon_e7 = _point_e7(lat, lon)
        if isinstance(radius_m, bool) or not isinstance(radius_m, (int, float)) or \
                not 0 <= radius_m <= MAX_RADIUS_M:
            raise ValueError(f"radius_m must be a number from 0 to {MAX_RADIUS_M:g}, got {radius_m!r}")
        if level not in ("cell", "sub"):
            raise ValueError(f"level must be 'cell' or 'sub', got {level!r}")
        lat, lon = lat_e7 / SCALE, lon_e7 / SCALE
        m_lat, m_lon = geodesy.metres_per_degree(lat)
        # the search box: the radius in degrees at the point's latitude, plus 2% and a block, which
        # covers the change of scale across the box
        pad = 1.02 * radius_m + (1500 if level == "cell" else 300)
        found = []
        for q, i, j in self.layout.items_in_area(to_e7(lat - pad / m_lat), to_e7(lon - pad / (m_lon * 0.98)),
                                                 to_e7(lat + pad / m_lat), to_e7(lon + pad / (m_lon * 0.98)), level):
            ql = self.layout.quadrants[q]
            clat, clon = (v / SCALE for v in ql.item_centroid_e7(i, j, level))
            way = navigate.direction(lat, lon, clat, clon)
            if touching:
                s, w, n, e = (v / SCALE for v in ql.item_bounds_e7(i, j, level))
                d = math.hypot(max(w - lon, 0.0, lon - e) * m_lon, max(s - lat, 0.0, lat - n) * m_lat)
            else:
                d = way.distance_m
            if d <= radius_m:
                item_id = GridLayout.item_id(q, i, j, level)
                found.append((d, item_id, way.bearing_deg))
        found.sort()
        if pakistan_only:
            flags = self._flags([f[1] for f in found], level)
            found = [f for f in found if flags.get(f[1], OUTSIDE) != OUTSIDE]
        code = ids.cell_code if level == "cell" else ids.sub_cell_code
        return [Nearby(item_id, code(item_id), d, b) for d, item_id, b in found]

    def distance(self, a, b, level="cell"):
        """Distance and direction from one cell or sub-cell to another, centroid to centroid.

        `a` and `b` are IDs (of `level`) or codes (which set their own level, so a cell and a
        sub-cell can be compared). No database access is needed.
        """
        ends = []
        for item in (a, b):
            lv, item_id = self._resolve_any(item, level)
            q, i, j = self.layout.item_from_id(item_id, lv)
            clat, clon = (v / SCALE for v in self.layout.quadrants[q].item_centroid_e7(i, j, lv))
            ends.append((ids.cell_code(item_id) if lv == "cell" else ids.sub_cell_code(item_id), clat, clon))
        (ca, lat1, lon1), (cb, lat2, lon2) = ends
        way = navigate.direction(lat1, lon1, lat2, lon2)
        m_lat, m_lon = geodesy.metres_per_degree((lat1 + lat2) / 2)
        return Separation(ca, cb, way.distance_m, way.bearing_deg, way.compass, way.compass_short,
                          (lon2 - lon1) * m_lon, (lat2 - lat1) * m_lat)

    def zone(self, *, polygon=None, circle=None, cells=None, place=None, pakistan_only=False):
        """A custom zone (pakgrid.zones.Zone) made from exactly one of:

        polygon: rings of (lat, lon), or a GeoJSON Polygon/MultiPolygon/Feature; the sub-cells whose
                 centroid is inside it.
        circle:  (lat, lon, radius_m) or (lat, lon, radius_m, touching); as cells_within(level="sub").
        cells:   cell codes (all 25 sub-cells), sub-cell codes or sub-cell IDs.
        place:   a place key, ID or Place; its sub-cells as cells_in_place(level="sub").

        pakistan_only keeps only sub-cells inside Pakistan or on its border (reads the database).
        Combine zones with | (union), & (intersection) and - (difference).
        """
        from .zones import ZoneError, make_zone, polygon_masks, rings_e7
        given = [k for k, v in (("polygon", polygon), ("circle", circle), ("cells", cells), ("place", place))
                 if v is not None]
        if len(given) != 1:
            raise ZoneError("give exactly one of polygon, circle, cells or place")
        masks = {}
        if polygon is not None:
            masks = polygon_masks(self.layout, rings_e7(polygon))
        elif circle is not None:
            if not isinstance(circle, (list, tuple)) or len(circle) not in (3, 4):
                raise ZoneError("circle must be (lat, lon, radius_m) or (lat, lon, radius_m, touching)")
            for n in self.cells_within(*circle[:3], level="sub", touching=bool(circle[3]) if len(circle) == 4 else False):
                cid, sub = ids.split_sub_cell_id(n.id)
                masks[cid] = masks.get(cid, 0) | 1 << sub
        elif cells is not None:
            if isinstance(cells, (str, int)):
                cells = [cells]
            for item in cells:
                if isinstance(item, str):
                    level, item_id = ids.parse_code(item)
                elif isinstance(item, int) and not isinstance(item, bool):
                    level, item_id = "sub", item
                else:
                    raise ZoneError(f"cells must be codes or sub-cell IDs, got {item!r}")
                self.layout.item_from_id(item_id, level)
                if level == "cell":
                    masks[item_id] = (1 << SUB * SUB) - 1
                else:
                    cid, sub = ids.split_sub_cell_id(item_id)
                    masks[cid] = masks.get(cid, 0) | 1 << sub
        else:
            masks = self._place_masks(place)
        if pakistan_only and masks:
            stored = self.store.sub_masks(masks)
            missing = [c for c in masks if c not in stored]
            if missing and not self.store.pakistan_edition:
                raise self.store._missing(missing[0])
            masks = {c: m & (stored[c][0] | stored[c][1]) for c, m in masks.items() if c in stored}
        return make_zone(masks, self.layout)

    def _place_masks(self, place):
        """{cell ID: mask} of a place's sub-cells, read straight from the tag tables."""
        p = self.get_place(place)
        if p.user:
            masks = {}
            for s in self._user.sub_cells(p.id):
                cid, sub = ids.split_sub_cell_id(s)
                masks[cid] = masks.get(cid, 0) | 1 << sub
            return masks
        if p.level == "locality":
            layer, tags = "locality", {p.id}
        else:
            layer = "tehsil"
            tags = {t.id for t in self._place_table().values() if t.level == "tehsil" and self._within(t, p)}
        full = (1 << SUB * SUB) - 1
        masks = {cid: full for cid, _, subs in self.store.cells_with_tags(tags, layer) if subs is None}
        for cid, _, subs in self.store.split_cells(layer):
            m = 0
            for k, t in enumerate(struct.unpack(_UNPACK[layer], subs)):
                if t in tags:
                    m |= 1 << k
            if m:
                masks[cid] = m
        return masks

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

    # -- Places (province, district, tehsil, locality) ----------------------

    @property
    def has_places(self):
        """True if this database has the places layer of this library's places version."""
        if self._has_places is None:
            self._has_places = (self.meta.get("places_version") == str(PLACES_VERSION)
                                and self.store.has_places())
        return self._has_places

    def place(self, item, level="sub"):
        """The locality, tehsil, district and province of a sub-cell (or cell); None outside Pakistan.

        A cell's place is the tehsil, and the locality, most of its 25 sub-cells have; where a
        boundary runs through the cell, ask for its sub-cells to get each one's own tags.
        """
        level, item_id = self._resolve_any(item, level)
        q, i, j = self.layout.item_from_id(item_id, level)
        def centroid():
            return self.layout.quadrants[q].item_centroid_e7(i, j, level)
        if level == "cell":
            return self._tags(item_id, None, centroid)
        cid, sub = ids.split_sub_cell_id(item_id)
        return self._tags(cid, sub, centroid)

    def places(self, level=None):
        """All places, or those of one level ("province", "district", "tehsil", "locality"), by ID."""
        found = self._place_table().values()
        if level is None:
            return list(found)
        if level not in PLACE_LEVELS:
            raise ValueError(f"level must be one of {PLACE_LEVELS}, got {level!r}")
        return [p for p in found if p.level == level]

    def find_places(self, text, level=None):
        """Places whose name (or local name) contains `text`, or whose key is `text` (case-insensitive);
        user localities, if a user places file is attached, come last."""
        t = text.strip().lower()
        found = [p for p in self.places(level)
                 if t in p.name.lower() or t == p.key or t in p.source_name.lower()
                 or (p.local_name and t in p.local_name)]
        if self._user is not None and level in (None, "locality"):
            found += [self._user_place(loc) for loc in self._user.find(text)]
        return found

    def get_place(self, place):
        """A Place by ID, key ('punjab/rawalpindi/taxila') or Place."""
        table = self._place_table()
        if isinstance(place, Place):
            return place
        if isinstance(place, int) and not isinstance(place, bool) and place in table:
            return table[place]
        if isinstance(place, str) and place.strip().lower() in self._key_map():
            return self._key_map()[place.strip().lower()]
        if self._user is not None and isinstance(place, (int, str)) and not isinstance(place, bool):
            try:
                return self._user_place(self._user.get(place))
            except InvalidIdError:
                pass
        raise InvalidIdError(f"no such place: {place!r} (use a place ID, or a key such as 'punjab/lahore')")

    def cells_in_place(self, place, level="cell"):
        """IDs of the cells (or sub-cells) in a place, in ID order.

        level="cell": cells whose main tehsil (for a locality: main locality) is in the place.
        level="sub": every sub-cell tagged with the place (or a tehsil in it), exactly, including
        those of cells that a boundary runs through.
        """
        p = self.get_place(place)
        if level not in ("cell", "sub"):
            raise ValueError(f"level must be 'cell' or 'sub', got {level!r}")
        if p.user:
            subs = self._user.sub_cells(p.id)
            if level == "sub":
                return subs
            return sorted(cid for cid in {ids.parent(s) for s in subs}
                          if (u := self._user.at_cell(cid)) is not None and u.id == p.id)
        if p.level == "locality":
            layer, tags = "locality", {p.id}
        else:
            layer = "tehsil"
            tags = {t.id for t in self._place_table().values() if t.level == "tehsil" and self._within(t, p)}
        rows = self.store.cells_with_tags(tags, layer)
        if level == "cell":
            return sorted(cid for cid, _, _ in rows)
        out = []
        for cid, tag, subs in rows:
            if subs is None:            # the 25 sub-cell IDs of a cell are consecutive
                first = ids.sub_cell_id(cid, 0)
                out.extend(range(first, first + SUB * SUB))
        for cid, tag, subs in self.store.split_cells(layer):
            out.extend(ids.sub_cell_id(cid, k) for k, t in enumerate(struct.unpack(_UNPACK[layer], subs))
                       if t in tags)
        out.sort()
        return out

    def _key_map(self):
        if self._keys is None:
            table = self._place_table()
            with self._load_lock:
                if self._keys is None:
                    self._keys = {p.key: p for p in table.values()}
        return self._keys

    def _within(self, place, ancestor):
        table = self._place_table()
        while place is not None:
            if place.id == ancestor.id:
                return True
            place = table.get(place.parent_id)
        return False

    def _place_table(self):
        if self._places is not None:
            return self._places
        with self._load_lock:           # threads arriving together load the table once
            return self._load_place_table()

    def _load_place_table(self):
        if self._places is None:
            if not self.has_places:
                found = self.meta.get("places_version")
                raise NotInDatabaseError(
                    f"this database ({self.store.path.name}) has "
                    + (f"places version {found}, not {PLACES_VERSION}; rebuild it" if found else "no places layer; add it")
                    + " with `pakgrid build-places`")
            self._places = {
                r[0]: Place(r[0], PLACE_LEVELS[r[1] - 1], r[2], r[3], r[4], r[5], r[6], r[7], r[8],
                            None if r[9] is None else r[9] / SCALE, None if r[10] is None else r[10] / SCALE,
                            bool(r[11]))
                for r in self.store.places_rows()}
        return self._places

    def _tags(self, cid, sub, centroid):
        """PlaceTags of a cell (sub=None: its main tags) or one of its sub-cells.

        centroid() gives the block's centroid in 1e-7 degree; it is needed only for a point locality.
        """
        table = self._place_table()
        row = self.store.cell_place(cid)
        if row is None:
            if not self.store.has_cell(cid) and not self.store.pakistan_edition:
                raise self.store._missing(cid)
            return None                       # wholly outside Pakistan
        tehsil, subs = row
        if sub is not None and subs is not None:
            tehsil = struct.unpack(_UNPACK["tehsil"], subs)[sub]
        if not tehsil:
            return None                       # this sub-cell lies outside Pakistan
        t = table[tehsil]
        d = table.get(t.parent_id)
        p = table.get(d.parent_id) if d else None
        row = self.store.cell_place(cid, "locality")
        locality = 0
        if row is not None:
            locality, subs = row
            if sub is not None and subs is not None:
                locality = struct.unpack(_UNPACK["locality"], subs)[sub]
        osm = table[locality] if locality else None
        if self._user is not None:
            user = (self._user.at_cell(cid) if sub is None
                    else self._user.at_sub_cells([ids.sub_cell_id(cid, sub)]).get(ids.sub_cell_id(cid, sub)))
            if user is not None:
                return PlaceTags(t, d, p, self._user_place(user), osm_locality=osm, user_locality=user)
        if osm is None or osm.area:
            return PlaceTags(t, d, p, osm, osm_locality=osm)
        lat_e7, lon_e7 = centroid()
        near = locality_is_near(osm.kind, osm.area, lat_e7, lon_e7, to_e7(osm.lat), to_e7(osm.lon))
        way = navigate.direction(osm.lat, osm.lon, lat_e7 / SCALE, lon_e7 / SCALE)
        return PlaceTags(t, d, p, osm, near, way.distance_m, way.compass_short, osm_locality=osm)

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
                "official_sha256": official_checksum(self.meta),
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
            "places": None if "places_version" not in self.meta else {
                "version": int(self.meta["places_version"]),
                "places": int(self.meta.get("places_count", 0)),
                "localities": int(self.meta.get("places_localities", 0)),
                "cells": int(self.meta.get("places_cells", 0)),
                "split_cells": int(self.meta.get("places_split_cells", 0)),
                "sources": self.meta.get("places_sources"),
                "attribution": self.meta.get("places_attribution"),
                "places_sha256": self.meta.get("places_sha256"),
                "official_sha256": official_places_checksum(self.meta),
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
