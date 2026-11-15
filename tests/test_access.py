"""Street access: what can drive through a street (pakgrid.access)."""

import json
import sqlite3

import pytest

from pakgrid import VEHICLES, Grid, UserPlacesError
from pakgrid.access import access_text, up_to, vehicles_list
from pakgrid.cli import main
from pakgrid.errors import InvalidIdError
from pakgrid.roads import nearest_on_line

STREET = [(31.5201, 74.3571), (31.5203, 74.3590), (31.5210, 74.3602)]      # a user road
GALI = [(31.5150, 74.3500), (31.5160, 74.3502)]                            # a street with no user road
ON_STREET = (31.5202, 74.3580)
ON_GALI = (31.5155, 74.3501)


@pytest.fixture
def grid(lahore_db):
    with Grid.open(lahore_db) as g:
        yield g


@pytest.fixture
def user(grid, tmp_path):
    return grid.attach_user_places(tmp_path / "user.sqlite")


def test_vehicle_rules():
    assert VEHICLES == ("foot", "bicycle", "motorbike", "rickshaw", "car", "pickup", "truck")
    assert up_to("rickshaw") == ("foot", "bicycle", "motorbike", "rickshaw") and up_to("foot") == ("foot",)
    assert vehicles_list(["car", "foot", "car"]) == ("foot", "car")       # repeats dropped
    assert access_text(["foot"]) == "On foot only"
    assert access_text(up_to("rickshaw")) == "Up to rickshaw size"
    assert access_text(VEHICLES) == "All vehicles"
    assert access_text(["motorbike", "foot"]) == "Only on foot, motorbike"
    for bad in ([], "car", ["plane"], None):
        with pytest.raises(ValueError):
            vehicles_list(bad)
    with pytest.raises(ValueError):
        up_to("van")


def test_on_a_user_road(user, grid):
    road = user.add_road("Street 7", author="ali", line=STREET)
    assert user.access_at(*ON_STREET) is None                         # no answer yet
    a = user.set_access(largest="rickshaw", author="sara", road=road.key)
    assert (a.source, a.key, a.name, a.largest, a.set_by) == ("road", road.key, "Street 7", "rickshaw", "sara")
    assert user.get_road(road.key).details["vehicles"] == ["foot", "bicycle", "motorbike", "rickshaw"]
    assert [h["action"] for h in user.road_history(road.key)] == ["add", "update"]   # in the road's history
    at = user.access_at(*ON_STREET)
    assert at.allows("motorbike") and not at.allows("car") and at.text == "Up to rickshaw size"
    assert at.distance_m == pytest.approx(nearest_on_line(road.line, *ON_STREET)[0])
    with pytest.raises(ValueError):
        at.allows("plane")
    # other details are kept; lookups carry the answer
    user.update_road(road.key, author="ali", details={"surface": "paved", "vehicles": ["foot"]})
    user.set_access(["car", "foot"], author="ali", road=road.key)
    assert user.get_road(road.key).details == {"surface": "paved", "vehicles": ["foot", "car"]}
    tags = grid.locate(*ON_STREET).place
    assert tags.access.vehicles == ("foot", "car") and not tags.access.nested
    d = tags.to_dict()
    assert d["access"]["text"] == "Only on foot, car" and d["street"]["vehicles"] == ["foot", "car"]
    json.dumps(d)
    assert user.remove_access(road.key, author="ali") is None         # take the answer back
    assert user.get_road(road.key).details == {"surface": "paved"} and user.access_at(*ON_STREET) is None
    assert grid.locate(*ON_STREET).place.access is None


def test_on_another_street(user, grid):
    a = user.set_access(largest="motorbike", author="ali", line=GALI, source_id="way/123", name="Gali 3")
    assert (a.source, a.source_id, a.name, a.text) == ("note", "way/123", "Gali 3", "Up to motorbike size")
    note = user.get_access_note("way/123")
    assert note.key == a.key == "note/1" and note.length_m == pytest.approx(112, abs=2)
    assert user.access_at(*ON_GALI).key == "note/1"
    assert user.access_at(31.5155, 74.3530) is None                   # ~270 m away
    assert user.access_at(31.5155, 74.3504, max_m=100).key == "note/1"
    assert grid.locate(*ON_GALI).place.access.largest == "motorbike"
    assert grid.locate(*ON_GALI).place.street is None                 # an answer, but no user road
    # the same street again: updated in place, by its source_id (no line needed)
    b = user.set_access(["foot"], author="sara", source_id="way/123")
    assert b.key == "note/1" and b.set_by == "sara" and b.name == "Gali 3" and len(user.access_notes()) == 1
    assert [h["action"] for h in user.access_history("way/123")] == ["add", "update"]
    assert user.access_history()[0]["note"]["vehicles"] == ["foot", "bicycle", "motorbike"]
    # a note without a source_id is a new note each time
    user.set_access(largest="car", author="ali", line=[(31.5300, 74.3400), (31.5302, 74.3410)])
    assert [n.key for n in user.access_notes()] == ["note/1", "note/2"]
    assert user.access_geojson()["features"][1]["properties"]["vehicles"][-1] == "car"
    # removing
    gone = user.remove_access("way/123", author="ali")
    assert gone.removed and user.access_at(*ON_GALI) is None and len(user.access_notes()) == 1
    assert user.get_access_note("note/1").removed and user.access_history("note/1")[-1]["action"] == "remove"
    with pytest.raises(InvalidIdError):
        user.get_access_note("way/123")                               # no live note for it now
    again = user.set_access(largest="truck", author="ali", line=GALI, source_id="way/123")
    assert again.key == "note/3" and again.text == "All vehicles"


