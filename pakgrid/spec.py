"""Fixed rules that define grid version 1.

Every ID, boundary and centroid in the grid follows from these values.
Changing any of them defines a new grid version; never edit them in place.
"""

GRID_VERSION = 1

# WGS 84 ellipsoid (the datum used by GPS).
DATUM = "WGS 84"
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563

# Bounding rectangle, in degrees.
SOUTH = 23.436
NORTH = 37.080
WEST = 60.800
EAST = 78.000

# Midlines that split the rectangle into four quadrants.
MID_LAT = 30.258
MID_LON = 69.400

# Target cell size and the number of sub-cells along each side of a cell.
CELL_SIZE_M = 1000.0
SUBDIVISIONS = 5

# Coordinates are stored as integers in units of 1 / COORD_SCALE degree (~1.1 cm).
COORD_SCALE = 10_000_000

# Bit widths of the parts of a sub-cell ID: [quadrant][row][column][sub].
QUADRANT_BITS = 2
ROW_BITS = 10
COL_BITS = 10
SUB_BITS = 5

# Quadrant number -> (code, name). The first bit of the code is east (1) or
# west (0); the second is north (1) or south (0).
QUADRANTS = {
    0: ("00", "South-West"),
    1: ("01", "North-West"),
    2: ("10", "South-East"),
    3: ("11", "North-East"),
}


def quadrant_bounds(q):
    """Return (south, north, west, east) in degrees for quadrant number q."""
    if q not in QUADRANTS:
        raise ValueError(f"quadrant must be 0-3, got {q!r}")
    south, north = (MID_LAT, NORTH) if q & 1 else (SOUTH, MID_LAT)
    west, east = (MID_LON, EAST) if q & 2 else (WEST, MID_LON)
    return south, north, west, east
