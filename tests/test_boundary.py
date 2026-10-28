import random

import pytest

from pakgrid import Grid, ids
from pakgrid.boundary import BORDER, INSIDE, OUTSIDE, Boundary, cell_flags, row_flags
from pakgrid.builder import build_database
from pakgrid.errors import NotInDatabaseError
from pakgrid.layout import SCALE
from pakgrid.verify import verify_database


@pytest.fixture(scope="module")
def boundary():
    return Boundary.default()


@pytest.fixture(scope="module")
def pakistan_db(tmp_path_factory):
    """The Pakistan edition of the same rows as lahore_db (quadrant 11, rows 135-145)."""
    path = tmp_path_factory.mktemp("db") / "lahore_pk.sqlite"
    build_database(path, quadrants=["11"], rows=(135, 146), pakistan_only=True)
    return path


def test_packaged_boundary(boundary):
    assert boundary.vertex_count == 86_591
    assert len(boundary.rings) == 48
    assert 23.80 < boundary.south_e7 / SCALE < 23.81 and 37.08 < boundary.north_e7 / SCALE < 37.09


@pytest.mark.parametrize("name,lat,lon,expected", [
    ("Lahore", 31.5204, 74.3587, True),
    ("Karachi", 24.86, 67.01, True),
    ("Skardu", 35.30, 75.63, True),
    ("Muzaffarabad", 34.37, 73.47, True),
    ("Astola Island", 25.12, 63.85, True),
    ("Gwadar peninsula neck", 25.13, 62.32, True),
    ("Arabian Sea off Karachi", 24.70, 66.90, False),
    ("Amritsar, India", 31.63, 74.87, False),
    ("Kandahar, Afghanistan", 31.63, 65.71, False),
    ("Zahedan, Iran", 29.50, 60.86, False),
])
def test_contains_known_places(boundary, name, lat, lon, expected):
    assert boundary.contains(lat, lon) is expected, name


# -- Independent brute-force classification ----------------------------------

