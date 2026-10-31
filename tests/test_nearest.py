"""Nearest lookup: PointIndex, Grid.nearest (points in your own database) and Grid.nearest_places."""

import json
import random
import threading

import pytest

from pakgrid import Grid, ids, navigate
from pakgrid.cli import main
from pakgrid.layout import to_e7
from pakgrid.nearest import MAX_NEAREST_M, PointIndex

PIN = (31.5204, 74.3587)


def _brute(points, lat, lon, k, max_m=MAX_NEAREST_M, where=None):
    """The k nearest by measuring every point, ties in the order given: [(id, distance)]."""
    found = []
    for seq, (pid, plat, plon) in enumerate(points):
        d = navigate.direction(lat, lon, plat, plon).distance_m
        if d <= max_m and (where is None or where(pid)):
            found.append((d, seq, pid))
    found.sort()
    return [(pid, d) for d, _, pid in found[:k]]


def _scatter(rng, n, lat=31.52, lon=74.36, spread=0.3):
    return [(f"p{i}", round(lat + (rng.random() - 0.5) * spread, 7), round(lon + (rng.random() - 0.5) * spread / 0.85, 7))
            for i in range(n)]


@pytest.mark.parametrize("n, spread, k", [(2000, 0.3, 5), (200, 0.3, 10), (15, 0.6, 5), (3, 0.05, 5),
                                          (500, 0.02, 1), (5000, 1.0, 25)])
def test_nearest_equals_measuring_every_point(grid, n, spread, k):
    rng = random.Random(n + k)
    points = _scatter(rng, n, spread=spread)
    index = grid.point_index(points)
    for _ in range(25):
        lat, lon = 31.52 + (rng.random() - 0.5) * spread, 74.36 + (rng.random() - 0.5) * spread
        got = index.nearest(lat, lon, k)
        assert [(x.id, x.distance_m) for x in got] == _brute(points, lat, lon, k)
        for x in got:
            way = navigate.direction(lat, lon, x.lat, x.lon)
            assert x.bearing_deg == way.bearing_deg and x.direction == way.compass_short
            assert x.text == f"{navigate.format_distance(x.distance_m)} {x.direction}"


def test_max_distance_and_filters(grid):
    rng = random.Random(7)
    points = _scatter(rng, 800, spread=0.4)
    index = grid.point_index(points)
    for max_m in (0, 150, 999, 1001, 2500, 12345.6):
        assert [(x.id, x.distance_m) for x in index.nearest(*PIN, 50, max_m=max_m)] == \
            _brute(points, *PIN, 50, max_m=max_m)
    even = lambda pid: int(pid[1:]) % 2 == 0         # noqa: E731
    assert [x.id for x in index.nearest(*PIN, 7, where=even)] == [p for p, _ in _brute(points, *PIN, 7, where=even)]
    inside = index.within(*PIN, 3000)
    assert [(x.id, x.distance_m) for x in inside] == _brute(points, *PIN, 10**6, max_m=3000)
    assert all(x.distance_m <= 3000 for x in inside) and inside


def test_ties_keep_the_order_points_were_added(grid):
    index = PointIndex(grid)
    for name in ("b", "a", "c"):
        index.add(name, 31.5300, 74.3587)            # the same spot: equal distances
    index.add("near", 31.5210, 74.3587)
    assert [x.id for x in index.nearest(*PIN, 4)] == ["near", "b", "a", "c"]


def test_add_move_remove(grid):
    index = grid.point_index([("r1", 31.5300, 74.3587), ("r2", 31.5250, 74.3587)])
    assert len(index) == 2 and "r1" in index and index.get("r2") == (31.5250, 74.3587)
    assert [x.id for x in index.nearest(*PIN, 1)] == ["r2"]
    index.add("r1", 31.5210, 74.3590)                # a rider moves
    assert [x.id for x in index.nearest(*PIN, 2)] == ["r1", "r2"] and len(index) == 2
    index.remove("r1")
    index.remove("nobody")
    assert [x.id for x in index.nearest(*PIN, 5)] == ["r2"] and "r1" not in index
    index.remove("r2")
    assert index.nearest(*PIN, 5) == [] and len(index) == 0
    with pytest.raises(ValueError):
        index.add("x", 45.0, 74.0)                   # outside the grid
    for bad in (float("nan"), "31.5", True):
        with pytest.raises(ValueError):
            index.add("x", bad, 74.0)


