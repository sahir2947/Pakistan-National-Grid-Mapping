"""The places layer: province, district, tehsil and locality tags on cells and sub-cells."""

import json
import shutil
import sqlite3
import struct

import pytest

from pakgrid import Grid, ids
from pakgrid.builder import build_database
from pakgrid.cli import main
from pakgrid.errors import InvalidIdError, NotInDatabaseError
from pakgrid.places import LOCALITY_REACH_M, PolygonLayer, _Unit, build_places, clean_name, locality_is_near
from pakgrid.verify import verify_database

# Places checksum of the Lahore test piece (quadrant 11, rows 135-145). It must be the same on
# every machine; CI runs these tests on Linux, macOS and Windows.
LAHORE_PLACES_SHA256 = "b9f30d1438f5294aaf9db060b9b57c148f02d8e40cf555da7ea7bce550783618"


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


LABEL = "Lahore Cant, Lahore Cantt, Lahore, Punjab"


def test_real_places(grid):
    tags = grid.locate(31.5204, 74.3587).place
    assert tags.label == LABEL
    assert (tags.locality.key, tags.tehsil.key, tags.district.key, tags.province.key) == \
        ("punjab/lahore/lahore-cantt/lahore-cant", "punjab/lahore/lahore-cantt", "punjab/lahore", "punjab")
    assert tags.tehsil.level == "tehsil" and tags.district.level == "district" and tags.province.level == "province"
    loc = tags.locality
    assert loc.level == "locality" and loc.kind == "town" and loc.local_name == "لاہور کینٹ" and not loc.area
    assert loc.source_id.startswith("node/") and not tags.near and tags.locality_text == "Lahore Cant"
    assert grid.place("11-0140-0471").label == tags.label
    assert grid.place(105265888).label == tags.label and grid.place(3289559, "cell").label == tags.label


def test_near_a_locality(grid):
    # a sub-cell beyond half a village's reach is near it: 'near X (1.2 km SE)'
    for cid, tag, blob in grid.store.iter_cell_places("locality"):
        loc = grid.get_place(tag) if tag else None
        if loc and loc.kind == "village" and blob is None:
            tags = grid.place(cid, "cell")
            if tags.near:
                break
    else:
        pytest.fail("no cell near a village in the Lahore piece")
    assert tags.locality.id == tag and 1000 < tags.locality_distance_m <= 2000 * 1.08
    assert tags.locality_text == f"near {loc.name} ({tags.locality_distance_m / 1000:.1f} km {tags.locality_direction})"
    assert tags.label.startswith(tags.locality_text + ", ")
    assert tags.to_dict()["locality"]["near"] is True


def test_locality_is_near_rule():
    # 2 km reach for a village: in within 1 km, near beyond; a city or town is in all its reach
    deg = 10_000_000 / 111_000                    # 1e-7 degree per metre of latitude
    assert not locality_is_near("village", False, round(999 * deg), 0, 0, 0)
    assert locality_is_near("village", False, round(1001 * deg), 0, 0, 0)
    assert not locality_is_near("city", False, round(9_999 * deg), 0, 0, 0)
    assert not locality_is_near("village", True, round(5_000 * deg), 0, 0, 0)       # areas: always in
    assert LOCALITY_REACH_M["village"] == 2000


def test_outside_pakistan_has_no_place(grid):
    loc = grid.locate(31.50, 74.80)          # India, east of Wagah
    assert not loc.in_pakistan and loc.place is None
    assert loc.to_dict()["place"] is None


def test_to_dict(grid):
    d = grid.locate(31.5204, 74.3587).to_dict()["place"]
    assert d["label"] == LABEL
    assert d["tehsil"] == {"id": d["tehsil"]["id"], "key": "punjab/lahore/lahore-cantt", "name": "Lahore Cantt"}
    assert d["locality"]["name"] == "Lahore Cant" and d["locality"]["kind"] == "town" and d["locality"]["near"] is False
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


def test_split_cells_keep_each_sub_cells_locality(grid):
    split = grid.store.split_cells("locality")
    assert split
    for cid, main_locality, blob in split[:50]:
        subs = struct.unpack("<25I", blob)
        for k, t in enumerate(subs):
            tags = grid.place(ids.sub_cell_id(cid, k))
            assert (tags.locality.id if tags and tags.locality else 0) == t
        cell = grid.place(cid, "cell")
        assert (cell.locality.id if cell and cell.locality else 0) == main_locality


def test_hierarchy_is_consistent(grid):
    by_id = {p.id: p for p in grid.places()}
    assert len(by_id) == 33_256
    assert [len(grid.places(lv)) for lv in ("province", "district", "tehsil", "locality")] == [7, 160, 577, 32_512]
    above = {"district": "province", "tehsil": "district", "locality": "tehsil"}
    for p in by_id.values():
        if p.level == "province":
            assert p.parent_id is None
        else:
            parent = by_id[p.parent_id]
            assert parent.level == above[p.level]
            assert p.key.startswith(parent.key + "/")
    assert len({p.key for p in by_id.values()}) == len(by_id)
    # the districts are the official ones (places version 1 had these wrong)
    assert grid.get_place("punjab/rawalpindi/taxila").source_id == "PK63007"
    assert grid.get_place("sindh/sukkur/new-sukkur").level == "tehsil"
    assert grid.get_place("punjab/muzaffargarh/kot-addu").level == "tehsil"
    assert grid.find_places("لاہور کینٹ")[0].name == "Lahore Cant"           # by local name


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
    dha = grid.get_place("punjab/lahore/lahore-cantt/dha-phase-vi")             # an area locality
    dha_subs = grid.cells_in_place(dha, level="sub")
    assert dha.area and dha_subs and set(dha_subs) <= set(cantt)
    assert all(grid.place(s).locality.id == dha.id for s in dha_subs[::20])
    for cid in grid.cells_in_place(dha):
        assert grid.place(cid, "cell").locality.id == dha.id
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
        assert g.has_places and g.locate(31.5204, 74.3587).place.label == LABEL
    assert build_places(path)["places_count"] == 33_256         # rebuilding replaces the layer
    assert verify_database(path).ok


def test_cli(lahore_db, capsys):
    assert main(["places", "lahore", "--level", "tehsil", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {p["key"] for p in out} == {"punjab/lahore/lahore-cantt", "punjab/lahore/lahore-city"}
    assert main(["places", "punjab/lahore/lahore-cantt", "--cells", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["place"]["name"] == "Lahore Cantt" and "11-0140-0471" in out["cells"]
    assert main(["show", "11-0140-0471-00", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["place"]["label"] == LABEL
    assert main(["places", "lahore", "--cells", "--db", str(lahore_db)]) == 0      # the district: an exact name
    assert json.loads(capsys.readouterr().out)["place"]["key"] == "punjab/lahore"
    assert main(["places", "lahore c", "--cells", "--db", str(lahore_db)]) == 1    # Lahore Cantt or Lahore City?
    capsys.readouterr()
    assert main(["places", "dha phase vi", "--level", "locality", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["level"] == "locality" and out[0]["kind"] == "residential"


def test_places_table_does_not_depend_on_the_part_built(lahore_db, south_edge_db):
    with Grid.open(lahore_db) as a, Grid.open(south_edge_db) as b:
        assert a.store.places_rows() == b.store.places_rows()
