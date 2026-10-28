import json
import shutil
import sqlite3

import pytest

from pakgrid import Grid
from pakgrid.builder import build_database
from pakgrid.cli import main
from pakgrid.errors import GridMismatchError
from pakgrid.verify import verify_database


def test_build_is_deterministic(tmp_path, lahore_db):
    again = tmp_path / "again.sqlite"
    report = build_database(again, quadrants=["11"], rows=(135, 146))
    assert again.read_bytes() == lahore_db.read_bytes()
    with Grid.open(lahore_db) as grid:
        assert grid.meta["content_sha256"] == report.content_sha256
    assert report.cells == 8_985 and report.sub_cells == 8_985 * 25


def test_build_refuses_to_overwrite(tmp_path):
    path = tmp_path / "g.sqlite"
    build_database(path, quadrants=[0], rows=(0, 1))
    with pytest.raises(FileExistsError):
        build_database(path, quadrants=[0], rows=(0, 1))
    build_database(path, quadrants=[0], rows=(0, 2), overwrite=True)


def test_build_creates_missing_folders(tmp_path):
    path = tmp_path / "new" / "folder" / "g.sqlite"
    build_database(path, quadrants=[0], rows=(0, 1))
    assert path.is_file()


def test_verify_passes(lahore_db):
    report = verify_database(lahore_db, full=True)
    assert report.ok, report.checks


def _tamper(src, dst, sql):
    shutil.copy(src, dst)
    db = sqlite3.connect(dst)
    db.execute(sql)
    db.commit()
    db.close()


def test_verify_detects_a_changed_centroid(tmp_path, lahore_db):
    bad = tmp_path / "bad.sqlite"
    _tamper(lahore_db, bad, "UPDATE cells SET centroid_lat = centroid_lat + 1 WHERE id = 3289606")
    report = verify_database(bad, full=True)
    failed = {name for name, passed, _, required in report.checks if required and not passed}
    assert failed == {"content checksum", "every record and flag regenerated from the rules"}


def test_verify_detects_a_changed_layout(tmp_path, lahore_db):
    bad = tmp_path / "bad.sqlite"
    _tamper(lahore_db, bad, "UPDATE rows SET ncols = ncols + 1 WHERE q = 3 AND row = 140")
    assert not verify_database(bad).ok
    with pytest.raises(GridMismatchError):
        Grid.open(bad)


def test_open_rejects_other_grid_versions(tmp_path, lahore_db):
    bad = tmp_path / "v2.sqlite"
    _tamper(lahore_db, bad, "UPDATE meta SET value = '2' WHERE key = 'grid_version'")
    with pytest.raises(GridMismatchError):
        Grid.open(bad)


def test_open_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Grid.open(tmp_path / "nope.sqlite")


def test_cli(lahore_db, tmp_path, capsys):
    assert main(["locate", "31.5204", "74.3587", "--accuracy", "5", "--db", str(lahore_db)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sub_cell"]["code"] == "11-0140-0471-00" and out["confident"] is True

    assert main(["show", "11-0140-0471", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["centroid"] == [31.5241703, 74.3631579]

    assert main(["info", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["total_cells"] == 2_498_422

    assert main(["verify", "--db", str(lahore_db)]) == 0
    assert "FAIL" not in capsys.readouterr().out

    out_db = tmp_path / "cli.sqlite"
    assert main(["build", "-o", str(out_db), "--quadrant", "00", "--rows", "0:2"]) == 0
    assert "content sha256" in capsys.readouterr().out

    assert main(["locate", "10", "10", "--db", str(lahore_db)]) == 1
    assert "outside the grid" in capsys.readouterr().err


# Content checksums of the test builds. They must come out the same on every
# machine; CI runs these tests on Linux, macOS and Windows.
LAHORE_SHA256 = "8cbb99316c829ddf1d8e7ed2580b53279244bd4318df199a0db8dbaa5707d141"
SOUTH_EDGE_SHA256 = "b9391727e2af931dd38f2ae2fbe8c985b5346b3c5f1809067aa987737267206c"


def test_builds_match_known_checksums(lahore_db, south_edge_db):
    assert verify_database(lahore_db).content_sha256 == LAHORE_SHA256
    assert verify_database(south_edge_db).content_sha256 == SOUTH_EDGE_SHA256


def test_centroid_latitudes_are_far_from_rounding_ties(layout):
    """Centroid latitudes come from sin/cos, whose last digit can differ between platforms.
    None is close enough to a rounding tie at 1e-7 degree for that to change a stored value."""
    import math
    from pakgrid import geodesy
    from pakgrid.layout import SCALE
    worst = 0.5
    for ql in layout.quadrants:
        lats = [ql.fine_lat(i) for i in range(5 * ql.nrows + 1)]
        bands = list(zip(lats, lats[1:])) + list(zip(lats[::5], lats[5::5]))
        for s, n in bands:
            x = geodesy.band_centroid_lat(s, n) * SCALE
            worst = min(worst, abs(x - math.floor(x) - 0.5))
    # 1e-6 of a unit is ~17 last-digit steps of x; platforms differ by a step or two at most
    assert worst > 1e-6, worst


def test_partial_build_has_no_official_checksum(lahore_db):
    report = verify_database(lahore_db)
    assert report.ok and report.official_sha256 is None
    notes = [c for c in report.checks if c[0] == "official checksum"]
    assert notes and notes[0][1:] == (False, "partial build: there is no official checksum to compare", False)


def test_complete_build_must_match_the_official_checksum(tmp_path, lahore_db):
    # Pretend the Lahore piece is a complete Pakistan edition: its content is not the official one.
    step, fake = tmp_path / "step.sqlite", tmp_path / "fake.sqlite"
    _tamper(lahore_db, step, "UPDATE meta SET value = 'full' WHERE key = 'coverage'")
    _tamper(step, fake, "UPDATE meta SET value = 'pakistan' WHERE key = 'edition'")
    report = verify_database(fake)
    failed = [name for name, passed, _, required in report.checks if required and not passed]
    assert failed == ["matches the official pakistan edition (grid v1)"]
    assert not report.ok and report.official_sha256 is not None


def test_verify_expected_checksum(lahore_db, capsys):
    assert verify_database(lahore_db, expected_sha256=LAHORE_SHA256.upper()).ok
    assert not verify_database(lahore_db, expected_sha256="0" * 64).ok
    assert main(["verify", "--db", str(lahore_db), "--expect", LAHORE_SHA256]) == 0
    out = capsys.readouterr().out
    assert f"content sha256: {LAHORE_SHA256}" in out and out.rstrip().endswith("OK")
    assert main(["verify", "--db", str(lahore_db), "--expect", "0" * 64]) == 2
    assert capsys.readouterr().out.rstrip().endswith("FAILED")
