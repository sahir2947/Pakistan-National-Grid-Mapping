"""Custom zones: sets of sub-cells from polygons, circles, cells and places."""

import json
import math
import random

import pytest

from pakgrid import Grid, Zone, ZoneError, ZoneIndex, geodesy, ids
from pakgrid.cli import main
from pakgrid.layout import SCALE, GridLayout, to_e7
from pakgrid.zones import FULL, point_in_rings, polygon_masks, rings_e7

BLOCK = [(31.50, 74.33), (31.50, 74.38), (31.54, 74.38), (31.54, 74.33)]
POINT = (31.5204, 74.3587)
LAYOUT = GridLayout.from_spec()


def _brute(layout, polygon):
    """{sub-cell ID} whose centroid is inside, testing every sub-cell in the polygon's box."""
    rings = rings_e7(polygon)
    ys = [y for r in rings for y, _ in r]
    xs = [x for r in rings for _, x in r]
    out = set()
    for q, i, j in layout.items_in_area(min(ys), min(xs), max(ys), max(xs), "sub"):
        lat, lon = layout.quadrants[q].item_centroid_e7(i, j, "sub")
        if point_in_rings(lat, lon, rings):
            out.add(GridLayout.item_id(q, i, j, "sub"))
    return out


def _subs(masks):
    return {ids.sub_cell_id(c, k) for c, m in masks.items() for k in range(25) if m >> k & 1}


def _random_polygon(rng, clat, clon, size, n=None):
    pts = []
    n = n or rng.randint(3, 12)
    for k in range(n):
        a, r = 2 * math.pi * k / n + rng.random() * 0.4, size * (0.2 + rng.random())
        pts.append((round(clat + r * math.sin(a), 7), round(clon + r * math.cos(a) / 0.85, 7)))
    return pts


def test_polygon_scan_equals_testing_every_sub_cell():
    rng = random.Random(81)
    shapes = [BLOCK,
              [BLOCK, [(31.51, 74.34), (31.51, 74.36), (31.53, 74.36), (31.53, 74.34)]],      # with a hole
              [(30.0, 69.30), (30.0, 69.50), (30.05, 69.45), (30.08, 69.30)],               # across the midline
              [(30.25, 69.0), (30.25, 69.1), (30.35, 69.1), (30.35, 69.0)],                 # across the midline
              ]
    for _ in range(30):
        shapes.append(_random_polygon(rng, 24.5 + rng.random() * 12, 61.5 + rng.random() * 15,
                                      rng.choice([0.002, 0.01, 0.05])))
    for poly in shapes:
        assert _subs(polygon_masks(LAYOUT, rings_e7(poly))) == _brute(LAYOUT, poly), poly


def test_vertices_and_edges_on_sub_cell_centroids():
    """Polygon corners and edges exactly on centroids: the exact integer rule decides, the same
    way as point_in_rings."""
    ql = LAYOUT.quadrants[3]
    i, j = 700, 2355
    row = i // 5

    def c(di, dj):
        lat, lon = ql.item_centroid_e7(i + di, j + dj, "sub")
        return (lat / SCALE, lon / SCALE)

    assert ql.ncols[row] * 5 > j + 10
    shapes = [[c(0, 0), c(0, 6), c(4, 6), c(4, 0)],              # corners on centroids
              [c(0, 0), c(4, 3), c(0, 6)],                       # sloped edges through centroids
              [c(0, 0), c(0, 6), c(2, 3), c(4, 6), c(4, 0), c(2, 3)]]   # self-touching bow tie
    for poly in shapes:
        assert _subs(polygon_masks(LAYOUT, rings_e7(poly))) == _brute(LAYOUT, poly)


def test_polygon_formats(grid):
    plain = grid.zone(polygon=BLOCK)
    geo = {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in BLOCK]]}
    assert grid.zone(polygon=geo) == plain
    assert grid.zone(polygon={"type": "Feature", "geometry": geo, "properties": {}}) == plain
    other = [(31.48, 74.20), (31.48, 74.22), (31.49, 74.22)]
    multi = {"type": "MultiPolygon", "coordinates": [geo["coordinates"], [[[lon, lat] for lat, lon in other]]]}
    assert grid.zone(polygon=multi) == plain | grid.zone(polygon=other)
    for bad, msg in ([[(31.5, 74.3), (31.6, 74.3)], "at least 3"], [[("a", 1), (2, 3), (4, 5)], "must be"],
                     [{"type": "Point", "coordinates": [74, 31]}, "Polygon"], [[], "empty"]):
        with pytest.raises(ZoneError, match=msg):
            grid.zone(polygon=bad)


