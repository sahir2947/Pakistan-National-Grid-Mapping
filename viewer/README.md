# Grid viewer

A map page that shows the Pakistan National Grid on OpenStreetMap:

- the bounding rectangle, with the GPS coordinates of its four corners
- the four quadrants (`00`, `01`, `10`, `11`) and the two midlines
- ~1 km cells from zoom 11, and ~200 m sub-cells (numbered 0–24) from zoom 14
- Pakistan's border (green), with cells coloured inside (blue), on the border
  (purple) or outside (grey, dashed); tick **Pakistan only** to hide the rest

**Places:** every cell and sub-cell is tagged with its locality (city, town,
village, neighbourhood, with its Urdu name where mapped), tehsil, district and
province (the places layer, project.md §5.7). The details panel shows them,
and **Places** colours each block by its tehsil (cells from zoom 11, sub-cells
from zoom 14, where tehsil lines can be seen stepping through cells). Search
accepts a place name (`Taxila`, `Kot Addu`, `DHA Phase VI`, or in Urdu) or key
(`punjab/lahore`), zooms to
it and outlines its exact area: every sub-cell tagged with it (so the outline
steps in ~200 m sub-cells), never just the box around it. Links such as
`index.html#place=punjab/lahore` open straight at a place.
Where a block is just outside a village or town, it shows as
`near Kharochan (1.2 km S)`. Sources and coverage: `pakgrid/data/PLACES_SOURCE.md`.

**Grid** shows or hides the rectangle, quadrants, cells and sub-cells; the
choice is remembered in this browser. With the grid hidden, **Places** still
colours blocks by tehsil.

**Around this point** (in a point's details): enter a radius in metres,
choose cells or sub-cells, and press **Find**. The blocks whose centre is
within it are shaded (tick *touching* for those with any part within it),
with the count and the nearest ten (project.md §7.5).

**Measure:** press it, then tap two blocks (cells when zoomed out, sub-cells
from zoom 14), or use **Measure from here** in a block's details, or search
`11-0140-0471 to 11-0150-0480`. It shows the distance and direction between
their centres, and the east/north metres.

**Zone** makes a custom zone, such as a delivery area (project.md §7.6):

1. Tap its corners and press **Done**. The sub-cells whose centre is inside
   it are shaded green (with **Pakistan only** ticked, only those in
   Pakistan), with the count and the area.
2. Tap anywhere to check a pin: green is in the zone, red is not.
3. The panel gives the zone's data (`Zone.to_dict()`) to store. You can also
   paste a stored zone's data to see it on the map.

**Download GeoJSON** (project.md §7.7) saves what is shown as a GeoJSON file
for any map or GIS tool: a place's exact outline (in its panel), a zone, a
block and its cell (in a block's details), or the blocks of a radius
search. The files are the same as the library makes.

**Draw** previews a new user locality (project.md §5.8). Tap the corners of
a polygon and press **Done** to see:

- the sub-cells it would tag (those whose centre is inside it);
- its tehsil and centre;
- its distance and direction from the three nearest known localities;
- the `user.add(...)` call that adds it with the library.

Nothing is stored in the viewer.

Tap anywhere to see that point's cell and sub-cell: codes, IDs, boundaries,
centroids, the point's grid address (for example `11-0140-0471-00 W23 S18`), and
whether the point, its cell and its sub-cell are in Pakistan. A blue arrow runs
from the sub-cell's centre to the point, and its direction is given as a compass
bearing, clockwise from north (0° N, 90° E, 180° S, 270° W), for example
`232° SW (South-West)`.
**My location** does the same for your phone's GPS position and shows whether
the reading is accurate enough to be sure of the block.

**Navigate here** (in the details panel) guides you from your position to that
point, the way a rider would be guided to a customer's pin:

- **Live GPS** follows your phone's position; **Simulate** lets you drag a
  rider marker around the map to test without moving.
- The panel shows the straight-line distance (metres or feet), the compass
  direction (for example `NE · 44° (North-East)`), and, once your heading is
  known, which way to turn (`bear left`, `slightly right`, …). Under 15 m it
  says **You've arrived**.
- The big arrow points at the pin. When your heading is known it turns with
  you (up = the way you're heading); otherwise it is drawn north-up.
- Heading comes from the GPS direction of travel while moving, else the
  phone's compass (iPhones ask permission once), else it is unknown.
- Links such as `index.html#nav=31.5204,74.3587` or
  `index.html#nav=11-0140-0471-00 W23 S18` open straight into navigation to
  that pin.
- The screen is only drawing: all guidance comes from
  `PakGrid.NavigationSession`, the same engine as Python's
  `pakgrid.NavigationSession` (see project.md §10.6). Use it the same way to
  build your own screen.

Search accepts a grid address (`11-0140-0471-00 W23 S18`), a code
(`11-0140-0471-00` or `11-0140-0471`) or `lat, lon`.
The address bar updates as you select, so a link such as
`index.html#11-0140-0471-00` opens straight at that block.

## Running it

It is a static page with no build step and no database:

```bash
python3 -m http.server -d viewer 8000    # then open http://localhost:8000
```

To use it on a phone, publish the repository with **GitHub Pages**
(Settings → Pages → deploy from the `main` branch). The viewer is then at
`https://<user>.github.io/<repository>/viewer/`.

## How it stays correct

- `layout.js` holds the exact row and column counts, `boundary.js` holds
  Pakistan's border, and `places.js` holds the places and every sub-cell's
  tehsil and locality (as runs along each row of sub-cells; 9.4 MB, ~2.8 MB
  compressed); all three are generated by
  the Python library with `pakgrid export-layout`.
- `pakgrid.js` recomputes every edge, centroid, border flag and grid address
  with the same formulas as the library, and carries the same navigation engine.
- `tests/test_viewer_js.py` runs the JavaScript in node and checks it against
  the Python library on thousands of random points and on exact edges, and
  plays the same scripted rides through both navigation engines, and checks
  that the viewer's place tags equal the library's for thousands of cells and
  sub-cells. The
  test fails if `layout.js` is out of date.

Map data, border data and localities © OpenStreetMap contributors (border via
geoBoundaries, localities via the Humanitarian OpenStreetMap Team; ODbL).
Tehsils, districts and provinces: UN OCHA (CC BY-IGO 3.0). Map tiles are loaded from
`tile.openstreetmap.org`; for heavy use, switch to a tile provider that allows it.
