# Changelog

Every notable change to pakgrid is recorded here, newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
package is versioned with [Semantic Versioning](https://semver.org/).

Besides the package version, pakgrid keeps **format versions** for the data
it writes. A file of one format version is never read as another. They change
only when that format changes, and each change will be listed here.

| Format | Version | Where |
|---|---|---|
| Grid (rectangle, rows, columns, IDs) | 1 | `pakgrid/spec.py` (`GRID_VERSION`), fixed for good |
| Grid database schema | 2 | `pakgrid/store.py` |
| Places layer | 2 | `pakgrid/places.py` |
| User places file | 1 | `pakgrid/userplaces.py` |
| Zone data | 1 | `pakgrid/zones.py` |
| Block set data | 1 | `pakgrid/blocks.py` |
| Walkthrough data | 1 | `pakgrid/walkthrough.py` |
| Conformance files | 1 | `pakgrid/conformance.py` |

## [Unreleased] — 0.1.0

Not yet released or tagged. Everything below is on `main` and will be the
first release. Pull request numbers are in brackets.

### Added

#### The grid
- The grid itself: Pakistan's rectangle split into 4 quadrants at the
  national midlines, ~1 km cells sized on the WGS 84 ellipsoid so each row
  fills its quadrant exactly, and 25 sub-cells of ~200 m per cell. Fixed
  boundaries, area-weighted centroids, and 22-bit cell / 27-bit sub-cell IDs
  that are unique across the grid. [#2]
- The `Grid` class: `locate` (with GPS-accuracy-aware confidence and
  candidate blocks), `locate_many`, `cell`, `sub_cell`, `parent`,
  `children`, `neighbours` (across quadrant lines too), `cells_in_area`,
  `offset` / `from_offset`, readable codes (`11-0140-0471-00`) and `info`. [#2]
- `pakgrid build` writes the grid database (SQLite, built once); `pakgrid
  locate`, `show` and `info` read it. [#2]
- The grid's east edge is at 78.0° E, so all of Baltistan is inside. [#4]
- Pakistan border flags: every cell and sub-cell is `inside`, `border` or
  `outside` Pakistan, using the geoBoundaries / OpenStreetMap border
  (including AJK and Gilgit-Baltistan); `in_pakistan`, `pakistan_flags`;
  the Pakistan edition (`pakgrid build --pakistan-only`), with the same IDs.
  [#6]
- `pakgrid verify` checks a database against the grid rules, its border and
  its checksum, and official checksums of both complete editions. `Grid` is
  safe to share across threads, and after a fork. [#10]

#### Addresses and navigation
- Grid addresses (`11-0140-0471-00 W23 S18`): a sub-cell code plus an offset
  in metres that turns back into the exact point with no database
  (`address_from_point`, `parse_address`, `resolve_address`). [#9]
- `direction` and `format_distance`, and the navigation engine
  (`NavigationSession`): GPS smoothing, heading choice and arrival, for live
  rider guidance on the phone. [#9]
- Navigation rules and a conformance test file (`conformance/NAVIGATION.md`,
  `navigation.json`, about 2,000 cases) so ports to other languages can
  prove they match; `pakgrid export-conformance`. [#11]

#### Places
- The places layer: province, district and tehsil on every cell and
  sub-cell. [#13]
- Places version 2: UN OCHA tehsils with the official hierarchy (577
  tehsils, 160 districts) and 32,512 OpenStreetMap localities, with Urdu names
  where mapped and "in" / "near X (1.2 km SE)" labels; `place`, `places`,
  `find_places`, `get_place`, `cells_in_place`; `pakgrid places` and
  `build-places`. [#15]
- User localities: an app's authorised users draw and name localities the
  official data misses, located from known localities by distance and
  direction, kept in a separate user places file with author and history.
  `pakgrid user-places`. [#16]
- User roads: streets drawn and named (also in Urdu) by authorised users,
  with optional details, exact sub-cells, length, places and junctions; the
  nearest street on lookups (`Street 7, Lahore Cant, …`). [#22]
- What can drive through a street: a tap-to-answer question (foot, bicycle,
  motorbike, rickshaw, car, pickup, truck) kept on a user road, or as an
  access note for any other street; riders see it on lookups within 25 m;
  `pakgrid user-places access`. [#23]

#### Areas, search and maps
- Radius search (`cells_within`) and cell-to-cell distance (`distance`);
  `pakgrid within`, `distance`. [#17]
- Custom zones (`grid.zone`): delivery areas as sets of sub-cells from
  polygons, circles, cells and places, with fast pin checks, set operations,
  a compact stored form and `ZoneIndex`; `pakgrid zone`. [#18]
- GeoJSON for any map: blocks, sets of blocks, zones and places as exact,
  valid, canonical outlines (`geojson`, `cells_geojson`, `place_geojson`,
  `zone.to_geojson`); `pakgrid geojson`. [#19]
- Nearest lookup: the k nearest points in memory (`PointIndex`), in an app's
  own database by cell ID (`grid.nearest`), or localities
  (`nearest_places`), exact; `pakgrid nearest`. [#20]
- Custom blocks (`grid.block_set`): an app's own named, coded,
  non-overlapping groups of cells, built by clicks or shapes and described
  with cell codes, size, places, city and directions (`describe_zone`);
  `pakgrid blocks`. [#21]

#### Walkthroughs
- Walkthroughs, stage 1: from a phone video's GPS track, the cleaned path,
  turns and photo moments from the road to the door, each with a step such
  as "In 25 m, turn right" (`grid.walkthrough`, `ffmpeg_commands`,
  `read_gpx`); the rider rule (`WalkthroughGuide`) with
  `conformance/WALKTHROUGH.md` and `walkthrough.json` (610 cases);
  `pakgrid walkthrough`. [#24]
- Walkthroughs, stage 2: an optional Django app, `pakgrid.contrib.walkthroughs`
  (`pip install "pakgrid[django]"`): upload, photo job (in the request, a
  thread, Celery or cron), owner approval, and riders' access through the
  app's own rules. [#25]
- The Django app's files can go to any storage, including a private bucket
  with expiring links; the grid database is optional. [#26]

#### Map viewer (`viewer/`)
- A static map page showing the grid on OpenStreetMap: tap for codes, IDs,
  boundaries and centroids; search by code or `lat, lon`. [#2]
- The offset shown as an arrow with a compass bearing [#7]; **Navigate here**
  for rider guidance [#8]; places, and a place's exact outline [#13, #14];
  **Draw** for user localities [#16]; **Grid** show/hide, **Around this
  point** and **Measure** [#17]; **Zone** [#18]; **Download GeoJSON** [#19];
  **Nearest places** [#20]; **Blocks** [#21]; **Roads** [#22] with "what can
  drive through" [#23]; **Walk** for walkthroughs [#24].
- Its JavaScript (`viewer/pakgrid.js`) repeats the library's rules, and the
  tests check it gives the same answers as Python.

#### Documentation and project
- `project.md`, the full design and reference [#1, #2]; the grid schematic
  [#2]; `docs/django.md`, using pakgrid in a Django REST framework backend
  [#10]; `gridfeatures.md`, the feature list [#12].
- Documentation for every feature: a features table in the README, and the
  API, command line, viewer, package layout, testing, roadmap and glossary
  in `project.md` brought up to date [#28]. Walkthroughs are recorded as a
  standalone pakgrid feature that no app uses yet [#27], and `project.md`
  describes using pakgrid in any app [#28].
- Continuous integration on GitHub Actions: Python 3.10–3.13 on Linux, and
  3.13 on macOS and Windows [#5]; official checksums built on all three
  [#10, #12].
- Type hints on every function, a `py.typed` marker so apps' type checkers
  check their calls into pakgrid, and `mypy` in continuous integration. [#30]
- `CONTRIBUTING.md` (bug reports and suggestions from anyone, code changes by
  the owner's permission only), `SECURITY.md` (private reporting), issue forms
  for bugs and feature requests, and a pull request checklist. [#31]
- The `ruff` linter in place of `pyflakes`: unused names, import order, line
  length and likely bugs, with its settings in `pyproject.toml`. [#32]
- Documentation gaps closed: docstrings on every public method, the `ids`
  helpers, the last methods and command-line options in the reference, and the
  package layout. [#33]

### Fixed
- From the Codex review of 16 November 2026: `pakgrid build --overwrite` no
  longer deletes the old database before building; it builds a temporary file
  and replaces the old one only when the build is complete, and refuses a bad
  quadrant before starting (CR-04). Rebuilding the places layer is one
  transaction, so a failure leaves the old layer (CR-11). Navigation ignores a
  reading whose time is not a finite number (it crashed on NaN) or that is older
  than the last one, in Python and JavaScript alike, and refuses bad options
  and targets; the rules and conformance file cover it (CR-05). A `Zone` can no
  longer be changed after it is made, so sets, dicts and `ZoneIndex` stay
  correct; zones still pickle and copy (CR-10). [#35]
- From the same review, the walkthrough Django app: an upload for an address
  needs the app's `CAN_UPLOAD` rule (without one, only staff may; a system
  check warns), and approving or auto-approving re-checks it, so nobody can
  replace the directions to someone else's door (CR-01). Tracks are limited
  (`MAX_TRACK_BYTES`, 20,000 readings, checked while reading), uploads are
  throttled (`UPLOAD_RATE`), and smoothing takes time in proportion to the
  readings: a 20,000-reading track takes ~0.9 s instead of ~26 s, with the same
  results (CR-02). One live walkthrough per address is a database constraint
  (migration `0003`), and the later of two simultaneous approvals wins, tested
  on PostgreSQL (CR-03). Malformed tracks and over-long `place_ref`s answer
  400 with a `code` instead of 500 (CR-06). [#36]
- Verifier gaps, bad sensor readings in navigation, and documentation, from a
  review. [#18]
- The JavaScript tests read output as UTF-8, which fixed the Windows run.
  [#19]
- The walkthrough Django app records any failure of the photo job (for
  example a storage error) as failed, instead of leaving it "processing".
  [#26]

### Changed
- **The walkthrough Django app now needs `CAN_UPLOAD` for addresses:** without
  it, only staff may record a walkthrough with a `place_ref` (it used to be any
  signed-in user). Set it to your app's rule before upgrading. [#36]
- Faster area searches, with exactly the same answers: a row's cells in a box
  or circle are found as one run of columns, and search boxes are no larger
  than needed. Lookups with a user places file (nearest street, what can drive
  through) take ~110 µs instead of ~190 µs; `nearest_places` ~0.2 ms in a city
  (was ~0.6 ms) and ~4 ms on average anywhere in Pakistan (was ~24 ms); a
  `PointIndex` of a few points spread far apart is ~6× faster; `cells_in_area`
  and `cells_within` are faster too. [#34]
- `gridfeatures.md` logs the library-quality work (type hints, contributor
  files, ruff, documentation, speed, licence, release), and project.md's
  roadmap and open decisions are brought up to date, including the licence
  choice; the docs no longer call pakgrid open source or say an app uses it
  today (each app's developers integrate it themselves). [#34]
- The walkthrough Django app's "sync" job takes photos straight from the
  uploaded file, and stores the video only if taking the photos fails. [#26]

[Unreleased]: https://github.com/sahir2947/Pakistan-National-Grid-Mapping/commits/main
