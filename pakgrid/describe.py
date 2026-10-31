"""Describe an area of the grid (a block or a zone): its size, cells, places and where it is.

    grid.describe_zone(zone)                     # or blocks.describe("LHR-01")
    # {"size": {"sub_cells": 506, "cells": 30, "whole_cells": 16, "area_km2": 20.2, "centre": [...]},
    #  "places": {"province": [...], "district": [...], "tehsil": [...], "locality": [...]},
    #  "city": {"name": "Lahore", "relation": "near", "distance_m": 6631.0, "direction": "SE",
    #           "text": "6.6 km SE of Lahore"},
    #  "nearest_locality": None, "label": "Askari 2, Lahore Cantt, Lahore, Punjab", ...}

Rules:

- **Size:** sub-cells, cells touched, whole cells (all 25 sub-cells in), area on the ellipsoid.
- **Centre:** the area's sub-cell centroid nearest the average of all of them, so it always lies in
  the area.
- **Places:** every province, district, tehsil and locality with sub-cells in the area, with how
  many, largest first (ties: the smaller place ID). User localities count too when a user places
  file is attached. Sub-cells outside Pakistan have no places; they are counted separately.
- **City:** the city or town with most sub-cells in the area ("in"); otherwise the nearest city or
  town to the centre at any distance ("near"), with the distance and direction from it.
- **Nearest locality:** when no locality has sub-cells in the area, the nearest one to the centre
  at any distance, with the distance and direction from it.
- **References:** for each place you name, the distance and direction from it to the centre.
- **Label:** the main locality (or "near" the nearest one), tehsil, district and province, said once
  each; None when the area is wholly outside Pakistan.
"""

import struct
from collections import Counter

from . import ids, navigate
from .layout import SCALE, SUB

CITY_KINDS = ("city", "town")


def _where(place, lat, lon):
    """{key, name, kind, distance_m, bearing_deg, direction, text}: from the place's point to (lat, lon)."""
    way = navigate.direction(place.lat, place.lon, lat, lon)
    return {"id": place.id, "key": place.key, "name": place.name, "kind": place.kind,
            "distance_m": round(way.distance_m, 1), "bearing_deg": round(way.bearing_deg, 1),
            "direction": way.compass_short,
            "text": f"{navigate.format_distance(way.distance_m)} {way.compass_short} of {place.name}"}


