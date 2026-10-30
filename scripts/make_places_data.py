"""Make pakgrid/data/places-v2.json.gz, the source data of places version 2, from the downloads.

    python scripts/make_places_data.py pak_admin3.geojson populated_places.geojson

Inputs (both checked against the SHA-256 recorded below, so the output is reproducible):

- pak_admin3.geojson: tehsils, from the UN OCHA Common Operational Dataset of administrative
  boundaries for Pakistan (HDX dataset "cod-ab-pak", file pak_admin_boundaries.geojson.zip).
  Each tehsil names its district and province, with their P-codes.
- populated_places.geojson: OpenStreetMap populated places of Pakistan, exported by the
  Humanitarian OpenStreetMap Team (HDX dataset "hotosm_pak_populated_places", file
  hotosm_pak_populated_places_osm_geojson.zip).

The output keeps what pakgrid uses: tehsil outlines with their names and parents, and named
localities (points and areas). Coordinates are rounded to 1e-7 degree, the grid's own precision
(pakgrid rounds to it on loading anyway), and stored as delta-encoded integers.
"""

import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ADMIN3_SHA256 = "5535d348a720a9b0e5a0b79ee570408eff515419527d51cb5e16cabfd8699961"
PLACES_SHA256 = "1b770058066d0b7c71c41d17baae8ceb99d771fac08333636265c48f55eee5bb"
OUT = Path(__file__).resolve().parent.parent / "pakgrid" / "data" / "places-v2.json.gz"
SCALE = 10_000_000

# OpenStreetMap place kinds used as localities (landuse=residential areas count as "residential")
KINDS = {"city", "town", "village", "hamlet", "isolated_dwelling", "suburb", "quarter", "neighbourhood",
         "city_block", "locality", "residential"}
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _ring(coords):
    flat, px, py = [], 0, 0
    pts = [(round(x * SCALE), round(y * SCALE)) for x, y, *_ in coords]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    for x, y in pts:
        flat += (x - px, y - py)
        px, py = x, y
    return flat


def _rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [_ring(r) for poly in polys for r in poly if len(r) >= 4]


def tehsils(path):
    out = []
    for f in json.load(open(path, encoding="utf-8"))["features"]:
        p = f["properties"]
        out.append([p["adm3_pcode"], p["adm3_name"], p["adm2_pcode"], p["adm2_name"], p["adm1_pcode"],
                    p["adm1_name"], _rings(f["geometry"])])
    out.sort()
    return out


def localities(path):
    out = []
    for f in json.load(open(path, encoding="utf-8"))["features"]:
        p, geom = f["properties"], f.get("geometry")
        kind = p.get("place") or ("residential" if p.get("landuse") == "residential" else None)
        if kind not in KINDS or not p.get("name") or not geom:
            continue
        name = (p.get("name_latin") or p["name"]).strip()
        local = p.get("name_ur") or (p["name"] if _ARABIC.search(p["name"]) else None)
        local = local.strip() if local and local.strip() != name else None
        if geom["type"] == "Point":
            x, y = geom["coordinates"][:2]
            out.append([p["id"], kind, name, local, "point", [round(x * SCALE), round(y * SCALE)]])
        elif geom["type"] in ("Polygon", "MultiPolygon"):
            out.append([p["id"], kind, name, local, "area", _rings(geom)])
    out.sort(key=lambda r: r[0])
    return out


def main(admin3, places):
    for path, want in ((admin3, ADMIN3_SHA256), (places, PLACES_SHA256)):
        got = _sha256(path)
        if got != want:
            sys.exit(f"{path}: SHA-256 {got} is not the recorded {want}; "
                     "a newer download needs a new places version")
    data = {
        "places_version": 2,
        "sources": {
            "tehsils": {"dataset": "UN OCHA COD-AB Pakistan (HDX cod-ab-pak), pak_admin3.geojson",
                        "valid_on": "2022-09-09", "license": "CC BY-IGO 3.0", "sha256": ADMIN3_SHA256},
            "localities": {"dataset": "OpenStreetMap populated places of Pakistan, HOT export "
                                      "(HDX hotosm_pak_populated_places), populated_places.geojson",
                           "snapshot": "2026-10-12", "license": "ODbL-1.0", "sha256": PLACES_SHA256},
        },
        "tehsils": tehsils(admin3),       # [pcode, name, district pcode, district, province pcode, province, rings]
        "localities": localities(places),  # [osm id, kind, name, local-script name, "point"|"area", coords]
    }
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    OUT.write_bytes(gzip.compress(text, compresslevel=9, mtime=0))
    print(f"wrote {OUT}: {len(data['tehsils'])} tehsils, {len(data['localities'])} localities, "
          f"{OUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
