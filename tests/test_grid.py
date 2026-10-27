import math
import random

import pytest

from pakgrid import Grid, geodesy, spec
from pakgrid.errors import InvalidIdError, NotInDatabaseError, OutsideGridError

LAHORE = (31.5204, 74.3587)


def test_lahore_worked_example(grid):
    loc = grid.locate(*LAHORE, accuracy_m=5)
    assert loc.code == "11-0140-0518-01" and loc.id == 105_267_393
    assert loc.cell_code == "11-0140-0518" and loc.cell_id == 3_289_606
    assert loc.sub_cell.centroid == (31.5205657, 74.3593368)
    b = loc.sub_cell.bounds
    assert (b.south, b.west, b.north, b.east) == (31.5196645, 74.3582835, 31.5214668, 74.3603901)
    assert loc.offset.east_m == pytest.approx(-60.5, abs=0.05)
    assert loc.offset.north_m == pytest.approx(-18.4, abs=0.05)
    assert loc.offset.text == "W60 S18"
    assert round(loc.offset.bearing_deg) == 253
    assert loc.edge_distance_m == pytest.approx(39.6, abs=0.05)
    assert loc.confident is True and loc.candidates == ()


def test_point_near_an_edge_is_not_confident(grid):
    loc = grid.locate(31.52058, 74.35831, accuracy_m=8)
    assert loc.code == "11-0140-0518-01"
    assert loc.edge_distance_m == pytest.approx(2.5, abs=0.05)
    assert loc.confident is False
    assert [grid.to_text(c) for c in loc.candidates] == ["11-0140-0518-00"]


def test_without_accuracy_confidence_is_unknown(grid):
    loc = grid.locate(*LAHORE)
    assert loc.confident is None and loc.candidates == ()


def test_cell_lookup(grid):
    cell = grid.cell("11-0140-0518")
    assert cell == grid.cell(3_289_606)
    assert (cell.quadrant, cell.row, cell.column) == ("11", 140, 518)
    assert cell.centroid == (31.5241703, 74.3614434)
    b = cell.bounds
    assert (b.south, b.west, b.north, b.east) == (31.5196645, 74.3561769, 31.5286764, 74.36671)


def test_sub_cell_lookup(grid):
    sub = grid.sub_cell("11-0140-0518-01")
    assert sub == grid.sub_cell(105_267_393)
    assert sub.cell_id == 3_289_606 and sub.index == 1


def test_children_tile_the_cell(grid):
    cell = grid.cell(3_289_606)
    kids = grid.children(3_289_606)
    assert [k.index for k in kids] == list(range(25))
    assert {grid.parent(k.id) for k in kids} == {cell.id}
    assert kids[0].bounds.south == cell.bounds.south and kids[0].bounds.west == cell.bounds.west
    assert kids[24].bounds.north == cell.bounds.north and kids[24].bounds.east == cell.bounds.east
    for k in kids:
        assert k.bounds.contains(*k.centroid)
        # area-weighted centroid is never north of the midpoint (allowing for 1e-7 degree storage)
        assert k.centroid[0] <= (k.bounds.south + k.bounds.north) / 2 + 1e-7
    assert kids[1].bounds.west == kids[0].bounds.east and kids[5].bounds.south == kids[0].bounds.north


def test_random_points_are_contained(grid):
    rng = random.Random(3)
    s, n = grid.cell(grid.from_text("11-0135-0000")).bounds.south, grid.cell(grid.from_text("11-0145-0000")).bounds.north
    points = [(rng.uniform(s, n - 1e-9), rng.uniform(spec.MID_LON, spec.EAST - 1e-9)) for _ in range(3000)]
    for (lat, lon), loc in zip(points, grid.locate_many(points)):
        b = loc.sub_cell.bounds
        assert b.south - 1e-7 <= lat < b.north + 1e-7 and b.west - 1e-7 <= lon < b.east + 1e-7
        assert loc == grid.locate(lat, lon)
        assert loc.offset.distance_m <= 145   # half-diagonal of a ~200 m block


def test_locate_many_with_accuracy(grid):
    out = grid.locate_many([(31.5204, 74.3587, 5), (31.52058, 74.35831, 8)])
    assert [loc.confident for loc in out] == [True, False]


def test_true_block_is_always_result_or_candidate(grid):
    rng = random.Random(4)
    m_lat, m_lon = geodesy.metres_per_degree(31.52)
    for _ in range(500):
        lat, lon = rng.uniform(31.515, 31.535), rng.uniform(74.34, 74.38)
        true_id = grid.locate(lat, lon).id
        acc = rng.uniform(2, 60)
        ang, r = rng.uniform(0, 2 * math.pi), acc * math.sqrt(rng.random())
        noisy = grid.locate(lat + r * math.cos(ang) / m_lat, lon + r * math.sin(ang) / m_lon, accuracy_m=acc)
        assert true_id == noisy.id or true_id in noisy.candidates


