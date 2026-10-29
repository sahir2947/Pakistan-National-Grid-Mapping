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
VIEWER_JS = VIEWER.as_posix()   # for paths written into JavaScript source (no Windows backslashes)
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
    const P = require('{VIEWER_JS}/pakgrid.js');
    const g = new P.Grid(require('{VIEWER_JS}/layout.js'));
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
    const P = require('{VIEWER_JS}/pakgrid.js');
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
    const P = require('{VIEWER_JS}/pakgrid.js');
    const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    console.log(JSON.stringify(cases.map((c) => P.direction(c[0], c[1], c[2], c[3], c[4]))));
    """
    got = json.loads(subprocess.run([NODE, "-e", js], input=json.dumps(cases),
                                    capture_output=True, text=True, check=True).stdout)
    for case, j in zip(cases, got):
        d = direction(*case)
        assert j["distance_m"] == pytest.approx(d.distance_m, rel=1e-12, abs=1e-9)
        assert j["bearing_deg"] == pytest.approx(d.bearing_deg, abs=1e-9)
        assert (j["compass"], j["compass_short"]) == (d.compass, d.compass_short)
        if d.turn_deg is None:
            assert j["turn_deg"] is None and j["turn"] is None
        else:
            assert j["turn_deg"] == pytest.approx(d.turn_deg, abs=1e-9) and j["turn"] == d.turn


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_addresses_match_python(layout):
    from pakgrid import address_from_point, parse_address
    from pakgrid.errors import InvalidIdError
    rng = random.Random(33)
    points = [(rng.uniform(23.5, 37.0), rng.uniform(60.9, 77.9)) for _ in range(2000)]
    for _ in range(300):   # just inside sub-cell edges, where rounding is nudged towards the centre
        lat, lon = rng.uniform(24, 37), rng.uniform(61, 77)
        q, row, col, sr, sc = layout.locate_e7(round(lat * 1e7), round(lon * 1e7))
        s, w, n, e = layout.quadrants[q].item_bounds_e7(row * 5 + sr, col * 5 + sc, "sub")
        points += [(s / 1e7 + 3e-7, rng.uniform(w, e) / 1e7), (rng.uniform(s, n) / 1e7, e / 1e7 - 3e-7)]
    texts = ["11-0140-0471-00 W23 S18", " 11-0140-0471-00  s18 w23 ", "11-0140-0471-00", "11-0140-0471-00 W23 E5",
             "11-0140-0471-00 E5 N151", "11-0140-0471-25 E1 N1", "11-0800-0000-00", "nonsense"]
    js = f"""
    const P = require('{VIEWER_JS}/pakgrid.js');
    const g = new P.Grid(require('{VIEWER_JS}/layout.js'));
    const {{ pts, texts }} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    console.log(JSON.stringify({{
      made: pts.map(([a, b]) => g.addressFromPoint(a, b).text),
      parsed: texts.map((t) => {{ const a = g.parseAddress(t); return a && a.text; }}),
      resolved: texts.map((t) => g.resolveAddress(t)),
    }}));
    """
    got = json.loads(subprocess.run([NODE, "-e", js], input=json.dumps({"pts": points, "texts": texts}),
                                    capture_output=True, text=True, check=True).stdout)
    assert got["made"] == [address_from_point(lat, lon).text for lat, lon in points]
    for text, js_text, js_point in zip(texts, got["parsed"], got["resolved"]):
        try:
            a = parse_address(text)
        except InvalidIdError:
            assert js_text is None and js_point is None, text
            continue
        assert js_text == a.text
        assert js_point == pytest.approx(list(a.point()), abs=1e-12)


def _nav_script(seed):
    """A random ride: GPS fixes, compass readings, manual positions, target changes and resets."""
    rng = random.Random(seed)
    lat, lon, t = rng.uniform(24, 36), rng.uniform(62, 76), 0.0
    events = [("target", lat + rng.uniform(-0.01, 0.01), lon + rng.uniform(-0.01, 0.01))]
    for _ in range(150):
        t += rng.choice([0.2, 1, 1, 1, 2.5, 4, 12])
        kind = rng.choices(["fix", "compass", "position", "target", "reset"], [60, 20, 15, 3, 2])[0]
        lat += rng.uniform(-3e-4, 3e-4)
        lon += rng.uniform(-3e-4, 3e-4)
        if kind == "fix":
            events.append(("fix", lat, lon, rng.choice([None, 0, 3, 8, 25, 60]), rng.choice([None, 0.5, 2, 7]),
                           rng.choice([None, rng.uniform(-30, 400)]), t))
        elif kind == "compass":
            events.append(("compass", rng.choice([rng.uniform(0, 360), 360, -15]), t))
        elif kind == "position":
            events.append(("position", lat, lon, rng.choice([None, None, rng.uniform(0, 360)])))
        elif kind == "target":
            events.append(("target", lat + rng.uniform(-0.002, 0.002), lon + rng.uniform(-0.002, 0.002)))
        else:
            events.append(("reset",))
    return events


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_navigation_engine_matches_python():
    from pakgrid import NavigationSession, format_distance
    scripts = [_nav_script(seed) for seed in range(40)]
    dists = [0, 2.5, 7.5, 97.5, 99.99, 100, 102.5, 995, 999.9, 1000, 1050, 1250, 12345.6, 91.44, 1609.344, 160934.4]
    js = f"""
    const P = require('{VIEWER_JS}/pakgrid.js');
    const {{ scripts, dists }} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
    const states = scripts.map((ev) => {{
      let nav = null;
      return ev.map((e) => {{
        switch (e[0]) {{
          case 'target': return nav ? nav.setTarget(e[1], e[2]) : (nav = new P.NavigationSession(e[1], e[2])).state();
          case 'fix': return nav.addFix(e[1], e[2], {{ accuracy: e[3], speed: e[4], heading: e[5], time: e[6] }});
          case 'compass': return nav.addCompass(e[1], e[2]);
          case 'position': return nav.setPosition(e[1], e[2], e[3]);
          default: return nav.reset();
        }}
      }});
    }});
    const text = dists.map((d) => [P.formatDistance(d, 'm'), P.formatDistance(d, 'ft')]);
    console.log(JSON.stringify({{ states, text }}));
    """
    got = json.loads(subprocess.run([NODE, "-e", js], input=json.dumps({"scripts": scripts, "dists": dists}),
                                    capture_output=True, text=True, check=True).stdout)
    assert got["text"] == [[format_distance(d, "m"), format_distance(d, "ft")] for d in dists]
    sources = set()
    for events, js_states in zip(scripts, got["states"]):
        nav = None
        for e, j in zip(events, js_states):
            if e[0] == "target":
                s = nav.set_target(*e[1:]) if nav else (nav := NavigationSession(*e[1:])).state
            elif e[0] == "fix":
                s = nav.add_fix(e[1], e[2], accuracy=e[3], speed=e[4], heading=e[5], time=e[6])
            elif e[0] == "compass":
                s = nav.add_compass(*e[1:])
            elif e[0] == "position":
                s = nav.set_position(*e[1:])
            else:
                s = nav.reset()
            py = s.to_dict()
            sources.add(py["heading_source"])
            assert j.keys() == py.keys()
            for k, v in py.items():
                if isinstance(v, float):
                    assert j[k] == pytest.approx(v, rel=1e-12, abs=1e-9), (e, k)
                else:
                    assert j[k] == v, (e, k)
    assert sources == {None, "gps", "compass", "manual", "movement"}


def _places_js_data():
    text = (VIEWER / "places.js").read_text(encoding="utf-8")
    start = text.index("var data = ") + len("var data = ")
    return json.loads(text[start:text.index(";\n", start)])


def test_committed_places_data_is_the_official_layer():
    from pakgrid.checksums import OFFICIAL_PLACES
    from pakgrid.places import PLACES_VERSION, _places_table
    data = _places_js_data()
    assert data["places_version"] == PLACES_VERSION
    assert data["places_sha256"] == OFFICIAL_PLACES[PLACES_VERSION], \
        "viewer/places.js is stale: run `pakgrid export-layout`"
    assert [p[:6] for p in data["places"]] == [list(r[:6]) for r in _places_table()]


_JS_PLACES = """
const P = require(process.argv[1] + '/pakgrid.js');
const places = new P.Places(require(process.argv[1] + '/places.js'));
const items = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(items.map(([q, row, col, sub]) => {
  const p = places.of(q, row, col, sub);
  return p && [p.tehsil.id, p.district && p.district.id, p.province && p.province.id, p.label, p.uncertain];
})));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("db", ["lahore_db", "south_edge_db", "north_edge_db"])
def test_javascript_places_match_python(db, request):
    from pakgrid import Grid
    path = request.getfixturevalue(db)
    rng = random.Random(41)
    with Grid.open(path) as grid:
        cells = [cid for cid, _, _ in grid.store.iter_cell_places()]
        split = [cid for cid, _, _ in grid.store.split_cells()]
        outside = [c[0] for c in grid.store.iter_cells() if c[7] == 0]
        chosen = rng.sample(cells, min(300, len(cells))) + split[:100] + rng.sample(outside, min(50, len(outside)))
        items, expected = [], []
        for cid in chosen:
            q, row, col = ids.split_cell_id(cid)
            for sub in [None] + list(range(25)):
                items.append([q, row, col, sub])
                tags = grid.place(cid, "cell") if sub is None else grid.place(ids.sub_cell_id(cid, sub))
                expected.append(tags and [tags.tehsil.id, tags.district and tags.district.id,
                                          tags.province and tags.province.id, tags.label, tags.district_uncertain])
    got = json.loads(subprocess.run([NODE, "-e", _JS_PLACES, str(VIEWER)], input=json.dumps(items),
                                    capture_output=True, text=True, check=True).stdout)
    assert len(got) == len(expected) and got == expected


