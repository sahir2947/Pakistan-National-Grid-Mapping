"""The places layer: province, district and tehsil tags on cells and sub-cells."""

import json
import shutil
import sqlite3
import struct

import pytest

from pakgrid import Grid, ids
from pakgrid.builder import build_database
from pakgrid.cli import main
from pakgrid.errors import InvalidIdError, NotInDatabaseError
from pakgrid.places import PolygonLayer, _Unit, build_places, clean_name
from pakgrid.verify import verify_database

# Places checksum of the Lahore test piece (quadrant 11, rows 135-145). It must be the same on
# every machine; CI runs these tests on Linux, macOS and Windows.
LAHORE_PLACES_SHA256 = "ef35c85c05cbbba5c376d5bb23c2b8d211a5f47552a150e0e8b903241d72bc4c"


@pytest.mark.parametrize("raw, name", [
    ("AHMADPUR EAST", "Ahmadpur East"), ("D.I.KHAN", "D.I. Khan"), ("DERA_MURAD_JAMALI", "Dera Murad Jamali"),
    ("ASHWAT SUB-", "Ashwat Sub-Tehsil"), ("SAMARBAGH(BARWA)", "Samarbagh (Barwa)"), ("NAGAR-II", "Nagar-II"),
    ("18-HAZARI", "18-Hazari"), ("FR D.I.KHAN", "FR D.I. Khan"), ("NUSHKI_", "Nushki"),
    ("Peshawar II", "Peshawar II"), ("Dera Ghazi Khan", "Dera Ghazi Khan"),
])
def test_clean_name(raw, name):
    assert clean_name(raw) == name


def _square(x0, y0, x1, y1):
    s = 10_000_000
    return [(x0 * s, y0 * s), (x1 * s, y0 * s), (x1 * s, y1 * s), (x0 * s, y1 * s), (x0 * s, y0 * s)]


def test_polygon_layer_holes_overlaps_and_nearest():
    outer = _Unit(0, "Outer", "OUTER", "a", [_square(0, 0, 10, 10), _square(4, 4, 6, 6)], 0)   # with a hole
    inner = _Unit(1, "Inner", "INNER", "b", [_square(4, 4, 6, 6)], 0)                           # fills the hole
    overlap = _Unit(2, "Small", "SMALL", "c", [_square(8, 1, 9, 2)], 0)                         # lies over Outer
    outer.area, inner.area, overlap.area = 100, 4, 1
    layer = PolygonLayer([outer, inner, overlap], [11, 22, 33])
    xs = [-1, 1, 5, 7, 8.5, 11]
    assert list(layer.scan(5.0, xs)) == [0, 11, 22, 11, 11, 0]
    assert list(layer.scan(1.5, xs)) == [0, 11, 11, 11, 33, 0]        # the smaller unit wins an overlap
    assert layer.at(5, 5) == 22 and layer.at(20, 20) == 0
    assert layer.nearest(5.0, 10.3) == 11                            # just east of Outer
    assert layer.nearest(50.0, 50.0) == 0                            # nothing within 0.5 degree


def test_real_places(grid):
    tags = grid.locate(31.5204, 74.3587).place
    assert tags.label == "Lahore Cantt, Lahore, Punjab"
    assert (tags.tehsil.key, tags.district.key, tags.province.key) == \
        ("punjab/lahore/lahore-cantt", "punjab/lahore", "punjab")
    assert tags.tehsil.level == "tehsil" and tags.district.level == "district" and tags.province.level == "province"
    assert not tags.district_uncertain
    assert grid.place("11-0140-0471").label == tags.label
    assert grid.place(105265888).label == tags.label and grid.place(3289559, "cell").label == tags.label


def test_outside_pakistan_has_no_place(grid):
    loc = grid.locate(31.50, 74.80)          # India, east of Wagah
    assert not loc.in_pakistan and loc.place is None
    assert loc.to_dict()["place"] is None


def test_to_dict(grid):
    d = grid.locate(31.5204, 74.3587).to_dict()["place"]
    assert d["label"] == "Lahore Cantt, Lahore, Punjab"
    assert d["tehsil"] == {"id": d["tehsil"]["id"], "key": "punjab/lahore/lahore-cantt", "name": "Lahore Cantt"}
    json.dumps(d)


