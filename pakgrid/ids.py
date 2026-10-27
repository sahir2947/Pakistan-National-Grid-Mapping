"""Packing, unpacking and formatting of cell and sub-cell IDs.

    cell ID     (22 bits):  [quadrant:2][row:10][column:10]
    sub-cell ID (27 bits):  [quadrant:2][row:10][column:10][sub:5]

Both fit in a 32-bit integer, and every ID is unique across the whole grid.
These functions check bit ranges only; whether a row or column exists in the
grid is checked by `Grid`, which knows the layout.
"""

import re

from . import spec
from .errors import InvalidIdError

ROW_SHIFT = spec.COL_BITS
QUADRANT_SHIFT = spec.ROW_BITS + spec.COL_BITS
SUB_SHIFT = spec.SUB_BITS

ROW_MASK = (1 << spec.ROW_BITS) - 1
COL_MASK = (1 << spec.COL_BITS) - 1
SUB_MASK = (1 << spec.SUB_BITS) - 1

CELL_ID_LIMIT = 1 << (spec.QUADRANT_BITS + spec.ROW_BITS + spec.COL_BITS)
SUB_CELL_ID_LIMIT = CELL_ID_LIMIT << spec.SUB_BITS
SUB_CELLS_PER_CELL = spec.SUBDIVISIONS * spec.SUBDIVISIONS

_CODE_RE = re.compile(r"^([01]{2})-(\d{4})-(\d{4})(?:-(\d{2}))?$")


def cell_id(quadrant, row, column):
    """Pack a quadrant number, row and column into a cell ID."""
    if not 0 <= quadrant <= 3:
        raise InvalidIdError(f"quadrant must be 0-3, got {quadrant!r}")
    if not 0 <= row <= ROW_MASK:
        raise InvalidIdError(f"row must be 0-{ROW_MASK}, got {row!r}")
    if not 0 <= column <= COL_MASK:
        raise InvalidIdError(f"column must be 0-{COL_MASK}, got {column!r}")
    return (quadrant << QUADRANT_SHIFT) | (row << ROW_SHIFT) | column


def split_cell_id(cid):
    """Unpack a cell ID into (quadrant, row, column)."""
    _check_int(cid, CELL_ID_LIMIT, "cell ID")
    return cid >> QUADRANT_SHIFT, (cid >> ROW_SHIFT) & ROW_MASK, cid & COL_MASK


def sub_cell_id(cid, sub):
    """Pack a cell ID and a sub-cell number (0-24) into a sub-cell ID."""
    _check_int(cid, CELL_ID_LIMIT, "cell ID")
    if not 0 <= sub < SUB_CELLS_PER_CELL:
        raise InvalidIdError(f"sub-cell number must be 0-{SUB_CELLS_PER_CELL - 1}, got {sub!r}")
    return (cid << SUB_SHIFT) | sub


def split_sub_cell_id(sid):
    """Unpack a sub-cell ID into (cell ID, sub-cell number)."""
    _check_int(sid, SUB_CELL_ID_LIMIT, "sub-cell ID")
    sub = sid & SUB_MASK
    if sub >= SUB_CELLS_PER_CELL:
        raise InvalidIdError(f"sub-cell ID {sid} has sub-cell number {sub}, which is above 24")
    return sid >> SUB_SHIFT, sub


def parent(sid):
    """The cell ID that contains a sub-cell."""
    return split_sub_cell_id(sid)[0]


def sub_index(sub_row, sub_col):
    """Sub-cell number from its row and column inside the cell (row 0 is south)."""
    return sub_row * spec.SUBDIVISIONS + sub_col


def split_sub_index(sub):
    """(sub_row, sub_col) of a sub-cell number."""
    return divmod(sub, spec.SUBDIVISIONS)


def cell_code(cid):
    """Readable code of a cell, e.g. '11-0140-0471'."""
    q, row, col = split_cell_id(cid)
    return f"{q:02b}-{row:04d}-{col:04d}"


def sub_cell_code(sid):
    """Readable code of a sub-cell, e.g. '11-0140-0471-00'."""
    cid, sub = split_sub_cell_id(sid)
    return f"{cell_code(cid)}-{sub:02d}"


def parse_code(code):
    """Parse a readable code. Returns ('cell', cell_id) or ('sub', sub_cell_id)."""
    m = _CODE_RE.match(code.strip()) if isinstance(code, str) else None
    if not m:
        raise InvalidIdError(f"not a grid code: {code!r} (expected QQ-RRRR-CCCC or QQ-RRRR-CCCC-SS)")
    q, row, col, sub = m.groups()
    cid = cell_id(int(q, 2), int(row), int(col))
    if sub is None:
        return "cell", cid
    return "sub", sub_cell_id(cid, int(sub))


def _check_int(value, limit, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidIdError(f"{what} must be an integer, got {value!r}")
    if not 0 <= value < limit:
        raise InvalidIdError(f"{what} must be in 0-{limit - 1}, got {value}")
