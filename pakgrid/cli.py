"""Command-line tools: pakgrid build | build-places | verify | locate | show | within | distance | zone |
places | user-places | info | export-layout | export-conformance."""

import argparse
import json
import os
import sys

from . import ids, spec
from .errors import PakgridError

DEFAULT_DB = os.environ.get("PAKGRID_DB", f"pakgrid_v{spec.GRID_VERSION}.sqlite")
DEFAULT_USER_PLACES = os.environ.get("PAKGRID_USER_PLACES")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pakgrid", description="Pakistan National Grid tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="generate the grid database")
    p.add_argument("-o", "--output", default=DEFAULT_DB)
    p.add_argument("--quadrant", action="append", metavar="CODE",
                   help="build only this quadrant (00, 01, 10, 11); repeatable")
    p.add_argument("--rows", metavar="START:STOP", help="build only these rows of each quadrant")
    p.add_argument("--pakistan-only", action="store_true",
                   help="leave out cells wholly outside Pakistan (IDs are unchanged)")
    p.add_argument("--overwrite", action="store_true", help="replace an existing file")
    p.add_argument("--no-places", action="store_true",
                   help="leave out the places layer (province, district, tehsil, locality); "
                        "add it later with build-places")

    p = sub.add_parser("build-places", help="add or rebuild the places layer in an existing database")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("verify", help="check a database against the grid rules and its checksum")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--full", action="store_true", help="also regenerate and compare every record")
    p.add_argument("--expect", metavar="SHA256",
                   help="also require this content checksum (complete builds are always compared "
                        "with the official checksum)")

    p = sub.add_parser("locate", help="find the sub-cell containing a GPS point")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("--accuracy", type=float, metavar="METRES")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--user-places", default=DEFAULT_USER_PLACES, metavar="FILE",
                   help="also use the localities in this user places file")

    p = sub.add_parser("show", help="print a cell or sub-cell by code")
    p.add_argument("code")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--user-places", default=DEFAULT_USER_PLACES, metavar="FILE",
                   help="also use the localities in this user places file")

    p = sub.add_parser("within", help="list the cells (or sub-cells) within a radius of a point, nearest first")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("radius", type=float, metavar="METRES")
    p.add_argument("--level", choices=("cell", "sub"), default="cell")
    p.add_argument("--pakistan-only", action="store_true", help="only blocks inside Pakistan or on its border")
    p.add_argument("--touching", action="store_true",
                   help="count a block when any part of it is within the radius (default: its centroid)")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("distance", help="distance and direction between two cells or sub-cells (by code)")
    p.add_argument("a", metavar="FROM")
    p.add_argument("b", metavar="TO")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("zone", help="make a custom zone (delivery area) and check pins against it")
    p.add_argument("--db", default=DEFAULT_DB)
    zact = p.add_subparsers(dest="action", required=True)
    z = zact.add_parser("make", help="make a zone and write it as JSON")
    src = z.add_mutually_exclusive_group(required=True)
    src.add_argument("--polygon", metavar="FILE",
                     help="JSON file: [[lat, lon], ...], a list of rings, or GeoJSON (Polygon, MultiPolygon, Feature)")
    src.add_argument("--circle", nargs=3, type=float, metavar=("LAT", "LON", "METRES"))
    src.add_argument("--cells", nargs="+", metavar="CODE", help="cell and sub-cell codes")
    src.add_argument("--place", metavar="KEY", help="a place key, e.g. punjab/lahore")
    z.add_argument("--touching", action="store_true", help="with --circle: blocks with any part inside")
    z.add_argument("--pakistan-only", action="store_true", help="keep only sub-cells in Pakistan")
    z.add_argument("-o", "--output", metavar="FILE", help="write the zone JSON here (default: print a summary only)")
    z = zact.add_parser("show", help="summarise a zone JSON file")
    z.add_argument("zone", metavar="ZONE.json")
    z.add_argument("--codes", action="store_true", help="also list its cell and sub-cell codes")
    z = zact.add_parser("check", help="is a pin in the zone?")
    z.add_argument("zone", metavar="ZONE.json")
    z.add_argument("lat", type=float)
    z.add_argument("lon", type=float)

    p = sub.add_parser("places", help="list places (province, district, tehsil, locality), or the cells of one")
    p.add_argument("text", nargs="?", help="only places whose name contains this text, or with this key")
    p.add_argument("--level", choices=("province", "district", "tehsil", "locality"))
    p.add_argument("--cells", action="store_true",
                   help="print the codes of the cells in the place (TEXT must match exactly one place)")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("user-places", help="add, change, remove and list localities drawn by users")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--file", default=DEFAULT_USER_PLACES or "user_places.sqlite",
                   help="the user places file (created if missing; default user_places.sqlite)")
    act = p.add_subparsers(dest="action", required=True)
    for name, helptext in (("add", "add a locality"), ("update", "change a locality")):
        a = act.add_parser(name, help=helptext)
        if name == "add":
            a.add_argument("name")
        else:
            a.add_argument("key")
            a.add_argument("--name")
        a.add_argument("--author", required=True, help="who is making the change (kept in the history)")
        a.add_argument("--polygon", metavar="FILE",
                       help="JSON file: [[lat, lon], ...], a list of such rings, or a GeoJSON Polygon")
        a.add_argument("--cells", nargs="+", metavar="CODE", help="cell and sub-cell codes instead of a polygon")
        a.add_argument("--kind", default="neighbourhood" if name == "add" else None,
                       help="street, neighbourhood, market, ... (default neighbourhood)")
        a.add_argument("--local-name", help="the name in Urdu or another local script")
        a.add_argument("--ref", action="append", metavar="KEY", help="a known locality to locate it from; repeatable")
        a.add_argument("--nearest", type=int, metavar="N", help="also locate it from the N nearest localities")
    a = act.add_parser("remove", help="remove a locality (its history is kept)")
    a.add_argument("key")
    a.add_argument("--author", required=True)
    a = act.add_parser("list", help="list the localities")
    a.add_argument("text", nargs="?", help="only those whose name contains this text")
    a.add_argument("--removed", action="store_true", help="include removed localities")
    a = act.add_parser("show", help="print one locality, with its sub-cell codes")
    a.add_argument("key")
    a = act.add_parser("history", help="print the change history (of one locality, or all)")
    a.add_argument("key", nargs="?")
    a = act.add_parser("export", help="write every locality to a JSON file (for backup)")
    a.add_argument("output")
    a = act.add_parser("import", help="add the localities of an exported JSON file")
    a.add_argument("input")
    a.add_argument("--author", required=True)
    a = act.add_parser("refresh", help="recompute tehsils and references after a new places version")
    a.add_argument("--author", required=True)

    p = sub.add_parser("info", help="print grid and database information")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("export-layout",
                       help="write the map viewer's data files (layout.js, boundary.js, places.js)")
    p.add_argument("-o", "--output-dir", default="viewer")

    p = sub.add_parser("export-conformance",
                       help="write the conformance test files for ports to other languages (navigation.json)")
    p.add_argument("-o", "--output-dir", default="conformance")

    args = parser.parse_args(argv)
    try:
        return _COMMANDS[args.command](args)
    except (PakgridError, FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"pakgrid: error: {exc}", file=sys.stderr)
        return 1


