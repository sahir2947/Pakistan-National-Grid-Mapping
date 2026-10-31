"""Custom blocks: named, coded groups of cells and sub-cells that an app's users make.

A block set is an app's own way of dividing an area: delivery territories, pricing bands, rider
teams, sales areas. Users build each block by clicking cells on a map or drawing a polygon, and
give it a code and a name. The library keeps the set in memory and turns it into JSON (and
GeoJSON) for the app to store wherever it likes: its database, a file, cloud storage.

    blocks = grid.block_set("lahore-territories")
    blocks.add("LHR-01", name="Gulberg North", cells=["11-0140-0471", "11-0140-0472"])
    blocks.add("LHR-02", name="DHA Phase 6", polygon=[(31.45, 74.44), (31.45, 74.48), (31.49, 74.48)])
    blocks.add_cells("LHR-01", ["11-0140-0473-12"])          # a click on the map adds a cell or sub-cell
    blocks.remove_cells("LHR-01", ["11-0140-0472"])          # ... or takes it away
    blocks.describe("LHR-01")      # cell codes, size, province/district/tehsil, localities, city, directions
    blocks.block_at(31.5204, 74.3587)                        # 'LHR-01': which block is this pin in?
    data = blocks.to_dict()                                  # store it anywhere
    blocks = BlockSet.from_dict(data, grid)                  # and load it back

Rules:

- A block is a set of sub-cells (a whole cell is its 25 sub-cells), as a custom zone is
  (pakgrid.zones): from cells, a polygon (sub-cells whose centre is inside), a circle, a place, or
  any mix, added and removed piece by piece.
- Within a set, blocks do not overlap: adding sub-cells that another block has is refused, naming
  that block, unless you ask to move them (take=True; a block that loses all its sub-cells that way
  stays in the set, empty, until you add to it or remove it).
- Codes are unique within a set and never empty; names and extra properties are free.
- describe() reports a block's cell codes, size, places and where it is (pakgrid.describe).
"""

import re

from . import ids, spec
from .errors import OutsideGridError, PakgridError
from .layout import SUB, to_e7
from .zones import FULL, Zone, make_zone

BLOCKSET_VERSION = 1


class BlockError(PakgridError, ValueError):
    """A block cannot be made or changed as asked (overlap, unknown code, bad data, ...)."""


