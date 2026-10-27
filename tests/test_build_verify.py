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
    assert report.cells == 8_462 and report.sub_cells == 8_462 * 25


def test_build_refuses_to_overwrite(tmp_path):
    path = tmp_path / "g.sqlite"
    build_database(path, quadrants=[0], rows=(0, 1))
    with pytest.raises(FileExistsError):
        build_database(path, quadrants=[0], rows=(0, 1))
    build_database(path, quadrants=[0], rows=(0, 2), overwrite=True)


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
    failed = {name for name, passed, _ in report.checks if not passed}
    assert failed == {"content checksum", "every record regenerated from the rules"}


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
    assert out["sub_cell"]["code"] == "11-0140-0518-01" and out["confident"] is True

    assert main(["show", "11-0140-0518", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["centroid"] == [31.5241703, 74.3614434]

    assert main(["info", "--db", str(lahore_db)]) == 0
    assert json.loads(capsys.readouterr().out)["total_cells"] == 2_353_170

    assert main(["verify", "--db", str(lahore_db)]) == 0
    assert "FAIL" not in capsys.readouterr().out

    out_db = tmp_path / "cli.sqlite"
    assert main(["build", "-o", str(out_db), "--quadrant", "00", "--rows", "0:2"]) == 0
    assert "content sha256" in capsys.readouterr().out

    assert main(["locate", "10", "10", "--db", str(lahore_db)]) == 1
    assert "outside the grid" in capsys.readouterr().err