def test_contains_is_the_sub_cell_of_the_pin(grid):
    zone = grid.zone(polygon=_random_polygon(random.Random(5), 31.51, 74.35, 0.02, 9))
    subs = set(zone.sub_cells())
    rng = random.Random(6)
    hits = 0
    for _ in range(3000):
        lat, lon = 31.48 + rng.random() * 0.08, 74.30 + rng.random() * 0.1
        inside = zone.contains(lat, lon)
        assert inside == (grid.locate(lat, lon).id in subs)
        hits += inside
    assert 100 < hits < 2900
    assert not zone.contains(40.0, 74.3) and not zone.contains(31.5, 90.0)    # outside the grid
    sid = grid.locate(*POINT).id
    assert (sid in grid.zone(polygon=BLOCK)) and grid.zone(polygon=BLOCK).contains(*POINT)


def test_counts_area_bounds_codes(grid):
    zone = grid.zone(polygon=BLOCK)
    subs = zone.sub_cells()
    assert zone.sub_cell_count == len(subs) == len(zone) and subs == sorted(subs)
    assert zone.cell_count == len({ids.parent(s) for s in subs})
    full = zone.cells(full_only=True)
    assert all(zone.covers_cell(c) == "all" for c in full) and len(full) < zone.cell_count
    exact = 0.0
    for s in subs:
        b = grid.sub_cell(s).bounds
        exact += geodesy.rect_area(b.south, b.north, b.west, b.east)
    assert zone.area_km2 == pytest.approx(exact / 1e6, rel=1e-5)     # stored bounds are rounded to 1e-7 degree
    assert 0.9 * 4.44 * 4.75 < zone.area_km2 < 1.1 * 4.44 * 4.75     # the drawn box is ~4.4 km x 4.7 km
    s, w, n, e = zone.bounds()                     # the box of the zone's cells
    for sid in subs[::7]:
        b = grid.sub_cell(sid).bounds
        assert s <= b.south and w <= b.west and b.north <= n and b.east <= e
    assert n - s < 0.06 and e - w < 0.07
    codes = zone.codes()
    assert sum(25 if len(c) == 12 else 1 for c in codes) == zone.sub_cell_count
    assert grid.zone(cells=codes) == zone
    assert Zone({}).empty and Zone({}).bounds() is None and Zone({1: 0}).empty


def test_circle_and_cells(grid):
    circle = grid.zone(circle=(*POINT, 2500))
    assert circle.sub_cells() == sorted(n.id for n in grid.cells_within(*POINT, 2500, level="sub"))
    touching = grid.zone(circle=(*POINT, 2500, True))
    assert set(circle.sub_cells()) < set(touching.sub_cells())
    one = grid.zone(cells=["11-0140-0471", "11-0140-0472-07", grid.from_text("11-0140-0473-24")])
    assert one.covers_cell(grid.from_text("11-0140-0471")) == "all" and one.sub_cell_count == 27
    assert grid.zone(cells="11-0140-0471").sub_cell_count == 25
    with pytest.raises(ZoneError, match="exactly one"):
        grid.zone()
    with pytest.raises(ZoneError, match="exactly one"):
        grid.zone(polygon=BLOCK, cells=["11-0140-0471"])
    with pytest.raises(ZoneError, match="circle must be"):
        grid.zone(circle=(31.5, 74.3))
    with pytest.raises(ZoneError):
        grid.zone(cells=[1.5])


@pytest.mark.parametrize("key", ["punjab/lahore/lahore-cantt", "punjab/lahore", "punjab",
                                 "punjab/lahore/lahore-cantt/dha-phase-vi", "punjab/lahore/lahore-cantt/lahore-cant"])
def test_place_zones_equal_cells_in_place(grid, key):
    zone = grid.zone(place=key)
    assert zone.sub_cells() == grid.cells_in_place(key, level="sub") and zone.sub_cell_count > 0


def test_user_locality_zone(lahore_db, tmp_path):
    with Grid.open(lahore_db) as grid:
        user = grid.attach_user_places(tmp_path / "u.sqlite")
        loc = user.add("Gali 7", polygon=BLOCK, author="ali")
        assert grid.zone(place=loc.key).sub_cells() == user.sub_cells(loc)


def test_pakistan_only(grid):
    border = [(31.49, 74.55), (31.49, 74.66), (31.54, 74.66), (31.54, 74.55)]   # across the border with India
    every = grid.zone(polygon=border)
    pk = grid.zone(polygon=border, pakistan_only=True)
    flags = grid.pakistan_flags(every.sub_cells(), level="sub")
    assert pk.sub_cells() == [s for s in every.sub_cells() if flags[s] != "outside"]
    assert 0 < pk.sub_cell_count < every.sub_cell_count


