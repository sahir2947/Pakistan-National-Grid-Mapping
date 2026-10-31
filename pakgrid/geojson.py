"""GeoJSON output: cells, sub-cells, zones and places as shapes for any map.

GeoJSON (RFC 7946) is what Google Maps, Mapbox, Leaflet, flutter_map and PostGIS read. A set of
sub-cells (a zone, a place, a radius search) becomes one outline, not thousands of squares:
its sub-cells are merged into polygons, with holes where the set has gaps, and polygons that only
touch at a corner kept apart, so the geometry is valid.

    grid.geojson("11-0140-0471")                    # a cell as a Feature
    grid.zone(place="punjab/lahore").to_geojson()   # Lahore district's exact outline
    grid.place_geojson("punjab/lahore")             # the same, with the place's names

The outline is exact: its corners are the sub-cells' own corners (1e-7 degree), and it covers
exactly the sub-cells of the set. Coordinates are [longitude, latitude]; outer rings run
anticlockwise and holes clockwise, as RFC 7946 asks. The output is canonical (each ring starts at
its south-westernmost corner; polygons and holes in order of that corner), so the same set
always gives the same GeoJSON, in Python and in viewer/pakgrid.js.
"""

from collections import defaultdict

from . import ids
from .layout import SCALE, SUB

# runs of set bits in a 5-bit sub-row pattern: [(first column, end column), ...]
_RUNS = []
for _p in range(32):
    _r, _k = [], 0
    while _k < SUB:
        if _p >> _k & 1:
            _s = _k
            while _k < SUB and _p >> _k & 1:
                _k += 1
            _r.append((_s, _k))
        else:
            _k += 1
    _RUNS.append(tuple(_r))

_LEFT = {"E": "N", "N": "W", "W": "S", "S": "E"}        # the left turn from each direction
_RIGHT = {v: k for k, v in _LEFT.items()}


def _blocks(layout, masks):
    """[(south, north, west, east)] in 1e-7 degree: maximal runs of sub-cells along each row."""
    runs = defaultdict(list)                               # (q, fine row) -> [(j0, j1)]
    for c, m in masks.items():
        q, row, col = ids.split_cell_id(c)
        for sr in range(SUB):
            for a, b in _RUNS[m >> (sr * SUB) & 31]:
                runs[(q, row * SUB + sr)].append((col * SUB + a, col * SUB + b))
    out = []
    for (q, i), rs in runs.items():
        ql = layout.quadrants[q]
        row = i // SUB
        s, n = ql.lat_e7[i], ql.lat_e7[i + 1]
        rs.sort()
        j0, j1 = rs[0]
        for a, b in rs[1:]:
            if a <= j1:
                j1 = max(j1, b)
            else:
                out.append((s, n, ql.fine_lon_e7(row, j0), ql.fine_lon_e7(row, j1)))
                j0, j1 = a, b
        out.append((s, n, ql.fine_lon_e7(row, j0), ql.fine_lon_e7(row, j1)))
    return out


def _net_segments(lines):
    """{coordinate: [(a, b, sign)]} -> [(coordinate, a, b, net)]: the parts of each line where the
    signed pieces do not cancel, maximal (consecutive parts with the same net joined)."""
    out = []
    for c, pieces in lines.items():
        delta = defaultdict(int)
        for a, b, sign in pieces:
            delta[a] += sign
            delta[b] -= sign
        xs = sorted(delta)
        net, run = 0, None                       # run: [start, end, net] being built
        for k in range(len(xs) - 1):
            net += delta[xs[k]]
            a, b = xs[k], xs[k + 1]
            if net and run and run[2] == net and run[1] == a:
                run[1] = b
            else:
                if run:
                    out.append((c, run[0], run[1], run[2]))
                run = [a, b, net] if net else None
        if run:
            out.append((c, run[0], run[1], run[2]))
    return out


def outline_rings(layout, masks):
    """Closed rings [(lon_e7, lat_e7), ...] outlining the sub-cells of {cell ID: mask}: outer rings
    anticlockwise, holes clockwise (the inside always on the left).

    The outline is made of directed edges (each block's sides, with the parts where neighbouring
    blocks meet cancelled out). A ring follows its edges, turning as far left as it can at a corner
    where the outline touches itself, and a ring that passes through a corner twice is split there,
    so every ring is simple (polygons may touch each other, or a hole its outer ring, at a point,
    as GeoJSON allows).
    """
    horizontal, vertical = defaultdict(list), defaultdict(list)
    for s, n, w, e in _blocks(layout, masks):
        horizontal[s].append((w, e, 1))        # bottom edge, inside above: runs east
        horizontal[n].append((w, e, -1))       # top edge: runs west
        vertical[e].append((s, n, 1))          # east edge, inside to the west: runs north
        vertical[w].append((s, n, -1))         # west edge: runs south
    edges = []                                 # (start, end, direction)
    for y, a, b, net in _net_segments(horizontal):
        edges.append(((a, y), (b, y), "E") if net > 0 else ((b, y), (a, y), "W"))
    for x, a, b, net in _net_segments(vertical):
        edges.append(((x, a), (x, b), "N") if net > 0 else ((x, b), (x, a), "S"))
    edges.sort()
    leaving = defaultdict(list)
    for k, (start, _, d) in enumerate(edges):
        leaving[start].append((d, k))
    rings, used = [], [False] * len(edges)
    for k0 in range(len(edges)):
        if used[k0]:
            continue
        ring, k = [edges[k0][0]], k0
        while not used[k]:
            used[k] = True
            start, end, d = edges[k]
            ring.append(end)
            k = _leftmost(leaving[end], d)
        rings.extend(_simplify(loop) for loop in _split(ring))
    return rings


