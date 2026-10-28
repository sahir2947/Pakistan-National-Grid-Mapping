"""Command-line tools: pakgrid build | verify | locate | show | info | export-layout."""

import argparse
import json
import os
import sys

from . import spec
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

    p = sub.add_parser("verify", help="check a database against the grid rules and its checksum")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--full", action="store_true", help="also regenerate and compare every record")

    p = sub.add_parser("locate", help="find the sub-cell containing a GPS point")
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("--accuracy", type=float, metavar="METRES")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("show", help="print a cell or sub-cell by code")
    p.add_argument("code")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("info", help="print grid and database information")
    p.add_argument("--db", default=DEFAULT_DB)

    p = sub.add_parser("export-layout", help="write the map viewer's data files (layout.js, boundary.js)")
    p.add_argument("-o", "--output-dir", default="viewer")

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
                            pakistan_only=args.pakistan_only,
                            progress=lambda msg: print(f"building {msg}…", file=sys.stderr))
    print(f"wrote {report.path}: {report.cells:,} cells, {report.sub_cells:,} sub-cells, "
          f"{report.size_bytes / 1e6:.0f} MB in {report.seconds:.1f} s")
    print(f"content sha256: {report.content_sha256}")
    return 0


def _verify(args):
    from .verify import verify_database
    report = verify_database(args.db, full=args.full)
    for name, passed, detail, required in report.checks:
        status = "PASS" if passed else "FAIL" if required else "NOTE"
        print(f"{status}  {name}" + (f"  ({detail})" if detail else ""))
    return 0 if report.ok else 2


def _export_layout(args):
    from .export import write_viewer_files
    for path in write_viewer_files(args.output_dir):
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
    return item.to_dict()


_COMMANDS = {
    "build": _build,
    "verify": _verify,
    "locate": _with_grid(lambda grid, a: grid.locate(a.lat, a.lon, a.accuracy).to_dict()),
    "show": _with_grid(_show),
    "info": _with_grid(lambda grid, a: grid.info()),
    "export-layout": _export_layout,
}

if __name__ == "__main__":
    sys.exit(main())
