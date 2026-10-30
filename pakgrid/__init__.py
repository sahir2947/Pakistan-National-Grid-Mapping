"""Pakistan National Grid: fixed, numbered ~1 km cells and ~200 m sub-cells.

    from pakgrid import Grid

    with Grid.open("pakgrid_v1.sqlite") as grid:
        loc = grid.locate(31.5204, 74.3587, accuracy_m=5)
        loc.code          # '11-0140-0471-00'
        loc.offset.text   # 'W23 S18'
"""

__version__ = "0.1.0"

from . import ids  # noqa: E402
from .address import GridAddress, address_from_point, parse_address, resolve_address  # noqa: E402
from .errors import (  # noqa: E402
    GridMismatchError, IntegrityError, InvalidIdError, NotInDatabaseError,
    OutsideGridError, PakgridError)
from .grid import Bounds, Cell, Grid, Location, Nearby, Offset, Separation, SubCell  # noqa: E402
from .navigate import Direction, NavigationSession, NavState, direction, format_distance  # noqa: E402
from .spec import GRID_VERSION  # noqa: E402
from .userplaces import Reference, UserLocality, UserPlaces, UserPlacesError  # noqa: E402
from .zones import Zone, ZoneError, ZoneIndex  # noqa: E402

__all__ = [
    "Grid", "Location", "Nearby", "Separation", "GridAddress", "address_from_point", "parse_address", "resolve_address", "Cell", "SubCell", "Bounds", "Offset", "Direction", "direction",
    "NavigationSession", "NavState", "format_distance", "ids",
    "GRID_VERSION", "PakgridError", "OutsideGridError", "InvalidIdError", "NotInDatabaseError",
    "GridMismatchError", "IntegrityError", "UserPlaces", "UserLocality", "Reference", "UserPlacesError",
    "Zone", "ZoneIndex", "ZoneError",
]
