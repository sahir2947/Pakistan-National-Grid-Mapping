"""Radius search (Grid.cells_within) and block-to-block distances (Grid.distance)."""

import json
import math

import pytest

from pakgrid import Grid, geodesy, ids, navigate
from pakgrid.cli import main
from pakgrid.errors import InvalidIdError
from pakgrid.grid import MAX_RADIUS_M
from pakgrid.layout import SCALE

POINT = (31.5204, 74.3587)


def _brute(grid, lat, lon, radius, level, touching=False):
    """Every block in a generous box, measured one by one, as [(distance, ID)]."""
    pad = (radius + 3000) / 94_000
    m_lat, m_lon = geodesy.metres_per_degree(lat)
    out = []
    for item in grid.cells_in_area(lat - pad, lon - pad / 0.85, lat + pad, lon + pad / 0.85, level=level):
        q, i, j = grid.layout.item_from_id(item, level)
        ql = grid.layout.quadrants[q]
        clat, clon = (v / SCALE for v in ql.item_centroid_e7(i, j, level))
        d = navigate.direction(lat, lon, clat, clon).distance_m
        if touching:
            s, w, n, e = (v / SCALE for v in ql.item_bounds_e7(i, j, level))
            d = math.hypot(max(w - lon, 0, lon - e) * m_lon, max(s - lat, 0, lat - n) * m_lat)
        if d <= radius:
            out.append((d, item))
    return sorted(out)


@pytest.mark.parametrize("level, radius, touching", [
    ("cell", 0, False), ("cell", 5000, False), ("cell", 5000, True), ("cell", 20000, False),
    ("sub", 0, True), ("sub", 750, False), ("sub", 750, True), ("sub", 3000, False),
])
def test_cells_within_matches_brute_force(grid, level, radius, touching):
    found = grid.cells_within(*POINT, radius, level=level, touching=touching)
    expected = _brute(grid, *POINT, radius, level, touching)
    assert [(n.distance_m, n.id) for n in found] == expected
    for n in found[:20]:
        assert n.code == (ids.cell_code(n.id) if level == "cell" else ids.sub_cell_code(n.id))
    if touching:                                   # the block holding the point, at distance 0
        here = grid.locate(*POINT)
        assert found[0].id == (here.cell_id if level == "cell" else here.id) and found[0].distance_m == 0


def test_counts_and_order(grid):
    found = grid.cells_within(*POINT, 5000)
    # ~1 km² cells: about pi * 5^2 = 78.5 of them have their centre within 5 km
    assert 70 <= len(found) <= 88
    assert [n.distance_m for n in found] == sorted(n.distance_m for n in found)
    near = grid.cells_within(*POINT, 1000, level="sub")
    assert 70 <= len(near) <= 88                   # ~0.04 km² sub-cells: pi / 0.04 = 78.5
    first = near[0]
    way = navigate.direction(*POINT, *grid.sub_cell(first.id).centroid)
    assert first.distance_m == way.distance_m and first.bearing_deg == way.bearing_deg
    json.dumps([n.to_dict() for n in found])


def test_pakistan_only(grid):
    wagah = (31.5150, 74.6100)                     # at the border with India, east of Lahore
    every = grid.cells_within(*wagah, 3000)
    inside = grid.cells_within(*wagah, 3000, pakistan_only=True)
    flags = grid.pakistan_flags([n.id for n in every])
    assert [n.id for n in inside] == [n.id for n in every if flags[n.id] != "outside"]
    assert 0 < len(inside) < len(every)


def test_bad_arguments(grid):
    for bad in (-1, MAX_RADIUS_M + 1, float("nan"), "5", True):
        with pytest.raises(ValueError):
            grid.cells_within(*POINT, bad)
    with pytest.raises(ValueError):
        grid.cells_within(*POINT, 100, level="block")
    with pytest.raises(ValueError):
        grid.cells_within(float("inf"), 74.0, 100)


def test_distance(grid):
    def centroid(code):                            # from the layout: 11-0150 is outside the test build
        q, i, j = grid.layout.item_from_id(grid.from_text(code), "cell")
        return tuple(v / SCALE for v in grid.layout.quadrants[q].item_centroid_e7(i, j, "cell"))

    sep = grid.distance("11-0140-0471", "11-0150-0480")
    a, b = centroid("11-0140-0471"), centroid("11-0150-0480")
    assert a == grid.cell("11-0140-0471").centroid
    way = navigate.direction(*a, *b)
    assert sep.distance_m == way.distance_m and sep.bearing_deg == way.bearing_deg
    assert sep.compass_short == way.compass_short == "NE" and sep.text == "13.8 km NE"
    assert abs(math.hypot(sep.east_m, sep.north_m) - sep.distance_m) < 0.01
    assert sep.east_m > 0 and sep.north_m > 0
    back = grid.distance("11-0150-0480", "11-0140-0471")
    assert back.distance_m == pytest.approx(sep.distance_m, abs=1e-6)
    assert abs((back.bearing_deg - sep.bearing_deg) % 360 - 180) < 0.2
    # a cell and one of its own sub-cells, mixing levels by code
    mixed = grid.distance("11-0140-0471-00", "11-0140-0471")
    assert 560 < mixed.distance_m < 570 and mixed.compass_short == "NE"
    # integer IDs take the level argument
    cid = grid.from_text("11-0140-0471")
    assert grid.distance(cid, cid + 1).distance_m == pytest.approx(
        navigate.direction(*a, *grid.cell(cid + 1).centroid).distance_m)
    same = grid.distance("11-0140-0471", "11-0140-0471")
    assert same.distance_m == 0 and same.to_dict()["text"] == "0 m N"
    # no database needed: cells outside this partial build work too
    assert grid.distance("00-0067-0690", "11-0140-0471").distance_m > 1_000_000
    with pytest.raises(InvalidIdError):
        grid.distance("11-9999-0471", "11-0140-0471")


def test_cli(lahore_db, capsys):
    assert main(["within", "31.5204", "74.3587", "1000", "--level", "sub", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["code"] == "11-0140-0471-00" and len(out) > 50
    assert main(["within", "31.5204", "74.3587", "1000", "--touching", "--pakistan-only", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["distance_m"] == 0
    assert main(["distance", "11-0140-0471", "11-0150-0480", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "13.8 km NE"
    assert main(["within", "31.5204", "74.3587", "999999", "--db", str(lahore_db)]) == 1


def test_south_edge_and_quadrant_lines(south_edge_db):
    with Grid.open(south_edge_db) as grid:
        ql = grid.layout.quadrants[0]
        lat = ql.south + 0.004
        for lon in (66.0, 69.4):                   # near the midline between quadrants 00 and 10
            found = grid.cells_within(lat, lon, 3000)
            assert found and [(n.distance_m, n.id) for n in found] == _brute(grid, lat, lon, 3000, "cell")