def _segment_hits_box(x1, y1, x2, y2, w, s, e, n):
    """Liang-Barsky: does the segment touch the closed box?"""
    t0, t1, dx, dy = 0.0, 1.0, x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - w), (dx, e - x1), (-dy, y1 - s), (dy, n - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _brute_flag(s, w, n, e, near_edges):
    """BORDER if any edge touches the box, else even-odd count of edges crossed by a ray
    east from the box centre (every edge crossing that latitude is in near_edges)."""
    for ax, ay, bx, by in near_edges:
        if _segment_hits_box(ax, ay, bx, by, w, s, e, n):
            return BORDER
    lat, lon = (s + n) / 2, (w + e) / 2
    crossings = sum(1 for ax, ay, bx, by in near_edges
                    if (ay <= lat) != (by <= lat) and ax + (lat - ay) * (bx - ax) / (by - ay) > lon)
    return INSIDE if crossings % 2 else OUTSIDE


def test_flags_match_brute_force_along_the_border(boundary, layout):
    rng = random.Random(5)
    all_vertices = [v for r in boundary.rings for v in r]
    checked = border_seen = 0
    for _ in range(25):
        vx, vy = rng.choice(all_vertices)
        q, row, col, _, _ = layout.locate_e7(vy, vx)
        ql = layout.quadrants[q]
        lats = [ql.fine_lat(i) for i in range(row * 5, row * 5 + 6)]
        lons = [ql.fine_lon(row, j) for j in range(5 * ql.ncols[row] + 1)]
        centres = [(lons[j] + lons[j + 1]) / 2 for j in range(len(lons) - 1)]
        sub_rows = [row_flags(*boundary.band(lats[k], lats[k + 1]), lons, centres) for k in range(5)]
        s, n = lats[0], lats[5]
        near = [(ax / SCALE, ay / SCALE, bx / SCALE, by / SCALE)
                for r in boundary.rings for (ax, ay), (bx, by) in zip(r, r[1:])
                if min(ay, by) <= n * SCALE + 1 and max(ay, by) >= s * SCALE - 1]
        for c in range(max(0, col - 3), min(ql.ncols[row], col + 4)):
            for k in range(5):
                for sc in range(5):
                    j = c * 5 + sc
                    expected = _brute_flag(lats[k], lons[j], lats[k + 1], lons[j + 1], near)
                    assert sub_rows[k][j] == expected, (q, row, c, k, sc)
                    checked += 1
                    border_seen += expected == BORDER
            flag, inside_mask, border_mask = cell_flags(sub_rows, c)
            subs = [sub_rows[k][c * 5 + sc] for k in range(5) for sc in range(5)]
            assert flag == (INSIDE if all(f == INSIDE for f in subs) else
                            OUTSIDE if all(f == OUTSIDE for f in subs) else BORDER)
            assert inside_mask == sum(1 << i for i, f in enumerate(subs) if f == INSIDE)
            assert border_mask == sum(1 << i for i, f in enumerate(subs) if f == BORDER)
    assert checked > 4000 and border_seen > 100


# -- Through the database ------------------------------------------------------

def test_locate_reports_pakistan(grid):
    lahore = grid.locate(31.5204, 74.3587)
    assert lahore.in_pakistan is True and lahore.sub_cell.pakistan == "inside"
    assert grid.cell(lahore.cell_id).pakistan == "inside"
    india = grid.locate(31.52, 74.70)
    assert india.in_pakistan is False and grid.cell(india.cell_id).pakistan == "outside"
    assert grid.in_pakistan(31.52, 74.70) is False and grid.in_pakistan(10.0, 10.0) is False


def test_border_points_use_the_line(grid):
    # walk east along 31.52 N from Lahore into India; every point agrees with the border line
    lon, seen_border = 74.36, 0
    while lon < 74.75:
        loc = grid.locate(31.52, lon)
        assert loc.in_pakistan == grid.boundary.contains(31.52, lon)
        if loc.sub_cell.pakistan == "border":
            seen_border += 1
        elif loc.sub_cell.pakistan == "inside":
            assert loc.in_pakistan
        else:
            assert not loc.in_pakistan
        lon += 0.0005
    assert seen_border > 0


def test_pakistan_filter_and_bulk_flags(grid):
    box = (31.50, 74.40, 31.56, 74.75)
    everything = grid.cells_in_area(*box)
    in_pk = grid.cells_in_area(*box, pakistan_only=True)
    flags = grid.pakistan_flags(everything)
    assert set(in_pk) == {c for c, f in flags.items() if f != "outside"}
    assert 0 < len(in_pk) < len(everything)
    assert {"inside", "border", "outside"} <= set(flags.values())
    subs = grid.cells_in_area(31.515, 74.5, 31.53, 74.6, level="sub")
    sub_flags = grid.pakistan_flags(subs, level="sub")
    assert {grid.sub_cell(s).pakistan for s in subs[:50]} <= set(sub_flags.values())
    assert all(grid.sub_cell(s).pakistan == sub_flags[s] for s in subs[:50])


def test_pakistan_edition(grid, pakistan_db):
    with Grid.open(pakistan_db) as pk:
        info = pk.info()
        assert info["database"]["edition"] == "pakistan"
        assert info["pakistan"]["cells_outside"] == 0
        assert info["pakistan"]["cells_inside"] == grid.info()["pakistan"]["cells_inside"]
        assert pk.meta["cells"] == str(info["pakistan"]["cells_inside"] + info["pakistan"]["cells_border"])
        # same IDs, same data for the cells that are stored
        lahore = pk.locate(31.5204, 74.3587)
        assert lahore == grid.locate(31.5204, 74.3587)
        assert pk.cell(lahore.cell_id) == grid.cell(lahore.cell_id)
        # cells wholly outside Pakistan are simply absent
        outside = grid.locate(31.52, 74.70).cell_id
        with pytest.raises(NotInDatabaseError, match="Pakistan edition"):
            pk.cell(outside)
        assert pk.in_pakistan(31.52, 74.70) is False
        box = (31.50, 74.40, 31.56, 74.75)
        assert pk.cells_in_area(*box, pakistan_only=True) == grid.cells_in_area(*box, pakistan_only=True)
    assert verify_database(pakistan_db, full=True).ok


def test_attribution_is_recorded(grid):
    p = grid.info()["pakistan"]
    assert p["boundary_license"] == "ODbL-1.0"
    assert "OpenStreetMap" in p["attribution"]
    assert p["boundary_sha256"] == Boundary.default().sha256
    assert grid.boundary.sha256 == Boundary.default().sha256


def test_ids_do_not_depend_on_the_border(grid, pakistan_db):
    with Grid.open(pakistan_db) as pk:
        for code in ("11-0140-0471-00", "11-0135-0000-00", "11-0145-0300-24"):
            sid = ids.parse_code(code)[1]
            assert pk.from_text(code) == grid.from_text(code) == sid