def _text(value, what, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BlockError(f"{what} must be a non-empty string, got {value!r}")
    return re.sub(r"\s+", " ", value).strip()


class Block:
    """One block of a set: a code, a name, free properties and its sub-cells (a Zone)."""

    def __init__(self, code, name, zone, properties):
        self.code = code
        self.name = name
        self.zone = zone
        self.properties = dict(properties or {})

    @property
    def sub_cell_count(self):
        return self.zone.sub_cell_count

    def contains(self, lat, lon):
        return self.zone.contains(lat, lon)

    def __repr__(self):
        return f"Block({self.code!r}, {self.name!r}, {self.zone.sub_cell_count:,} sub-cells)"


class BlockSet:
    """A named set of non-overlapping blocks (see the module docstring)."""

    def __init__(self, grid, name="blocks"):
        self.grid = grid
        self.name = _text(name, "the block set's name")
        self._blocks = {}                       # code -> Block, in the order added
        self._owner = {}                        # cell ID -> {code: mask}

    # -- Building ---------------------------------------------------------------------

    def add(self, code, *, name=None, cells=None, polygon=None, circle=None, place=None, properties=None,
            take=False, pakistan_only=False):
        """Add a block made from any of cells, polygon, circle and place (at least one). Returns it."""
        code = _text(code, "code")
        if code in self._blocks:
            raise BlockError(f"there is already a block {code!r} in this set")
        masks = self._masks(cells, polygon, circle, place, pakistan_only)
        if not masks:
            raise BlockError("the block would have no sub-cells")
        self._claim(code, masks, take)
        block = Block(code, _text(name, "name", required=False), make_zone(masks, self.grid.layout), properties)
        self._blocks[code] = block
        return block

    def add_cells(self, code, cells=None, *, polygon=None, circle=None, place=None, take=False, pakistan_only=False):
        """Add cells or sub-cells (or a polygon's, circle's or place's) to an existing block."""
        block = self.get(code)
        masks = self._masks(cells, polygon, circle, place, pakistan_only)
        masks = {c: m & ~block.zone.masks.get(c, 0) & FULL for c, m in masks.items()}
        masks = {c: m for c, m in masks.items() if m}
        self._claim(block.code, masks, take)
        block.zone = block.zone | make_zone(masks, self.grid.layout)
        return block

    def remove_cells(self, code, cells=None, *, polygon=None, circle=None, place=None):
        """Take cells or sub-cells away from a block. A block cannot become empty (remove it instead)."""
        block = self.get(code)
        masks = self._masks(cells, polygon, circle, place, False)
        rest = block.zone - make_zone(masks, self.grid.layout)
        if rest.empty:
            raise BlockError(f"that would leave block {block.code!r} empty; remove the block instead")
        self._release(block.code, block.zone)
        block.zone = rest
        self._claim(block.code, rest.masks, False)
        return block

    def toggle(self, code, cell, take=False):
        """For a click on the map: add the cell or sub-cell to the block if it is not in it, else
        take it away. Returns True if it was added."""
        block = self.get(code)
        masks = self._masks([cell], None, None, None, False)
        inside = all(block.zone.masks.get(c, 0) & m == m for c, m in masks.items())
        if inside:
            self.remove_cells(code, [cell])
            return False
        self.add_cells(code, [cell], take=take)
        return True

    def update(self, code, *, name=None, properties=None, new_code=None):
        """Change a block's name, properties (replaced) or code."""
        block = self.get(code)
        if name is not None:
            block.name = _text(name, "name")
        if properties is not None:
            block.properties = dict(properties)
        if new_code is not None and new_code != block.code:
            new_code = _text(new_code, "code")
            if new_code in self._blocks:
                raise BlockError(f"there is already a block {new_code!r} in this set")
            self._release(block.code, block.zone)
            old = block.code
            block.code = new_code
            self._blocks = {(new_code if c == old else c): b for c, b in self._blocks.items()}   # keeps the order
            self._claim(new_code, block.zone.masks, False)
        return block

    def remove(self, code):
        """Remove a block from the set."""
        block = self.get(code)
        self._release(block.code, block.zone)
        del self._blocks[block.code]

    # -- Reading ------------------------------------------------------------------------

    def get(self, code):
        try:
            return self._blocks[code]
        except (KeyError, TypeError):
            raise BlockError(f"no block {code!r} in this set") from None

    def blocks(self):
        """All blocks, in the order they were added."""
        return list(self._blocks.values())

    def __len__(self):
        return len(self._blocks)

    def __contains__(self, code):
        return code in self._blocks

    def block_at(self, lat, lon):
        """The code of the block holding the pin, or None."""
        try:
            q, row, col, sr, sc = self.grid.layout.locate_e7(to_e7(lat), to_e7(lon))
        except OutsideGridError:
            return None
        bit = 1 << (sr * SUB + sc)
        for code, m in self._owner.get(ids.cell_id(q, row, col), {}).items():
            if m & bit:
                return code
        return None

    def block_of(self, item):
        """The code of the block holding a sub-cell (ID or code), or for a cell code the blocks
        holding any part of it, as {code: sub-cells}."""
        level, item_id = ids.parse_code(item) if isinstance(item, str) else ("sub", item)
        if level == "sub":
            cid, sub = ids.split_sub_cell_id(item_id)
            for code, m in self._owner.get(cid, {}).items():
                if m >> sub & 1:
                    return code
            return None
        return {code: bin(m).count("1") for code, m in self._owner.get(item_id, {}).items()}

    def describe(self, code, references=()):
        """A block's full description: code, name, properties, cell codes, size, places, city,
        nearest locality and directions (see pakgrid.describe)."""
        block = self.get(code)
        if block.zone.empty:
            raise BlockError(f"block {block.code!r} has no sub-cells")
        out = {"code": block.code, "name": block.name, "properties": block.properties}
        out.update(self.grid.describe_zone(block.zone, references))
        out["cell_codes"] = block.zone.codes()             # whole cells as cell codes, the rest as sub-cell codes
        out["whole_cells"] = [ids.cell_code(c) for c in block.zone.cells(full_only=True)]
        out["partial_cells"] = {ids.cell_code(c): [k for k in range(SUB * SUB) if m >> k & 1]
                                for c, m in sorted(block.zone.masks.items()) if m != FULL}
        return out

    # -- Storing ------------------------------------------------------------------------

    def to_dict(self):
        """JSON-ready data for the whole set (store it anywhere; load it with from_dict)."""
        return {"blockset_version": BLOCKSET_VERSION, "grid_version": spec.GRID_VERSION, "name": self.name,
                "blocks": [{"code": b.code, "name": b.name, "properties": b.properties, "zone": b.zone.to_dict()}
                           for b in self._blocks.values()]}

    @classmethod
    def from_dict(cls, data, grid):
        if data.get("blockset_version") != BLOCKSET_VERSION or data.get("grid_version") != spec.GRID_VERSION:
            raise BlockError(f"not block set version {BLOCKSET_VERSION} data for grid version {spec.GRID_VERSION}")
        out = cls(grid, data.get("name") or "blocks")
        for b in data.get("blocks", []):
            zone = Zone.from_dict(b["zone"], grid)
            code = _text(b.get("code"), "code")
            if code in out._blocks:
                raise BlockError(f"block {code!r} appears twice")
            out._claim(code, zone.masks, False)
            out._blocks[code] = Block(code, _text(b.get("name"), "name", required=False), zone, b.get("properties"))
        return out

    def to_geojson(self):
        """A FeatureCollection: each block's outline, with its code, name, properties and size."""
        from .geojson import feature_collection
        feats = []
        for b in self._blocks.values():
            f = b.zone.to_geojson({"code": b.code, "name": b.name, **b.properties})
            f["id"] = b.code
            feats.append(f)
        return feature_collection(feats)

    # -- Internals ----------------------------------------------------------------------

    def _masks(self, cells, polygon, circle, place, pakistan_only):
        sources = [(k, v) for k, v in (("cells", cells), ("polygon", polygon), ("circle", circle), ("place", place))
                   if v is not None]
        if not sources:
            raise BlockError("give cells, a polygon, a circle or a place")
        zone = None
        for k, v in sources:
            z = self.grid.zone(**{k: v}, pakistan_only=pakistan_only)
            zone = z if zone is None else zone | z
        return dict(zone.masks)

    def _claim(self, code, masks, take):
        """Record that `code` owns these sub-cells, refusing (or with take, moving) any another block owns."""
        clashes = {}
        for c, m in masks.items():
            for other, om in self._owner.get(c, {}).items():
                if other != code and om & m:
                    clashes[other] = clashes.get(other, 0) + bin(om & m).count("1")
        if clashes and not take:
            names = ", ".join(f"{o!r} ({n} sub-cell{'s' if n != 1 else ''})" for o, n in sorted(clashes.items()))
            raise BlockError(f"those cells are already in block {names}; pass take=True to move them")
        for other in clashes:
            ob = self._blocks[other]
            rest = ob.zone - make_zone(masks, self.grid.layout)
            self._release(other, ob.zone)
            ob.zone = rest
            self._claim(other, rest.masks, False)
        for c, m in masks.items():
            owners = self._owner.setdefault(c, {})
            owners[code] = owners.get(code, 0) | m

    def _release(self, code, zone):
        for c in zone.masks:
            owners = self._owner.get(c)
            if owners and code in owners:
                del owners[code]
                if not owners:
                    del self._owner[c]
