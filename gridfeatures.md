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
| R1 | **Place names for every cell and sub-cell** | 🔨 | **Layer 1 ✅:** province, district and tehsil on every cell and sub-cell (project.md §5.7; 79 tehsils have an uncertain district with the current data, see `pakgrid/data/PLACES_SOURCE.md`). **Layer 2 ⬜:** localities (cities, towns, villages, neighbourhoods) from OpenStreetMap; needs `download.geofabrik.de` allowed in the environment's network settings. **Later:** better district data from UN OCHA (needs `data.humdata.org`) |

## Proposed

Suggested features for delivery and logistics apps, built on what the grid
already has. Move one to **Requested** to start it.

| # | Feature | What it gives an app | Status |
|---|---|---|---|
| P1 | **Radius search** | All cells or sub-cells within X km of a point: "shops that deliver here", "riders near this order" | ⬜ |
| P2 | **Custom zones** | A delivery area drawn as a polygon → the exact cells inside it, and a fast "is this pin in my zone?" check | ⬜ |
| P3 | **Province / district / city per cell** | Each cell tagged with its administrative area (from the same geoBoundaries source as the border), for reports, pricing and zone setup | ⬜ |
| P4 | **Larger blocks** | Groups of cells, e.g. 5 km or 10 km squares, for heat maps and order counts | ⬜ |
| P5 | **GeoJSON export** | Any cell, sub-cell or zone as GeoJSON, so apps can draw it on Google Maps, Mapbox or Leaflet | ⬜ |
| P6 | **Cell-to-cell distances** | Distance and direction between any two cells' centres, for batching and fee estimates | ⬜ |
| P7 | **Nearest point lookup** | Given a list of shops or riders stored by cell, find the nearest ones to a pin quickly | ⬜ |

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
| Places layer 1: province, district and tehsil tags (R1) | `pakgrid/places.py`, project.md §5.7 |
