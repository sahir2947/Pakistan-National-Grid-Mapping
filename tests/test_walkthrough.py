"""Walkthroughs: photos of the walk from the road to a door (pakgrid.walkthrough)."""

import json
import math
import random
import shutil
import subprocess

import pytest

from pakgrid import Grid, Walkthrough, WalkthroughError, geodesy, ids, navigate
from pakgrid import walkthrough as W
from pakgrid.cli import main

REF = (31.5201, 74.3571)
M_LAT, M_LON = geodesy.metres_per_degree(REF[0])
LEGS = [(0, 40), (30, 0), (0, 20)]         # metres east, north: 40 m north, 30 m east, 20 m north


def at(x, y):
    return REF[0] + y / M_LAT, REF[1] + x / M_LON


def walk(legs=LEGS, noise=0.0, seed=1, speed=1.2, accuracy=6.0):
    """A walk along the legs at a steady speed, one reading a second, with GPS noise (metres)."""
    pts = [(0.0, 0.0)]
    for dx, dy in legs:
        pts.append((pts[-1][0] + dx, pts[-1][1] + dy))
    total = sum(math.hypot(*leg) for leg in legs)
    rng = random.Random(seed)
    track, t, s = [], 0.0, 0.0
    while s <= total + 1e-9:
        rem, (x, y) = s, pts[-1]
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            seg = math.hypot(bx - ax, by - ay)
            if rem <= seg + 1e-9:
                x, y = ax + (bx - ax) * rem / seg, ay + (by - ay) * rem / seg
                break
            rem -= seg
        track.append((t, *at(x + rng.gauss(0, noise) if noise else x, y + rng.gauss(0, noise) if noise else y),
                      accuracy))
        t, s = t + 1.0, s + speed
    return track, at(*pts[-1])


@pytest.fixture
def grid(lahore_db):
    with Grid.open(lahore_db) as g:
        yield g


def test_build_a_clean_walk(grid):
    track, pin = walk()
    wt = grid.walkthrough(track, pin=pin, start=REF, author="ali", properties={"shop": 42})
    d = wt.to_dict()
    assert 85 <= d["length_m"] <= 90                     # smoothing rounds the corners a little
    assert [t["turn"] for t in d["turns"]] == ["turn right", "turn left"]
    assert [round(t["along_m"]) for t in d["turns"]] == [39, 68]
    assert d["start"] == [round(REF[0], 7), round(REF[1], 7)] and d["pin"] == [round(pin[0], 7), round(pin[1], 7)]
    assert d["video_start_s"] == 0 and d["video_end_s"] == track[-1][0] and d["duration_s"] == track[-1][0]
    assert d["track"] == {"readings": len(track), "kept": len(track),
                          "dropped": {"invalid": 0, "inaccurate": 0, "repeated_time": 0, "jump": 0}}
    frames = d["frames"]
    gaps = [b["along_m"] - a["along_m"] for a, b in zip(frames, frames[1:])]
    assert all(W.MIN_FRAME_GAP_M - 1e-9 <= g < W.FRAME_EVERY_M + W.MIN_FRAME_GAP_M for g in gaps)
    assert frames[0]["along_m"] == 0 and frames[-1]["along_m"] == d["length_m"] and frames[-1]["to_go_m"] == 0
    assert [f["t_s"] for f in frames] == sorted(f["t_s"] for f in frames) and frames[-1]["t_s"] == d["video_end_s"]
    assert [f["index"] for f in frames] == list(range(len(frames))) and frames[3]["image"] == "frame-003.jpg"
    turns = [f for f in frames if f["turn"]]
    assert [f["along_m"] for f in turns] == [t["along_m"] for t in d["turns"]]
    assert 80 <= turns[0]["heading_deg"] <= 100 and (frames[0]["heading_deg"] < 10 or frames[0]["heading_deg"] > 350)
    assert frames[0]["text"] == "In 40 m, turn right" and turns[0]["text"] == "Turn right now"
    assert frames[-1]["text"] == "You have arrived"
    assert d["pin_sub_cell"] == ids.sub_cell_code(grid.locate(*pin).sub_cell.id) == frames[-1]["sub_cell"]
    assert d["start_sub_cell"] in d["sub_cells"] and d["pin_sub_cell"] in d["sub_cells"]
    assert d["author"] == "ali" and d["properties"] == {"shop": 42}
    # the photos are where the walker was at that moment of the video
    for f in frames:
        t = f["t_s"]
        k = min(int(t), len(track) - 2)
        lat = track[k][1] + (track[k + 1][1] - track[k][1]) * (t - k)
        lon = track[k][2] + (track[k + 1][2] - track[k][2]) * (t - k)
        assert navigate.direction(lat, lon, f["lat"], f["lon"]).distance_m < 4.5
    json.dumps(d)