def _build(args):
    from .builder import build_database
    rows = tuple(int(x) for x in args.rows.split(":")) if args.rows else None
    report = build_database(args.output, quadrants=args.quadrant, rows=rows, overwrite=args.overwrite,
                            pakistan_only=args.pakistan_only, places=not args.no_places,
                            progress=lambda msg: print(f"building {msg}…", file=sys.stderr))
    print(f"wrote {report.path}: {report.cells:,} cells, {report.sub_cells:,} sub-cells, "
          f"{report.size_bytes / 1e6:.0f} MB in {report.seconds:.1f} s")
    print(f"content sha256: {report.content_sha256}")
    return 0


def _build_places(args):
    from .places import build_places
    meta = build_places(args.db, progress=lambda msg: print(f"building {msg}…", file=sys.stderr))
    print(f"wrote the places layer (version {meta['places_version']}) to {args.db}: {meta['places_count']} places, "
          f"{meta['places_cells']:,} cells tagged, "
          f"{meta['places_locality_sub_cells']:,} sub-cells with a locality")
    print(f"places sha256: {meta['places_sha256']}")
    return 0


def _places(grid, args):
    from .grid import PLACE_LEVELS
    found = grid.find_places(args.text, args.level) if args.text else grid.places(args.level)
    if not args.cells:
        return [p.to_dict() for p in found]
    exact = [p for p in found if args.text and args.text.strip().lower() in (p.key, p.name.lower())]
    if len(exact) > 1:          # 'Lahore': the district before the city of the same name
        top = min(PLACE_LEVELS.index(p.level) for p in exact)
        exact = [p for p in exact if PLACE_LEVELS.index(p.level) == top]
    choice = exact if len(exact) == 1 else found
    if len(choice) != 1:
        from .errors import InvalidIdError
        raise InvalidIdError(f"{len(choice)} places match {args.text!r}; give a key such as "
                             f"{choice[0].key!r}" if choice else f"no place matches {args.text!r}")
    p = choice[0]
    return {"place": p.to_dict(), "cells": [ids.cell_code(c) for c in grid.cells_in_place(p)]}


def _verify(args):
    from .verify import verify_database
    report = verify_database(args.db, full=args.full, expected_sha256=args.expect)
    for name, passed, detail, required in report.checks:
        status = "PASS" if passed else "FAIL" if required else "NOTE"
        print(f"{status}  {name}" + (f"  ({detail})" if detail else ""))
    if report.content_sha256:
        print(f"content sha256: {report.content_sha256}")
    print("OK" if report.ok else "FAILED")
    return 0 if report.ok else 2


