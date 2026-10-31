"""Custom blocks (BlockSet) and area descriptions (Grid.describe_zone)."""

import json

import pytest

from pakgrid import BlockError, BlockSet, Grid, ids, navigate
from pakgrid.cli import main

POLY = [(31.49, 74.30), (31.49, 74.33), (31.51, 74.33), (31.51, 74.30)]       # clear of the cells below
PIN = (31.5204, 74.3587)


def test_build_by_clicking_and_drawing(grid):
    blocks = grid.block_set("lahore")
    a = blocks.add("LHR-01", name="Gulberg North", cells=["11-0140-0471", "11-0140-0472"], properties={"fee": 50})
    assert a.sub_cell_count == 50 and a.name == "Gulberg North" and a.properties == {"fee": 50}
    assert blocks.toggle("LHR-01", "11-0140-0473-12") is True            # a click adds a sub-cell
    assert a.sub_cell_count == 51
    assert blocks.toggle("LHR-01", "11-0140-0473-12") is False           # a second click takes it away
    assert blocks.toggle("LHR-01", "11-0140-0472") is False              # a whole cell, too
    assert a.sub_cell_count == 25
    b = blocks.add("LHR-02", name="Model Block", polygon=POLY)
    assert b.zone == grid.zone(polygon=POLY)                             # a polygon's sub-cells
    c = blocks.add("LHR-03", cells=["11-0141-0480"], circle=(31.535, 74.40, 600))   # a mix
    assert c.zone == grid.zone(cells=["11-0141-0480"]) | grid.zone(circle=(31.535, 74.40, 600))
    assert [x.code for x in blocks.blocks()] == ["LHR-01", "LHR-02", "LHR-03"] and len(blocks) == 3


def test_blocks_do_not_overlap(grid):
    blocks = grid.block_set()
    blocks.add("A", cells=["11-0140-0471"])
    with pytest.raises(BlockError, match="already in block 'A' \\(1 sub-cell\\)"):
        blocks.add("B", cells=["11-0140-0471-03", "11-0140-0470"])
    assert "B" not in blocks and blocks.get("A").sub_cell_count == 25    # nothing changed
    blocks.add("B", cells=["11-0140-0471-03", "11-0140-0470"], take=True)
    assert blocks.get("A").sub_cell_count == 24 and blocks.get("B").sub_cell_count == 26
    assert blocks.block_of("11-0140-0471-03") == "B" and blocks.block_of("11-0140-0471-04") == "A"
    assert blocks.block_of("11-0140-0471") == {"A": 24, "B": 1}
    with pytest.raises(BlockError, match="already"):
        blocks.add_cells("A", ["11-0140-0471-03"])
    blocks.add_cells("A", ["11-0140-0471-03"], take=True)
    assert blocks.block_of("11-0140-0471-03") == "A"
    with pytest.raises(BlockError, match="already a block"):
        blocks.add("A", cells=["11-0140-0480"])
    with pytest.raises(BlockError, match="empty"):
        blocks.remove_cells("A", ["11-0140-0471"])


def test_pins_and_lookups(grid):
    blocks = grid.block_set()
    blocks.add("A", polygon=POLY)
    blocks.add("B", cells=["11-0140-0471"])
    assert blocks.block_at(*PIN) == "B" and blocks.block_at(31.50, 74.315) == "A"
    assert blocks.block_at(31.60, 74.60) is None and blocks.block_at(45.0, 74.0) is None
    for code in ("A", "B"):
        for s in blocks.get(code).zone.sub_cells()[::37]:
            assert blocks.block_of(s) == code


def test_update_remove(grid):
    blocks = grid.block_set()
    blocks.add("A", cells=["11-0140-0471"])
    blocks.update("A", name="Shop row", properties={"team": 3}, new_code="LHR-09")
    assert "A" not in blocks and blocks.get("LHR-09").name == "Shop row"
    assert blocks.block_at(*PIN) == "LHR-09" and blocks.get("LHR-09").properties == {"team": 3}
    blocks.remove("LHR-09")
    assert blocks.block_at(*PIN) is None and len(blocks) == 0
    for bad in ("", "  ", None, 5):
        with pytest.raises(BlockError):
            blocks.add(bad, cells=["11-0140-0471"])
    with pytest.raises(BlockError, match="give cells"):
        blocks.add("X")
    with pytest.raises(BlockError, match="no block"):
        blocks.get("nope")


