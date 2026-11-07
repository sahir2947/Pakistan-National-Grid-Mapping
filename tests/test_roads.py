"""User roads: streets drawn and named by an app's users, kept in the user places file."""

import json
import math
import random

import pytest

from pakgrid import Grid, UserPlacesError, ids, navigate
from pakgrid.cli import main
from pakgrid.layout import SCALE, GridLayout, to_e7
from pakgrid.roads import STREET_ON_M, line_masks, nearest_on_line

LAYOUT = GridLayout.from_spec()
STREET = [(31.5201, 74.3571), (31.5203, 74.3590), (31.5210, 74.3602)]
BOULEVARD = [(31.5180, 74.3585), (31.5240, 74.3592)]


@pytest.fixture
def user(lahore_db, tmp_path):
    with Grid.open(lahore_db) as grid:
        yield grid.attach_user_places(tmp_path / "user.sqlite")


def _brute_masks(line):
    """Sub-cells whose closed box the line touches: dense sampling along each segment plus box tests."""
    pts = [(to_e7(a), to_e7(b)) for a, b in line]
    found = set()
    for (y1, x1), (y2, x2) in zip(pts, pts[1:]):
        lo_y, hi_y, lo_x, hi_x = min(y1, y2), max(y1, y2), min(x1, x2), max(x1, x2)
        for q, i, j in LAYOUT.items_in_area(lo_y - 30000, lo_x - 30000, hi_y + 30000, hi_x + 30000, "sub"):
            s, w, n, e = LAYOUT.quadrants[q].item_bounds_e7(i, j, "sub")
            if _segment_meets_box(y1, x1, y2, x2, s, w, n, e):
                found.add(GridLayout.item_id(q, i, j, "sub"))
    return found


def _segment_meets_box(y1, x1, y2, x2, s, w, n, e):
    """Liang-Barsky clip of the segment against the closed box, in exact fractions."""
    from fractions import Fraction
    t0, t1 = Fraction(0), Fraction(1)
    dx, dy = x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - w), (dx, e - x1), (-dy, y1 - s), (dy, n - y1)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = Fraction(q, p)
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _subs(masks):
    return {ids.sub_cell_id(c, k) for c, m in masks.items() for k in range(25) if m >> k & 1}


def test_sub_cells_crossed_are_exact():
    rng = random.Random(5)
    lines = [STREET, BOULEVARD, [(30.0, 69.30), (30.2, 69.55)],               # across the 00/10 midline
             [(30.20, 69.3), (30.32, 69.32)],                                   # across the mid latitude
             [(31.52, 74.30), (31.52, 74.40)], [(31.50, 74.35), (31.56, 74.35)]]  # along a row and a column
    for _ in range(25):
        lat, lon = 24.5 + rng.random() * 12, 61.5 + rng.random() * 15
        line = [(lat, lon)]
        for _ in range(rng.randint(1, 6)):
            lat += (rng.random() - 0.5) * 0.04
            lon += (rng.random() - 0.5) * 0.04
            line.append((round(lat, 7), round(lon, 7)))
        lines.append(line)
    for line in lines:
        pts = [(to_e7(a), to_e7(b)) for a, b in line]
        assert _subs(line_masks(LAYOUT, pts)) == _brute_masks(line), line