def _export_layout(args):
    from .export import write_viewer_files
    for path in write_viewer_files(args.output_dir,
                                   progress=lambda msg: print(f"building {msg}…", file=sys.stderr)):
        print(f"wrote {path}")
    return 0


def _export_conformance(args):
    from .conformance import write_conformance_files
    for path in write_conformance_files(args.output_dir):
        print(f"wrote {path}")
    return 0


def _with_grid(fn):
    def run(args):
        from .grid import Grid
        with Grid.open(args.db, user_places=getattr(args, "user_places", None)) as grid:
            print(json.dumps(fn(grid, args), indent=2, ensure_ascii=False))
        return 0
    return run


def _show(grid, args):
    from .ids import parse_code
    level, _ = parse_code(args.code)
    item = grid.cell(args.code) if level == "cell" else grid.sub_cell(args.code)
    out = item.to_dict()
    if grid.has_places:
        place = grid.place(args.code)
        out["place"] = place.to_dict() if place else None
    return out


def _zone_summary(zone):
    b = zone.bounds()
    return {"sub_cells": zone.sub_cell_count, "cells": zone.cell_count, "full_cells": len(zone.cells(full_only=True)),
            "area_km2": round(zone.area_km2, 3), "bounds": [round(v, 7) for v in b] if b else None}


def _zone(grid, args):
    from .zones import Zone
    if args.action == "make":
        polygon = None
        if args.polygon:
            with open(args.polygon, encoding="utf-8") as f:
                polygon = json.load(f)
        circle = (*args.circle, args.touching) if args.circle else None
        zone = grid.zone(polygon=polygon, circle=circle, cells=args.cells, place=args.place,
                         pakistan_only=args.pakistan_only)
        out = _zone_summary(zone)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(zone.to_dict(), f, separators=(",", ":"))
            out["written"] = args.output
        return out
    with open(args.zone, encoding="utf-8") as f:
        zone = Zone.from_dict(json.load(f), grid)
    if args.action == "show":
        out = _zone_summary(zone)
        if args.codes:
            out["codes"] = zone.codes()
        return out
    loc = grid.layout.locate_e7(*(round(v * 1e7) for v in (args.lat, args.lon)))
    return {"inside": zone.contains(args.lat, args.lon), "point": [args.lat, args.lon],
            "sub_cell": ids.sub_cell_code(ids.sub_cell_id(ids.cell_id(*loc[:3]), loc[3] * 5 + loc[4]))}


def _user_places(args):
    from .grid import Grid
    with Grid.open(args.db) as grid:
        user = grid.attach_user_places(args.file)
        out = _user_action(grid, user, args)
        if out is not None:
            print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _user_action(grid, user, args):
    a = args.action
    if a in ("add", "update"):
        polygon = None
        if args.polygon:
            with open(args.polygon, encoding="utf-8") as f:
                polygon = json.load(f)
        common = dict(author=args.author, polygon=polygon, cells=args.cells, kind=args.kind,
                      local_name=args.local_name, nearest=args.nearest)
        if a == "add":
            return user.add(args.name, references=args.ref or (), **common).to_dict()
        return user.update(args.key, name=args.name, references=args.ref, **common).to_dict()
    if a == "remove":
        return user.remove(args.key, author=args.author).to_dict()
    if a == "list":
        found = user.find(args.text) if args.text else user.localities(include_removed=args.removed)
        return [{"key": loc.key, "name": loc.name, "kind": loc.kind, "text": loc.text,
                 "sub_cells": loc.sub_cell_count, "removed": loc.removed} for loc in found]
    if a == "show":
        loc = user.get(args.key)
        return dict(loc.to_dict(), sub_cell_codes=[ids.sub_cell_code(s) for s in user.sub_cells(loc)])
    if a == "history":
        return user.history(args.key)
    if a == "export":
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(user.export_data(), f, ensure_ascii=False, indent=1)
        print(f"wrote {args.output}")
        return None
    if a == "import":
        with open(args.input, encoding="utf-8") as f:
            added = user.import_data(json.load(f), author=args.author)
        return [loc.key for loc in added]
    return [loc.key for loc in user.refresh(author=args.author)]


_COMMANDS = {
    "user-places": _user_places,
    "build": _build,
    "build-places": _build_places,
    "places": _with_grid(_places),
    "verify": _verify,
    "locate": _with_grid(lambda grid, a: grid.locate(a.lat, a.lon, a.accuracy).to_dict()),
    "show": _with_grid(_show),
    "within": _with_grid(lambda grid, a: [n.to_dict() for n in grid.cells_within(
        a.lat, a.lon, a.radius, level=a.level, pakistan_only=a.pakistan_only, touching=a.touching)]),
    "distance": _with_grid(lambda grid, a: grid.distance(a.a, a.b).to_dict()),
    "zone": _with_grid(_zone),
    "info": _with_grid(lambda grid, a: grid.info()),
    "export-layout": _export_layout,
    "export-conformance": _export_conformance,
}

if __name__ == "__main__":
    sys.exit(main())
