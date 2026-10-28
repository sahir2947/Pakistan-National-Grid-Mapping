"""The map viewer's JavaScript must agree with the Python library (skipped without node)."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from pakgrid import geodesy, ids
from pakgrid.boundary import Boundary, cell_flags, row_flags
from pakgrid.export import boundary_js, layout_js
from pakgrid.navigate import _COMPASS_8, _COMPASS_16
from pakgrid.layout import (EAST_E7, MID_LAT_E7, MID_LON_E7, NORTH_E7, SCALE, SOUTH_E7, WEST_E7, to_e7)

VIEWER = Path(__file__).resolve().parent.parent / "viewer"
NODE = shutil.which("node")


def test_committed_viewer_data_is_up_to_date():
    assert (VIEWER / "layout.js").read_text() == layout_js(), \
        "viewer/layout.js is stale: run `pakgrid export-layout`"
    assert (VIEWER / "boundary.js").read_text(encoding="utf-8") == boundary_js(), \
        "viewer/boundary.js is stale: run `pakgrid export-layout`"


def _python_answer(layout, lat_e7, lon_e7):
    q, row, col, sr, sc = layout.locate_e7(lat_e7, lon_e7)
    ql = layout.quadrants[q]
    sub = ids.sub_index(sr, sc)
    i0, j0 = row * 5 + sr, col * 5 + sc
    s, n = ql.fine_lat(i0), ql.fine_lat(i0 + 1)
    w, e = ql.fine_lon(row, j0), ql.fine_lon(row, j0 + 1)
    cs, cn = ql.fine_lat(row * 5), ql.fine_lat(row * 5 + 5)
    cw, ce = ql.fine_lon(row, col * 5), ql.fine_lon(row, col * 5 + 5)
    return {
        "code": ids.sub_cell_code(ids.sub_cell_id(ids.cell_id(q, row, col), sub)),
        "bounds": [to_e7(s), to_e7(w), to_e7(n), to_e7(e)],
        "centroid": [to_e7(geodesy.band_centroid_lat(s, n)), to_e7((w + e) / 2)],
        "cell_centroid": [to_e7(geodesy.band_centroid_lat(cs, cn)), to_e7((cw + ce) / 2)],
    }


_JS = """
const P = require(process.argv[1] + '/pakgrid.js');
const g = new P.Grid(require(process.argv[1] + '/layout.js'));
const pts = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const e7 = (x) => Math.round(x * 1e7);
const out = pts.map(([lat, lon]) => {
  const r = g.locate(lat / 1e7, lon / 1e7);
  if (!r) return null;
  const sub = r.subRow * 5 + r.subCol;
  const d = g.describe(r.q, r.row, r.col, sub), c = g.describe(r.q, r.row, r.col, null);
  const b = d.bounds;
  return { code: d.code, bounds: [e7(b.south), e7(b.west), e7(b.north), e7(b.east)],
           centroid: d.centroid.map(e7), cell_centroid: c.centroid.map(e7) };
});
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_matches_python(layout):
    rng = random.Random(11)
    pts = [(rng.randint(SOUTH_E7, NORTH_E7), rng.randint(WEST_E7, EAST_E7)) for _ in range(5000)]
    # exact edges: midlines, outer edges, and points on cell and sub-cell lines
    pts += [(MID_LAT_E7, MID_LON_E7), (SOUTH_E7, WEST_E7), (NORTH_E7, EAST_E7), (MID_LAT_E7 - 1, MID_LON_E7 - 1)]
    ql = layout.quadrants[3]
    for i in (700, 702, 1000):
        for j in (2590, 2592, 3000):
            pts.append((ql.lat_e7[i], ql.fine_lon_e7(i // 5, j)))
    got = json.loads(subprocess.run([NODE, "-e", _JS, str(VIEWER)], input=json.dumps(pts),
                                    capture_output=True, text=True, check=True).stdout)
    for (lat, lon), js in zip(pts, got):
        py = _python_answer(layout, lat, lon)
        assert js["code"] == py["code"], (lat / SCALE, lon / SCALE)
        assert js["bounds"] == py["bounds"]
        # centroids may differ by 1e-7 degree if a platform's sin/cos differ in the last digit
        for a, b in zip(js["centroid"] + js["cell_centroid"], py["centroid"] + py["cell_centroid"]):
            assert abs(a - b) <= 1


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_rejects_points_outside():
    js = f"""
    const P = require('{VIEWER}/pakgrid.js');
    const g = new P.Grid(require('{VIEWER}/layout.js'));
    console.log(JSON.stringify([g.locate(23.0, 70), g.locate(30, 79), g.parseCode('11-0800-0000'),
                                g.parseCode('11-0140-0518-01')]));
    """
    out = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out == [None, None, None, {"q": 3, "row": 140, "col": 518, "sub": 1}]


_JS_FLAGS = """
const P = require(process.argv[1] + '/pakgrid.js');
const g = new P.Grid(require(process.argv[1] + '/layout.js'));
const B = new P.Boundary(require(process.argv[1] + '/boundary.js'));
const req = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const rows = req.rows.map(([q, row]) => {
  const f = g.rowFlags(B, q, row);
  return { subs: f.subs.map((a) => Array.from(a)), cells: Array.from(f.cells) };
});
const points = req.points.map(([lat, lon]) => B.contains(lat, lon));
process.stdout.write(JSON.stringify({ rows, points }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_border_flags_match_python(layout):
    boundary = Boundary.default()
    rng = random.Random(12)
    vertices = [v for r in boundary.rings for v in r]
    rows = sorted({layout.locate_e7(y, x)[:2] for x, y in rng.sample(vertices, 30)})
    points = [(rng.uniform(23.8, 37.1), rng.uniform(60.8, 77.2)) for _ in range(2000)]
    points += [(y / SCALE + 1e-6, x / SCALE) for x, y in rng.sample(vertices, 500)]
    got = json.loads(subprocess.run([NODE, "-e", _JS_FLAGS, str(VIEWER)],
                                    input=json.dumps({"rows": rows, "points": points}),
                                    capture_output=True, text=True, check=True).stdout)
    for (q, row), js in zip(rows, got["rows"]):
        ql = layout.quadrants[q]
        lons = [ql.fine_lon(row, j) for j in range(5 * ql.ncols[row] + 1)]
        centres = [(lons[j] + lons[j + 1]) / 2 for j in range(len(lons) - 1)]
        subs = [row_flags(*boundary.band(ql.fine_lat(row * 5 + k), ql.fine_lat(row * 5 + k + 1)), lons, centres)
                for k in range(5)]
        assert js["subs"] == [list(a) for a in subs], (q, row)
        assert js["cells"] == [cell_flags(subs, c)[0] for c in range(ql.ncols[row])], (q, row)
    assert got["points"] == [boundary.contains(lat, lon) for lat, lon in points]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_compass_matches_python():
    bearings = [i * 7.3 % 360 for i in range(200)] + [k * 11.25 for k in range(32)] + [359.9]
    js = f"""
    const P = require('{VIEWER}/pakgrid.js');
    console.log(JSON.stringify({json.dumps(bearings)}.map((b) => P.compass(b))));
    """
    got = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, check=True).stdout)
    for b, c in zip(bearings, got):
        assert (c["name"], c["short"]) == (_COMPASS_8[round(b / 45) % 8], _COMPASS_16[round(b / 22.5) % 16]), b


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_direction_matches_python():
    from pakgrid import direction
    rng = random.Random(21)
    cases = []
    for _ in range(400):
        lat, lon = rng.uniform(24, 37), rng.uniform(61, 77)
        cases.append((lat, lon, lat + rng.uniform(-0.05, 0.05), lon + rng.uniform(-0.05, 0.05),
                      rng.choice([None, rng.uniform(0, 360)])))
    cases += [(31.5215, 74.3570, 31.5204, 74.3587, 90), (31.52085, 74.35855, 31.5204, 74.3587, 180),
              (31.5, 74.3, 31.5, 74.3, 45), (31.5, 74.3, 31.6, 74.3001, 350)]
    js = f"""
    const P = require('{VIEWER}/pakgrid.js');
    const cases = {json.dumps(cases)};
    console.log(JSON.stringify(cases.map((c) => P.direction(c[0], c[1], c[2], c[3], c[4]))));
    """
    got = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, check=True).stdout)
    for case, j in zip(cases, got):
        d = direction(*case)
        assert j["distance_m"] == pytest.approx(d.distance_m, rel=1e-12, abs=1e-9)
        assert j["bearing_deg"] == pytest.approx(d.bearing_deg, abs=1e-9)
        assert (j["compass"], j["compass_short"]) == (d.compass, d.compass_short)
        if d.turn_deg is None:
            assert j["turn_deg"] is None and j["turn"] is None
        else:
            assert j["turn_deg"] == pytest.approx(d.turn_deg, abs=1e-9) and j["turn"] == d.turn