def test_set_operations(grid):
    a, b = grid.zone(polygon=BLOCK), grid.zone(circle=(*POINT, 3000))
    sa, sb = set(a.sub_cells()), set(b.sub_cells())
    assert set((a | b).sub_cells()) == sa | sb
    assert set((a & b).sub_cells()) == sa & sb
    assert set((a - b).sub_cells()) == sa - sb
    assert (a - a).empty and (a & a) == a and hash(a | b) == hash(b | a)
    assert (a - b).contains(31.539, 74.331)        # a corner of the box, beyond the circle


def test_store_and_load(grid):
    zone = grid.zone(polygon=[BLOCK, [(31.51, 74.34), (31.51, 74.36), (31.53, 74.36), (31.53, 74.34)]])
    data = json.loads(json.dumps(zone.to_dict()))
    assert data["sub_cells"] == zone.sub_cell_count and data["full"] == sorted(data["full"])
    back = Zone.from_dict(data, grid)
    assert back == zone and back.contains(*POINT) == zone.contains(*POINT)
    assert grid.zone(polygon=BLOCK).to_dict() == grid.zone(polygon=BLOCK).to_dict()      # deterministic
    loose = Zone.from_dict(data)                    # no layout: contents work, pin checks need one
    assert loose == zone and loose.sub_cells() == zone.sub_cells()
    with pytest.raises(ZoneError, match="layout"):
        loose.contains(*POINT)
    for bad in ({**data, "zone_version": 9}, {**data, "partial": [[data["partial"][0][0], 0]]},
                {**data, "partial": [[data["partial"][0][0], FULL]]}):
        with pytest.raises(ZoneError):
            Zone.from_dict(bad, grid)
    with pytest.raises(Exception):
        Zone.from_dict({**data, "full": [ids.cell_id(3, 9999, 0)]}, grid)


def test_zone_index(grid):
    shops = {"shop-42": grid.zone(polygon=BLOCK), "shop-77": grid.zone(circle=(31.49, 74.30, 3000)),
             "lahore": grid.zone(place="punjab/lahore")}
    index = ZoneIndex(shops)
    rng = random.Random(9)
    for _ in range(2000):
        lat, lon = 31.48 + rng.random() * 0.09, 74.25 + rng.random() * 0.15
        assert index.at(lat, lon) == [n for n, z in shops.items() if z.contains(lat, lon)]
    assert index.at(*POINT) == ["shop-42", "lahore"]
    assert index.at_sub_cell(grid.locate(*POINT).id) == ["shop-42", "lahore"]
    assert index.at(45.0, 74.0) == []
    with pytest.raises(ZoneError):
        ZoneIndex({"x": Zone({})})


def test_too_large():
    with pytest.raises(ZoneError, match="more than"):
        polygon_masks(LAYOUT, rings_e7([(24, 61), (24, 77), (37, 77), (37, 61)]))


def test_cli(lahore_db, tmp_path, capsys):
    out = tmp_path / "zone.json"
    poly = tmp_path / "poly.json"
    poly.write_text(json.dumps([list(p) for p in BLOCK]))
    base = ["zone", "--db", str(lahore_db)]
    assert main(base + ["make", "--polygon", str(poly), "-o", str(out)]) == 0
    made = json.loads(capsys.readouterr().out)
    with Grid.open(lahore_db) as grid:
        zone = grid.zone(polygon=BLOCK)
        assert made["sub_cells"] == zone.sub_cell_count and json.loads(out.read_text()) == zone.to_dict()
    assert main(base + ["check", str(out), str(POINT[0]), str(POINT[1])]) == 0
    assert json.loads(capsys.readouterr().out) == {"inside": True, "point": list(POINT), "sub_cell": "11-0140-0471-00"}
    assert main(base + ["check", str(out), "31.6", "74.3"]) == 0
    assert json.loads(capsys.readouterr().out)["inside"] is False
    assert main(base + ["show", str(out), "--codes"]) == 0
    assert json.loads(capsys.readouterr().out)["codes"]
    assert main(base + ["make", "--circle", "31.5204", "74.3587", "1000", "--touching", "--pakistan-only"]) == 0
    assert json.loads(capsys.readouterr().out)["sub_cells"] > 78
    assert main(base + ["make", "--place", "punjab/lahore/lahore-cantt"]) == 0
    assert main(base + ["make", "--cells", "11-0140-0471", "11-0140-0472-03"]) == 0
    capsys.readouterr()
    assert main(base + ["make", "--polygon", str(tmp_path / "missing.json")]) == 1


def test_lat_lon_e7_rounding_matches_locate(grid):
    zone = grid.zone(cells=[grid.locate(*POINT).code])
    lat, lon = (v / SCALE for v in (to_e7(POINT[0]), to_e7(POINT[1])))
    assert zone.contains(lat, lon) and zone.contains(*POINT)
