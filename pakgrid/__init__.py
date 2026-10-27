"""Pakistan National Grid: fixed, numbered ~1 km cells and ~200 m sub-cells.

    from pakgrid import Grid

    with Grid.open("pakgrid_v1.sqlite") as grid:
        loc = grid.locate(31.5204, 74.3587, accuracy_m=5)
        loc.code          # '11-0140-0518-01'
        loc.offset.text   # 'W60 S18'
"""

__version__ = "0.1.0"

from . import ids  # noqa: E402
from .errors import (  # noqa: E402
    GridMismatchError, IntegrityError, InvalidIdError, NotInDatabaseError,
    OutsideGridError, PakgridError)
from .grid import Bounds, Cell, Grid, Location, Offset, SubCell  # noqa: E402
from .navigate import Direction, direction  # noqa: E402
from .spec import GRID_VERSION  # noqa: E402

__all__ = [
    "Grid", "Location", "Cell", "SubCell", "Bounds", "Offset", "Direction", "direction", "ids",
    "GRID_VERSION", "PakgridError", "OutsideGridError", "InvalidIdError", "NotInDatabaseError",
    "GridMismatchError", "IntegrityError",
]