def test_store_and_load(grid):
    blocks = grid.block_set("teams")
    blocks.add("A", name="North", cells=["11-0140-0471", "11-0140-0472-07"], properties={"price": "B"})
    blocks.add("B", polygon=POLY)
    data = json.loads(json.dumps(blocks.to_dict()))
    back = BlockSet.from_dict(data, grid)
    assert back.name == "teams" and [b.code for b in back.blocks()] == ["A", "B"]
    assert back.get("A").zone == blocks.get("A").zone and back.get("A").properties == {"price": "B"}
    assert back.block_at(*PIN) == "A"
    clash = json.loads(json.dumps(data))
    clash["blocks"][1]["zone"] = clash["blocks"][0]["zone"]
    with pytest.raises(BlockError, match="already"):
        BlockSet.from_dict(clash, grid)
    with pytest.raises(BlockError):
        BlockSet.from_dict({**data, "blockset_version": 9}, grid)
    geo = blocks.to_geojson()
    assert [f["id"] for f in geo["features"]] == ["A", "B"]
    assert geo["features"][0]["properties"]["name"] == "North" and geo["features"][0]["properties"]["price"] == "B"


def test_describe_a_block_in_a_city(grid):
    blocks = grid.block_set()
    blocks.add("LHR-01", name="Gulberg North", cells=["11-0140-0471", "11-0140-0472", "11-0140-0473-12"])
    d = blocks.describe("LHR-01", references=["punjab/lahore/lahore-cantt/lahore"])
    assert d["code"] == "LHR-01" and d["name"] == "Gulberg North"
    size = d["size"]
    assert (size["sub_cells"], size["cells"], size["whole_cells"]) == (51, 3, 2)
    assert size["area_km2"] == pytest.approx(blocks.get("LHR-01").zone.area_km2, abs=1e-6)
    assert d["whole_cells"] == ["11-0140-0471", "11-0140-0472"] and d["partial_cells"] == {"11-0140-0473": [12]}
    assert d["cell_codes"] == ["11-0140-0471", "11-0140-0472", "11-0140-0473-12"]
    assert grid.locate(*size["centre"]).id in blocks.get("LHR-01").zone        # the centre is in the block
    p = d["places"]
    assert [x["name"] for x in p["province"]] == ["Punjab"] and [x["name"] for x in p["district"]] == ["Lahore"]
    assert [x["key"] for x in p["tehsil"]] == ["punjab/lahore/lahore-cantt"] and p["tehsil"][0]["sub_cells"] == 51
    assert sum(x["sub_cells"] for x in p["locality"]) <= 51 and p["locality"][0]["name"] == "Lahore Cant"
    assert d["city"]["relation"] == "in" and d["city"]["name"] == "Lahore Cant"
    assert d["nearest_locality"] is None and d["outside_pakistan_sub_cells"] == 0
    assert d["label"] == "Lahore Cant, Lahore Cantt, Lahore, Punjab"
    ref = d["references"][0]
    lahore = grid.get_place("punjab/lahore/lahore-cantt/lahore")
    way = navigate.direction(lahore.lat, lahore.lon, *size["centre"])
    assert ref["distance_m"] == round(way.distance_m, 1) and ref["direction"] == way.compass_short
    assert ref["text"] == f"{navigate.format_distance(way.distance_m)} {way.compass_short} of Lahore"
    json.dumps(d)


def test_describe_spanning_several_places(grid):
    d = grid.describe_zone(grid.zone(circle=(31.525, 74.30, 3000)))    # crosses the Lahore Cantt / City line
    tehsils = d["places"]["tehsil"]
    assert len(tehsils) >= 2 and sum(t["sub_cells"] for t in tehsils) == d["size"]["sub_cells"] - d["outside_pakistan_sub_cells"]
    counts = [t["sub_cells"] for t in tehsils]
    assert counts == sorted(counts, reverse=True)
    for t in tehsils[:2]:
        z = grid.zone(circle=(31.525, 74.30, 3000)) & grid.zone(place=t["key"])
        assert z.sub_cell_count == t["sub_cells"]