def test_lines_on_grid_lines_touch_both_sides():
    ql = LAYOUT.quadrants[3]
    lat = ql.lat_e7[140 * 5 + 2] / SCALE                    # exactly on a sub-cell row line
    found = _subs(line_masks(LAYOUT, [(to_e7(lat), to_e7(74.3600)), (to_e7(lat), to_e7(74.3620))]))
    rows = {(s & 31) // 5 for s in found}
    assert rows == {1, 2}                                   # the sub-cells above and below


def test_add_and_describe(user):
    s7 = user.add_road("Street 7", author="ali", kind="Street", local_name="گلی ۷", line=STREET,
                       details={"oneway": False, "surface": "paved", "vehicles": ["motorbike", "car", "car"]})
    assert s7.key == "punjab/lahore/lahore-cantt/roads/street-7" and s7.kind == "street"
    assert s7.details == {"oneway": False, "surface": "paved", "vehicles": ["car", "motorbike"]}
    expected = sum(navigate.direction(*a, *b).distance_m for a, b in zip(STREET, STREET[1:]))
    assert s7.length_m == round(expected, 1)
    assert user.road_sub_cells(s7) == sorted(_brute_masks(STREET)) and s7.sub_cell_count == len(_brute_masks(STREET))
    d = user.describe_road(s7)
    assert d["length_m"] == s7.length_m and d["start"] == list(STREET[0]) and d["end"] == list(STREET[-1])
    assert d["sub_cell_codes"] == [ids.sub_cell_code(s) for s in user.road_sub_cells(s7)]
    assert [t["key"] for t in d["places"]["tehsil"]] == ["punjab/lahore/lahore-cantt"]
    assert d["label"].startswith("Street 7, ") and d["label"].endswith("Lahore Cantt, Lahore, Punjab")
    json.dumps(d, ensure_ascii=False)
    f = s7.to_geojson()
    assert f["geometry"]["type"] == "LineString" and f["geometry"]["coordinates"][0] == [STREET[0][1], STREET[0][0]]
    assert f["properties"]["surface"] == "paved"
    assert user.roads_geojson()["features"][0]["id"] == s7.key
    assert user.find_roads("street 7")[0].key == s7.key and user.find_roads("گلی")[0].key == s7.key
    assert user.add_road("Street 7", author="ali", line=[(31.53, 74.36), (31.531, 74.362)]).key == s7.key + "-2"


def test_junctions(user):
    s7 = user.add_road("Street 7", author="ali", line=STREET)
    mb = user.add_road("Main Boulevard", author="ali", kind="main road", line=BOULEVARD)
    near_end = (STREET[-1][0] + 0.00005, STREET[-1][1] + 0.00002)            # ~6 m from Street 7's end
    gali = user.add_road("Gali 3", author="sara", line=[near_end, (31.5230, 74.3610)])
    far = user.add_road("Far Road", author="sara", line=[(31.5300, 74.3700), (31.5310, 74.3720)])
    j = user.junctions(s7)
    assert [(x["name"], x["how"]) for x in j] == [("Main Boulevard", "crosses"), ("Gali 3", "meets")]
    assert j[0]["along_m"] < j[1]["along_m"] <= s7.length_m
    d, _, _ = nearest_on_line(BOULEVARD, *j[0]["point"])
    assert d < 0.5                                                         # the crossing is on both roads
    assert [x["name"] for x in user.junctions(mb)] == ["Street 7"] and user.junctions(far) == []
    assert [x["name"] for x in user.junctions(gali)] == ["Street 7"]


def test_streets_on_lookups(user):
    grid = user.grid
    plain = grid.locate(31.52025, 74.3580).place
    assert plain.street is None
    s7 = user.add_road("Street 7", author="ali", line=STREET)
    tags = grid.locate(31.52025, 74.3580).place                           # ~6 m from the street
    assert tags.street.road.key == s7.key and tags.street.on and tags.street_text == "Street 7"
    assert tags.label == "Street 7, " + plain.label
    assert tags.to_dict()["street"]["name"] == "Street 7"
    near = grid.locate(31.5207, 74.3580).place                             # ~60 m away
    assert not near.street.on and near.street_text.startswith("near Street 7, ")
    assert STREET_ON_M < near.street.distance_m <= 100
    assert grid.locate(31.5300, 74.3800).place.street is None             # nothing within 100 m
    sid = grid.locate(31.52025, 74.3580).id
    assert grid.place(sid).street.road.key == s7.key
    m = user.nearest_road(31.52025, 74.3580)
    d, p, _ = nearest_on_line(s7.line, 31.52025, 74.3580)
    assert m.distance_m == d and (m.lat, m.lon) == (round(p[0], 7), round(p[1], 7))


def test_update_remove_history(user):
    grid = user.grid
    r = user.add_road("Street 7", author="ali", line=STREET)
    new = user.update_road(r.key, author="sara", name="Street Seven", kind="gali", details={"oneway": True})
    assert new.key == r.key and new.name == "Street Seven" and new.kind == "gali" and new.details == {"oneway": True}
    moved = user.update_road(r.key, author="sara", line=BOULEVARD)
    assert user.road_sub_cells(moved) == sorted(_brute_masks(BOULEVARD)) and moved.length_m != r.length_m
    user.remove_road(r.key, author="omar")
    assert user.roads() == [] and user.roads(include_removed=True)[0].removed_by == "omar"
    assert grid.locate(31.52025, 74.3580).place.street is None
    with pytest.raises(UserPlacesError, match="removed"):
        user.update_road(r.key, author="ali", name="x")
    assert [(h["action"], h["author"]) for h in user.road_history(r.key)] == \
        [("add", "ali"), ("update", "sara"), ("update", "sara"), ("remove", "omar")]


@pytest.mark.parametrize("kwargs, message", [
    (dict(name="X", author="ali", line=[(31.52, 74.35)]), "at least 2"),
    (dict(name="X", author="ali", line=[(31.52, 74.35), (31.52, 74.35)]), "at least 2"),
    (dict(name="X", author="ali", line=[("a", 1), (2, 3)]), "must be \\(lat, lon\\)"),
    (dict(name="X", author="", line=STREET), "author"),
    (dict(name="X", author="ali", line=[(31.50, 74.82), (31.51, 74.84)]), "not pass through any sub-cell in Pakistan"),
    (dict(name="X", author="ali", line=STREET, details={"vehicles": ["tank"]}), "vehicles"),
    (dict(name="X", author="ali", line=STREET, details={"oneway": "yes"}), "oneway"),
    (dict(name="X", author="ali", line=STREET, details=["oneway"]), "dict"),
])
def test_bad_input(user, kwargs, message):
    with pytest.raises(UserPlacesError, match=message):
        user.add_road(**kwargs)
    assert user.roads() == []


def test_geojson_line_and_export_import(user, lahore_db, tmp_path):
    geo = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in STREET]}}
    r = user.add_road("Street 7", author="ali", line=geo, details={"surface": "unpaved"})
    assert r.line == tuple(STREET)
    user.add("Gali 9", cells=["11-0140-0471"], author="ali")
    data = json.loads(json.dumps(user.export_data(), ensure_ascii=False))
    assert [x["key"] for x in data["roads"]] == [r.key]
    with Grid.open(lahore_db) as grid:
        other = grid.attach_user_places(tmp_path / "copy.sqlite")
        added = other.import_data(data, author="admin")
        assert {getattr(x, "key") for x in added} == {r.key, user.localities()[0].key}
        assert other.get_road(r.key).details == {"surface": "unpaved"}
        assert other.road_sub_cells(r.key) == user.road_sub_cells(r)


