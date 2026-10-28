import random

import pytest

from pakgrid import GridAddress, address_from_point, geodesy, parse_address, resolve_address
from pakgrid.errors import InvalidIdError, OutsideGridError


def test_lahore_worked_example(grid):
    a = address_from_point(31.5204, 74.3587)
    assert a.text == "11-0140-0471-00 W23 S18" == str(a)
    assert (a.sub_cell_id, a.east_m, a.north_m) == (105_265_888, -23, -18)
    assert grid.locate(31.5204, 74.3587).address == a
    assert grid.locate(31.5204, 74.3587).to_dict()["address"] == a.text


def test_centroids_without_a_database_match_the_database(grid):
    rng = random.Random(4)
    for _ in range(300):
        loc = grid.locate(rng.uniform(31.48, 31.57), rng.uniform(69.5, 77.9))
        assert GridAddress(loc.id).centroid() == loc.sub_cell.centroid


def test_round_trip_stays_in_the_sub_cell_and_within_1_5_m(layout):
    rng = random.Random(7)
    points = [(rng.uniform(23.5, 37.0), rng.uniform(60.9, 77.9)) for _ in range(3000)]
    # points a few centimetres inside sub-cell edges, where rounding could cross the edge
    for _ in range(500):
        lat, lon = rng.uniform(24, 37), rng.uniform(61, 77)
        q, row, col, sr, sc = layout.locate_e7(round(lat * 1e7), round(lon * 1e7))
        s, w, n, e = layout.quadrants[q].item_bounds_e7(row * 5 + sr, col * 5 + sc, "sub")
        points.append((s / 1e7 + 3e-7, rng.uniform(w, e) / 1e7))
        points.append((rng.uniform(s, n) / 1e7, e / 1e7 - 3e-7))
    for lat, lon in points:
        lat, lon = rng.uniform(23.5, 37.0), rng.uniform(60.9, 77.9)
        a = address_from_point(lat, lon)
        back = parse_address(a.text)
        assert back == a
        plat, plon = back.point()
        east, north = geodesy.local_offset(lat, lon, plat, plon)
        assert (east ** 2 + north ** 2) ** 0.5 <= 1.5
        assert address_from_point(plat, plon).sub_cell_id == a.sub_cell_id


@pytest.mark.parametrize("text,expected", [
    ("11-0140-0471-00 W23 S18", (105_265_888, -23, -18)),
    ("  11-0140-0471-00   w23   s18 ", (105_265_888, -23, -18)),
    ("11-0140-0471-00 S18 W23", (105_265_888, -23, -18)),
    ("11-0140-0471-00 E0 N0", (105_265_888, 0, 0)),
    ("11-0140-0471-00", (105_265_888, 0, 0)),
    ("11-0140-0471-00 E5 N150", (105_265_888, 5, 150)),
])
def test_parse(text, expected):
    a = parse_address(text)
    assert (a.sub_cell_id, a.east_m, a.north_m) == expected


def test_canonical_text_and_code_only_address():
    assert parse_address("11-0140-0471-00 s18 w23").text == "11-0140-0471-00 W23 S18"
    centre = parse_address("11-0140-0471-00")
    assert centre.text == "11-0140-0471-00 E0 N0"
    assert centre.point() == pytest.approx(centre.centroid(), abs=1e-12)
    assert resolve_address("11-0140-0471-00 W23 S18") == pytest.approx(parse_address("11-0140-0471-00 W23 S18").point())


@pytest.mark.parametrize("bad", [
    "", "11-0140-0471", "11-0140-0471-00 W23", "11-0140-0471-00 W23 E5", "11-0140-0471-00 N1 S2",
    "11-0140-0471-00 W23 S151", "11-0140-0471-25 W1 S1", "11-0800-0000-00", "11-0140-0900-00",
    "11-0140-0471-00 X1 N2", None, 5,
])
def test_bad_addresses(bad):
    with pytest.raises(InvalidIdError):
        parse_address(bad)


def test_point_outside_the_grid():
    with pytest.raises(OutsideGridError):
        address_from_point(10.0, 10.0)
