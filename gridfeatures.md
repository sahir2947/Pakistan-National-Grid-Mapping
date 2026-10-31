# Grid features: to-do list

Features to add to `pakgrid` before the first release (v0.1.0). Each feature
is built as its own tested pull request, and its status is updated here.

**Status:** ⬜ to do · 🔨 in progress · ✅ done

## How pakgrid is structured

Cells, sub-cells and quadrants are the foundation. Everything else is a layer
of tags on their IDs, and every place name that exists, or will exist, is
tagged to pakgrid cell and sub-cell IDs. Apps use those tags.

```
Big rectangle
  Quadrants
    Cells and sub-cells          ← foundation: IDs never change
      Pakistan map cut-out       ← layers: tags on cell and sub-cell IDs,
        Province                    each versioned and updatable on its own
          Districts
            Tehsils
              Localities
```

## Requested

Features asked for by the project owner. Add new requests here.

| # | Feature | Status | Notes |
|---|---|---|---|
| R1 | **Place names for every cell and sub-cell** | ✅ | **Layer 1 ✅:** province, district and tehsil on every cell and sub-cell, now from UN OCHA with the official hierarchy (577 tehsils, 160 districts). **Layer 2 ✅:** localities: 32,512 cities, towns, villages, neighbourhoods and residential areas from OpenStreetMap (HOT export), with Urdu names where mapped, "in" or "near X (1.2 km SE)" (places version 2; project.md §5.7, `pakgrid/data/PLACES_SOURCE.md`) |
| R2 | **User data feeding: localities drawn by users** | ✅ | An authorised user draws a polygon around cells and sub-cells (or lists them) and names a locality. The library stores it in a separate user places file, located from one or more known localities as distance and direction vectors (`500 m SW of Askari 2`). Lookups show it inside the polygon. Every change is kept with its author. The viewer's **Draw** tool previews it (project.md §5.8) |
| R3 | **Radius search, cell-to-cell distance, show/hide grid** | ✅ | `grid.cells_within(lat, lon, radius_m)`: the cells or sub-cells within a radius, nearest first (centre or any part; optionally only in Pakistan). `grid.distance(a, b)`: distance, bearing, compass and east/north metres between two blocks' centres. Both are also CLI commands (`within`, `distance`) and in the viewer (**Around this point**, **Measure**), which also gets a **Grid** toggle to show or hide the grid (project.md §7.5) |
| R4 | **Custom zones** | ✅ | `grid.zone(polygon= / circle= / cells= / place=)`: a delivery area or other region as a set of sub-cells. `zone.contains(lat, lon)` checks a pin in ~3 µs, and `ZoneIndex` finds which of many zones hold a pin. Zones combine with union, intersection and difference, and store as compact JSON (`to_dict` / `from_dict`). Also `pakgrid zone`, and a **Zone** tool in the viewer (project.md §7.6) |
| R5 | **GeoJSON for backend and frontend** | ✅ | Blocks, sets of blocks, zones and places as GeoJSON for any map (Google Maps, Mapbox, Leaflet, flutter_map). Sets become exact merged outlines with holes, valid and canonical. Available as `grid.geojson`, `cells_geojson`, `place_geojson` and `zone.to_geojson`, as `pakgrid geojson`, and as viewer downloads; the viewer's JavaScript makes identical output (project.md §7.7) |
| R6 | **Nearest lookup** | ✅ | The k nearest points to a pin, exact and nearest first. Three sources: points in memory (`PointIndex`: riders that move, shop lists), points in the app's own database by cell ID (`grid.nearest(lat, lon, fetch)`, reading only the cells around the pin), or localities (`grid.nearest_places`). Also `pakgrid nearest`, and "Nearest places" in the viewer (project.md §7.8) |
| R7 | **Custom blocks** | ✅ | Users make their own named, coded blocks by clicking cells (or sub-cells) on the map or drawing a polygon; blocks in a set never overlap. Each block is described: cell codes, size (sub-cells, cells, area), province, district, tehsils, localities, city (in or near), and the distance and direction from the nearest place when it is in open country, or from any place you name. Stored anywhere as JSON or GeoJSON. Available as `grid.block_set`, `describe_zone`, `pakgrid blocks`, and a **Blocks** tool in the viewer (project.md §7.9) |

## Proposed

Suggested features for delivery and logistics apps, built on what the grid
already has. Move one to **Requested** to start it.

| # | Feature | What it gives an app | Status |
|---|---|---|---|
| P1 | **Radius search** | All cells or sub-cells within X km of a point: "shops that deliver here", "riders near this order" | ✅ done as R3 |
| P2 | **Custom zones** | A delivery area drawn as a polygon → the exact cells inside it, and a fast "is this pin in my zone?" check | ✅ done as R4 |
| P3 | **Province / district / city per cell** | Each cell tagged with its administrative area, for reports, pricing and zone setup | ✅ done as R1 |
| P4 | **Larger blocks** | Groups of cells, e.g. 5 km or 10 km squares, for heat maps and order counts | ✅ done as R7 (custom blocks, chosen instead of fixed squares) |
| P5 | **GeoJSON export** | Any cell, sub-cell or zone as GeoJSON, so apps can draw it on Google Maps, Mapbox or Leaflet | ✅ done as R5 |
| P6 | **Cell-to-cell distances** | Distance and direction between any two cells' centres, for batching and fee estimates | ✅ done as R3 |
| P7 | **Nearest point lookup** | Given a list of shops or riders stored by cell, find the nearest ones to a pin quickly | ✅ done as R6 |

## Done

Features already in the library, for reference.

| Feature | Where |
|---|---|
| Grid of 4 quadrants, ~1 km cells and ~200 m sub-cells, with IDs, centroids and boundaries | `pakgrid` core, project.md §3–§5 |
| Locate a GPS point, with accuracy-aware confidence | `Grid.locate`, project.md §8–§9 |
| Pakistan border flags and the Pakistan edition of the database | project.md §5.6 |
| Grid addresses (`11-0140-0471-00 W23 S18`) | project.md §4.4 |
| Rider navigation engine (Python and JavaScript) | project.md §10.6 |
| Map viewer | `viewer/` |
| Django backend guide, thread-safe `Grid`, official checksums | `docs/django.md` |
| Navigation rules and conformance test file for ports | `conformance/` |
| Places: province, district, tehsil and locality tags (R1, places version 2) | `pakgrid/places.py`, project.md §5.7 |
| User places: localities drawn by users, with references and history (R2) | `pakgrid/userplaces.py`, project.md §5.8 |
| Radius search, cell-to-cell distances, show/hide grid (R3) | `Grid.cells_within`, `Grid.distance`, project.md §7.5 |
| Custom zones (R4) | `Grid.zone`, `pakgrid/zones.py`, project.md §7.6 |
| GeoJSON (R5) | `pakgrid/geojson.py`, project.md §7.7 |
| Nearest lookup (R6) | `pakgrid/nearest.py`, project.md §7.8 |
| Custom blocks (R7) | `pakgrid/blocks.py`, `pakgrid/describe.py`, project.md §7.9 |