def test_candidates_near_a_corner(grid):
    b = grid.sub_cell("11-0140-0518-06").bounds
    loc = grid.locate(b.south + 0.00001, b.west + 0.00001, accuracy_m=5)  # ~1 m from the SW corner
    codes = {grid.to_text(c) for c in loc.candidates}
    assert codes == {"11-0140-0518-00", "11-0140-0518-01", "11-0140-0518-05"}


def test_offset_round_trip(grid):
    off = grid.offset("11-0140-0518-01", *LAHORE)
    lat, lon = grid.from_offset("11-0140-0518-01", off.east_m, off.north_m)
    assert (lat, lon) == (pytest.approx(LAHORE[0], abs=1e-11), pytest.approx(LAHORE[1], abs=1e-11))
    cell_off = grid.offset(3_289_606, *LAHORE, level="cell")
    expected = geodesy.local_offset(31.5241703, 74.3614434, *LAHORE)
    assert (cell_off.east_m, cell_off.north_m) == pytest.approx(expected)


def test_neighbours_and_area(grid):
    nb = grid.neighbours("11-0140-0518")
    assert grid.from_text("11-0140-0517") in nb and grid.from_text("11-0140-0519") in nb
    assert nb == grid.neighbours(3_289_606, level="cell")
    sub_nb = grid.neighbours("11-0140-0518-12")
    assert len(sub_nb) == 8 and all(grid.parent(s) == 3_289_606 for s in sub_nb)
    area = grid.cells_in_area(31.522, 74.358, 31.526, 74.362)
    assert 3_289_606 in area and area == sorted(area)


def test_codes(grid):
    assert grid.from_text("11-0140-0518-01") == 105_267_393
    assert grid.to_text(105_267_393) == "11-0140-0518-01"
    assert grid.to_text(3_289_606, level="cell") == "11-0140-0518"
    with pytest.raises(InvalidIdError):
        grid.from_text("11-0800-0000")        # quadrant 11 has 757 rows
    with pytest.raises(InvalidIdError):
        grid.from_text("11-0140-0900")        # row 140 has 769 columns


def test_wrong_level_and_missing_cells(grid):
    with pytest.raises(InvalidIdError):
        grid.cell("11-0140-0518-01")
    with pytest.raises(InvalidIdError):
        grid.sub_cell("11-0140-0518")
    with pytest.raises(NotInDatabaseError):
        grid.cell("00-0000-0000")            # valid cell, but not in this partial database
    with pytest.raises(NotInDatabaseError):
        grid.locate(24.0, 61.0)


@pytest.mark.parametrize("lat,lon", [(23.0, 70.0), (38.0, 70.0), (30.0, 60.0), (30.0, 78.0)])
def test_outside(grid, lat, lon):
    with pytest.raises(OutsideGridError):
        grid.locate(lat, lon)


@pytest.mark.parametrize("lat,lon", [(float("nan"), 70.0), (30.0, float("inf")), ("30", 70.0), (True, 70.0)])
def test_bad_input(grid, lat, lon):
    with pytest.raises(ValueError):
        grid.locate(lat, lon)


def test_bad_accuracy(grid):
    with pytest.raises(ValueError):
        grid.locate(*LAHORE, accuracy_m=-1)


def test_midline_and_outer_edges(south_edge_db, north_edge_db):
    with Grid.open(south_edge_db) as south, Grid.open(north_edge_db) as north:
        assert south.locate(spec.MID_LAT, spec.MID_LON).code == "11-0000-0000-00"
        assert south.locate(spec.SOUTH, spec.WEST).code == "00-0000-0000-00"
        assert south.locate(spec.SOUTH, spec.EAST).code.startswith("10-0000-")
        top = north.locate(spec.NORTH, spec.EAST)
        assert top.code == "11-0756-0719-24"
        assert north.locate(spec.MID_LAT - 1e-7, spec.MID_LON - 1e-7).code.startswith("00-0755-")
        # neighbours across the midline exist in the other quadrants
        cell = north.locate(spec.MID_LAT - 1e-6, spec.MID_LON - 1e-6).cell_id
        quads = {north.to_text(c, level="cell")[:2] for c in north.neighbours(cell)}
        assert quads == {"00", "01", "10", "11"}


def test_info(grid):
    info = grid.info()
    assert info["total_cells"] == 2_353_170 and info["total_sub_cells"] == 58_829_250
    assert info["quadrants"]["11"] == {"name": "North-East", "rows": 757, "cells": 568_326}
    assert info["database"]["coverage"] == "partial"


def test_to_dict_is_json_ready(grid):
    import json
    d = grid.locate(*LAHORE, accuracy_m=5).to_dict()
    assert json.loads(json.dumps(d))["sub_cell"]["code"] == "11-0140-0518-01"
    assert d["offset"] == {"east_m": -60.5, "north_m": -18.4, "distance_m": 63.2,
                           "bearing_deg": 253.1, "text": "W60 S18"}