def test_noise_and_bad_readings(grid):
    track, pin = walk(noise=2.5, seed=4)
    bad = [(5.5, float("nan"), 74.3, 5), (6.5, REF[0], REF[1], -1), (7.5, 95.0, 74.3, 5),
           (8.5, *at(0, 10), 80.0), (9.5, *at(300, 10), 5.0),            # inaccurate; a 300 m jump
           (10.0, *at(0, 12), 5.0), ("x", *REF, 5)]                     # a time already seen; no time
    wt = grid.walkthrough(track + bad, pin=pin, start=REF, author="ali")
    assert wt.data["track"]["dropped"] == {"invalid": 4, "inaccurate": 1, "repeated_time": 1, "jump": 1}
    assert [t["turn"] for t in wt.turns] == ["turn right", "turn left"]
    dict_track = [{"t": t, "lat": lat, "lon": lon, "accuracy": a} for t, lat, lon, a in track]
    assert W.build(dict_track, pin=pin, start=REF, author="ali").length_m == wt.length_m
    no_acc = [(t, lat, lon) for t, lat, lon, _ in track]
    assert W.build(no_acc, pin=pin, author="ali").data["start"] != wt.data["start"]   # start: the first reading


def test_refused(grid):
    track, pin = walk()
    with pytest.raises(WalkthroughError, match="first reading is .* from the start"):
        W.build(track, pin=pin, start=at(0, -50), author="ali")
    with pytest.raises(WalkthroughError, match="last reading is .* from the pin"):
        W.build(track, pin=at(30, 100), start=REF, author="ali")
    with pytest.raises(WalkthroughError, match="at least 2 good readings"):
        W.build(track[:1] + [(1.0, float("nan"), 1, 1)], pin=pin, author="ali")
    long_track, long_pin = walk(legs=[(0, 300), (300, 0)])
    with pytest.raises(WalkthroughError, match="at most 500 m"):
        W.build(long_track, pin=long_pin, author="ali")
    slow, slow_pin = walk(legs=[(0, 40)], speed=0.1)
    with pytest.raises(WalkthroughError, match="at most 300 s"):
        W.build(slow, pin=slow_pin, author="ali")
    still = [(t, *REF, 5.0) for t in range(10)]
    with pytest.raises(WalkthroughError):
        W.build(still, pin=REF, author="ali")
    for kw in (dict(author=""), dict(author="a", start=(1, 2, 3)), dict(author="a", pin=(float("nan"), 1))):
        with pytest.raises(WalkthroughError):
            W.build(track, **{"pin": pin, **kw})
    with pytest.raises(WalkthroughError, match="reading must be"):
        W.build([(1, 2)], pin=pin, author="a")


def test_the_rider_rule(grid):
    track, pin = walk()
    wt = Walkthrough.from_dict(json.loads(json.dumps(grid.walkthrough(track, pin=pin, start=REF, author="a").to_dict())))
    g = wt.guide()
    assert g.state.gps == "start" and g.state.frame == 0 and g.state.text == "In 40 m, turn right"
    s = g.update(*at(1, 20), accuracy_m=5)
    assert s.gps == "ok" and abs(s.progress_m - 20) < 2 and s.next_turn == "turn right" and s.text == "In 20 m, turn right"
    assert wt.frames[s.frame]["along_m"] == min((f["along_m"] for f in wt.frames), key=lambda a: abs(a - s.progress_m))
    p = s.progress_m
    assert g.update(*at(0, 14), accuracy_m=5).progress_m == p                  # 6 m back: GPS wobble, ignored
    assert g.update(*at(0, 5), accuracy_m=5).progress_m < 6                   # 15 m back: really went back
    assert g.update(*at(0, 30), accuracy_m=50).gps == "weak" and g.state.progress_m < 6
    s = g.update(*at(60, 30), accuracy_m=5)
    assert s.gps == "off_path" and s.text.startswith("Go back to the path: it is ") and s.off_path_m > 30
    assert g.update(float("nan"), 74.0).gps == "ignored" and g.state.off_path_m is None
    s = g.update(*at(0, 36), accuracy_m=float("inf"))                          # a bad accuracy counts as 20 m
    assert s.gps == "ok" and s.text == "Turn right now"
    s = g.update(*at(15, 40), accuracy_m=5)
    assert s.next_turn == "turn left" and s.text.startswith("In ")
    s = g.update(*at(30, 48))
    assert s.next_turn is None and s.text.startswith("The door is ") and not s.arrived
    s = g.update(*pin)
    assert s.arrived and s.text == "You have arrived" and s.frame == len(wt.frames) - 1 and s.to_go_m == 0
    # by hand
    s = g.show(3)
    assert (s.frame, s.gps, s.off_path_m) == (3, "manual", None) and s.progress_m == wt.frames[3]["along_m"]
    assert g.next().frame == 4 and g.previous().frame == 3 and g.show(-5).frame == 0 and g.show(999).frame == len(wt.frames) - 1
    assert g.next().frame == len(wt.frames) - 1
    json.dumps(s.to_dict())