def test_bad_input(user):
    road = user.add_road("Street 7", author="ali", line=STREET)
    cases = [dict(author="ali", line=GALI),                                     # no answer
             dict(vehicles=["car"], largest="car", author="ali", line=GALI),    # two answers
             dict(vehicles=[], author="ali", line=GALI),
             dict(vehicles=["boat"], author="ali", line=GALI),
             dict(largest="car", author="ali"),                                 # no street
             dict(largest="car", author="ali", source_id="way/9"),              # unknown street, no line
             dict(largest="car", author="", line=GALI),
             dict(largest="car", author="ali", road=road.key, name="x"),        # a road brings its own name
             dict(largest="car", author="ali", line=[(31.5, 74.3), (31.5, 74.4)]),     # 9.5 km
             dict(largest="car", author="ali", line=[(31.5, 74.3)]),
             dict(largest="car", author="ali", line=[(31.53, 74.70), (31.531, 74.70)])]   # India
    for kw in cases:
        with pytest.raises(UserPlacesError):
            user.set_access(**kw)
    with pytest.raises(InvalidIdError):
        user.set_access(largest="car", author="ali", road="nope")
    assert user.access_notes() == [] and user.access_at(float("nan"), 74.3) is None
    assert user.access_at(95.0, 74.3) is None


def test_nearest_answer_wins(user):
    user.add_road("Street 7", author="ali", line=STREET)
    user.set_access(largest="car", author="ali", road=user.roads()[0].key)
    near = [(ON_STREET[0] + 0.00005, ON_STREET[1] - 0.0002), (ON_STREET[0] + 0.00005, ON_STREET[1] + 0.0002)]
    user.set_access(largest="foot", author="ali", line=near, name="Lane")
    assert user.access_at(*ON_STREET).source == "road"                # the road is nearer
    assert user.access_at(ON_STREET[0] + 0.00006, ON_STREET[1]).source == "note"
    # the same line twice: the newer answer wins
    user.set_access(largest="pickup", author="sara", line=near, name="Lane")
    with sqlite3.connect(user.path) as db:                            # make the order certain
        db.execute("UPDATE access_notes SET updated_at = '2000-01-01T00:00:00+00:00' WHERE id = 1")
    assert user.access_at(ON_STREET[0] + 0.00006, ON_STREET[1]).largest == "pickup"


def test_old_files_get_the_tables(grid, tmp_path):
    path = tmp_path / "old.sqlite"
    grid.attach_user_places(path)
    with sqlite3.connect(path) as db:
        db.executescript("DROP TABLE access_notes; DROP TABLE access_cells; DROP TABLE access_history;")
    user = grid.attach_user_places(path)
    assert user.access_notes() == [] and user.access_at(*ON_GALI) is None


def test_road_vehicle_details_use_the_new_list(user):
    r = user.add_road("Gali 9", author="ali", line=STREET, details={"vehicles": ["truck", "rickshaw", "foot"]})
    assert r.details["vehicles"] == ["foot", "rickshaw", "truck"]
    with pytest.raises(UserPlacesError, match="vehicles are foot, bicycle"):
        user.add_road("Gali 10", author="ali", line=GALI, details={"vehicles": ["van"]})


def test_export_and_import(user, grid, tmp_path):
    user.set_access(largest="motorbike", author="ali", line=GALI, source_id="way/123", name="Gali 3")
    user.set_access(largest="car", author="ali", line=[(31.5300, 74.3400), (31.5302, 74.3410)])
    user.set_access(largest="foot", author="ali", line=[(31.5310, 74.3400), (31.5312, 74.3410)])
    user.remove_access("note/3", author="ali")
    data = json.loads(json.dumps(user.export_data()))
    assert [n["removed"] for n in data["access_notes"]] == [False, False]
    other = grid.attach_user_places(tmp_path / "other.sqlite")
    added = other.import_data(data, author="import")
    assert [a.text for a in added] == ["Up to motorbike size", "Up to car size"]
    assert other.get_access_note("way/123").name == "Gali 3" and other.access_at(*ON_GALI).set_by == "import"
    assert other.import_data(data, author="import") == []             # nothing twice


def test_cli(lahore_db, tmp_path, capsys):
    f = tmp_path / "u.sqlite"
    base = ["user-places", "--db", str(lahore_db), "--file", str(f)]
    line = tmp_path / "gali.json"
    line.write_text(json.dumps([list(p) for p in GALI]))
    assert main(base + ["access", "set", "--author", "ali", "--largest", "rickshaw", "--line", str(line),
                        "--source-id", "way/123", "--name", "Gali 3"]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "Up to rickshaw size"
    assert main(base + ["access", "at", *map(str, ON_GALI)]) == 0
    assert json.loads(capsys.readouterr().out)["access"]["key"] == "note/1"
    assert main(base + ["access", "set", "--author", "ali", "--vehicles", "foot", "motorbike",
                        "--source-id", "way/123"]) == 0
    assert json.loads(capsys.readouterr().out)["vehicles"] == ["foot", "motorbike"]
    assert main(base + ["access", "list"]) == 0
    assert [n["source_id"] for n in json.loads(capsys.readouterr().out)] == ["way/123"]
    assert main(base + ["access", "history", "--street", "way/123"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(base + ["access", "set", "--largest", "car", "--line", str(line)]) == 1     # no author
    assert "--author" in capsys.readouterr().err
    assert main(base + ["access", "remove", "--author", "ali", "--street", "way/123"]) == 0
    assert json.loads(capsys.readouterr().out)["removed_by"] == "ali"
    assert main(base + ["access", "at", *map(str, ON_GALI)]) == 0
    assert json.loads(capsys.readouterr().out) == {"access": None}