def test_older_files_gain_the_road_tables(lahore_db, tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite"
    with Grid.open(lahore_db) as grid:
        grid.attach_user_places(path).add("Gali 9", cells=["11-0140-0471"], author="ali")
    db = sqlite3.connect(path)
    db.executescript("DROP TABLE road_cells; DROP TABLE road_history; DROP TABLE roads;")     # as made before roads
    db.close()
    with Grid.open(lahore_db, user_places=path) as grid:
        user = grid.user_places
        assert user.roads() == [] and user.localities()[0].name == "Gali 9"
        user.add_road("Street 7", author="ali", line=STREET)
        assert len(user.roads()) == 1


def test_cli(lahore_db, tmp_path, capsys):
    f = tmp_path / "u.sqlite"
    line = tmp_path / "line.json"
    line.write_text(json.dumps([list(p) for p in STREET]))
    base = ["user-places", "--db", str(lahore_db), "--file", str(f)]
    assert main(base + ["add-road", "Street 7", "--line", str(line), "--author", "ali", "--oneway", "--surface", "paved",
                        "--vehicles", "motorbike", "car", "--local-name", "گلی ۷"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["key"] == "punjab/lahore/lahore-cantt/roads/street-7"
    assert out["details"] == {"oneway": True, "surface": "paved", "vehicles": ["car", "motorbike"]}
    assert main(base + ["update-road", out["key"], "--author", "sara", "--speed-kmh", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["details"]["speed_kmh"] == 30
    assert main(base + ["roads"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "Street 7"
    assert main(base + ["road", out["key"]]) == 0
    assert json.loads(capsys.readouterr().out)["label"].startswith("Street 7, ")
    assert main(base + ["near-road", "31.52025", "74.3580"]) == 0
    assert json.loads(capsys.readouterr().out)["on"] is True
    assert main(["locate", "31.52025", "74.3580", "--db", str(lahore_db), "--user-places", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["place"]["street"]["name"] == "Street 7"
    assert main(base + ["roads-geojson"]) == 0
    assert json.loads(capsys.readouterr().out)["features"][0]["geometry"]["type"] == "LineString"
    assert main(base + ["remove-road", out["key"], "--author", "omar"]) == 0
    capsys.readouterr()
    with pytest.raises(SystemExit):                                           # argparse refuses it
        main(base + ["add-road", "Bad", "--line", str(line), "--author", "ali", "--vehicles", "tank"])
    capsys.readouterr()


def test_long_diagonal_road_is_quick(user):
    import time
    line = [(31.48, 74.20), (31.57, 74.55)]                    # ~35 km across the test piece
    t = time.perf_counter()
    r = user.add_road("Long Road", author="ali", line=line)
    assert time.perf_counter() - t < 5
    assert r.sub_cell_count == len(_brute_masks(line)) and math.isclose(
        r.length_m, navigate.direction(*line[0], *line[1]).distance_m, abs_tol=0.06)