def test_split_cells_keep_each_sub_cells_tehsil(grid):
    split = grid.store.split_cells()
    assert split
    for cid, main_tehsil, blob in split[:50]:
        subs = struct.unpack("<25H", blob)
        assert len({s for s in subs if s}) >= 1
        for k, t in enumerate(subs):
            tags = grid.place(ids.sub_cell_id(cid, k))
            assert (tags.tehsil.id if tags else 0) == t
        assert grid.place(cid, "cell").tehsil.id == main_tehsil


def test_hierarchy_is_consistent(grid):
    by_id = {p.id: p for p in grid.places()}
    assert len(by_id) == 687
    assert [len(grid.places(lv)) for lv in ("province", "district", "tehsil")] == [7, 126, 554]
    for p in by_id.values():
        if p.level == "province":
            assert p.parent_id is None
        else:
            parent = by_id[p.parent_id]
            assert parent.level == ("province" if p.level == "district" else "district")
            assert p.key.startswith(parent.key + "/")
    assert len({p.key for p in by_id.values()}) == len(by_id)
    taxila = grid.get_place("punjab/rawalpindi/taxila")
    assert taxila.uncertain and taxila.source_name == "TAXILA"


def test_cells_in_place_both_ways(grid):
    lahore = grid.get_place("punjab/lahore")
    cells = grid.cells_in_place(lahore)
    subs = grid.cells_in_place("punjab/lahore", level="sub")
    assert cells and subs and cells == sorted(cells)
    for cid in cells[::25]:
        assert grid.place(cid, "cell").district.id == lahore.id
    for sid in subs[::500]:
        assert grid.place(sid).district.id == lahore.id
    cantt = grid.cells_in_place("punjab/lahore/lahore-cantt", level="sub")
    assert set(cantt) <= set(subs)
    assert grid.cells_in_place(grid.get_place("punjab")) and grid.find_places("lahore cantt")[0].key.endswith("cantt")
    with pytest.raises(InvalidIdError):
        grid.get_place("nowhere/at-all")


def test_checksum_is_stable_and_verified(lahore_db, tmp_path):
    report = verify_database(lahore_db, full=True)
    assert report.ok and report.places_sha256 == LAHORE_PLACES_SHA256
    bad = tmp_path / "bad.sqlite"
    shutil.copy(lahore_db, bad)
    db = sqlite3.connect(bad)
    db.execute("UPDATE cell_places SET tehsil = tehsil + 1 WHERE id = 3289559")
    db.commit()
    db.close()
    report = verify_database(bad)
    assert not report.ok
    assert [n for n, ok, _, req in report.checks if req and not ok] == ["places checksum"]


def test_build_without_places_then_add_them(tmp_path, capsys):
    path = tmp_path / "plain.sqlite"
    build_database(path, quadrants=["11"], rows=(140, 141), places=False)
    with Grid.open(path) as g:
        assert not g.has_places and g.locate(31.5204, 74.3587).place is None and g.info()["places"] is None
        with pytest.raises(NotInDatabaseError):
            g.place(105265888)
    assert verify_database(path).ok
    assert main(["build-places", "--db", str(path)]) == 0
    assert "places layer" in capsys.readouterr().out
    with Grid.open(path) as g:
        assert g.has_places and g.locate(31.5204, 74.3587).place.label == "Lahore Cantt, Lahore, Punjab"
    assert build_places(path)["places_count"] == 687            # rebuilding replaces the layer
    assert verify_database(path).ok


def test_cli(lahore_db, capsys):
    assert main(["places", "lahore", "--level", "tehsil", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {p["key"] for p in out} == {"punjab/lahore/lahore-cantt", "punjab/lahore/lahore-city"}
    assert main(["places", "punjab/lahore/lahore-cantt", "--cells", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["place"]["name"] == "Lahore Cantt" and "11-0140-0471" in out["cells"]
    assert main(["show", "11-0140-0471-00", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["place"]["label"] == "Lahore Cantt, Lahore, Punjab"
    assert main(["places", "lahore", "--cells", "--db", str(lahore_db)]) == 0      # the district: an exact name
    assert json.loads(capsys.readouterr().out)["place"]["key"] == "punjab/lahore"
    assert main(["places", "lahore c", "--cells", "--db", str(lahore_db)]) == 1    # Lahore Cantt or Lahore City?


def test_places_table_does_not_depend_on_the_part_built(lahore_db, south_edge_db):
    with Grid.open(lahore_db) as a, Grid.open(south_edge_db) as b:
        assert a.store.places_rows() == b.store.places_rows()
