"""Check an installed pakgrid (a wheel or sdist installed with pip): run it from outside the source
tree, so it tests what users get, not the checkout.

    python -m venv /tmp/v && /tmp/v/bin/pip install dist/pakgrid-*.whl
    cd /tmp && /tmp/v/bin/python /path/to/scripts/check_installed_package.py 0.1.0

Checks the version, that the package and its data, typing marker, Django app and migrations are
all there, that the command line runs, and that a small grid builds and answers a lookup with the
packaged border and places data.
"""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
import tempfile


def main(expected_version: str) -> None:
    import pakgrid
    from pakgrid import address_from_point, resolve_address

    here = os.path.dirname(os.path.abspath(pakgrid.__file__))
    assert "site-packages" in here or "dist-packages" in here, f"not the installed copy: {here}"
    assert pakgrid.__version__ == expected_version, (pakgrid.__version__, expected_version)

    files = importlib.resources.files("pakgrid")
    wanted = ["py.typed", "data/places-v2.json.gz", "data/geoBoundaries-PAK-ADM0.geojson.gz",
              "data/BOUNDARY_SOURCE.md", "data/PLACES_SOURCE.md",
              "contrib/walkthroughs/models.py", "contrib/walkthroughs/management/commands/process_walkthroughs.py"]
    migrations = files.joinpath("contrib").joinpath("walkthroughs").joinpath("migrations")
    wanted += [f"contrib/walkthroughs/migrations/{m.name}" for m in migrations.iterdir() if m.name.endswith(".py")]
    for name in wanted:
        node = files
        for part in name.split("/"):
            node = node.joinpath(part)
        assert node.is_file(), f"missing from the package: {name}"
    assert any(m.name.startswith("0004_") for m in migrations.iterdir()), "the latest migration is missing"

    # no database: a grid address there and back
    lat, lon = resolve_address(address_from_point(31.5204, 74.3587).text)
    assert abs(lat - 31.5204) < 1e-4 and abs(lon - 74.3587) < 1e-4

    # the command line, as installed beside this Python (not whatever `pakgrid` is on PATH), and a
    # small build that uses the packaged border and places data
    scripts = os.path.dirname(sys.executable)
    cli = os.path.join(scripts, "pakgrid.exe" if os.name == "nt" else "pakgrid")
    assert os.path.isfile(cli), f"the pakgrid command was not installed: {cli}"
    for cmd in ([cli, "--help"], [sys.executable, "-m", "pakgrid", "--help"]):
        subprocess.run(cmd, check=True, capture_output=True)
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "g.sqlite")
        subprocess.run([cli, "build", "-o", db, "--quadrant", "11", "--rows", "140:141"],
                       check=True, capture_output=True)
        out = subprocess.run([cli, "locate", "31.5204", "74.3587", "--db", db],
                             check=True, capture_output=True, text=True).stdout
        assert '"11-0140-0471-00"' in out and "Lahore" in out, out
        subprocess.run([cli, "verify", "--db", db], check=True, capture_output=True)
    print(f"pakgrid {pakgrid.__version__} installed correctly: {len(wanted)} files, CLI, build, lookup")


if __name__ == "__main__":
    main(sys.argv[1])
