"""Exceptions raised by pakgrid."""


class PakgridError(Exception):
    """Base class for all pakgrid errors."""


class OutsideGridError(PakgridError, ValueError):
    """A coordinate lies outside the grid's bounding rectangle."""


class InvalidIdError(PakgridError, ValueError):
    """An ID or code does not name a cell or sub-cell in the grid."""


class NotInDatabaseError(PakgridError, LookupError):
    """A valid cell is missing from the database (for example, a partial build)."""


class GridMismatchError(PakgridError):
    """The database was built from different grid rules than this library."""


class IntegrityError(PakgridError):
    """The database contents disagree with the grid layout or its checksum."""
