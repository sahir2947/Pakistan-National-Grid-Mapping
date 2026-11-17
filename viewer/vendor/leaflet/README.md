# Leaflet 1.9.4

An unmodified copy of [Leaflet](https://leafletjs.com) 1.9.4's `dist` folder
(`leaflet.js`, `leaflet.css` and `images/`), with its licence (BSD 2-Clause,
`LICENSE`). The viewer loads it from here rather than from a CDN, so it works
offline and on a network that blocks or slows third-party hosts.

The files are byte-for-byte the npm package `leaflet@1.9.4`. Their SHA-256
hashes are the `integrity` values in `viewer/index.html`, and
`tests/test_viewer_js.py` checks that they match:

| File | SHA-256 (base64) |
| --- | --- |
| `leaflet.js` | `20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=` |
| `leaflet.css` | `p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=` |

To upgrade: `npm pack leaflet@<version>`, copy its `dist/leaflet.js`,
`dist/leaflet.css`, `dist/images/` and `LICENSE` here, and update both
`integrity` values in `index.html` and the table above.
