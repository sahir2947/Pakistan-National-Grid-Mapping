# Place boundaries used for the places layer

The places layer tags every cell and sub-cell with its tehsil, district and
province (see `pakgrid/places.py` and project.md §5.7). It is built from these
three files, each the geoBoundaries release unchanged apart from gzip
compression.

| File | Level | Units | Dataset | Underlying data | Licence |
|---|---|---|---|---|---|
| `geoBoundaries-PAK-ADM1.geojson.gz` | Province / territory | 7 | geoBoundaries gbOpen `PAK-ADM1-70912109` | Wikimedia Commons (2019) | Public domain |
| `geoBoundaries-PAK-ADM2.geojson.gz` | District | 126 | geoBoundaries gbOpen `PAK-ADM2-60131773` | Wikimedia Commons (2019) | Public domain |
| `geoBoundaries-PAK-ADM3.geojson.gz` | Tehsil | 554 | geoBoundaries gbOpen `PAK-ADM3-9618217` | Pathways Data, from the Pakistan Bureau of Statistics 2017 census | ODbL 1.0 |

Downloaded from
`https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/PAK/<LEVEL>/geoBoundaries-PAK-<LEVEL>.geojson`
(geoBoundaries build of 12 December 2023).

| File (uncompressed) | SHA-256 |
|---|---|
| `geoBoundaries-PAK-ADM1.geojson` | `34c973399b464f8b43a6cfbc9255ff6d19d911477f1549d0ff0f1dcaaf63c37b` |
| `geoBoundaries-PAK-ADM2.geojson` | `108eeaa75a6de678f022e0b6d8fd8ab4c93e4b4d1a9c6a28995afe7c0731182c` |
| `geoBoundaries-PAK-ADM3.geojson` | `cc18531b0b955d11bfe5875f6cf680a57c86bfde7d96be1ebba716906a529388` |

## How each level is used (places version 1)

- **Tehsils** are the detailed layer (231,827 vertices). Every sub-cell that
  touches Pakistan is tagged with the tehsil containing its centroid. Along
  the edges the tehsil polygons stop slightly short of pakgrid's border (a
  different source), so 43,484 sub-cells there take the tag of their
  neighbours, and 702 sub-cells on islands that no tehsil includes (Astola,
  islands off Karachi) take the nearest tehsil.
- **Districts and provinces** are coarse outlines (24,092 and 4,208 vertices).
  They are not used to tag blocks directly. Instead each tehsil's district is
  the district that covers most of it, and each district's province is the
  province that covers most of it. That keeps the hierarchy consistent.

## Known limitations

- **Some tehsils have the wrong district.** The district outlines are coarse
  and from a different source than the tehsils, so for some tehsils no
  district covers most of them clearly. 79 tehsils whose main district covers
  less than 70% of them are marked `uncertain`, and some of those are wrong
  (for example New Sukkur, which is in Sukkur district, comes out in
  Shikarpur; Naurang, in Lakki Marwat, comes out in Bannu). Provinces are
  reliable: every district lies within one province.
- **The data is dated.** Districts are from 2019 and tehsils from the 2017
  census; Pakistan has created new districts and tehsils since. Azad Jammu &
  Kashmir is a single district in the district data.
- Names are as in the sources, cleaned for display (`D.I.KHAN` becomes
  `D.I. Khan`); the original spelling is kept as `source_name`.

A later places version can replace these sources (for example with the UN
OCHA administrative boundaries for Pakistan, which come with an explicit
tehsil → district → province hierarchy). Grid IDs never change; apps keep
their stored cell and sub-cell IDs and see the new names.

## Licence and attribution

> Place boundaries via geoBoundaries (www.geoboundaries.org): provinces and
> districts (public domain, from Wikimedia Commons); tehsils © Pathways Data /
> Pakistan Bureau of Statistics 2017 census, available under the ODbL.

The tehsil tags in a grid database are derived from ODbL data, so a database
that contains the places layer must carry this attribution, and a modified
version of it that is shared publicly must also be offered under the ODbL. The
`pakgrid` source code itself is not affected.
