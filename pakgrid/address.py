"""Grid addresses: a sub-cell code plus an offset in whole metres from its centre.

    11-0140-0471-00 W23 S18

means "the point 23 m west and 18 m south of the centre of sub-cell
11-0140-0471-00". The code alone (no offset) means the sub-cell's centre.

Any app can store and exchange this text. Turning it back into a GPS point,
or a GPS point into an address, needs no database: the centre comes straight
from the grid rules. Offsets are rounded to whole metres, so a point turned
into an address and back moves by about 0.7 m at most (up to ~1.4 m within a
metre of a sub-cell edge, where rounding is nudged towards the centre so the
address always stays inside its own sub-cell).

Canonical form: the code, then the east-west part (E or W), then the
north-south part (N or S), separated by single spaces, upper case. Parsing
also accepts lower case, extra spaces, and the two offset parts in either order.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from . import geodesy, ids
from .errors import InvalidIdError
from .layout import SCALE, SUB, GridLayout, to_e7

# An offset can only reach about 100 m from the centre of a ~200 m sub-cell;
# anything far beyond that cannot belong to the stated sub-cell.
MAX_OFFSET_M = 150

_ADDRESS_RE = re.compile(
    r"^\s*([01]{2}-\d{4}-\d{4}-\d{2})"
    r"(?:\s+([EWNS])\s*(\d{1,4})\s+([EWNS])\s*(\d{1,4}))?\s*$", re.IGNORECASE)


@lru_cache(maxsize=1)
def _layout():
    return GridLayout.from_spec()


@dataclass(frozen=True)
class GridAddress:
    """A sub-cell plus an offset in whole metres from its centre (east and north positive)."""
    sub_cell_id: int
    east_m: int = 0
    north_m: int = 0

    @property
    def code(self):
        return ids.sub_cell_code(self.sub_cell_id)

    @property
    def text(self):
        """Canonical text, e.g. '11-0140-0471-00 W23 S18'."""
        ew = "E" if self.east_m >= 0 else "W"
        ns = "N" if self.north_m >= 0 else "S"
        return f"{self.code} {ew}{abs(self.east_m)} {ns}{abs(self.north_m)}"

    def __str__(self):
        return self.text

    def centroid(self):
        """(lat, lon) of the sub-cell's centre."""
        cid, sub = ids.split_sub_cell_id(self.sub_cell_id)
        q, row, col = ids.split_cell_id(cid)
        sr, sc = ids.split_sub_index(sub)
        lat_e7, lon_e7 = _layout().quadrants[q].item_centroid_e7(row * SUB + sr, col * SUB + sc, "sub")
        return lat_e7 / SCALE, lon_e7 / SCALE

    def point(self):
        """(lat, lon) the address refers to: the centre moved by the offset."""
        clat, clon = self.centroid()
        return geodesy.apply_offset(clat, clon, self.east_m, self.north_m)


def parse_address(text):
    """GridAddress from text such as '11-0140-0471-00 W23 S18' (or just the code)."""
    m = _ADDRESS_RE.match(text) if isinstance(text, str) else None
    if not m:
        raise InvalidIdError(f"not a grid address: {text!r} "
                             "(expected QQ-RRRR-CCCC-SS, optionally followed by an offset such as W23 S18)")
    code, d1, v1, d2, v2 = m.groups()
    level, sid = ids.parse_code(code)
    _layout().item_from_id(sid, "sub")          # the sub-cell must exist in the grid
    if d1 is None:
        return GridAddress(sid)
    parts = {d1.upper(): int(v1), d2.upper(): int(v2)}
    east = parts.get("E", 0) - parts.get("W", 0)
    north = parts.get("N", 0) - parts.get("S", 0)
    if len(parts) != 2 or not ({"E", "W"} & parts.keys() and {"N", "S"} & parts.keys()):
        raise InvalidIdError(f"grid address offset needs one east/west and one north/south part: {text!r}")
    if max(abs(east), abs(north)) > MAX_OFFSET_M:
        raise InvalidIdError(f"grid address offset is further than {MAX_OFFSET_M} m from the sub-cell centre, "
                             f"so it cannot belong to that sub-cell: {text!r}")
    return GridAddress(sid, east, north)


def _sub_cell_at(lat, lon):
    q, row, col, sr, sc = _layout().locate_e7(to_e7(lat), to_e7(lon))
    return ids.sub_cell_id(ids.cell_id(q, row, col), ids.sub_index(sr, sc))


def _toward_zero(v):
    return v - 1 if v > 0 else v + 1 if v < 0 else v


def address_from_point(lat, lon):
    """The grid address of a GPS point: its sub-cell and the offset from that sub-cell's centre.

    The offset is rounded to whole metres. If rounding would carry the point over the
    sub-cell's edge, it is rounded towards the centre instead, so the address always
    resolves to a point inside its own sub-cell.
    """
    sid = _sub_cell_at(lat, lon)
    clat, clon = GridAddress(sid).centroid()
    east, north = geodesy.local_offset(clat, clon, lat, lon)
    east, north = round(east), round(north)
    for e, n in ((east, north), (_toward_zero(east), north), (east, _toward_zero(north)),
                 (_toward_zero(east), _toward_zero(north))):
        if _sub_cell_at(*geodesy.apply_offset(clat, clon, e, n)) == sid:
            return GridAddress(sid, e, n)
    return GridAddress(sid, _toward_zero(east), _toward_zero(north))  # unreachable for 200 m sub-cells


def resolve_address(text):
    """(lat, lon) of an address given as text."""
    return parse_address(text).point()