_JS_SHAPES = """
const P = require(process.argv[1] + '/pakgrid.js');
const grid = new P.Grid(require(process.argv[1] + '/layout.js'));
const places = new P.Places(require(process.argv[1] + '/places.js'));
const req = JSON.parse(require('fs').readFileSync(0, 'utf8'));
function inside(rings, lat, lon) {              // even-odd rule, as the viewer fills them
  let odd = false;
  for (const ring of rings) {
    for (let a = 0, b = ring.length - 1; a < ring.length; b = a++) {
      const [ya, xa] = ring[a], [yb, xb] = ring[b];
      if ((ya > lat) !== (yb > lat) && lon < xa + (lat - ya) * (xb - xa) / (yb - ya)) odd = !odd;
    }
  }
  return odd;
}
const out = {};
for (const key of req.keys) {
  const place = places.list.find((p) => p.key === key), shape = places.shape(grid, place);
  out[key] = { subCells: shape.subCells, hits: req.points.map(([lat, lon]) => inside(shape.rings, lat, lon)),
               rectHits: req.points.map(([lat, lon]) => shape.rects.some((r) => r[0] <= lat && lat < r[2] && r[1] <= lon && lon < r[3])) };
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_place_shapes_are_exact(lahore_db):
    """The drawn shape of a place covers exactly the sub-cells tagged with it."""
    from pakgrid import Grid
    keys = ["punjab/lahore", "punjab/lahore/lahore-cantt", "punjab/lahore/lahore-city", "punjab/sheikhpura",
            "balochistan/zhob", "punjab"]
    rng = random.Random(51)
    with Grid.open(lahore_db) as grid:
        tagged = [cid for cid, _, _ in grid.store.iter_cell_places()]
        split = [cid for cid, _, _ in grid.store.split_cells()]
        sids = [ids.sub_cell_id(c, k) for c in rng.sample(tagged, 400) + split[:80] for k in range(25)]
        points = [grid.sub_cell(s).centroid for s in sids]
        expected = {}
        for key in keys:
            p = grid.get_place(key)
            expected[key] = [bool((t := grid.place(s)) and grid._within(t.tehsil, p)) for s in sids]
    got = json.loads(subprocess.run([NODE, "-e", _JS_SHAPES, str(VIEWER)], input=json.dumps({"keys": keys, "points": points}),
                                    capture_output=True, text=True, check=True).stdout)
    for key in keys:
        assert any(expected[key]) and not all(expected[key]), key       # the sample has points in and out
        assert got[key]["hits"] == expected[key], key                    # the drawn rings
        assert got[key]["rectHits"] == expected[key], key                # the rectangles
    assert got["punjab"]["subCells"] == 5_138_873                        # as Grid.cells_in_place counts
