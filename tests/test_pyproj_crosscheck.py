"""Cross-checks of pakgrid's geodesy against pyproj (skipped when pyproj is not installed)."""

import math

import pytest

from pakgrid import direction, geodesy, spec

pyproj = pytest.importorskip("pyproj", reason="pyproj cross-checks are optional")


@pytest.fixture(scope="module")
def geod():
    return pyproj.Geod(ellps="WGS84")


def test_meridian_arc_matches_pyproj(geod):
    for s, n in [(spec.SOUTH, spec.MID_LAT), (spec.MID_LAT, spec.NORTH), (31.5, 31.509)]:
        _, _, dist = geod.inv(70.0, s, 70.0, n)
        assert geodesy.meridian_arc(s, n) == pytest.approx(dist, abs=1e-4)


def test_metres_per_degree_matches_pyproj(geod):
    for lat in (24.0, 30.0, 36.9):
        m_lat, m_lon = geodesy.metres_per_degree(lat)
        _, _, dn = geod.inv(70.0, lat - 5e-5, 70.0, lat + 5e-5)
        _, _, de = geod.inv(70.0 - 5e-5, lat, 70.0 + 5e-5, lat)
        assert m_lat * 1e-4 == pytest.approx(dn, rel=1e-8)
        assert m_lon * 1e-4 == pytest.approx(de, rel=1e-8)


def test_local_offset_matches_pyproj_within_a_cell(geod):
    clat, clon = 31.5241703, 74.3614434
    for lat, lon in [(31.5287, 74.3667), (31.5197, 74.3562), (31.5210, 74.3650)]:
        east, north = geodesy.local_offset(clat, clon, lat, lon)
        az, _, dist = geod.inv(clon, clat, lon, lat)
        assert math.hypot(east, north) == pytest.approx(dist, abs=0.01)
        assert geodesy.bearing(east, north) == pytest.approx(az % 360, abs=0.01)


@pytest.mark.parametrize("lat", [24.9, 31.5, 36.0])
@pytest.mark.parametrize("km,max_bearing_err", [(0.2, 0.01), (1, 0.01), (100, 0.4)])
def test_direction_matches_pyproj(geod, lat, km, max_bearing_err):
    for az in range(0, 360, 30):
        lon2, lat2, _ = geod.fwd(70.0, lat, az, km * 1000)
        fwd_az, _, dist = geod.inv(70.0, lat, lon2, lat2)
        d = direction(lat, 70.0, lat2, lon2)
        assert d.distance_m == pytest.approx(dist, rel=1e-4)
        assert abs((d.bearing_deg - fwd_az + 180) % 360 - 180) < max_bearing_err


def test_grid_is_inside_wgs84_limits():
    assert -90 < spec.SOUTH < spec.NORTH < 90 and -180 < spec.WEST < spec.EAST < 180
    assert math.isclose(geodesy.A, 6378137.0)
