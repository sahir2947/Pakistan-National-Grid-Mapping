"""GeoJSON output: exact, valid outlines of cells, zones and places."""

import json
import random

import pytest

from pakgrid import Zone, ids
from pakgrid.cli import main
from pakgrid.geojson import geometry, outline_rings, polygons
from pakgrid.layout import GridLayout, to_e7

LAYOUT = GridLayout.from_spec()
POINT = (31.5204, 74.3587)


def _polys(geom):
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def _e7(ring):
    return [(to_e7(x), to_e7(y)) for x, y in ring]


def _area2(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))


def _inside_geometry(lon_e7, lat_e7, geom):
    inside = False
    for poly in _polys(geom):
        for ring in poly:
            r = _e7(ring)
            for (x1, y1), (x2, y2) in zip(r, r[1:]):
                if x1 == x2 and (y1 > lat_e7) != (y2 > lat_e7) and x1 > lon_e7:
                    inside = not inside
    return inside


def _check(masks, layout=LAYOUT):
    """The geometry is valid GeoJSON, covers exactly the sub-cells and nothing else."""
    geom = geometry(layout, masks)
    subs = {ids.sub_cell_id(c, k) for c, m in masks.items() for k in range(25) if m >> k & 1}
    if not subs:
        assert geom is None
        return None
    json.dumps(geom)
    total = 0
    for poly in _polys(geom):
        for k, ring in enumerate(poly):
            r = _e7(ring)
            assert r[0] == r[-1] and len(r) >= 5
            assert len(set(r[:-1])) == len(r) - 1                       # simple: no vertex twice
            for (x1, y1), (x2, y2) in zip(r, r[1:]):
                assert (x1 == x2) != (y1 == y2)                            # axis-aligned, no zero-length edge
            for a, b, c in zip(r, r[1:], r[2:] + r[1:2]):
                assert not (a[0] == b[0] == c[0] or a[1] == b[1] == c[1])  # no point on a straight run
            area = _area2(r)
            assert (area > 0) if k == 0 else (area < 0)                    # outer anticlockwise, holes clockwise
            assert min(r[:-1], key=lambda p: (p[1], p[0])) == r[0]         # starts at its south-west corner
            total += area
    # the area enclosed equals the sum of the sub-cells' boxes, exactly (planar, in 1e-7 degree)
    expected = 0
    box = None
    for s in subs:
        q, i, j = layout.item_from_id(s, "sub")
        so, w, n, e = layout.quadrants[q].item_bounds_e7(i, j, "sub")
        expected += 2 * (n - so) * (e - w)
        box = (so, w, n, e) if box is None else (min(box[0], so), min(box[1], w), max(box[2], n), max(box[3], e))
    assert total == expected
    # and it holds exactly the sub-cells of the set: test every sub-cell centroid around it
    for q, i, j in layout.items_in_area(box[0] - 30000, box[1] - 30000, box[2] + 30000, box[3] + 30000, "sub"):
        lat, lon = layout.quadrants[q].item_centroid_e7(i, j, "sub")
        assert _inside_geometry(lon, lat, geom) == (GridLayout.item_id(q, i, j, "sub") in subs)
    return geom


def _random_masks(rng, q, row, col, n_cells, density):
    masks = {}
    for _ in range(n_cells):
        c = ids.cell_id(q, row + rng.randint(0, 5), col + rng.randint(0, 5))
        m = 0
        for k in range(25):
            if rng.random() < density:
                m |= 1 << k
        masks[c] = masks.get(c, 0) | m
    return masks


