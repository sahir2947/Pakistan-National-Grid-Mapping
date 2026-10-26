# Pakistan National Grid Mapping — Project Overview

## Purpose

Develop a **4-quadrant grid** that projects a rectangular (Cartesian) plane onto
Pakistan's national territory. Any location in the country can then be given a
simple grid reference (quadrant + easting + northing) instead of latitude and
longitude.

## Goals

1. Define a single projected coordinate system covering all of Pakistan.
2. Place a grid origin near the centre of the country and split the plane into
   four quadrants (NE, NW, SW, SE).
3. Lay a regular grid of square cells (e.g. 100 km, 10 km and 1 km) over the plane.
4. Provide tools to convert between geographic coordinates (WGS 84 lat/lon) and
   grid references, in both directions.
5. Publish maps and data layers showing the grid over Pakistan's boundary.

## Study Area

Pakistan's approximate geographic extent:

| Bound | Value |
|-------|-------|
| South | ~23.6° N |
| North | ~37.1° N |
| West  | ~60.9° E |
| East  | ~77.8° E |

The country spans UTM zones 41N–43N, so a single UTM zone cannot cover it
without large distortion at the edges. A custom national projection is needed.

## Proposed Approach

### 1. Projection

- **Datum:** WGS 84 (EPSG:4326) as the geographic reference.
- **Projection:** Lambert Conformal Conic (LCC) with two standard parallels
  chosen to minimise scale error over the country's north–south extent
  (candidate values around 27° N and 34° N). Transverse Mercator centred on
  ~69.5° E is the alternative to evaluate.
- **Units:** metres.

### 2. Grid origin and quadrants

- **Origin (0, 0):** near the centre of the national extent
  (candidate ~30.4° N, 69.4° E; final choice to be confirmed).
- **Axes:** X (easting) positive to the east, Y (northing) positive to the north.
- **Quadrants:**

  | Quadrant | X | Y |
  |----------|---|---|
  | I  — NE  | + | + |
  | II — NW  | − | + |
  | III — SW | − | − |
  | IV — SE  | + | − |

- **Grid references** take the form `Quadrant-Easting-Northing`, with easting and
  northing given as absolute distances from the origin, e.g. `NE-123450-067890`.

### 3. Grid cells

- Hierarchical square cells: 100 km → 10 km → 1 km.
- Each cell gets a unique ID derived from its quadrant and row/column index.

### 4. Implementation (planned)

- **Language:** Python.
- **Libraries:** `pyproj` (projection maths), `shapely` / `geopandas` (geometry and
  grid generation), `matplotlib` or QGIS (map output).
- **Outputs:**
  - Projection definition (PROJ string / WKT).
  - Grid layers as GeoJSON / GeoPackage.
  - Conversion functions: `latlon_to_grid()` and `grid_to_latlon()`.
  - Reference maps of the grid over Pakistan.

## Planned Repository Layout

```
.
├── README.md
├── project.md          # this file
├── data/               # boundary data and generated grid layers
├── src/                # projection, grid generation and conversion code
├── notebooks/          # exploration and validation
├── maps/               # rendered map outputs
└── tests/              # conversion accuracy tests
```

## Milestones

- [ ] Choose and document projection parameters
- [ ] Fix grid origin and quadrant convention
- [ ] Obtain Pakistan boundary data
- [ ] Generate grid cell layers
- [ ] Implement lat/lon ↔ grid conversion
- [ ] Validate accuracy (round-trip error, scale distortion at the borders)
- [ ] Produce reference maps
- [ ] Write user documentation

## Open Questions

- Final origin point: geometric centre of the extent, or a named landmark?
- LCC or Transverse Mercator: which gives lower distortion for this purpose?
- Should grid references avoid negative numbers by using a false easting/northing
  in addition to quadrant labels?
- Which boundary dataset to use as the authoritative national outline?