def test_describe_far_from_any_locality(lahore_db, tmp_path):
    """Open country with no locality in it: directions from the nearest locality and city instead."""
    with Grid.open(lahore_db) as grid:
        far = grid.zone(cells=["11-0136-0200"])              # open country west of Lahore: no locality
        d = grid.describe_zone(far)
        assert d["places"]["locality"] == [] and d["places"]["tehsil"]
        near = d["nearest_locality"]
        assert near and near["text"].endswith(f"of {near['name']}") and d["label"].startswith("near ")
        assert d["city"]["relation"] == "near" and d["city"]["kind"] in ("city", "town")


def test_describe_outside_pakistan(grid):
    d = grid.describe_zone(grid.zone(cells=["11-0140-0520"]))          # in India, east of the border
    assert d["outside_pakistan_sub_cells"] == 25 and d["places"]["tehsil"] == [] and d["label"] is None
    assert d["nearest_locality"]["text"].endswith(d["nearest_locality"]["name"])   # still: where it is from a place


def test_user_localities_count(lahore_db, tmp_path):
    with Grid.open(lahore_db) as grid:
        user = grid.attach_user_places(tmp_path / "u.sqlite")
        loc = user.add("Gali 7", cells=["11-0140-0471-00", "11-0140-0471-01"], author="ali")
        blocks = grid.block_set()
        blocks.add("A", cells=["11-0140-0471"])
        top = {x["key"]: x for x in blocks.describe("A")["places"]["locality"]}
        assert top[loc.key]["sub_cells"] == 2 and top[loc.key]["user"] is True


def test_cli(lahore_db, tmp_path, capsys):
    f = tmp_path / "blocks.json"
    base = ["blocks", "--db", str(lahore_db), "--file", str(f)]
    assert main(base + ["add", "LHR-01", "--name", "Gulberg North", "--cells", "11-0140-0471", "11-0140-0472",
                        "--set-name", "lahore"]) == 0
    assert json.loads(capsys.readouterr().out)["sub_cells"] == 50
    poly = tmp_path / "poly.json"
    poly.write_text(json.dumps([list(p) for p in POLY]))
    assert main(base + ["add", "LHR-02", "--polygon", str(poly)]) == 0
    capsys.readouterr()
    assert main(base + ["add", "LHR-03", "--cells", "11-0140-0471"]) == 1               # overlap refused
    assert "already in block 'LHR-01'" in capsys.readouterr().err
    assert main(base + ["add-cells", "LHR-01", "--cells", "11-0140-0473-12"]) == 0
    assert main(base + ["remove-cells", "LHR-01", "--cells", "11-0140-0472"]) == 0
    assert main(base + ["update", "LHR-01", "--new-code", "LHR-1"]) == 0
    capsys.readouterr()
    assert main(base + ["list"]) == 0
    assert [(b["code"], b["sub_cells"]) for b in json.loads(capsys.readouterr().out)][0] == ("LHR-1", 26)
    assert main(base + ["describe", "LHR-1", "--ref", "punjab/lahore/lahore-cantt/lahore"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["label"].endswith("Lahore Cantt, Lahore, Punjab") and d["references"][0]["name"] == "Lahore"
    assert main(base + ["at", "31.5204", "74.3587"]) == 0
    assert json.loads(capsys.readouterr().out) == {"block": "LHR-1"}
    assert main(base + ["geojson"]) == 0
    assert len(json.loads(capsys.readouterr().out)["features"]) == 2
    assert main(base + ["remove", "LHR-02"]) == 0
    assert json.loads(f.read_text())["name"] == "lahore" and len(json.loads(f.read_text())["blocks"]) == 1
    assert ids.cell_code(json.loads(f.read_text())["blocks"][0]["zone"]["full"][0]) == "11-0140-0471"
