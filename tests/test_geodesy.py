import pytest

from pakgrid import geodesy, spec
from pakgrid.layout import GridLayout


def test_gauss_legendre_integrates_polynomials_exactly():
    assert geodesy._integrate(lambda x: x ** 7 - 3 * x ** 2 + 1, -1.0, 2.0) == pytest.approx(
        (2 ** 8 - 1) / 8 - (8 + 1) + 3, rel=1e-13)
    assert sum(geodesy._GL_WEIGHTS) == pytest.approx(2.0, rel=1e-14)


def test_total_area():
    total = geodesy.rect_area(spec.SOUTH, spec.NORTH, spec.WEST, spec.EAST) / 1e6
    assert total == pytest.approx(2_352_494, abs=1)


def test_quadrant_areas():
    areas = [geodesy.rect_area(*spec.quadrant_bounds(q)) / 1e6 for q in range(4)]
    assert [round(a) for a in areas] == [608_168, 568_079, 608_168, 568_079]


def test_cells_fill_each_quadrant_exactly():
    layout = GridLayout.from_spec()
    for ql in layout.quadrants:
        total = 0.0
        for row in range(ql.nrows):
            s, n = ql.fine_lat(row * 5), ql.fine_lat(row * 5 + 5)
            # all cells in a row span the same longitude width, so they have equal area
            w, e = ql.fine_lon(row, 0), ql.fine_lon(row, 5)
            total += ql.ncols[row] * geodesy.rect_area(s, n, w, e)
        assert total == pytest.approx(geodesy.rect_area(ql.south, ql.north, ql.west, ql.east), rel=1e-12)


def test_band_centroid_is_just_south_of_midpoint():
    s, n = 31.5196645, 31.5286764
    c = geodesy.band_centroid_lat(s, n)
    assert s < c < (s + n) / 2
    shift_m = ((s + n) / 2 - c) * geodesy.metres_per_degree(c)[0]
    assert 0.005 < shift_m < 0.012   # ~0.8 cm for a 1 km cell


def test_offset_round_trip():
    east, north = geodesy.local_offset(31.5205657, 74.3593368, 31.5204, 74.3587)
    assert (east, north) == (pytest.approx(-60.5, abs=0.05), pytest.approx(-18.4, abs=0.05))
    lat, lon = geodesy.apply_offset(31.5205657, 74.3593368, east, north)
    assert (lat, lon) == (pytest.approx(31.5204, abs=1e-12), pytest.approx(74.3587, abs=1e-12))


def test_bearing():
    assert geodesy.bearing(0, 1) == 0
    assert geodesy.bearing(1, 0) == 90
    assert geodesy.bearing(0, -1) == 180
    assert geodesy.bearing(-1, 0) == 270
