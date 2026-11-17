# Contributing to pakgrid

Thank you for your interest in pakgrid. This page says what you can do, and how.

## The short version

- **Bug reports and suggestions are welcome from anyone.** Open an issue (see
  below).
- **Code changes are by permission only.** pakgrid is not open source. Nobody
  may change its code, or add a feature, without the owner's written
  permission. Please do not open a pull request that nobody asked for; ask in
  an issue first.
- **Security problems are reported privately**, never in a public issue. See
  [SECURITY.md](SECURITY.md).

The licence, *Pakgrid Org License 1.0*, is being finalised. Until its file is in this
repository, all rights in pakgrid are reserved by the owner.

## Reporting a bug

Open an issue with the **Bug report** form. The most useful reports say:

- the pakgrid version (`python -c "import pakgrid; print(pakgrid.__version__)"`)
  and Python version;
- the exact call or command, and the coordinates, codes or IDs involved;
- what you expected, and what happened instead (the full error, if any).

If the bug is about a wrong cell, sub-cell, address or place, give the GPS
point as `lat, lon` with all its digits. Most of these can be checked in the
map viewer (`viewer/index.html`).

## Suggesting a feature

Open an issue with the **Feature request** form. Say what you are trying to
do and why the library does not do it today. pakgrid is a **standalone
library**: features must be useful to any app that uses it, not built around
one app's own code.

## Making a change (with permission)

If the owner has agreed to a change, this is how to make it. The owner will
tell you the terms for your contribution before it is merged.

### Set up

```
git clone https://github.com/sahir2947/Pakistan-National-Grid-Mapping.git
cd Pakistan-National-Grid-Mapping
python -m pip install -e ".[test]" ruff==0.17.5 mypy==2.4.1
```

You also need Node.js 20 or later. The tests run the map viewer's JavaScript
with it.

### Check your work

Run these before you push. Continuous integration runs the same checks on
every pull request, on Linux, macOS and Windows.

```
python -m pytest -q          # all tests
python -m ruff check .       # lint (settings in pyproject.toml)
python -m mypy               # type check (settings in pyproject.toml)
```

Some tests need a tool and skip without it: Node.js (the viewer and conformance
tests), ffmpeg and Celery (the walkthrough photo job), and PostgreSQL (the
walkthrough app's concurrency tests). To run those on PostgreSQL, point them at
a server with database and user `postgres`:

```
PAKGRID_TEST_POSTGRES=localhost:5432 python -m pytest -q tests/test_django_walkthroughs.py
```

CI sets `PAKGRID_REQUIRE` (for example `PAKGRID_REQUIRE=node,ffmpeg`) so that a
missing tool fails the run instead of skipping its tests; you can do the same.
If you change the viewer, also run `scripts/check_viewer_in_browser.js` (see
its header).

### The rules a change must keep

- **Grid IDs never change.** The grid is version 1 and fixed for good: its
  rectangle, rows, columns, cell and sub-cell IDs and codes must come out
  exactly the same. Apps store these IDs.
- **Stored formats have version numbers.** If you change what a file holds
  (the grid database, places layer, user places file, zone, block set,
  walkthrough or conformance data), its format version goes up, and older files
  are refused or upgraded, never misread. The versions are listed at the top
  of [CHANGELOG.md](CHANGELOG.md).
- **Python and JavaScript agree.** The map viewer (`viewer/pakgrid.js`) repeats
  the library's rules, and the tests check they give the same answers. A rule
  changed in one is changed in the other.
- **Generated files are regenerated, not edited.** If a test says a file is
  stale, run the command it names:
  `pakgrid export-layout` (`viewer/layout.js`, `boundary.js`, `places.js`) or
  `pakgrid export-conformance` (`conformance/*.json`).
- **Tests come with the change.** New behaviour gets tests, and a fixed bug
  gets a test that failed before the fix.
- **Every function has type hints.** `mypy` checks this.
- **No dependencies in the core.** The `pakgrid` package itself uses only the
  Python standard library. Optional parts (such as the Django app in
  `pakgrid.contrib`) declare theirs as extras in `pyproject.toml`.
- **Documentation and changelog.** Update `project.md` (and the README, if a
  feature is visible there) and add a line to the `Unreleased` section of
  `CHANGELOG.md`.

### Style

- Follow the code around you: plain names, short docstrings that say what a
  function gives back, and comments that explain *why*.
- Lines up to 120 characters.
- One change per pull request. Commit messages start with what changed, for
  example `Roads: refuse a line with fewer than 2 points`.