def test_bad_queries(grid):
    index = grid.point_index([("a", *PIN)])
    for k in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            index.nearest(*PIN, k)
    for m in (-1, MAX_NEAREST_M + 1, "10"):
        with pytest.raises(ValueError):
            index.nearest(*PIN, 1, max_m=m)
    with pytest.raises(ValueError):
        index.nearest(float("inf"), 74.0)


def test_across_the_quadrant_midline(south_edge_db):
    """Points on both sides of the midline between quadrants 00 and 10, where rows do not line up."""
    with Grid.open(south_edge_db) as grid:
        ql = grid.layout.quadrants[0]
        lat0 = ql.south + 0.01
        rng = random.Random(11)
        points = [(f"m{i}", round(lat0 + rng.random() * 0.015, 7), round(69.33 + rng.random() * 0.14, 7))
                  for i in range(300)]
        index = grid.point_index(points)
        for _ in range(20):
            lat, lon = lat0 + rng.random() * 0.015, 69.35 + rng.random() * 0.1
            assert [(x.id, x.distance_m) for x in index.nearest(lat, lon, 8)] == _brute(points, lat, lon, 8)


def test_points_in_your_own_database(grid):
    """Grid.nearest asks fetch for a few cells at a time, nearest first, each cell once."""
    rng = random.Random(3)
    points = _scatter(rng, 3000, spread=0.4)
    by_cell = {}
    for pid, lat, lon in points:                    # each point stored with its cell ID, as an app would
        cid = ids.cell_id(*grid.layout.locate_e7(to_e7(lat), to_e7(lon))[:3])
        by_cell.setdefault(cid, []).append((pid, lat, lon))
    asked = []

    def fetch(cell_ids):                              # like Shop.objects.filter(cell_id__in=cell_ids)
        asked.append(list(cell_ids))
        return [row for c in cell_ids for row in by_cell.get(c, [])]

    got = grid.nearest(*PIN, fetch, k=6)
    # the same answer as measuring everything (ties: the order fetch returned them)
    assert [x.distance_m for x in got] == [d for _, d in _brute(points, *PIN, 6)]
    flat = [c for batch in asked for c in batch]
    assert len(flat) == len(set(flat))               # never the same cell twice
    assert len(flat) < 60                            # only the cells around the pin, not all ~1,000
    assert got[0].id in {p for p, _, _ in points}


def test_nearest_places(grid):
    found = grid.nearest_places(*PIN, k=5)
    locs = [p for p in grid.places("locality")]
    brute = sorted((navigate.direction(*PIN, p.lat, p.lon).distance_m, p.id) for p in locs)[:5]
    assert [(x.distance_m, x.id.id) for x in found] == brute
    assert found[0].id.level == "locality" and found[0].id.name == "Askari 2"
    towns = grid.nearest_places(*PIN, k=3, kind={"town", "city"})
    assert [x.id.kind in ("town", "city") for x in towns] == [True] * 3
    assert towns[0].id.name == "Lahore Cant" and grid.nearest_places(*PIN, k=2, kind="village")[0].id.kind == "village"
    assert grid.nearest_places(*PIN, k=5, max_m=100) == []


def test_threads(grid):
    index = grid.point_index()
    errors = []

    def rider(n):
        try:
            for step in range(200):
                index.add(f"r{n}", 31.50 + step * 1e-4, 74.30 + n * 1e-3)
                index.nearest(*PIN, 3)
        except Exception as exc:                     # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=rider, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(index) == 8


def test_cli(lahore_db, tmp_path, capsys):
    pts = tmp_path / "riders.json"
    pts.write_text(json.dumps([["r1", 31.5300, 74.3587], ["r2", 31.5210, 74.3590], ["r3", 31.4900, 74.3000]]))
    assert main(["nearest", "31.5204", "74.3587", "-k", "2", "--points", str(pts), "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [x["id"] for x in out] == ["r2", "r1"] and out[0]["distance_m"] < out[1]["distance_m"]
    csv_file = tmp_path / "shops.csv"
    csv_file.write_text("id,lat,lon\nshop-1,31.5300,74.3587\nshop-2,31.5210,74.3590\n")
    assert main(["nearest", "31.5204", "74.3587", "--points", str(csv_file), "--db", str(lahore_db)]) == 0
    assert [x["id"] for x in json.loads(capsys.readouterr().out)] == ["shop-2", "shop-1"]
    assert main(["nearest", "31.5204", "74.3587", "-k", "3", "--kind", "town", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["name"] == "Lahore Cant" and {x["kind"] for x in out} == {"town"}
    assert main(["nearest", "31.5204", "74.3587", "-k", "0", "--db", str(lahore_db)]) == 1
