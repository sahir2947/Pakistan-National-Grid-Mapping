"""Checks that a grid database matches the grid rules and its own checksum."""

from dataclasses import dataclass, field

from . import spec
from .boundary import FLAG_NAMES, Boundary
from .builder import content_hash, hash_cell, hash_layout, iter_cell_records
from .grid import _layout_from_store
from .layout import GridLayout
from .store import SCHEMA_VERSION, SUB_RECORD, GridStore


@dataclass
class VerifyReport:
    ok: bool = True
    checks: list = field(default_factory=list)

    def add(self, name, passed, detail="", required=True):
        """Record a check; one with required=False is information and cannot fail the report."""
        self.checks.append((name, passed, detail, required))
        self.ok = self.ok and (passed or not required)


def verify_database(path, full=False):
    """Verify a database file.

    Always: grid version, layout against the grid rules, the stored border
    against its checksum, and the content checksum over every stored record
    (including border flags). With full=True, also regenerates every record
    and flag from the rules and the stored border, and compares (takes about
    as long as a build).
    """
    report = VerifyReport()
    store = GridStore(path)
    try:
        meta = store.meta()
        report.add("grid version", meta.get("grid_version") == str(spec.GRID_VERSION),
                   f"database {meta.get('grid_version')}, library {spec.GRID_VERSION}")
        report.add("schema version", meta.get("schema_version") == str(SCHEMA_VERSION),
                   f"database {meta.get('schema_version')}, library {SCHEMA_VERSION}")
        if not report.ok:
            return report

        expected = GridLayout.from_spec()
        stored_layout = _layout_from_store(store)
        report.add("layout matches grid rules", stored_layout.same_shape(expected))
        rows_ok = all(
            (s, n) == (expected.quadrants[q].lat_e7[r * 5], expected.quadrants[q].lat_e7[(r + 1) * 5])
            for q, r, s, n, _ in store.rows())
        report.add("row boundaries match grid rules", rows_ok)

        boundary = Boundary.from_packed(store.boundary_rings(), meta.get("boundary_name", ""))
        report.add("stored border matches its checksum", boundary.sha256 == meta.get("boundary_sha256"),
                   f"{boundary.vertex_count:,} vertices")
        packaged = Boundary.default().sha256
        report.add("stored border is the one packaged with this library", boundary.sha256 == packaged,
                   "" if boundary.sha256 == packaged else "built with a different border; flags follow that border",
                   required=False)

        h = content_hash()
        hash_layout(h, store.quadrants(), store.rows())
        h.update(boundary.sha256.encode())
        n = 0
        counts = [0, 0, 0]
        for (cid, blob, inside, border), cell in zip(store.iter_sub_blobs(), store.iter_cells()):
            if cell[0] != cid or len(blob) != SUB_RECORD.size:
                report.add("cells and sub-cells aligned", False, f"mismatch at cell {cid}")
                return report
            hash_cell(h, cid, cell[1:], blob, (inside, border))
            counts[cell[7]] += 1
            n += 1
        report.add("record count", n == int(meta.get("cells", -1)), f"{n:,} cells")
        report.add("border flag counts", counts == [int(meta.get(f"cells_{k}", -1)) for k in FLAG_NAMES],
                   f"{counts[2]:,} inside, {counts[1]:,} border, {counts[0]:,} outside")
        report.add("content checksum", h.hexdigest() == meta.get("content_sha256"), h.hexdigest()[:16] + "…")

        if full:
            qs = [int(x, 2) for x in meta["quadrants_built"].split(",")]
            rows = None if meta["rows_built"] == "all" else tuple(map(int, meta["rows_built"].split(":")))
            fresh = iter_cell_records(expected, boundary, qs, rows, meta.get("edition") == "pakistan")
            mismatch = None
            for (cid, cell, sub_values, masks), stored_cell, (_, blob, inside, border) in zip(
                    fresh, store.iter_cells(), store.iter_sub_blobs()):
                if ((cid, *cell) != tuple(stored_cell) or SUB_RECORD.pack(*sub_values) != blob
                        or masks != (inside, border)):
                    mismatch = cid
                    break
            report.add("every record and flag regenerated from the rules", mismatch is None,
                       "" if mismatch is None else f"first difference at cell {mismatch}")
    finally:
        store.close()
    return report
