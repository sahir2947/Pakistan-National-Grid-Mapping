# Grid features: to-do list

Features to add to `pakgrid` before the first release (v0.1.0). Each feature
is built as its own tested pull request, and its status is updated here.

**Status:** ⬜ to do · 🔨 in progress · ✅ done

## Requested

Features asked for by the project owner. Add new requests here.

| # | Feature | Status | Notes |
|---|---|---|---|
| | *(none yet)* | | |

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
