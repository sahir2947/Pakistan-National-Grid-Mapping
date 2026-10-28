"""The conformance files for ports: up to date, and passed by the Python and JavaScript implementations."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from pakgrid import NavigationSession, direction, format_distance, geodesy
from pakgrid.cli import main
from pakgrid.conformance import navigation_json
from pakgrid.navigate import _COMPASS_8, _COMPASS_16, turn_words

ROOT = Path(__file__).resolve().parent.parent
SUITE_PATH = ROOT / "conformance" / "navigation.json"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def suite():
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _same(want, got, field, suite, where):
    """Compare like a port's runner: numbers within the field's tolerance, everything else exactly."""
    tol = suite["field_tolerance"].get(field)
    if isinstance(want, float) and tol and not isinstance(want, bool):
        assert isinstance(got, (int, float)) and abs(got - want) <= suite["tolerance"][tol], (where, field, want, got)
    else:
        assert want == got, (where, field, want, got)


def _compare(expected, actual, suite, where):
    for field, want in expected.items():
        _same(want, actual[field], field, suite, where)


def _num(v):
    return math.nan if v == "NaN" else v


def test_committed_file_is_up_to_date(suite):
    """Regenerate and compare. Numbers may differ in the last digit on another platform, so
    they are compared within tolerance; words, statuses and the structure must be identical."""
    fresh = json.loads(navigation_json())
    assert fresh.keys() == suite.keys()

    def walk(a, b, path):
        if isinstance(a, dict):
            assert isinstance(b, dict) and a.keys() == b.keys(), path
            for k in a:
                walk(a[k], b[k], f"{path}.{k}")
        elif isinstance(a, list):
            assert isinstance(b, list) and len(a) == len(b), path
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i}]")
        elif isinstance(a, float) and isinstance(b, float):
            assert abs(a - b) <= 1e-9 * max(1.0, abs(a)), (path, a, b)
        else:
            assert a == b, (path, a, b)

    walk(fresh, suite, "suite")
    if fresh != suite:      # only numbers differ, within tolerance
        pytest.skip("this platform's last digits differ slightly from the committed file (allowed)")
    assert SUITE_PATH.read_text(encoding="utf-8") == navigation_json(), \
        "conformance/navigation.json is stale: run `pakgrid export-conformance`"


def test_python_passes_the_file(suite):
    for i, c in enumerate(suite["compass"]):
        b = c["bearing_deg"]
        assert (_COMPASS_8[round(b / 45) % 8], _COMPASS_16[round(b / 22.5) % 16]) == \
            (c["compass"], c["compass_short"]), i
    for c in suite["turn_words"]:
        assert turn_words(c["turn_deg"]) == c["turn"]
    for c in suite["format_distance"]:
        assert format_distance(c["metres"], c["units"]) == c["text"]
    for c in suite["metres_per_degree"]:
        m_lat, m_lon = geodesy.metres_per_degree(c["lat"])
        _compare({"lat_m": c["lat_m"], "lon_m": c["lon_m"]}, {"lat_m": m_lat, "lon_m": m_lon}, suite, c)
    for i, c in enumerate(suite["direction"]):
        _compare(c["expected"], direction(*c["from"], *c["to"], heading=c["heading"]).__dict__, suite, i)
    n = 0
    for s in suite["sessions"]:
        nav = NavigationSession(*s["target"], **s["options"])
        for k, step in enumerate(s["steps"]):
            a = {key: _num(v) for key, v in step["args"].items()}
            call = step["call"]
            if call == "add_fix":
                state = nav.add_fix(a["lat"], a["lon"], accuracy=a["accuracy"], speed=a["speed"],
                                    heading=a["heading"], time=a["time"])
            elif call == "add_compass":
                state = nav.add_compass(a["heading"], time=a["time"])
            elif call == "set_position":
                state = nav.set_position(a["lat"], a["lon"], heading=a["heading"])
            elif call == "set_target":
                state = nav.set_target(a["lat"], a["lon"])
            else:
                state = nav.reset()
            _compare(step["expect"], state.to_dict(), suite, (s["name"], k))
            n += 1
    assert n > 1000


def test_file_covers_every_behaviour(suite):
    states = [st["expect"] for s in suite["sessions"] for st in s["steps"]]
    assert {s["heading_source"] for s in states} == {None, "gps", "compass", "manual", "movement"}
    assert {s["status"] for s in states} == {"waiting", "navigating", "almost", "arrived"}
    assert {s["arrow_mode"] for s in states} == {None, "relative", "north-up"}
    assert {st["call"] for s in suite["sessions"] for st in s["steps"]} == \
        {"add_fix", "add_compass", "set_position", "set_target", "reset"}
    from pakgrid.conformance import _ARGS
    for s in suite["sessions"]:
        for st in s["steps"]:
            assert tuple(st["args"]) == _ARGS[st["call"]], st
    assert any(st["args"].get("heading") == "NaN" for s in suite["sessions"] for st in s["steps"])
    words = {c["turn"] for c in suite["turn_words"]}
    assert words == {"ahead", "turn around"} | {f"{w} {side}" for w in ("slightly", "bear", "turn")
                                                  for side in ("left", "right")}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_reference_runner_passes():
    out = subprocess.run([NODE, str(ROOT / "conformance" / "runners" / "javascript.js")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    assert out.stdout.strip().startswith("OK:")


def test_cli_writes_the_file(tmp_path, capsys):
    assert main(["export-conformance", "-o", str(tmp_path / "out")]) == 0
    assert "navigation.json" in capsys.readouterr().out
    assert json.loads((tmp_path / "out" / "navigation.json").read_text(encoding="utf-8"))["suite"] == "navigation"