def test_single_shapes():
    c = ids.cell_id(3, 140, 471)
    assert _check({c: (1 << 25) - 1})["type"] == "Polygon"                     # a whole cell
    assert _check({c: 1 | 1 << 6})["type"] == "MultiPolygon"                    # touching at a corner: kept apart
    holed = _check({c: (1 << 25) - 1 - (1 << 12)})
    assert holed["type"] == "Polygon" and len(holed["coordinates"]) == 2        # a hole in the middle
    assert _check({c: 0}) is None and _check({}) is None
    # a ring of 8 around a hole whose corners touch the outer ring at four points
    ring = sum(1 << k for k in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23))
    _check({c: ring})
    _check({c: sum(1 << k for k in range(25) if (k // 5 + k % 5) % 2 == 0)})  # a checkerboard


@pytest.mark.parametrize("seed", range(12))
def test_random_sets(seed):
    rng = random.Random(seed)
    density = rng.choice([0.2, 0.5, 0.8, 0.95])
    _check(_random_masks(rng, 3, 140, 470, rng.randint(1, 25), density))


@pytest.mark.parametrize("q_pair", [(0, 2), (1, 3), (0, 1), (2, 3)])
def test_across_quadrant_lines(q_pair):
    """Rows do not line up across the midline between quadrants 00 and 10 (or 01 and 11): the
    outline still covers exactly the sub-cells, with the shared edge cancelled."""
    rng = random.Random(sum(q_pair))
    masks = {}
    qa, qb = q_pair
    la, lb = LAYOUT.quadrants[qa], LAYOUT.quadrants[qb]
    if (qa, qb) in ((0, 2), (1, 3)):                     # east-west neighbours: last columns of a, first of b
        r_a = la.nrows - 4 if qa == 0 else 3
        r_b = min(range(lb.nrows), key=lambda r: abs(lb.lat_e7[r * 5] - la.lat_e7[r_a * 5]))
        for dr in range(4):
            for dc in range(3):
                masks[ids.cell_id(qa, r_a + dr, la.ncols[r_a + dr] - 1 - dc)] = rng.getrandbits(25) | 1 << 4
                masks[ids.cell_id(qb, r_b + dr, dc)] = rng.getrandbits(25) | 1
    else:                                                # north-south neighbours: top rows of a, bottom of b
        for dr in range(3):
            for dc in range(4):
                masks[ids.cell_id(qa, la.nrows - 1 - dr, 300 + dc)] = rng.getrandbits(25) | 0x1F << 20
                masks[ids.cell_id(qb, dr, 300 + dc)] = rng.getrandbits(25) | 0x1F
    _check(masks)
    full = {c: (1 << 25) - 1 for c in masks}
    assert len(_polys(_check(full))) == 1                  # whole cells on both sides: one shape


def test_deterministic():
    rng = random.Random(4)
    masks = _random_masks(rng, 3, 140, 470, 20, 0.6)
    shuffled = dict(sorted(masks.items(), key=lambda kv: rng.random()))
    assert geometry(LAYOUT, masks) == geometry(LAYOUT, shuffled)
    assert polygons(outline_rings(LAYOUT, masks)) == polygons(outline_rings(LAYOUT, shuffled))


def test_grid_features(grid):
    f = grid.geojson("11-0140-0471-00")
    assert f["type"] == "Feature" and f["id"] == "11-0140-0471-00"
    b = grid.sub_cell("11-0140-0471-00").bounds
    assert f["geometry"]["coordinates"][0] == [[b.west, b.south], [b.east, b.south], [b.east, b.north],
                                               [b.west, b.north], [b.west, b.south]]
    p = f["properties"]
    assert p["level"] == "sub" and p["pakistan"] == "inside" and p["place"].endswith("Lahore Cantt, Lahore, Punjab")
    assert p["centroid"] == [grid.sub_cell("11-0140-0471-00").centroid[1], grid.sub_cell("11-0140-0471-00").centroid[0]]
    cell = grid.geojson("11-0140-0471")
    assert cell["properties"]["level"] == "cell" and grid.geojson(grid.from_text("11-0140-0471"), "cell") == cell
    fc = grid.cells_geojson(["11-0140-0471", "11-0140-0472-03"])
    assert fc["type"] == "FeatureCollection" and [x["id"] for x in fc["features"]] == ["11-0140-0471", "11-0140-0472-03"]
    merged = grid.cells_geojson(["11-0140-0471", "11-0140-0472"], merge=True)
    assert merged["geometry"]["type"] == "Polygon" and len(merged["geometry"]["coordinates"][0]) == 5
    assert merged["properties"]["sub_cells"] == 50
    json.dumps([f, cell, fc, merged])


@pytest.mark.parametrize("key", ["punjab/lahore/lahore-cantt", "punjab/lahore/lahore-cantt/dha-phase-vi",
                                 "punjab/lahore/lahore-cantt/lahore-cant"])
def test_place_outlines_are_exact(grid, key):
    f = grid.place_geojson(key)
    zone = grid.zone(place=key)
    assert f["geometry"] == zone.to_geojson()["geometry"] == _check(zone.masks)
    props = f["properties"]
    assert props["key"] == key and props["sub_cells"] == zone.sub_cell_count
    assert props["area_km2"] == pytest.approx(zone.area_km2)


def test_zone_geojson(grid):
    zone = grid.zone(polygon=[[(31.48, 74.30), (31.48, 74.40), (31.56, 74.40), (31.56, 74.30)],
                              [(31.50, 74.33), (31.50, 74.37), (31.54, 74.37), (31.54, 74.33)]])
    f = zone.to_geojson({"shop": "shop-42"})
    assert f["properties"]["shop"] == "shop-42" and f["geometry"]["type"] == "Polygon"
    assert len(f["geometry"]["coordinates"]) == 2                               # the outer edge and the hole
    _check(zone.masks)
    # the outline really is the zone: a pin inside the outline is in the zone, and outside is not
    rng = random.Random(3)
    for _ in range(500):
        lat, lon = 31.47 + rng.random() * 0.1, 74.29 + rng.random() * 0.12
        if any(abs(to_e7(v) - to_e7(round(v, 3))) == 0 for v in (lat, lon)):
            continue
        assert zone.contains(lat, lon) == _inside_geometry(to_e7(lon), to_e7(lat), f["geometry"])
    assert Zone({}).to_geojson()["geometry"] is None


def test_cli(lahore_db, tmp_path, capsys):
    base = ["geojson", "--db", str(lahore_db)]
    assert main(base + ["--cells", "11-0140-0471", "11-0140-0472-03"]) == 0
    assert json.loads(capsys.readouterr().out)["type"] == "FeatureCollection"
    assert main(base + ["--cells", "11-0140-0471", "11-0140-0472", "--merge"]) == 0
    assert json.loads(capsys.readouterr().out)["geometry"]["type"] == "Polygon"
    assert main(base + ["--place", "punjab/lahore/lahore-cantt"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["name"] == "Lahore Cantt"
    assert main(base + ["--circle", "31.5204", "74.3587", "1500"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["sub_cells"] > 100
    out = tmp_path / "zone.json"
    assert main(["zone", "--db", str(lahore_db), "make", "--circle", "31.5204", "74.3587", "1000", "-o", str(out)]) == 0
    capsys.readouterr()
    target = tmp_path / "zone.geojson"
    assert main(base + ["--zone", str(out), "-o", str(target)]) == 0
    capsys.readouterr()
    assert json.loads(target.read_text())["type"] == "Feature"
    assert main(base) == 1                                                      # nothing to export
