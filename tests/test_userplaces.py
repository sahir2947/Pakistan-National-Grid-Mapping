"""User places: localities drawn by an app's users, kept in a separate file beside the grid."""

import json
import threading

import pytest

from pakgrid import Grid, UserPlacesError, ids, navigate
from pakgrid.cli import main
from pakgrid.errors import InvalidIdError, NotInDatabaseError
from pakgrid.layout import SCALE, GridLayout, to_e7
from pakgrid.userplaces import MAX_REFERENCE_KM, USER_ID_BASE, point_in_rings

# A block in Lahore Cantt (inside the Lahore test piece: quadrant 11, rows 135-145)
BLOCK = [(31.5195, 74.3560), (31.5195, 74.3620), (31.5240, 74.3620), (31.5240, 74.3560)]
POINT = (31.5204, 74.3587)          # inside BLOCK


@pytest.fixture
def user(lahore_db, tmp_path):
    with Grid.open(lahore_db) as grid:
        yield grid.attach_user_places(tmp_path / "user.sqlite")


def _brute_force(grid, polygon):
    """Sub-cells whose centroid is inside the polygon (ray casting in floating point) and in Pakistan."""
    lats, lons = [p[0] for p in polygon], [p[1] for p in polygon]
    found = []
    for sid in grid.cells_in_area(min(lats), min(lons), max(lats), max(lons), level="sub"):
        lat, lon = grid.sub_cell(sid).centroid
        inside = False
        for (y1, x1), (y2, x2) in zip(polygon, polygon[1:] + polygon[:1]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        if inside and grid.place(sid) is not None:
            found.append(sid)
    return found


def test_add_from_a_polygon(user):
    grid = user.grid
    loc = user.add("Gali 7", polygon=BLOCK, author="ali@shop-42", kind="Street")
    subs = user.sub_cells(loc)
    assert subs == _brute_force(grid, BLOCK) and len(subs) == loc.sub_cell_count >= 4
    assert loc.id == USER_ID_BASE + 1 and loc.kind == "street"
    assert loc.key == "punjab/lahore/lahore-cantt/gali-7" and loc.tehsil_key == "punjab/lahore/lahore-cantt"
    centre = grid.locate(loc.lat, loc.lon)
    assert centre.id in subs and (centre.sub_cell.centroid == (loc.lat, loc.lon))
    assert loc.created_by == loc.updated_by == "ali@shop-42" and not loc.removed
    # with no references asked for, the nearest official locality is used
    (ref,) = loc.references
    p = grid.get_place(ref.place_key)
    assert p.level == "locality" and not p.user
    way = navigate.direction(p.lat, p.lon, loc.lat, loc.lon)
    assert ref.distance_m == round(way.distance_m, 1) and ref.direction == way.compass_short
    assert abs(ref.east_m ** 2 + ref.north_m ** 2 - ref.distance_m ** 2) < 0.01 * ref.distance_m ** 2 + 1
    assert ref.text == f"{navigate.format_distance(ref.distance_m)} {ref.direction} of {p.name}"
    nearest = min((q for q in grid.places("locality")),
                  key=lambda q: ((q.lon - loc.lon) * 94_350) ** 2 + ((q.lat - loc.lat) * 111_000) ** 2)
    assert ref.place_key == nearest.key
    assert loc.text == f"Gali 7 ({ref.text})"
    json.dumps(loc.to_dict())


def test_lookups_show_the_user_locality(user):
    grid = user.grid
    osm = grid.locate(*POINT).place
    loc = user.add("Gali 7", polygon=BLOCK, author="ali")
    tags = grid.locate(*POINT).place
    assert tags.locality.user and tags.locality.key == loc.key and tags.user_locality == loc
    assert tags.osm_locality == osm.locality and tags.tehsil == osm.tehsil
    assert tags.label == f"{loc.text}, Lahore Cantt, Lahore, Punjab"
    d = tags.to_dict()["locality"]
    assert d["user"] is True and d["references"][0]["text"] == loc.references[0].text
    assert d["osm_locality"]["key"] == osm.locality.key
    # outside it, nothing changes
    outside = grid.locate(31.5300, 74.3700).place
    assert not outside.locality or not outside.locality.user
    # the place API knows it
    assert grid.get_place(loc.key).user and grid.get_place(loc.id).key == loc.key
    assert grid.find_places("gali 7")[-1].key == loc.key
    assert grid.cells_in_place(loc.key, level="sub") == user.sub_cells(loc)
    for cid in grid.cells_in_place(loc.key):
        assert grid.place(cid, "cell").locality.key == loc.key


def test_cells_and_codes(user):
    grid = user.grid
    loc = user.add("Mohalla Test", cells=["11-0140-0471"], author="ali", local_name="محلہ")
    first = ids.sub_cell_id(3289559, 0)
    assert user.sub_cells(loc) == list(range(first, first + 25))
    assert grid.place("11-0140-0471", "cell").locality.local_name == "محلہ"
    one = user.add("Corner", cells=["11-0140-0471-07", first + 8], author="ali")
    assert user.sub_cells(one) == [first + 7, first + 8]
    assert loc.shape == {"sub_cells": [ids.sub_cell_code(s) for s in range(first, first + 25)]}


def test_overlaps_smallest_wins(user):
    grid = user.grid
    big = user.add("Mohalla", polygon=BLOCK, author="ali")
    small = user.add("Gali", cells=[grid.locate(*POINT).code], author="ali")
    assert grid.locate(*POINT).place.locality.key == small.key
    other = next(s for s in user.sub_cells(big) if s != grid.locate(*POINT).id)
    assert grid.place(other).locality.key == big.key
    user.remove(small, author="sara")
    assert grid.locate(*POINT).place.locality.key == big.key


def test_references(user):
    grid = user.grid
    gali = user.add("Gali 7", polygon=BLOCK, author="ali", nearest=3)
    assert len(gali.references) == 3
    d = [r.distance_m for r in gali.references]
    assert d == sorted(d)
    cant = "punjab/lahore/lahore-cantt/lahore-cant"
    shop = user.add("Shop Row", cells=["11-0140-0470"], author="ali", references=[gali.key, cant], nearest=1)
    keys = [r.place_key for r in shop.references]
    assert keys[:2] == [gali.key, cant] and len(keys) == 3 and keys[2] not in keys[:2]
    assert shop.references[0].text.endswith("of Gali 7")
    by_id = user.add("Lane", cells=["11-0140-0469"], author="ali", references=[grid.get_place(cant).id])
    assert by_id.references[0].place_key == cant
    with pytest.raises(UserPlacesError, match="a reference must be a locality"):
        user.add("X", cells=["11-0140-0468"], author="ali", references=["punjab/lahore"])
    with pytest.raises(InvalidIdError):
        user.add("X", cells=["11-0140-0468"], author="ali", references=["punjab/lahore/nowhere"])
    with pytest.raises(UserPlacesError, match="twice"):
        user.add("X", cells=["11-0140-0468"], author="ali", references=[cant, cant])
    far = next(p for p in grid.places("locality") if abs(p.lat - 31.52) > 1)
    with pytest.raises(UserPlacesError, match=f"within {MAX_REFERENCE_KM:g} km"):
        user.add("X", cells=["11-0140-0468"], author="ali", references=[far.key])
    assert [loc.key for loc in user.localities()] == [gali.key, shop.key, by_id.key]   # failed adds left nothing


@pytest.mark.parametrize("kwargs, message", [
    (dict(polygon=BLOCK, author=None), "author"),
    (dict(polygon=BLOCK, author=" "), "author"),
    (dict(author="ali"), "exactly one of polygon or cells"),
    (dict(author="ali", polygon=BLOCK, cells=["11-0140-0471"]), "exactly one of polygon or cells"),
    (dict(author="ali", polygon=[(31.52, 74.35), (31.53, 74.36)]), "at least 3"),
    (dict(author="ali", polygon=[(31.52040, 74.35870), (31.52041, 74.35870), (31.52041, 74.35871)]),
     "covers no sub-cell"),
    (dict(author="ali", polygon=[(31.50, 74.82), (31.50, 74.84), (31.52, 74.84), (31.52, 74.82)]),
     "covers no sub-cell"),                                           # in India
    (dict(author="ali", polygon=[(31.0, 73.0), (31.0, 75.0), (32.0, 75.0), (32.0, 73.0)]), "too large"),
    (dict(author="ali", polygon=[("a", 1), (1, 2), (2, 3)]), "must be \\(lat, lon\\)"),
])
def test_bad_input(user, kwargs, message):
    with pytest.raises((UserPlacesError, NotInDatabaseError), match=message):
        user.add("Bad", **kwargs)
    assert user.localities() == []


def test_geojson_and_holes(user):
    grid = user.grid
    hole = [(31.5200, 74.3575), (31.5200, 74.3600), (31.5215, 74.3600), (31.5215, 74.3575)]
    geo = {"type": "Polygon", "coordinates": [[[lon, lat] for lat, lon in BLOCK],
                                              [[lon, lat] for lat, lon in hole]]}
    loc = user.add("Ring Road Block", polygon=geo, author="ali")
    subs = set(user.sub_cells(loc))
    assert subs == set(_brute_force(grid, BLOCK)) - set(_brute_force(grid, hole))
    assert loc.shape["polygon"][0][0] == [BLOCK[0][0], BLOCK[0][1]]


def test_point_in_rings_exact():
    sq = [[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]]
    assert point_in_rings(5, 5, sq) and not point_in_rings(15, 5, sq)
    assert point_in_rings(0, 5, sq) and not point_in_rings(10, 5, sq)     # south edge in, north edge out
    assert point_in_rings(5, 0, sq) and not point_in_rings(5, 10, sq)     # west edge in, east edge out
    # a C shape (points are (lat, lon)): lon 0-10, lat 0-10, with a notch at lon 4-10, lat 4-6
    concave = [[(0, 0), (0, 10), (4, 10), (4, 4), (6, 4), (6, 10), (10, 10), (10, 0), (0, 0)]]
    assert point_in_rings(5, 2, concave) and not point_in_rings(5, 8, concave)
    assert point_in_rings(2, 8, concave) and point_in_rings(8, 8, concave)
    holed = sq + [[(3, 3), (3, 7), (7, 7), (7, 3), (3, 3)]]
    assert not point_in_rings(5, 5, holed) and point_in_rings(1, 1, holed)


def test_update_remove_history(user):
    grid = user.grid
    loc = user.add("Gali 7", polygon=BLOCK, author="ali")
    new = user.update(loc.key, author="sara", name="Gali Number 7", local_name="گلی ۷", kind="lane")
    assert new.key == loc.key and new.name == "Gali Number 7" and new.kind == "lane"
    assert new.references == loc.references and new.created_by == "ali" and new.updated_by == "sara"
    moved = user.update(loc.key, author="sara", cells=["11-0140-0465-12"])
    assert user.sub_cells(moved) == [grid.from_text("11-0140-0465-12")]
    assert [r.place_key for r in moved.references] == [r.place_key for r in loc.references]
    assert (moved.lat, moved.lon) != (loc.lat, loc.lon)
    assert moved.references[0].distance_m != loc.references[0].distance_m     # recomputed from the new centre
    renear = user.update(loc.key, author="sara", nearest=2)
    assert len(renear.references) == 2
    gone = user.remove(loc.key, author="omar")
    assert gone.removed and gone.removed_by == "omar"
    assert user.localities() == [] and user.localities(include_removed=True)[0].key == loc.key
    assert not grid.locate(*POINT).place.locality or not grid.locate(*POINT).place.locality.user
    with pytest.raises(UserPlacesError, match="removed"):
        user.update(loc.key, author="ali", name="Back")
    hist = user.history(loc.key)
    assert [(h["action"], h["author"]) for h in hist] == [
        ("add", "ali"), ("update", "sara"), ("update", "sara"), ("update", "sara"), ("remove", "omar")]
    assert hist[1]["locality"]["local_name"] == "گلی ۷"
    again = user.add("Gali 7", polygon=BLOCK, author="ali")       # the old key stays with the removed one
    assert again.key == loc.key + "-2"


def test_keys_never_clash(user):
    grid = user.grid
    cant = grid.get_place("punjab/lahore/lahore-cantt/lahore-cant")
    loc = user.add(cant.name, polygon=BLOCK, author="ali")
    assert loc.key == cant.key + "-2"
    assert user.add(cant.name, cells=["11-0140-0471"], author="ali").key == cant.key + "-3"
    assert user.add("!!!", cells=["11-0140-0470"], author="ali").key.endswith("/locality")


def test_export_import_and_reopen(user, lahore_db, tmp_path):
    a = user.add("Gali 7", polygon=BLOCK, author="ali", nearest=2)
    b = user.add("Mohalla", cells=["11-0140-0475"], author="ali", references=[a.key])
    user.add("Old", cells=["11-0140-0470"], author="ali")
    user.remove(user.find("old")[0], author="ali")
    data = json.loads(json.dumps(user.export_data()))
    assert [x["key"] for x in data["localities"]] == [a.key, b.key]
    with Grid.open(lahore_db) as grid:
        other = grid.attach_user_places(tmp_path / "copy.sqlite")
        added = other.import_data(data, author="admin")
        assert [x.key for x in added] == [a.key, b.key]
        assert [r.place_key for r in added[1].references] == [a.key]
        assert other.sub_cells(added[0]) == user.sub_cells(a)
        assert other.import_data(data, author="admin") == []            # already there
    with Grid.open(lahore_db, user_places=user.path) as grid:          # reopened from the file
        assert grid.locate(*POINT).place.locality.key == a.key
        assert grid.place("11-0140-0475-12").locality.key == b.key
        assert grid.user_places.refresh(author="admin") == []           # nothing changed


def test_threads_share_one_file(user):
    grid = user.grid
    codes = [f"11-0140-{c:04d}" for c in range(460, 470)]
    errors = []

    def work(code):
        try:
            loc = user.add(f"Lane {code}", cells=[code], author="t")
            assert grid.place(code, "cell").locality.key == loc.key
        except Exception as exc:          # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(c,)) for c in codes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(user.localities()) == len(codes)


def test_needs_the_places_layer(tmp_path):
    from pakgrid.builder import build_database
    path = tmp_path / "plain.sqlite"
    build_database(path, quadrants=["11"], rows=(140, 141), places=False)
    with Grid.open(path) as grid:
        with pytest.raises(NotInDatabaseError):
            grid.attach_user_places(tmp_path / "u.sqlite")


def test_cli(lahore_db, tmp_path, capsys):
    f = tmp_path / "u.sqlite"
    poly = tmp_path / "poly.json"
    poly.write_text(json.dumps([list(p) for p in BLOCK]))
    base = ["user-places", "--db", str(lahore_db), "--file", str(f)]
    assert main(base + ["add", "Gali 7", "--polygon", str(poly), "--author", "ali", "--kind", "street",
                        "--nearest", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["key"] == "punjab/lahore/lahore-cantt/gali-7" and len(out["references"]) == 2
    assert main(base + ["add", "Mohalla", "--cells", "11-0140-0475", "--author", "ali", "--ref", out["key"]]) == 0
    assert json.loads(capsys.readouterr().out)["references"][0]["place_key"] == out["key"]
    assert main(base + ["update", out["key"], "--author", "sara", "--local-name", "گلی ۷"]) == 0
    capsys.readouterr()
    assert main(base + ["list"]) == 0
    assert [x["name"] for x in json.loads(capsys.readouterr().out)] == ["Gali 7", "Mohalla"]
    assert main(base + ["show", out["key"]]) == 0
    assert json.loads(capsys.readouterr().out)["sub_cell_codes"]
    assert main(["show", "11-0140-0475-00", "--db", str(lahore_db), "--user-places", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["place"]["locality"]["name"] == "Mohalla"
    assert main(base + ["export", str(tmp_path / "backup.json")]) == 0
    capsys.readouterr()
    assert main(base + ["remove", "punjab/lahore/lahore-cantt/mohalla", "--author", "sara"]) == 0
    capsys.readouterr()
    assert main(base + ["history"]) == 0
    assert [h["action"] for h in json.loads(capsys.readouterr().out)] == ["add", "add", "update", "remove"]
    assert main(base + ["add", "Bad", "--author", "ali"]) == 1          # neither polygon nor cells
    assert "exactly one of polygon or cells" in capsys.readouterr().err


def test_centre_is_the_covered_sub_cell_nearest_the_average(user):
    grid = user.grid
    loc = user.add("Gali 7", polygon=BLOCK, author="ali")
    layout = GridLayout.from_spec()
    cents = []
    for s in user.sub_cells(loc):
        q, i, j = layout.item_from_id(s, "sub")
        cents.append(layout.quadrants[q].item_centroid_e7(i, j, "sub"))
    my, mx = sum(c[0] for c in cents) / len(cents), sum(c[1] for c in cents) / len(cents)
    best = min(cents, key=lambda c: ((c[0] - my) ** 2 + (0.85 * (c[1] - mx)) ** 2, c))
    assert (to_e7(loc.lat), to_e7(loc.lon)) == best
    assert grid.sub_cell(grid.locate(loc.lat, loc.lon).id).centroid == (best[0] / SCALE, best[1] / SCALE)
