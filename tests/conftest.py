import pytest

from pakgrid import Grid
from pakgrid.builder import build_database
from pakgrid.layout import GridLayout


@pytest.fixture(scope="session")
def layout():
    return GridLayout.from_spec()


@pytest.fixture(scope="session")
def lahore_db(tmp_path_factory):
    """Quadrant 11, rows 135-145: covers the Lahore worked example."""
    path = tmp_path_factory.mktemp("db") / "lahore.sqlite"
    build_database(path, quadrants=["11"], rows=(135, 146))
    return path


@pytest.fixture(scope="session")
def south_edge_db(tmp_path_factory):
    """Rows 0-2 of every quadrant: the south edge and the north side of the midline."""
    path = tmp_path_factory.mktemp("db") / "south.sqlite"
    build_database(path, rows=(0, 3))
    return path


@pytest.fixture(scope="session")
def north_edge_db(tmp_path_factory):
    """Rows 753-756 of every quadrant: the north edge and the south side of the midline."""
    path = tmp_path_factory.mktemp("db") / "north.sqlite"
    build_database(path, rows=(753, 757))
    return path


@pytest.fixture
def grid(lahore_db):
    with Grid.open(lahore_db) as g:
        yield g
