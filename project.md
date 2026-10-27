# Pakistan National Grid — Proposal (v0.1, minimum starting point)

> Status: draft for discussion. Scope will be refined as the project develops.

## 1. Vision

An **open-source national grid for Pakistan**: every location in the country
belongs to exactly one fixed, numbered grid block. The grid is a standalone
system that any app can use. **Dukandaari is its first user**, relying on it for
delivery logistics: grid addresses for customers and shops, batching orders by
area, rider zones, and delivery fees.

Later phases can add a public API, a map website, search and routing. This
document covers only the **minimum starting point**: the grid itself.

## 2. Minimum scope

| # | Requirement | How it is met |
|---|-------------|---------------|
| 1 | Divide Pakistan into quadrants | 4 quadrants split at the national midlines (§3) |
| 2 | Cells inside each quadrant | ~1 km × 1 km cells (§4) |
| 3 | Sub-cells inside each cell | 5 × 5 = 25 sub-cells of ~200 m × 200 m (§4) |
| 4 | Sizes may flex to fill the quadrant | Cell sizes are stretched slightly so each row fills the quadrant exactly, leaving no gaps or partial cells (§4) |
| 5 | Every cell unique within its quadrant | Unique (row, column) pair, packed into one integer (§5) |
| 6 | Every sub-cell unique within its cell | Index 0–24 (§5) |
| 7 | Centroids for every cell and sub-cell | Computed on the WGS 84 ellipsoid (§4.4) |
| 8 | Corrects for Earth's curvature | All sizes measured on the WGS 84 ellipsoid, not a flat plane (§4) |
| 9 | Linked to real GPS points | Every boundary is a line of latitude or longitude, so it *is* a GPS coordinate (§4) |
| 10 | Own static database as single source of truth | One read-only SQLite file, versioned (§6) |
| 11 | No front end for now | Python library only |
| 12 | Python compatible | Python package, standard library plus `pyproj` (§7) |
| 13 | Most efficient links between levels for RAM and disk | The parent is encoded inside the child's ID, so no link tables are needed (§5, §6) |

**Out of scope for now:** website, public API, search, routing, personal data
(for example CNIC), and clipping the grid to Pakistan's exact border.

## 3. Quadrants

The grid covers a latitude/longitude bounding box around Pakistan:

| | Latitude | Longitude |
|---|---|---|
| South-west corner | 23.436° N | 60.800° E |
| North-east corner | 37.080° N | 77.000° E |
| Midlines (splits) | 30.258° N | 68.900° E |

| Code | Quadrant | Example region | Rows | Cells |
|------|----------|----------------|------|-------|
| `00` | South-West | Gwadar / Makran coast | 756 | 608,259 |
| `01` | North-West | Quetta / Afghan border | 757 | 568,326 |
| `10` | South-East | Karachi / Sindh | 756 | 608,259 |
| `11` | North-East | Lahore / Islamabad / GB | 757 | 568,326 |
| | **Total** | | | **2,353,170 cells · 58,829,250 sub-cells** |

The first bit of the code means east (1) or west (0). The second bit means
north (1) or south (0).

> The east boundary (77.0° E) should be checked against the boundary dataset
> before the grid is frozen. Changing any boundary changes every ID, so it must
> be settled before v1.

## 4. Cells and sub-cells

### 4.1 Rows (north–south)

1. Measure the quadrant's height along a meridian on the WGS 84 ellipsoid
   (≈ 756 km).
2. Number of rows = that height ÷ 1,000 m, rounded to the nearest whole number.
3. Split the quadrant into that many **equal latitude bands**.

Result: every row is between **999.0 m and 1,000.3 m** tall, and the rows exactly
fill the quadrant from south to north.

### 4.2 Columns (east–west), corrected for curvature

East–west distance shrinks further north, because lines of longitude converge.
One degree of longitude is ≈ 102 km at Karachi and ≈ 89 km in Gilgit-Baltistan.
So **each row gets its own number of columns**:

1. Measure the quadrant's width along that row's middle latitude on the ellipsoid.
2. Number of columns = width ÷ 1,000 m, rounded to the nearest whole number.
3. Split the row into that many **equal longitude spans**.

Result: every cell is between **999.3 m and 1,000.7 m** wide, and every row fills
the quadrant exactly from west to east. Northern rows have fewer columns than
southern rows (at most 828 columns).

### 4.3 Sub-cells

Each cell is split into a **5 × 5** grid of equal latitude/longitude steps,
giving 25 sub-cells of about 200 m × 200 m. The pattern is the same in every
cell.

### 4.4 Boundaries and centroids

- Every boundary is a line of constant latitude or longitude, so cell and
  sub-cell corners are exact GPS coordinates. They need no projection to be
  converted back.
- **Centroid** = the area-weighted centre of the cell on the ellipsoid. For a
  1 km cell this sits less than 1 cm south of the plain midpoint, but it is
  computed properly so the grid is correct by definition.
- **Edge rule:** a point exactly on a line belongs to the cell to its north or
  east. Points on the outer north and east edges of the grid belong to the
  last row or column. This guarantees every point maps to exactly one cell.

## 5. IDs: linking quadrant → cell → sub-cell

No link tables are needed, because **the parent is encoded inside the child's
integer ID**:

```
Cell key      (22 bits):  [quadrant:2][row:10][col:10]
Sub-cell key  (27 bits):  [quadrant:2][row:10][col:10][sub:5]
```

- Rows (max 757) and columns (max 828) each fit in 10 bits (max 1,023).
- Sub-cell index = `sub_row × 5 + sub_col` (0–24) fits in 5 bits.
- Both keys fit in a standard **32-bit integer** (`INTEGER` / `INT`).

