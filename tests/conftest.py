import os
import shutil

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


def pytest_sessionstart(session):
    """PAKGRID_REQUIRE=node,ffmpeg,celery,postgres (any of them) makes a missing tool an error, so a
    CI job that is meant to run the tests needing it cannot skip them silently."""
    missing = []
    for tool in filter(None, (t.strip() for t in os.environ.get("PAKGRID_REQUIRE", "").split(","))):
        if tool in ("node", "ffmpeg"):
            ok = shutil.which(tool) is not None
        elif tool == "celery":
            ok = __import__("importlib.util").util.find_spec("celery") is not None
        elif tool == "postgres":
            ok = bool(os.environ.get("PAKGRID_TEST_POSTGRES"))
        else:
            raise pytest.UsageError(f"PAKGRID_REQUIRE: unknown tool {tool!r} (node, ffmpeg, celery, postgres)")
        if not ok:
            missing.append(tool)
    if missing:
        raise pytest.UsageError(f"PAKGRID_REQUIRE: not available: {', '.join(missing)}")
