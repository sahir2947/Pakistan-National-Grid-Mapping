import random

import pytest

from pakgrid import geodesy, spec
from pakgrid.errors import OutsideGridError
from pakgrid.layout import (EAST_E7, MID_LAT_E7, MID_LON_E7, NORTH_E7, SOUTH_E7, WEST_E7, GridLayout,
                            rounding_margins)


def test_counts(layout):
    assert [ql.nrows for ql in layout.quadrants] == [756, 757, 756, 757]
    assert [ql.ncells for ql in layout.quadrants] == [608_259, 568_326, 608_259, 568_326]
    assert layout.total_cells == 2_353_170
    assert layout.total_sub_cells == 58_829_250


def test_rows_and_columns_fit_10_bits(layout):
    assert max(ql.nrows for ql in layout.quadrants) <= 1023
    assert max(max(ql.ncols) for ql in layout.quadrants) == 828


def test_lahore_row(layout):
    ql = layout.quadrants[3]
    assert (ql.ncols[0], ql.ncols[140], ql.ncols[756]) == (779, 769, 720)


def test_cell_sizes(layout):
    heights, widths = [], []
    for ql in layout.quadrants:
        for row in range(ql.nrows):
            s, n = ql.fine_lat(row * 5), ql.fine_lat(row * 5 + 5)
            mid = (s + n) / 2
            heights.append(geodesy.meridian_arc(s, n))
            widths.append(geodesy.parallel_length(mid, ql.fine_lon(row, 0), ql.fine_lon(row, 5)))
    assert 999.0 <= min(heights) and max(heights) <= 1000.4
    assert 999.3 <= min(widths) and max(widths) <= 1000.7


def test_layout_is_safe_from_floating_point_differences():
    # Every row/column count is far from a rounding boundary, so last-digit
    # differences between platforms cannot change the layout.
    for q in spec.QUADRANTS:
        assert min(rounding_margins(q)) > 1e-6


def test_quadrants_share_edges(layout):
    q0, q1, q2, q3 = layout.quadrants
    assert q0.lat_e7 == q2.lat_e7 and q1.lat_e7 == q3.lat_e7
    assert q0.lat_e7[0] == SOUTH_E7 and q1.lat_e7[-1] == NORTH_E7
    assert q0.lat_e7[-1] == q1.lat_e7[0] == MID_LAT_E7
    assert q0.east_e7 == q2.west_e7 == MID_LON_E7
    assert q0.west_e7 == WEST_E7 and q2.east_e7 == EAST_E7