Getting from a child to its parent is one bit shift. No lookup is needed.

```python
cell_key = sub_key >> 5
quadrant = cell_key >> 20
row      = (cell_key >> 10) & 0x3FF
col      = cell_key & 0x3FF
sub      = sub_key & 0x1F
```

**Readable form**, for sharing on a call or WhatsApp:
`QQ-RRRR-CCCC-SS`, e.g. `11-0412-0388-17`.

> This replaces the `(X << 10) | Y` stored as `SMALLINT` in the README.
> Two 10-bit values need 20 bits, which does not fit in a 16-bit `SMALLINT`.

## 6. Static database (single source of truth)

A single read-only **SQLite** file, `pakgrid_v1.sqlite`. SQLite is part of
Python's standard library and needs no server. It is generated once by the
library and then never edited. Any change to the grid means a new version file.

| Table | Rows | Contents |
|-------|------|----------|
| `meta` | 1 | grid version, datum (WGS 84), bounding box, midlines, target sizes |
| `quadrants` | 4 | code, bounds, number of rows, number of cells |
| `rows` | ~3,026 | quadrant, row, south/north latitude, number of columns, column width in degrees |
| `cells` | 2,353,170 | cell key (primary key), centroid latitude/longitude |
| `sub_cells` | 58,829,250 | *optional:* sub-cell key, centroid latitude/longitude |

Coordinates are stored as **integers in units of 10⁻⁷ degree** (about 1.1 cm),
not as decimal text. This is smaller, exact and faster to compare.

### Storage footprint (measured with SQLite)

| Option | Disk |
|--------|------|
| Cells with centroid only; boundaries derived from `rows` | **≈ 45 MB** |
| Cells with centroid and all four boundaries stored | ≈ 93 MB |
| Adding a materialised `sub_cells` table | ≈ +1.1 GB |

**Recommendation:** store quadrants, rows and cells (≈ 45 MB), and define
sub-cells by rule from their parent cell. A sub-cell's boundary and centroid
are pure arithmetic on the parent cell's values, so storing all 58.8 million of
them adds ≈ 1.1 GB without adding any information. The library can still
materialise the `sub_cells` table with one build flag if a consumer needs it.

### RAM footprint

Converting a GPS point to a cell needs only the `rows` table (~3,000 rows,
< 100 KB in memory):

1. Pick the quadrant by comparing with the midlines.
2. `row = floor((lat − quadrant_south) / row_height)`
3. `col = floor((lon − quadrant_west) / that row's column width)`
4. The sub-cell is found the same way inside the cell.

Every lookup takes the same amount of work (O(1)). There is no spatial index
and no PostGIS.

## 7. Python library (planned interface)

Package name: `pakgrid`. It requires Python ≥ 3.10 and `pyproj` (for ellipsoid
measurements).

```python
import pakgrid

key = pakgrid.locate(31.5204, 74.3587)          # GPS → sub-cell key (Lahore)
pakgrid.to_text(key)                            # '11-0140-0518-01'
pakgrid.parent(key)                             # sub-cell key → cell key
pakgrid.children(cell_key)                      # cell key → 25 sub-cell keys
pakgrid.centroid(key)                           # (lat, lon)
pakgrid.bounds(key)                             # (south, west, north, east)
pakgrid.neighbours(cell_key)                    # adjacent cells, across quadrant lines too
pakgrid.build_database("pakgrid_v1.sqlite", materialise_sub_cells=False)
```

(The Lahore code above is computed from the rules in §4; final values depend on the frozen boundaries.)

## 8. Validation (tests before v1 is frozen)

- **No gaps or overlaps:** the cell areas in each quadrant add up to the
  quadrant's area on the ellipsoid.
- **Round trip:** for a large random sample of GPS points, the cell returned
  by `locate()` contains the point.
- **Uniqueness:** every key in the database is distinct and decodes back to
  its own row, column and sub-cell.
- **Size limits:** cell heights and widths stay within ±1% of the target
  (currently 999.0–1,000.7 m).
- **Determinism:** building the database twice produces identical files.

## 9. How Dukandaari uses it

- Dukandaari keeps customers, orders and any personal data (for example CNIC)
  in **its own database**. Next to each address it stores only the sub-cell key
  (4 bytes) plus the exact pin and text notes.
- **Order batching:** group by `cell_key` (`sub_key >> 5`).
- **Rider zones and shop delivery areas** are sets of cell keys.
- **Final drop-off:** the sub-cell's centroid gets the rider to the right block,
  and the exact pin and notes get them to the door.

## 10. Roadmap

1. **v0.1 — Grid core** *(this document)*: Python library, SQLite database,
   tests.
2. **v0.2 — Grid API**: HTTP service exposing `locate`, `centroid`, `bounds`
   and `neighbours`.
3. **v0.3 — Dukandaari pilot**: one city, for example Lahore or Karachi.
4. **v1.0 — Public open-source release**: frozen grid version, documentation,
   and a flag marking cells inside or outside Pakistan's border.
5. **Later**: map website with grid overlay (MapLibre with OpenStreetMap data),
   search and routing, and SDKs for Flutter and JavaScript.

## 11. Open decisions

- Confirm the outer boundaries (especially the east edge, currently 77.0° E).
- Store sub-cells by rule (recommended) or materialise all of them?
- Licence for the open-source release (MIT or Apache-2.0 suggested).
- Readable code format: is `QQ-RRRR-CCCC-SS` acceptable?
