"""Checks that a grid database matches the grid rules and its own checksum."""

from dataclasses import dataclass, field

from . import spec
from .builder import _CELL_REC, content_hash, hash_layout, iter_cell_records
from .grid import _layout_from_store
from .layout import GridLayout
from .store import SUB_RECORD, GridStore


@dataclass
class VerifyReport:
    ok: bool = True
    checks: list = field(default_factory=list)

    def add(self, name, passed, detail=""):
        self.checks.append((name, passed, detail))
        self.ok = self.ok and passed


def verify_database(path, full=False):
    """Verify a database file.

    Always: grid version, layout against the grid rules, and the content
    checksum over every stored record. With full=True, also regenerates every
    record from the rules and compares (takes about as long as a build).
    """
    report = VerifyReport()
    store = GridStore(path)
    try:
        meta = store.meta()
        report.add("grid version", meta.get("grid_version") == str(spec.GRID_VERSION),
                   f"database {meta.get('grid_version')}, library {spec.GRID_VERSION}")

        expected = GridLayout.from_spec()
        stored_layout = _layout_from_store(store)
        report.add("layout matches grid rules", stored_layout.same_shape(expected))
        rows_ok = all(
            (s, n) == (expected.quadrants[q].lat_e7[r * 5], expected.quadrants[q].lat_e7[(r + 1) * 5])
            for q, r, s, n, _ in store.rows())
        report.add("row boundaries match grid rules", rows_ok)

        h = content_hash()
        hash_layout(h, store.quadrants(), store.rows())
        n = 0
        cells = store.iter_cells()
        for (cid, blob), cell in zip(store.iter_sub_blobs(), cells):
            if cell[0] != cid or len(blob) != SUB_RECORD.size:
                report.add("cells and sub-cells aligned", False, f"mismatch at cell {cid}")
                return report
            h.update(_CELL_REC.pack(*cell))
            h.update(blob)
            n += 1
        report.add("record count", n == int(meta.get("cells", -1)), f"{n:,} cells")
        report.add("content checksum", h.hexdigest() == meta.get("content_sha256"), h.hexdigest()[:16] + "…")

        if full:
            qs = [int(x, 2) for x in meta["quadrants_built"].split(",")]
            rows = None if meta["rows_built"] == "all" else tuple(map(int, meta["rows_built"].split(":")))
            fresh = iter_cell_records(expected, qs, rows)
            stored_cells = store.iter_cells()
            stored_subs = store.iter_sub_blobs()
            mismatch = None
            for (cid, cell, sub_values), stored_cell, (_, blob) in zip(fresh, stored_cells, stored_subs):
                if (cid, *cell) != tuple(stored_cell) or SUB_RECORD.pack(*sub_values) != blob:
                    mismatch = cid
                    break
            report.add("every record regenerated from the rules", mismatch is None,
                       "" if mismatch is None else f"first difference at cell {mismatch}")
    finally:
        store.close()
    return report