def test_edges_are_strictly_increasing(layout):
    for ql in layout.quadrants:
        assert all(a < b for a, b in zip(ql.lat_e7, ql.lat_e7[1:]))
        for row in (0, ql.nrows // 2, ql.nrows - 1):
            lons = [ql.fine_lon_e7(row, j) for j in range(5 * ql.ncols[row] + 1)]
            assert all(a < b for a, b in zip(lons, lons[1:]))
            assert lons[0] == ql.west_e7 and lons[-1] == ql.east_e7


def _random_point(rng):
    return rng.randint(SOUTH_E7, NORTH_E7), rng.randint(WEST_E7, EAST_E7)


def test_every_point_falls_in_its_cell(layout):
    rng = random.Random(1)
    for _ in range(20_000):
        lat, lon = _random_point(rng)
        q, row, col, sr, sc = layout.locate_e7(lat, lon)
        s, w, n, e = layout.quadrants[q].item_bounds_e7(row * 5 + sr, col * 5 + sc, "sub")
        assert s <= lat and w <= lon
        assert lat < n or lat == NORTH_E7
        assert lon < e or lon == EAST_E7


def test_edge_rule_points_on_lines_go_north_and_east(layout):
    ql = layout.quadrants[3]
    lat = ql.lat_e7[140 * 5 + 2]          # a sub-cell line inside row 140
    lon = ql.fine_lon_e7(140, 518 * 5)    # the west edge of column 518
    assert layout.locate_e7(lat, lon) == (3, 140, 518, 2, 0)
    assert layout.locate_e7(lat - 1, lon - 1) == (3, 140, 517, 1, 4)


def test_midlines_belong_to_north_and_east_quadrants(layout):
    assert layout.locate_e7(MID_LAT_E7, MID_LON_E7) == (3, 0, 0, 0, 0)
    assert layout.locate_e7(MID_LAT_E7 - 1, MID_LON_E7 - 1)[0] == 0


def test_outer_edges_belong_to_last_row_and_column(layout):
    q, row, col, sr, sc = layout.locate_e7(NORTH_E7, EAST_E7)
    ql = layout.quadrants[q]
    assert (q, row, col, sr, sc) == (3, ql.nrows - 1, ql.ncols[-1] - 1, 4, 4)
    assert layout.locate_e7(SOUTH_E7, WEST_E7) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("lat,lon", [(SOUTH_E7 - 1, WEST_E7), (NORTH_E7 + 1, EAST_E7),
                                     (MID_LAT_E7, WEST_E7 - 1), (MID_LAT_E7, EAST_E7 + 1)])
def test_outside_raises(layout, lat, lon):
    with pytest.raises(OutsideGridError):
        layout.locate_e7(lat, lon)


def _touches(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


@pytest.mark.parametrize("level", ["cell", "sub"])
def test_neighbours_are_symmetric_and_touch(layout, level):
    rng = random.Random(2)
    samples = []
    for _ in range(300):
        q, row, col, sr, sc = layout.locate_e7(*_random_point(rng))
        samples.append((q, row, col) if level == "cell" else (q, row * 5 + sr, col * 5 + sc))
    # items on quadrant edges and outer edges
    for q in range(4):
        ql = layout.quadrants[q]
        last_i = ql.level_rows(level) - 1
        for i in (0, last_i):
            samples += [(q, i, 0), (q, i, ql.level_cols(i, level) - 1)]
    for item in samples:
        q, i, j = item
        own = layout.quadrants[q].item_bounds_e7(i, j, level)
        found = layout.neighbours(q, i, j, level)
        assert len(found) == len(set(found)) and item not in found
        assert 3 <= len(found) <= 10
        for nq, ni, nj in found:
            assert _touches(own, layout.quadrants[nq].item_bounds_e7(ni, nj, level))
            assert item in layout.neighbours(nq, ni, nj, level)


def test_neighbours_cross_quadrant_lines(layout):
    q0 = layout.quadrants[0]
    top_right = (0, q0.nrows - 1, q0.ncols[-1] - 1)   # touches the centre of the grid
    quadrants = {q for q, _, _ in layout.neighbours(*top_right, "cell")}
    assert quadrants == {0, 1, 2, 3}


def test_interior_cell_has_eight_or_more_neighbours(layout):
    found = layout.neighbours(3, 140, 518, "cell")
    assert 8 <= len(found) <= 10
    assert (3, 140, 517) in found and (3, 140, 519) in found


@pytest.mark.parametrize("level", ["cell", "sub"])
def test_items_in_area_matches_brute_force(layout, level):
    box = (315_150_000, 743_500_000, 315_300_000, 743_700_000)  # ~1.7 km x 1.9 km near Lahore
    got = layout.items_in_area(*box, level)
    ql = layout.quadrants[3]
    expected = []
    for i in range(ql.level_rows(level)):
        s, _, n, _ = ql.item_bounds_e7(i, 0, level)
        if s > box[2] or n <= box[0]:
            continue
        for j in range(ql.level_cols(i, level)):
            _, w, _, e = ql.item_bounds_e7(i, j, level)
            if w <= box[3] and e > box[1]:
                expected.append((3, i, j))
    assert got == expected and got


def test_items_in_area_whole_grid_counts(layout):
    assert len(layout.items_in_area(SOUTH_E7, WEST_E7, NORTH_E7, EAST_E7, "cell")) == 2_353_170


def test_items_in_area_outside_is_empty(layout):
    assert layout.items_in_area(0, 0, 10, 10, "cell") == []


def test_from_spec_is_deterministic():
    a, b = GridLayout.from_spec(), GridLayout.from_spec()
    assert all(x == y for x, y in zip(a.quadrants, b.quadrants))