def centre_of(layout, masks):
    """(lat_e7, lon_e7): the sub-cell centroid nearest the average of the area's centroids."""
    cents = []
    for c, m in masks.items():
        q, row, col = ids.split_cell_id(c)
        ql = layout.quadrants[q]
        for k in range(SUB * SUB):
            if m >> k & 1:
                cents.append(ql.item_centroid_e7(row * SUB + k // SUB, col * SUB + k % SUB, "sub"))
    my = sum(c[0] for c in cents) / len(cents)
    mx = sum(c[1] for c in cents) / len(cents)
    return min(cents, key=lambda c: ((c[0] - my) ** 2 + (0.85 * (c[1] - mx)) ** 2, c))


def _tags(row, layer):
    """The 25 sub-cell tags of a cell from its (main tag, subs) row; zeros if it has none."""
    if row is None:
        return (0,) * (SUB * SUB)
    main, subs = row
    return struct.unpack("<25H" if layer == "tehsil" else "<25I", subs) if subs else (main,) * (SUB * SUB)


def tag_counts(grid, masks):
    """(Counter tehsil ID -> sub-cells, Counter locality ID -> sub-cells, sub-cells outside Pakistan)."""
    tehsils, localities, outside = Counter(), Counter(), 0
    user = grid.user_places
    for c, m in masks.items():
        bits = [k for k in range(SUB * SUB) if m >> k & 1]
        row = grid.store.cell_place(c)
        if row is None and not grid.store.pakistan_edition and not grid.store.has_cell(c):
            raise grid.store._missing(c)
        t_tags = _tags(row, "tehsil")
        l_tags = _tags(grid.store.cell_place(c, "locality"), "locality")
        users = user.at_sub_cells([ids.sub_cell_id(c, k) for k in bits]) if user is not None else {}
        for k in bits:
            t = t_tags[k]
            if not t:
                outside += 1
                continue
            tehsils[t] += 1
            u = users.get(ids.sub_cell_id(c, k))
            if u is not None:
                localities[u.id] += 1
            elif l_tags[k]:
                localities[l_tags[k]] += 1
    return tehsils, localities, outside


def nearest_place(grid, lat, lon, kinds=None):
    """The nearest official locality (of these kinds) at any distance: the fast search within its
    49 km reach, else every locality measured (for areas far from any town, in desert or mountains)."""
    near = grid.nearest_places(lat, lon, k=1, kind=set(kinds) if kinds else None)
    if near:
        return near[0].id
    best = None
    for p in grid._place_table().values():
        if p.level == "locality" and (not kinds or p.kind in kinds):
            d = navigate.direction(lat, lon, p.lat, p.lon).distance_m
            if best is None or (d, p.id) < best[:2]:
                best = (d, p.id, p)
    return best[2] if best else None


def describe(grid, masks, references=()):
    """The description of an area given as {cell ID: mask} (see the module docstring)."""
    from .zones import make_zone
    zone = make_zone(masks, grid.layout)
    if zone.empty:
        raise ValueError("the area has no sub-cells")
    clat_e7, clon_e7 = centre_of(grid.layout, zone.masks)
    clat, clon = clat_e7 / SCALE, clon_e7 / SCALE
    size = {"sub_cells": zone.sub_cell_count, "cells": zone.cell_count,
            "whole_cells": len(zone.cells(full_only=True)), "area_km2": round(zone.area_km2, 6),
            "centre": [clat, clon], "bounds": [round(v, 7) for v in zone.bounds()]}
    out = {"size": size, "places": None, "city": None, "nearest_locality": None, "references": [],
           "outside_pakistan_sub_cells": None, "label": None}
    if not grid.has_places:
        return out
    table = grid._place_table()
    tehsils, localities, outside = tag_counts(grid, zone.masks)
    out["outside_pakistan_sub_cells"] = outside

    def place(pid):
        return table[pid] if pid in table else grid.get_place(pid)       # user localities by ID

    def ranked(counter):
        return [{"id": p.id, "key": p.key, "name": p.name, "sub_cells": n,
                 **({"kind": p.kind, "user": p.user} if p.level == "locality" else {})}
                for p, n in sorted(((place(i), n) for i, n in counter.items()), key=lambda x: (-x[1], x[0].id))]

    districts, provinces = Counter(), Counter()
    for t, n in tehsils.items():
        d = table[t].parent_id
        districts[d] += n
        provinces[table[d].parent_id] += n
    out["places"] = {"province": ranked(provinces), "district": ranked(districts), "tehsil": ranked(tehsils),
                     "locality": ranked(localities)}

    towns = [(n, i) for i, n in localities.items() if place(i).kind in CITY_KINDS]
    if towns:
        n, i = max(towns, key=lambda x: (x[0], -x[1]))
        city = place(i)
        out["city"] = {**_where(city, clat, clon), "relation": "in", "sub_cells": n}
    else:
        near = nearest_place(grid, clat, clon, CITY_KINDS)
        if near:
            out["city"] = {**_where(near, clat, clon), "relation": "near"}
    if not localities:
        near = nearest_place(grid, clat, clon)
        if near:
            out["nearest_locality"] = _where(near, clat, clon)
    for ref in references:
        p = grid.get_place(ref)
        if p.lat is None:
            raise ValueError(f"{p.key} is a {p.level}; a reference must be a locality (it has a point)")
        out["references"].append(_where(p, clat, clon))

    names = []
    if localities:
        names.append(ranked(localities)[0]["name"])
    elif out["nearest_locality"] and tehsils:
        names.append(f"near {out['nearest_locality']['name']}")
    if tehsils:
        t = table[ranked(tehsils)[0]["id"]]
        d = table[t.parent_id]
        names += [t.name, d.name, table[d.parent_id].name]
    out["label"] = ", ".join(n for k, n in enumerate(names) if not k or n.lower() != names[k - 1].lower()) or None
    return out

