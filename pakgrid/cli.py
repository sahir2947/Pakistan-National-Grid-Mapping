"""Command-line tools: pakgrid build | build-places | verify | locate | show | places | info |
export-layout | export-conformance."""

import argparse
import json
import os
import sys

from . import ids, spec
from .errors import PakgridError

DEFAULT_DB = os.environ.get("PAKGRID_DB", f"pakgrid_v{spec.GRID_VERSION}.sqlite")


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

    p = sub.add_parser("show", help="print a cell or sub-cell by code")
    p.add_argument("code")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("places", help="list places (province, district, tehsil, locality), or the cells of one")
    p.add_argument("text", nargs="?", help="only places whose name contains this text, or with this key")
    p.add_argument("--level", choices=("province", "district", "tehsil", "locality"))
    p.add_argument("--cells", action="store_true",
                   help="print the codes of the cells in the place (TEXT must match exactly one place)")
    p.add_argument("--db", default=DEFAULT_DB)

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
        with Grid.open(args.db) as grid:
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


_COMMANDS = {
    "build": _build,
    "build-places": _build_places,
    "places": _with_grid(_places),
    "verify": _verify,
    "locate": _with_grid(lambda grid, a: grid.locate(a.lat, a.lon, a.accuracy).to_dict()),
    "show": _with_grid(_show),
    "info": _with_grid(lambda grid, a: grid.info()),
    "export-layout": _export_layout,
    "export-conformance": _export_conformance,
}

if __name__ == "__main__":
    sys.exit(main())
