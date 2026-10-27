# Pakistan National Grid (`pakgrid`) — Project Documentation

| | |
|---|---|
| **Status** | v0.1 library implemented (Python package `pakgrid`, see the README); scope will be refined as the project develops |
| **Grid version** | v1 (proposed, not yet frozen) |
| **First user** | Dukandaari app (delivery logistics) |
| **Licence** | Open source (MIT or Apache-2.0, to be decided) |
| **Last updated** | 2026-10-27 |

![How a grid code is built: quadrants, rows and columns, sub-cells, and bit packing, worked through for central Lahore](docs/grid_schematic.png)

*Schematic of the grid, worked through for central Lahore
(`11-0140-0471-00`). Printable A4 version: [docs/grid_schematic.pdf](docs/grid_schematic.pdf).*

---

## Contents

1. [Overview](#1-overview)
2. [Scope of v0.1](#2-scope-of-v01)
3. [Grid design](#3-grid-design)
4. [Identifiers](#4-identifiers)
5. [Pre-built catalogue and database](#5-pre-built-catalogue-and-database)
6. [Library architecture](#6-library-architecture)
7. [Public interface](#7-public-interface)
8. [How a lookup works](#8-how-a-lookup-works)
9. [Handling GPS inaccuracy](#9-handling-gps-inaccuracy)
10. [Rider navigation](#10-rider-navigation)
11. [From request to response](#11-from-request-to-response)
12. [Performance](#12-performance)
13. [Using it in Dukandaari](#13-using-it-in-dukandaari)
14. [Validation and testing](#14-validation-and-testing)
15. [Roadmap](#15-roadmap)
16. [Open decisions](#16-open-decisions)
17. [Changes from the initial README spec](#17-changes-from-the-initial-readme-spec)
18. [Glossary](#18-glossary)

---

## 1. Overview

### 1.1 The problem

Addresses in Pakistan are mostly free text, for example *"near the green
mosque, gali 3, behind the PSO pump"*. Many areas have no street names or house
numbers, so couriers phone customers for directions. That causes delays,
failed deliveries and wasted fuel.

### 1.2 The idea

A **fixed national grid** in which every location in Pakistan belongs to
exactly one permanently numbered block:

- **4 quadrants** divide the country.
- About **2.5 million cells** of ~1 km × 1 km make up the quadrants.
- About **62.5 million sub-cells** of ~200 m × 200 m make up the cells.
- Every cell and sub-cell has a fixed ID, fixed boundaries and a fixed
  **centroid** (centre point) with a real GPS coordinate.

A location can then be written as a short code, such as `11-0140-0471-00`.
For door-level precision, the code can carry an offset from the block's
centroid: `11-0140-0471-00 W23 S18` (23 m west and 18 m south of the block's
centre).

### 1.3 Long-term vision

A **standalone, open-source mapping platform for Pakistan** built around the
grid, with a public API that any app can use: delivery apps, courier companies,
emergency services, government bodies. **Dukandaari is the first user.**

```
        Pakistan Grid Platform (open source)
  ┌──────────────────────────────────────────┐
  │ Grid engine  ·  Map  ·  Search  ·  Routes │
  └───────────────────┬──────────────────────┘
                      │ Public API + SDKs
     ┌────────────────┼────────────────┐
  Dukandaari     Courier firms     Anyone else
```

The platform builds on existing open data and tools rather than recreating
Google Maps:

- **OpenStreetMap** for roads, buildings and place names
- **MapLibre** to display the map
- **OSRM** or **Valhalla** for road routing
- **Nominatim** for place search

What is new is the **national grid address layer** on top.

---

## 2. Scope of v0.1

v0.1 is the **grid itself**: a Python library and a static database.

| # | Requirement | How it is met | Section |
|---|-------------|---------------|---------|
| 1 | Divide the territory into quadrants | 4 quadrants split at the national midlines | §3.2 |
| 2 | Cells inside each quadrant | ~1 km × 1 km cells | §3.3–3.4 |
| 3 | Sub-cells inside each cell | 5 × 5 = 25 sub-cells of ~200 m × 200 m | §3.5 |
| 4 | Sizes may flex to fill the quadrant | Each row's cells are stretched slightly so the row fills the quadrant exactly, leaving no gaps or partial cells | §3.3–3.4 |
| 5 | Every cell unique | Cell ID (quadrant + row + column) is unique across the whole grid | §4.5 |
| 6 | Every sub-cell unique | Sub-cell ID (cell ID + index 0–24) is unique across the whole grid | §4.5 |
| 7 | Centroids for every cell and sub-cell | Area-weighted centre on the WGS 84 ellipsoid | §3.7 |
| 8 | Corrects for Earth's curvature | All sizes measured on the WGS 84 ellipsoid | §3.3–3.4 |
| 9 | Linked to real GPS points | Every boundary is a line of latitude or longitude | §3.6 |
| 10 | IDs and boundaries pre-determined | Full catalogue generated before use | §5 |
| 11 | Own static database as single source of truth | One read-only, versioned SQLite file | §5 |
| 12 | Python compatible | Pure Python 3.10+, standard library only | §6 |
| 13 | Efficient links between levels | The parent is encoded in the child's ID, so no link tables are needed | §4 |
| 14 | No front end for now | Library and command-line tools only | — |

**Out of scope for v0.1:**

- website, public HTTP API, search, road routing
- personal data of any kind (for example CNIC)
- clipping the grid to Pakistan's exact border (planned later, §5.6)

---

## 3. Grid design

### 3.1 The bounding rectangle

The grid covers a latitude/longitude rectangle that contains all of Pakistan.

| Corner | GPS point | Approximate location |
|---|---|---|
| South-West | 23.436° N, 60.800° E | Gulf of Oman / Arabian Sea, ~200 km south of Chabahar (Iran) |
| North-West | 37.080° N, 60.800° E | North-east Iran, near the Turkmenistan border |
| North-East | 37.080° N, 78.000° E | Xinjiang, China, in the Kunlun mountains north of the Karakoram |
| South-East | 23.436° N, 78.000° E | Madhya Pradesh, India, ~60 km east of Bhopal |

- The **south edge** is the Tropic of Cancer, just south of Pakistan's
  southern tip.
- The **north edge** matches Pakistan's northern limit.
- The **east edge** (78.0° E) includes all of Gilgit-Baltistan, up to the
  Karakoram Pass (~77.8° E). It was moved from 77.0° E after checking the
  grid on the map viewer, which showed 77.0° E cut off a corner of Baltistan.

**Area:** 2,497,710 km² on the WGS 84 ellipsoid. Pakistan, including
Gilgit-Baltistan and AJK, is about 882,000 km², or **~35%** of the rectangle.
The rest covers parts of Iran, Afghanistan, China, India and the Arabian Sea.
Cells outside Pakistan will be flagged later (§5.6); their IDs stay valid.

### 3.2 Quadrants

The rectangle is split at its midlines: **30.258° N** and **69.400° E**, the
middle of the rectangle in each direction.

| Code | Quadrant | Example region | Area (km²) | Rows | Cells |
|------|----------|----------------|-----------:|-----:|------:|
| `00` | South-West | Karachi, Quetta, Gwadar, Hyderabad, Sukkur | 645,709 | 756 | 645,806 |
| `01` | North-West | Pishin, Chaman (northern Balochistan); mostly Afghanistan and Iran | 603,146 | 757 | 603,405 |
| `10` | South-East | Multan, Bahawalpur, Dera Ghazi Khan, Thar | 645,709 | 756 | 645,806 |
| `11` | North-East | Lahore, Islamabad, Peshawar, Zhob, Gilgit-Baltistan | 603,146 | 757 | 603,405 |
| | **Total** | | **2,497,710** | | **2,498,422** |

- In each code, the **first bit** is east (1) or west (0) and the **second
  bit** is north (1) or south (0).
- The southern quadrants are larger because a degree of longitude is wider
  further south.
- The midlines are geometric, not regional: Quetta (30.18° N) is just south of
  30.258° N, and Karachi (67.0° E) is west of 69.4° E, so both are in `00`.
  Check a place's quadrant with `pakgrid locate` or the map viewer.

### 3.3 Rows (north–south)

For each quadrant:

1. Measure its height along a meridian on the WGS 84 ellipsoid (~756 km).
2. **Number of rows** = height ÷ 1,000 m, rounded to the nearest whole number.
3. Split the quadrant into that many **equal latitude bands**.

Result: every row is **999.0–1,000.4 m** tall, and the rows exactly fill the
quadrant. Rows are numbered from 0 at the south edge.

### 3.4 Columns (east–west) and the curvature correction

Lines of longitude converge towards the pole. One degree of longitude is
~102 km wide near Karachi but only ~89 km in Gilgit-Baltistan. So **every row
gets its own number of columns**:

1. Measure the quadrant's width along the row's middle latitude on the
   ellipsoid.
2. **Number of columns** = width ÷ 1,000 m, rounded to the nearest whole number.
3. Split the row into that many **equal longitude spans**.

Result: every cell is **999.3–1,000.7 m** wide, and every row fills the
quadrant exactly. Columns are numbered from 0 at the west edge. In quadrant
`11`, for example, row 0 has 828 columns, row 140 has 817, and row 756 has 765.
The maximum anywhere is 879.

Because column counts differ from row to row, cells in neighbouring rows are
**offset sideways, like bricks in a wall**. This matters for neighbours (§8.4)
and is why "nearest centroid" must not be used to find a cell (§8.3).

### 3.5 Sub-cells

Each cell is split into a **5 × 5** grid of equal latitude and longitude steps,
giving 25 sub-cells of ~200 m × 200 m.

```
 20  21  22  23  24     ← north
 15  16  17  18  19
 10  11  12  13  14     sub = sub_row × 5 + sub_col
  5   6   7   8   9
  0   1   2   3   4     ← south (sub_row 0)
 west             east
```

### 3.6 Boundaries and the edge rule

- Every boundary is a line of constant latitude or constant longitude, so every
  corner is an exact GPS coordinate. No map projection is needed to convert
  back.
- **Edge rule:** a point exactly on a boundary belongs to the cell or sub-cell
  to its **north or east**. Points on the outer north and east edges of the
  rectangle belong to the last row or column. This guarantees every point maps
  to exactly one block.

### 3.7 Centroids: the fixed points of the grid

Every cell and sub-cell has a **centroid**: the area-weighted centre of the
block on the WGS 84 ellipsoid. Centroids are:

- **real GPS coordinates** (WGS 84, the same system used by GPS, phones and
  Google Maps)
- **precise to ~1 cm**, stored in units of 10⁻⁷ degree
- **permanent**: computed once when the database is built and never changed
  within a grid version

The area-weighted centroid sits slightly south of the plain midpoint, because
the southern half of a block is fractionally wider. The difference is ~0.8 cm
for a 1 km cell, but the correct value is the one stored.

**Worked example, central Lahore:**

| | South / West edge | North / East edge | Centroid |
|---|---|---|---|
| Cell `11-0140-0471` | 31.5196645° N, 74.3578947° E | 31.5286764° N, 74.3684211° E | **31.5241703° N, 74.3631579° E** |
| Sub-cell `11-0140-0471-00` | 31.5196645° N, 74.3578947° E | 31.5214668° N, 74.3600000° E | **31.5205657° N, 74.3589474° E** |

**Total fixed points:** 2,498,422 cell centroids + 62,460,550 sub-cell
centroids = **64,958,972**.

**Notes:**

- A centroid is a geometric reference point. It may fall on a rooftop, a canal
  or a field. For deliveries, the customer's exact pin locates the door (§13).
- The ground under Pakistan moves a few centimetres a year with the tectonic
  plates. Over decades that is a few metres, which is irrelevant for 200 m
  blocks.

---

## 4. Identifiers

### 4.1 Bit layout

IDs pack the quadrant, row, column and sub-cell into one integer. Each part
gets a fixed-width slot:

```
Cell ID      (22 bits):  [ quadrant : 2 ][ row : 10 ][ column : 10 ]
Sub-cell ID  (27 bits):  [ quadrant : 2 ][ row : 10 ][ column : 10 ][ sub : 5 ]
```

| Part | Range | Bits |
|---|---|---|
| Quadrant | 0–3 | 2 |
| Row | 0–756 (max 1,023) | 10 |
| Column | 0–878 (max 1,023) | 10 |
| Sub-cell | 0–24 (max 31) | 5 |

Both IDs fit in a standard **32-bit integer** (4 bytes).

### 4.2 Building and reading IDs

```python
cell_id = (quadrant << 20) | (row << 10) | column
sub_id  = (cell_id << 5) | sub

cell_id  = sub_id >> 5               # parent cell, no lookup needed
quadrant = cell_id >> 20
row      = (cell_id >> 10) & 0x3FF
column   = cell_id & 0x3FF
sub      = sub_id & 0x1F
```

### 4.3 Worked example: central Lahore (31.5204° N, 74.3587° E)

| Step | Working | Result |
|---|---|---|
| Quadrant | north of 30.258°, east of 69.400° | `11` = **3** |
| Row | (31.5204 − 30.258) ÷ 0.0090119 = 140.08 → floor | **140** |
| Columns in row 140 | width 816,810 m ÷ 1,000 → 817 columns of 0.0105263° | — |
| Column | (74.3587 − 69.400) ÷ 0.0105263 = 471.08 → floor | **471** |
| Sub-cell | 0.08 up → sub-row 0; 0.08 across → sub-col 0; 0 × 5 + 0 | **0** |
| Cell ID | (3 « 20) \| (140 « 10) \| 471 | **3,289,559** |
| Sub-cell ID | (3,289,559 « 5) \| 0 | **105,265,888** |

```
 11  0010001100  0111010111  00000
 ──  ──────────  ──────────  ─────
 Q    row 140    column 471  sub 0
```

### 4.4 Readable codes

| Form | Example | Use |
|---|---|---|
| Sub-cell | `11-0140-0471-00` | Shared on a call or by WhatsApp; printed on parcels |
| Cell | `11-0140-0471` | Zones, batching |
| Sub-cell + offset | `11-0140-0471-00 W23 S18` | Door-level location: metres from the centroid |

### 4.5 Uniqueness

Row and column numbers start from 0 in every quadrant, and sub-cell numbers
0–24 repeat in every cell. Because each **ID includes the levels above it**,
every ID is still unique across the whole grid:

| Identifier | Unique within |
|---|---|
| Row, column | its quadrant |
| Sub-cell number (0–24) | its cell |
| **Cell ID** | **the whole grid** (2,498,422 distinct IDs) |
| **Sub-cell ID** | **the whole grid** (62,460,550 distinct IDs) |

For example, row 140, column 471 exists in all four quadrants, but the
quadrant bits make each one a different cell:

| Code | Cell ID |
|---|---|
| `00-0140-0471` | 143,831 |
| `01-0140-0471` | 1,192,407 |
| `10-0140-0471` | 2,240,983 |
| `11-0140-0471` | 3,289,559 |

An app stores the single integer ID and never needs a separate quadrant
column to tell cells apart.

### 4.6 Why this layout

- **No link tables.** Every sub-cell ID contains its parent cell's ID, and
  every cell ID contains its quadrant.
- **Small.** 4 bytes per address in any app's database.
- **Grouped.** Cells in the same row have consecutive IDs, so batching by area
  is a bit shift or a numeric range.

---

## 5. Pre-built catalogue and database

### 5.1 Build once, then use

Every cell and sub-cell exists **in advance**, with a fixed ID, boundaries and
centroid. A GPS lookup is just one way of searching this catalogue.

```
 BUILD (once per grid version, by the project)
   Grid rules → Layout → Builder → pakgrid_v1.sqlite → published with checksum
                                          │
                                          ▼ download
 USE (any app)
   Grid.open("pakgrid_v1.sqlite") → cell(id) · sub_cell(id) · locate(lat, lon) · …
```

The builder walks the whole grid in order:

```
for each quadrant 00, 01, 10, 11:
    for each row:
        for each column in that row:
            write cell: ID, 4 boundaries, centroid
            write its 25 sub-cells: IDs, boundaries, centroids
```

### 5.2 Database schema (SQLite)

SQLite is part of Python's standard library, needs no server, and is a single
file.

| Table | Rows | Contents |
|---|---|---|
| `meta` | 1 | grid version, datum (WGS 84), corners, midlines, target sizes, build checksum |
| `quadrants` | 4 | code, boundaries, row count, cell count |
| `rows` | ~3,026 | quadrant, row, south/north latitude, column count, column width (degrees) |
| `cells` | 2,498,422 | cell ID (primary key), south, west, north, east, centroid latitude/longitude |
| `sub_cells` | 2,498,422 | cell ID (primary key) plus one packed record for its 25 sub-cells |

**Coordinate encoding:** all coordinates are stored as integers in units of
10⁻⁷ degree (~1.1 cm). This is smaller than decimal text, exact, and fast to
compare.

**Packed sub-cell record** (62 × int32 = 248 bytes per cell):

| Field | Values | Meaning |
|---|---|---|
| Latitude lines | 6 | south edge, 4 inner lines, north edge |
| Longitude lines | 6 | west edge, 4 inner lines, east edge |
| Centroids | 25 × (lat, lon) | one per sub-cell, in index order 0–24 |

Neighbouring sub-cells share edges, so each edge line is stored only once.

### 5.3 Storage options (measured)

| Option | How sub-cells are stored | Size of sub-cells | Total with cells |
|---|---|---|---|
| A. One row per sub-cell | 62.5 M rows with ID, 4 boundaries, centroid | ~2.45 GB | ~2.55 GB |
| **B. Packed per cell (chosen)** | 2.5 M packed records, as in §5.2 | **~680 MB** | **781 MB** (measured) |
| C. Calculated from the parent | Not stored | 0 | ~99 MB |

**Option B is chosen.** Every sub-cell's ID, boundaries and centroid are
pre-determined and stored, so the database remains the single source of truth.
It is ~3.5× smaller than option A, and it fits within GitHub Releases' 2 GB
per-file limit, which option A does not.

### 5.4 Memory

Only the `rows` table (~3,000 rows, < 100 KB) is held in memory. The 781 MB
file stays on disk and is read on demand through SQLite and the operating
system's file cache.

### 5.5 Distribution and versioning

- The database is too large for a git repository (100 MB limit), so it is
  published on **GitHub Releases** together with its SHA-256 checksum.
- The database is **read-only and immutable**. Within a grid version, an ID
  always refers to the same block, boundaries and centroid.
- Any change to the grid rules produces a **new version file**
  (`pakgrid_v2.sqlite`). Existing IDs are never redefined.

### 5.6 Pakistan border filter (later)

Once an authoritative boundary dataset is agreed:

- an `in_pakistan` flag is added for every cell and sub-cell, and
- optionally, a smaller "Pakistan only" edition of the database (~280 MB) is
  published.

IDs do not change, because they come from the rectangle and not from the
border.

---

## 6. Library architecture

### 6.1 Package layout

```
pakgrid/
├── spec.py       Fixed grid rules: version, WGS 84, corners, midlines, 1 km target, 5 × 5
├── geodesy.py    Ellipsoid maths: meridian and parallel lengths, areas, centroids, vectors
├── layout.py     Each row's latitude band and column count (~3,000 rows)
├── ids.py        Pack/unpack IDs, readable codes, parent/children, validation
├── errors.py     Exception types
├── builder.py    Walks the grid and writes the database (build phase only)
├── store.py      Read-only database access; unpacks sub-cell records
├── verify.py     Checks a database against the grid rules and its checksum
├── grid.py       The public Grid class
├── navigate.py   Distance, bearing and turn guidance between two points
└── cli.py        Command-line tools
tests/            Gap/overlap, round-trip, uniqueness, determinism and accuracy tests
```

### 6.2 Principles

- **Layered.** Each module depends only on the ones above it. `spec` knows
  nothing about databases; `grid` knows nothing about ellipsoid formulas.
- **No dependencies.** Pure Python 3.10+ with the standard library (`math`,
  `sqlite3`, `struct`). `pyproj` is used only in tests to cross-check the
  geodesy.
- **Deterministic.** Building twice produces byte-identical files. Every row
  and column count is far from a rounding boundary, so floating-point
  differences between platforms cannot change the layout. Centroids could, in
  rare cases, differ by 1 × 10⁻⁷ degree between platforms; `pakgrid verify`
  reports this.
- **Exact point location.** A GPS point is rounded to 10⁻⁷ degree (~1.1 cm)
  and compared with the stored integer boundaries, so every point falls in
  exactly one block.
- **Portable core.** `layout`, `ids` and `navigate` together need only the
  ~100 KB `rows` table. They can be ported to Dart for offline use in Flutter.

---

## 7. Public interface

### 7.1 Python library: 14 functions

Integer cell IDs and sub-cell IDs can look alike, so functions that accept
either take a `level` argument (`"cell"` or `"sub"`). A readable code sets the
level by itself.

| # | Function | Returns |
|---|---|---|
| **Finding a location** | | |
| 1 | `locate(lat, lon, accuracy_m=None)` | `Location`: sub-cell, offset from its centroid, edge distance, confidence and candidates |
| 2 | `locate_many(points)` | A list of `Location`; each point is `(lat, lon)` or `(lat, lon, accuracy_m)` |
| **Lookup by ID** | | |
| 3 | `cell(id_or_code)` | `Cell`: ID, code, quadrant, row, column, bounds, centroid |
| 4 | `sub_cell(id_or_code)` | `SubCell`: ID, code, parent cell, index 0–24, bounds, centroid |
| **Hierarchy and area** | | |
| 5 | `parent(sub_id)` | The parent cell ID (bit shift; no database access) |
| 6 | `children(cell)` | The 25 `SubCell`s of a cell, in order 0–24 |
| 7 | `neighbours(item, level="cell")` | IDs of touching cells or sub-cells, across quadrant lines too |
| 8 | `cells_in_area(south, west, north, east, level="cell")` | IDs of cells or sub-cells overlapping a box |
| **Vectors and navigation** | | |
| 9 | `offset(item, lat, lon, level="sub")` | `Offset` from a centroid to a point: east/north metres, distance, bearing, text such as `W23 S18` |
| 10 | `from_offset(item, east_m, north_m, level="sub")` | `(lat, lon)` of a block's centroid plus an offset |
| 11 | `direction(from_lat, from_lon, to_lat, to_lon, heading=None)` | `Direction`: metres, feet, bearing, compass name, and turn left/right |
| **Readable codes** | | |
| 12 | `to_text(id, level="sub")` | `105265888` → `11-0140-0471-00` |
| 13 | `from_text(code)` | `11-0140-0471-00` → `105265888`, checked against the grid |
| **Info** | | |
| 14 | `info()` | Grid version, corners, counts, database coverage and checksum |

Every result object has a `to_dict()` method that returns JSON-ready data.

```python
from pakgrid import Grid

with Grid.open("pakgrid_v1.sqlite") as grid:
    loc = grid.locate(31.5204, 74.3587, accuracy_m=5)
    loc.code               # '11-0140-0471-00'
    loc.offset.text        # 'W23 S18'
    loc.confident          # True
    loc.sub_cell.centroid  # (31.5205657, 74.3589474)
```

**Errors:** `OutsideGridError` (point outside the rectangle),
`InvalidIdError` (an ID or code that is not in the grid),
`NotInDatabaseError` (a valid cell missing from a partial build),
`GridMismatchError` (database built with different grid rules) and
`IntegrityError` (database contents do not match the layout).

### 7.2 Command-line tools

| Command | Purpose |
|---|---|
| `pakgrid build [-o FILE] [--quadrant CODE] [--rows START:STOP]` | Generate the database; the options build a partial database for tests or pilots |
| `pakgrid verify [--db FILE] [--full]` | Check a database against the rules and its checksum; `--full` regenerates every record |
| `pakgrid locate LAT LON [--accuracy M]` | Look up a point; prints JSON |
| `pakgrid show CODE` | Print a cell's or sub-cell's details as JSON |
| `pakgrid info` | Print grid and database information |
| `pakgrid export-layout` | Write `viewer/layout.js`, the row and column counts used by the map viewer |

The database path defaults to `pakgrid_v1.sqlite`, or the `PAKGRID_DB`
environment variable.

### 7.3 Map viewer

`viewer/index.html` is a static web page that draws the grid on OpenStreetMap,
for checking the grid visually before it is used in an app:

- the rectangle, with its four corner coordinates, plus the quadrants and midlines
- cells from zoom 11 and sub-cells (numbered 0–24) from zoom 14
- tap a point or use **My location** to see its codes, IDs, boundaries,
  centroids, offset and GPS confidence
- search by code or by `lat, lon`; links such as `#11-0140-0471-00` open at
  that block

It needs no database. It recomputes the grid from `viewer/layout.js` with the
same formulas as the library, and `tests/test_viewer_js.py` checks that the
two agree. See [viewer/README.md](viewer/README.md).

### 7.4 HTTP API (v0.2): 10 endpoints

| Method | Endpoint | Library function |
|---|---|---|
| GET | `/locate?lat=&lon=&accuracy=` | `locate` |
| POST | `/locate` | `locate_many` |
| GET | `/cells/{id}` | `cell` |
| GET | `/sub-cells/{id}` | `sub_cell` |
| GET | `/cells/{id}/children` | `children` |
| GET | `/{id}/neighbours` | `neighbours` |
| GET | `/cells?bbox=south,west,north,east` | `cells_in_area` |
| GET | `/{id}/offset?lat=&lon=` | `offset` |
| GET | `/{id}/point?east=&north=` | `from_offset` |
| GET | `/info` | `info` |

- Every endpoint accepts either an integer ID or a readable code.
- `parent` is not needed as an endpoint, because the parent is part of the ID.
- `direction` runs on the rider's phone (§10).

---

## 8. How a lookup works

### 8.1 `locate(lat, lon, accuracy_m)`, step by step

| Step | What happens | Cost |
|---|---|---|
| 1 | Check the point is inside the rectangle | arithmetic |
| 2 | Quadrant: compare with the midlines | arithmetic |
| 3 | Row = floor((lat − quadrant south) ÷ row height) | arithmetic |
| 4 | Column = floor((lon − quadrant west) ÷ that row's column width), read from the in-memory `rows` table | arithmetic |
| 5 | Sub-cell from the leftover fractions × 5 | arithmetic |
| 6 | Read the cell's packed sub-cell record by primary key | one indexed read |
| 7 | **Containment check** against the stored boundaries; step to a neighbour if needed (§8.2) | arithmetic |
| 8 | **Vector** from the sub-cell's centroid to the point | arithmetic |
| 9 | **Margins** to each edge → confidence and candidates (§9.3) | arithmetic |

Steps 1–5 locate the right record. Steps 6–7 make **the stored database the
final authority**.

### 8.2 Centroid vectors

For a point and a centroid:

```
dE = (point lon − centroid lon) × metres per degree of longitude at that latitude
dN = (point lat − centroid lat) × metres per degree of latitude at that latitude
distance = √(dE² + dN²)        bearing = atan2(dE, dN), clockwise from north
```

Both scale factors come from the WGS 84 ellipsoid at the centroid's latitude.
Over ≤ 1 km they are accurate to millimetres.

**Containment in metres:** the point is inside if
`|dE| ≤ half-width` and `|dN| ≤ half-height`. If not, the **sign of the
vector** gives the neighbour to step to: negative dE means west, positive dN
means north. In testing, about 1 point in 100,000 needed one step, caused by
rounding at an edge.

**Example**, sub-cell `11-0140-0471-00` (200.0 m × 199.8 m):

| Point | Accuracy | Vector from centroid | Nearest edge | Result |
|---|---|---|---|---|
| 31.5204, 74.3587 | 5 m | 23.5 m W, 18.4 m S (30 m at 232°) | 76.5 m (west) | Confident |
| 31.52058, 74.35792 | 8 m | 97.6 m W, 1.6 m N (97.6 m at 271°) | 2.4 m (west) | Not confident; candidate: `11-0140-0470-04`, across the cell's west edge |

### 8.3 Do not use "nearest centroid"

Because rows have different column counts (§3.4), cells in neighbouring rows
are offset. Near a row edge, the closest centroid can belong to the row above
while the point is in the row below. **The arithmetic and containment check
always give the correct block.** The centroid is used only for the vector.

### 8.4 Neighbours

- **Same row:** column − 1 and column + 1.
- **Rows above and below:** the cells whose longitude range overlaps this
  cell's. This is usually 1–2 cells, sometimes 3, and is not simply the same
  column number.
- **At a midline:** continues into the adjacent quadrant, so routing does not
  break at quadrant edges.

### 8.5 Reverse lookup

`sub_cell(id)` and `from_offset(id, east_m, north_m)` turn a stored ID (plus
offset) back into boundaries, a centroid or an exact GPS point.

---

## 9. Handling GPS inaccuracy

### 9.1 The problem

Lookup is always exact: one point gives one block. But **the point itself may
be wrong by several metres**, and near an edge that pushes it into the
neighbouring block.

| Situation | Typical phone GPS error |
|---|---|
| Outdoors, clear sky | 3–5 m |
| Dense streets, near buildings | 10–30 m |
| Indoors | 20–50 m or more |
| Wi-Fi or mobile network location only | 50–500 m |

### 9.2 How often it matters

The share of points close enough to an edge to be affected:

| GPS error | 200 m sub-cell | 1 km cell |
|---|---|---|
| 5 m | ~10% | ~2% |
| 10 m | ~19% | ~4% |
| 30 m | ~51% | ~12% |

The worst case is always **the adjacent block**, at most a few hundred metres
away.

### 9.3 Accuracy-aware `locate()`

The app sends the phone's reported accuracy radius. In Flutter's `geolocator`
package this is `position.accuracy`.

- `east-west margin = half-width − |dE|`, `north-south margin = half-height − |dN|`
- If the smaller margin is at least the accuracy, the result is
  **`confident: true`**.
- Otherwise, the **direction of the small margin identifies exactly which
  neighbours are possible**: usually 1, or 3 near a corner, instead of all 8
  surrounding blocks. These are returned in `candidates`.

### 9.4 Averaging several readings

Take readings over 5–10 seconds:

1. Convert each reading to a vector from the same centroid.
2. Drop outliers.
3. Average the rest, weighting each by 1 ÷ accuracy².

This reduces random jitter, typically from ~8 m to ~3–4 m. It does not remove
a consistent error, such as a signal bouncing off buildings.

### 9.5 App practices

1. **The customer confirms the pin.** GPS only centres the map; the customer
   drags the pin to their door and confirms. This is the single most
   effective fix.
2. **Accuracy gate.** If the accuracy is worse than ~50 m, ask the customer to
   step outside, wait, or place the pin by hand.
3. **Save the address once.** The confirmed pin, its accuracy and its sub-cell
   ID are stored together and reused; they are not recalculated on every order.
4. **Delay changes when tracking riders.** A rider's current cell changes only
   once they are a few metres inside the new cell, to avoid flickering between
   two cells.

---

## 10. Rider navigation

### 10.1 What it does

The rider app shows **distance and direction to the customer**, for example
*"202 m (664 ft) south-east, bear right"*, and updates it every second.

1. When the rider opens the order, the backend sends the target pin, rebuilt
   with `from_offset()` from the stored sub-cell ID and offset.
2. Every second, the phone computes `direction(rider position, target,
   heading)` locally. This is the same vector maths as §8.2, runs in under
   1 µs, and needs no server and no database.

```
distance = √(dE² + dN²)                      metres; × 3.28084 for feet
bearing  = atan2(dE, dN)                     compass direction to the target
turn     = bearing − rider's heading         degrees left (−) or right (+)
```

### 10.2 Examples (target 31.5204° N, 74.3587° E)

| Rider position | Heading | Distance | Direction | Turn | Display |
|---|---|---|---|---|---|
| 31.5215, 74.3570 | East (90°) | 202 m / 664 ft | South-East (127°) | 37° right | ↘ "202 m · bear right" |
| 31.52085, 74.35855 | South (180°) | 52 m / 170 ft | SSE (164°) | 16° left | ↓ "52 m · slightly left" |

### 10.3 Guidance by distance

| Distance | Display |
|---|---|
| > 1 km | Distance, compass direction, block code, and an "Open road directions" button |
| 50 m – 1 km | Rotating arrow and distance, updated every second |
| 15 – 50 m | "Almost there: 30 m, slightly left" |
| < ~15 m | **"You've arrived"**, the address notes, and a button to call the customer |

### 10.4 Reliability on a motorbike

| Problem | Fix |
|---|---|
| The phone's compass is disturbed by the engine and metal frame | While moving (> ~2 m/s), use the GPS direction of travel; use the compass only when stopped |
| The rider's GPS drifts 5–10 m | Smooth recent readings; below ~15 m switch to "arrived" |
| Numbers flicker | Round to 10 m above 100 m, and to 5 m below |
| Units | Metres or feet per rider setting; both by default |

### 10.5 Limit

This is **straight-line** distance and direction. Roads may make the actual
route longer. For long distances, the app hands off to road routing: a Google
Maps link now, the platform's own OpenStreetMap-based routing later. The arrow
is most useful for the final approach (≲ 500 m), especially in lanes.

---

## 11. From request to response

### 11.1 Flow: a customer saves an address

```
 Phone (Flutter)        Dukandaari backend          pakgrid               Grid DB (SQLite)
      │                        │                       │                        │
 1 ── │ lat, lon, accuracy ──► │                       │                        │
      │                   2 ── │ validate → locate() ─►│ 3 arithmetic → ID      │
      │                        │                       │ 4 ── read by ID ──────►│
      │                        │                       │◄──── 248-byte record ──│
      │                        │◄────── result ────────│ 5 check, vector,       │
      │                   6 ── │ save in own DB        │   confidence           │
      │◄────── reply ──────────│                       │                        │
 7    │ "Block 11-0140-0471-00 ✓"                      │                        │
```

**Startup (once):** `Grid.open()` reads `meta`, checks the version and loads the
`rows` table into memory.

**Request:**
```json
POST /addresses
{ "lat": 31.5204, "lon": 74.3587, "accuracy_m": 5, "notes": "House 12, gali 3" }
```

**Grid result** (actual output of `Location.to_dict()`):
```json
{
  "grid_version": 1,
  "point": [31.5204, 74.3587],
  "sub_cell": {
    "id": 105265888,
    "code": "11-0140-0471-00",
    "cell_id": 3289559,
    "cell_code": "11-0140-0471",
    "index": 0,
    "centroid": [31.5205657, 74.3589474],
    "bounds": { "south": 31.5196645, "west": 74.3578947,
                "north": 31.5214668, "east": 74.36 }
  },
  "cell": { "id": 3289559, "code": "11-0140-0471" },
  "offset": { "east_m": -23.5, "north_m": -18.4, "distance_m": 29.8,
              "bearing_deg": 232.0, "text": "W23 S18" },
  "accuracy_m": 5.0,
  "confident": true,
  "edge_distance_m": 76.5,
  "candidates": []
}
```

### 11.2 Where the time goes

| Part | Time |
|---|---|
| Phone ↔ server network | ~50–300 ms |
| Dukandaari backend and its own database | ~1–10 ms |
| **Grid lookup** | **~0.02 ms** |

### 11.3 Errors

| Case | Response |
|---|---|
| Invalid numbers | `400`: invalid latitude or longitude |
| Outside the rectangle | `422`: point outside the grid |
| Accuracy worse than ~50 m | Result returned; the app asks the customer to adjust the pin |
| Close to an edge | `confident: false` with `candidates` |

### 11.4 v0.2: through the HTTP API

The backend calls `GET /locate?lat=…&lon=…&accuracy=…` on the grid service
instead of the local library. The JSON is the same. This adds ~1–5 ms within
the same data centre.

---

## 12. Performance

### 12.1 Measured on the full v0.1 database

**Test setup:**

- the full grid: 2,498,422 cells and 62,460,550 sub-cells in a 781 MB
  SQLite file
- 200,000 random points across the whole rectangle
- Python 3.13 on one CPU core of a cloud machine, with no tuning

| Operation | Per call | Per second |
|---|---|---|
| Layout only (point → IDs, no database) | 1.9 µs | ~520,000 |
| `cell(id)` | 9–12 µs | ~85,000–110,000 |
| `sub_cell(id)` | 15 µs | ~68,000 |
| `neighbours(cell id)` | 18 µs | ~56,000 |
| `locate(lat, lon)` | 22 µs | ~46,000 |
| `locate(lat, lon, accuracy_m=8)`, including candidates | 30 µs | ~33,000 |

**Build and verify:**

| | Result |
|---|---|
| `pakgrid build` (full grid) | 26 s, 781 MB, ~51 MB peak memory |
| `pakgrid verify --full` | 24 s; regenerates every record and compares |
| Content SHA-256 (grid v1) | `55ea85531dedbcaa44d9d384276e2468b16d411766d798215c0dcc5c5202e1ff` |
| `Grid.open()` | ~9 ms |
| Memory while serving lookups | ~4 MB above the Python interpreter |

### 12.2 In practice

| Scenario | Time |
|---|---|
| One address saved | ~0.02 ms |
| 100,000 orders in a day | ~2 s of total work |
| Re-assigning 1 million addresses | ~25 s |
| On a phone, offline (layout only) | a few µs |

### 12.3 Caveats

- The first lookups after a restart may take ~1 ms while the operating system
  caches the file.
- If ever needed, multiple processes, an in-memory copy of the database, or a
  compiled core (Rust or C) would each give 5–50× more speed.

---

## 13. Using it in Dukandaari

| What | Where it lives |
|---|---|
| Grid catalogue (cells, sub-cells, centroids) | Grid database (`pakgrid_v1.sqlite`), read-only |
| Customer, shop and order records | **Dukandaari's own database** |
| Personal data (names, phone numbers, CNIC) | **Dukandaari's own database only**, protected there. Never in the grid |
| Per address | Sub-cell ID (4 bytes), exact pin, accuracy, offset, text notes |

**Uses:**

- **Addresses:** the customer drops and confirms a pin; `locate()` gives the
  sub-cell ID. Shops are registered the same way.
- **Order batching:** group orders by cell ID (`sub_id >> 5`).
- **Rider zones and shop delivery areas:** sets of cell IDs (`cells_in_area`,
  `neighbours`).
- **Delivery fees and time estimates:** from the distance between cells or
  centroids.
- **Rider navigation:** block-level guidance, then distance and direction to
  the exact pin (§10).
- **Offline use (optional):** a Dart port of `layout`, `ids` and `navigate`
  with the ~100 KB `rows` table lets the Flutter app work out IDs and
  directions without internet.

---

## 14. Validation and testing

| Test | Pass condition |
|---|---|
| No gaps or overlaps | Cell areas in each quadrant add up to the quadrant's ellipsoidal area; total ≈ 2,497,710 km² |
| Round trip | For millions of random points, the block returned by `locate()` contains the point |
| Uniqueness | Every stored ID is distinct and decodes back to its own quadrant, row, column and sub-cell |
| Size limits | Cell heights and widths within ±1% of target (currently 999.0–1,000.7 m) |
| Centroids | Each centroid lies inside its block and matches an independent `pyproj` calculation |
| Edges | Points exactly on boundaries follow the edge rule (§3.6) |
| Neighbours | Neighbour lists are symmetric (if A neighbours B, B neighbours A), including across quadrants |
| Determinism | Two builds produce byte-identical files and the same checksum |
| GPS noise | Simulated noise: the true block is always the result or one of its `candidates` |

---

## 15. Roadmap

| Version | Deliverable |
|---|---|
| **v0.1 — Grid core** ✅ | Python library, full database (option B), CLI, tests, documentation (implemented) |
| **v0.2 — Grid API** | HTTP service with the 10 endpoints in §7.4 |
| **v0.3 — Dukandaari pilot** | One city (Lahore or Karachi): addresses, batching, rider navigation |
| **v1.0 — Public release** | Frozen grid v1, Pakistan border flag, published database, documentation |
| **Later** | Map website with grid overlay (MapLibre + OpenStreetMap), search, road routing, Flutter and JavaScript SDKs |

---

## 16. Open decisions

| Decision | Current proposal |
|---|---|
| Final outer boundaries | **Decided:** east edge moved from 77.0° E to 78.0° E so all of Baltistan (up to the Karakoram Pass, ~77.8° E) is inside; other edges confirmed. Fixed from here on, because changing them changes every ID |
| Boundary dataset for the Pakistan border filter | To be chosen |
| Open-source licence | MIT or Apache-2.0 |
| Readable code format | `QQ-RRRR-CCCC-SS`, plus an optional offset `W23 S18` |
| Package and project name | `pakgrid` (working name) |
| Accuracy threshold for accepting a pin | ~50 m |

---

## 17. Changes from the initial README spec

| README spec | This design | Reason |
|---|---|---|
| Sinusoidal projection | Rows and columns measured directly on the WGS 84 ellipsoid; boundaries are latitude/longitude lines | Every boundary and centroid is a GPS coordinate, with no projection to convert |
| One origin per quadrant, fixed 1 km steps | Equal bands per quadrant, column count per row | Cells fill each quadrant exactly, with no partial cells |
| `cell_id = (X << 10) \| Y` as `SMALLINT` (16-bit) | 22-bit cell ID / 27-bit sub-cell ID in a 32-bit `INT` | Two 10-bit values need 20 bits, which overflows a 16-bit `SMALLINT` |
| CNIC linked in the grid database | Personal data kept only in the app's own database | The grid is open and public, and must hold no personal data |
| Sub-cell ID `Sub_X × 5 + Sub_Y` | `sub_row × 5 + sub_col` (0–24) | Same idea, with a fixed numbering order (§3.5) |

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **WGS 84** | The global coordinate system and Earth model used by GPS |
| **Ellipsoid** | The slightly flattened sphere used to model the Earth's shape |
| **Quadrant** | One of the 4 parts of the rectangle, split at the midlines |
| **Cell** | A ~1 km × 1 km block inside a quadrant |
| **Sub-cell** | A ~200 m × 200 m block; 25 per cell |
| **Centroid** | The area-weighted centre of a cell or sub-cell; a fixed GPS point |
| **Offset / vector** | Metres east and north from a centroid to a point |
| **Bearing** | Direction measured clockwise from north, in degrees |
| **Margin** | Distance from a point to the nearest edge of its block |
| **Catalogue** | The pre-built set of all cells and sub-cells in the database |
| **µs** | Microsecond: one millionth of a second |