def test_stored_form(grid):
    track, pin = walk()
    wt = grid.walkthrough(track, pin=pin, start=REF, author="a")
    geo = wt.to_geojson()
    assert geo["features"][0]["geometry"]["type"] == "LineString" and len(geo["features"]) == 1 + len(wt.frames)
    assert geo["features"][1]["properties"]["text"] == wt.frames[0]["text"]
    assert wt.frame_times() == [f["t_s"] for f in wt.frames]
    cmd = wt.ffmpeg_commands("walk.mp4", "out", width=640)[2]
    assert cmd[:6] == ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{wt.frames[2]['t_s']:.2f}"]
    assert cmd[-1].endswith("frame-002.jpg") and "scale=640:-2" in cmd
    for bad in ({}, {"walkthrough_version": 2}, {"walkthrough_version": 1}, {"walkthrough_version": 1, "path": [[1, 2, 0, 0]], "frames": []}):
        with pytest.raises(WalkthroughError):
            Walkthrough.from_dict(bad)
    assert W.build(track, pin=pin, start=REF, author="a").data["pin_sub_cell"] is None        # without a grid


def test_gpx():
    gpx = """<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
      <trkpt lat="31.5201" lon="74.3571"><time>2026-11-14T10:00:05Z</time></trkpt>
      <trkpt lat="31.5202" lon="74.3571"><time>2026-11-14T10:00:06Z</time></trkpt>
      <trkpt lat="31.5203" lon="74.3571"></trkpt></trkseg></trk></gpx>"""
    assert W.read_gpx(gpx) == [{"t": 0.0, "lat": 31.5201, "lon": 74.3571}, {"t": 1.0, "lat": 31.5202, "lon": 74.3571}]
    assert W.read_gpx(gpx, "2026-11-14T10:00:00Z")[0]["t"] == 5.0
    assert W.read_gpx(gpx, "2026-11-14T15:00:00+05:00")[1]["t"] == 6.0
    with pytest.raises(WalkthroughError):
        W.read_gpx("<gpx></gpx>")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_photos_from_a_video(grid, tmp_path):
    track, pin = walk()
    video = tmp_path / "walk.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=5:duration={track[-1][0] + 1}",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    wt = grid.walkthrough(track, pin=pin, start=REF, author="a")
    for c in wt.ffmpeg_commands(video, tmp_path, width=160):
        subprocess.run(c, check=True)
    assert all((tmp_path / f["image"]).stat().st_size > 500 for f in wt.frames)


def test_cli(lahore_db, tmp_path, capsys):
    track, pin = walk()
    tf = tmp_path / "track.json"
    tf.write_text(json.dumps([{"t": t, "lat": lat, "lon": lon, "accuracy": a} for t, lat, lon, a in track]))
    out = tmp_path / "wt.json"
    assert main(["walkthrough", "build", "--db", str(lahore_db), "--track", str(tf), "--pin", *map(str, pin),
                 "--start", *map(str, REF), "--author", "ali", "-o", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["turns"] == ["turn right", "turn left"] and summary["frames"] == len(json.loads(out.read_text())["frames"])
    assert main(["walkthrough", "guide", str(out), "%f,%f" % at(1, 20), "%f,%f,50" % at(1, 30)]) == 0
    states = json.loads(capsys.readouterr().out)
    assert [s["gps"] for s in states] == ["ok", "weak"] and states[0]["text"] == "In 20 m, turn right"
    assert main(["walkthrough", "frames", str(out), "--video", "v.mp4", "--frames-dir", "f", "--print"]) == 0
    assert capsys.readouterr().out.count("ffmpeg -y") == summary["frames"]
    assert main(["walkthrough", "build", "--db", str(lahore_db), "--track", str(tf), "--pin", "31.6", "74.4",
                 "--author", "ali"]) == 1
    assert "from the pin" in capsys.readouterr().err