def _split(ring):
    """Split a closed ring that passes through a vertex twice (where a hole touches the outer edge
    at a corner) into simple loops; each keeps its direction, so outer edges and holes stay apart."""
    seen = {}
    for k, p in enumerate(ring[:-1]):
        if p in seen:
            i = seen[p]
            return _split(ring[i:k + 1]) + _split(ring[:i] + ring[k:])
        seen[p] = k
    return [ring]


def _leftmost(options, d):
    """The edge turning most to the left from direction d (left, then straight, then right)."""
    for want in (_LEFT[d], d, _RIGHT[d]):
        for od, k in options:
            if od == want:
                return k
    raise AssertionError("the outline is not closed")       # cannot happen


def _simplify(ring):
    """Drop the points where the outline goes straight on; start at the south-westernmost corner."""
    pts = ring[:-1]
    keep = []
    n = len(pts)
    for k in range(n):
        (x0, y0), (x1, y1), (x2, y2) = pts[k - 1], pts[k], pts[(k + 1) % n]
        if not ((x0 == x1 == x2) or (y0 == y1 == y2)):
            keep.append(pts[k])
    first = min(range(len(keep)), key=lambda k: (keep[k][1], keep[k][0]))
    keep = keep[first:] + keep[:first]
    return keep + [keep[0]]


def _signed_area2(ring):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))


def _inside(px2, py2, ring):
    """Even-odd test of the point (px2 / 2, py2 / 2) against a rectilinear ring (doubled
    coordinates, so the midpoint of an edge stays an integer): only its vertical edges count."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if x1 == x2 and (2 * y1 > py2) != (2 * y2 > py2) and 2 * x1 > px2:
            inside = not inside
    return inside


def polygons(rings):
    """Group rings into polygons: [[outer ring, hole, ...], ...], in canonical order."""
    outers, holes = [], []
    for r in rings:
        (outers if _signed_area2(r) > 0 else holes).append(r)
    boxes = [(min(x for x, _ in r), min(y for _, y in r), max(x for x, _ in r), max(y for _, y in r))
             for r in outers]
    areas = [_signed_area2(r) for r in outers]
    members = [[] for _ in outers]
    for h in holes:
        (x1, y1), (x2, y2) = h[0], h[1]
        px2, py2 = x1 + x2, y1 + y2              # the midpoint of its first edge, doubled
        best = None
        for k, r in enumerate(outers):
            b = boxes[k]
            if 2 * b[0] <= px2 <= 2 * b[2] and 2 * b[1] <= py2 <= 2 * b[3] and _inside(px2, py2, r):
                if best is None or areas[k] < areas[best]:
                    best = k
        if best is None:
            raise AssertionError("a hole outside every outer ring")   # cannot happen
        members[best].append(h)
    key = lambda r: (r[0][1], r[0][0])           # noqa: E731
    out = [[r] + sorted(m, key=key) for r, m in zip(outers, members)]
    out.sort(key=lambda p: key(p[0]))
    return out


def _coords(ring):
    return [[x / SCALE, y / SCALE] for x, y in ring]


def geometry(layout, masks):
    """GeoJSON geometry (Polygon or MultiPolygon) of the sub-cells in {cell ID: mask}, or None if
    there are none."""
    polys = polygons(outline_rings(layout, masks))
    if not polys:
        return None
    if len(polys) == 1:
        return {"type": "Polygon", "coordinates": [_coords(r) for r in polys[0]]}
    return {"type": "MultiPolygon", "coordinates": [[_coords(r) for r in p] for p in polys]}


def box_geometry(south, west, north, east):
    """GeoJSON Polygon of a latitude/longitude box (degrees), anticlockwise from its south-west corner."""
    return {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north],
                                                [west, south]]]}


def feature(geom, properties=None, feature_id=None):
    f = {"type": "Feature", "geometry": geom, "properties": dict(properties or {})}
    if feature_id is not None:
        f["id"] = feature_id
    return f


def feature_collection(features):
    return {"type": "FeatureCollection", "features": list(features)}
