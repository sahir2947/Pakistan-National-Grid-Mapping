# Pakistan boundary used for the border flags

`geoBoundaries-PAK-ADM0.geojson.gz` is the national (ADM0) boundary of
Pakistan from geoBoundaries, unchanged apart from gzip compression.

| | |
|---|---|
| Dataset | geoBoundaries gbOpen, `PAK-ADM0-80972501` |
| Covers | Territory administered by Pakistan, including Azad Jammu & Kashmir and Gilgit-Baltistan; land only (the coastline is the edge) |
| Underlying data | OpenStreetMap, via Wambacher's OSM boundaries |
| Source data updated | 19 January 2023 (geoBoundaries build: 12 December 2023) |
| Downloaded from | `https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/PAK/ADM0/geoBoundaries-PAK-ADM0.geojson` |
| SHA-256 of the uncompressed file | `0caf6e9e0153e3fe482b9c6ed0aa745bdea9b282423bb9b2ca6ce905e09439d8` (matches the geoBoundaries repository) |
| Size | 48 polygons (mainland and islands), 86,591 vertices |

## Licence and attribution

The boundary data is licensed under the
[Open Data Commons Open Database License 1.0 (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).

> Boundary data © OpenStreetMap contributors, available under the ODbL,
> via geoBoundaries (www.geoboundaries.org).

The border flags in a grid database are derived from this data, so a grid
database that contains them must carry this attribution, and a modified
version of such a database that is shared publicly must also be offered under
the ODbL. The `pakgrid` source code itself is not affected.

## Updating

To switch to a newer boundary, replace this file and update the table above.
Rebuilding the database recomputes the flags; cell and sub-cell IDs never
change. The boundary's checksum is recorded in each database's `meta` table.
