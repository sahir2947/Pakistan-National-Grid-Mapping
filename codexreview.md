# Codex Review — Pakistan National Grid Mapping

**Reviewed:** 30 October 2026  
**Repository:** https://github.com/sahir2947/Pakistan-National-Grid-Mapping  
**Branch:** `main`, tree `902a8734f46617484e8bed4b0d19ca5ca0155c11`  
**Method:** Static GitHub source, repository tree, tests, configuration and documentation review. Tests and full builds were not executed.

## Executive assessment

The project implements a deterministic Pakistan-focused spatial grid with Python APIs, CLI, SQLite generation and verification, provenance metadata, user-defined localities, navigation, a Leaflet browser viewer and cross-language conformance fixtures. Strengths include cross-platform CI, reproducible checksums, compact identifiers, a separated writable locality layer and substantial test coverage. Important correctness, documentation, input-validation and deployment concerns remain.

## Prioritized findings

### P1: Reference-only locality updates use scaled coordinates as real degrees

**File:** `pakgrid/userplaces.py`, `UserPlaces.update` (around lines 286–305).

`lat, lon = to_e7(old.lat), to_e7(old.lon)` is performed before the references branch. If callers update `references` or `nearest` without changing the shape, these E7 integers are sent to `_references(lat, lon, ...)`, which expects latitude/longitude degrees. This can lead to invalid reference distances, failures or nonsensical bearings. When shape is changed, `lat, lon` instead come from `_tehsil_and_centre`; verify that path independently.

**Fix:** Keep decimal-degree coordinates separate from E7 storage values, converting only for persistence. Add regression tests for `update(..., references=...)` and `update(..., nearest=...)` with no polygon/cell changes and check distance/bearing against `navigate.direction`.

### P1: `verify_database` silently truncates paired streams with `zip`

**File:** `pakgrid/verify.py`, `verify_database` (around lines 66–114).

The verifier pairs `store.iter_sub_blobs()` with `store.iter_cells()` using `zip`; its full regeneration check uses `zip` over three iterators. `zip` stops at the shortest stream. Some discrepancies can be detected indirectly through expected counts/hashes, but there is no explicit invariant that every stream has exactly the expected length; full regeneration can miss extra trailing rows.

**Fix:** Replace with `itertools.zip_longest` plus a sentinel, fail on any unmatched element, count `cells` and `sub_cells` independently and verify regeneration exhaustion. Add corruption tests for missing, extra, first, middle and last records.

### P2: Unvalidated GPS accuracy can corrupt navigation smoothing

**Files:** `pakgrid/navigate.py` (`NavigationSession.add_fix` around lines 187–204); `viewer/pakgrid.js` (`addFix` near line 551).

Python uses `max(accuracy or 20.0, 1.0)` and JS uses `Math.max(o.accuracy || 20, 1)`. These expressions do not robustly validate invalid, infinite, negative or nonnumeric sensor measurements. Invalid values can propagate to smoothing or displayed accuracy.

**Fix:** Define a shared sensor input contract: finite coordinates in valid ranges, finite non-negative accuracy/speed, finite normalized heading and sensible timestamps. Reject bad inputs consistently in both ports. Add parity tests for NaN, Infinity, negatives, missing/zero accuracy and out-of-order samples.

### P2: README bit-width/storage assertions contradict implemented identifiers

**Files:** `README.md` initial architecture sections; `pakgrid/ids.py`; `pakgrid/spec.py`.

The README claims a 16-bit `SMALLINT` cell ID constructed using two 10-bit coordinates. Those require at least 20 bits before shard information, whereas the implementation explicitly uses a 22-bit global cell ID (2 quadrant + 10 row + 10 column) and 27-bit sub-cell ID. The initial shard/tuple narrative is not the authoritative description of the code.

**Fix:** Mark the original proposal as superseded or rewrite it to describe the implemented version. Document numeric ranges, database types, versioning and migrations. Do not recommend `SMALLINT` for a 22-bit value.

### P2: SQLite `immutable=1` requires disciplined data-file publication

**File:** `pakgrid/store.py`, `GridStore.__init__` (around lines 80–113).

Read-only SQLite connections use `mode=ro&immutable=1`, which assumes database files never change under an open reader. The builder's atomic replacement is useful, but an application needs a documented snapshot rollover protocol.

**Fix:** Publish verified versioned filenames, reopen `Grid` instances before switching versions and never mutate an opened immutable DB in place. Add concurrent reader/replacement tests.

### P2: Large viewer payload and externally hosted dependencies

**Files:** `viewer/places.js` (~9.4 MB); `viewer/boundary.js` (~0.95 MB); `viewer/index.html`.

A large generated JS places payload can increase initial load, parse time and memory on mobile. Leaflet is loaded from unpkg, and map tiles depend on external service availability. Offline arithmetic does not imply the map UI works completely offline.

**Fix:** Profile low-end mobile performance; split/lazy-load place data, enable compression and caching, consider locally pinned dependencies, and document online map-tile requirements and acceptable usage limits.

### P3: Identity-linked location handling needs a privacy/deployment threat model

**File:** `README.md`, architectural proposal and lifecycle.

The document describes linking CNIC identity numbers to geographic addresses. Although this is not a complete identity service in the reviewed package, a production integration would process sensitive identity and location records.

**Fix:** Specify purpose limitation, consent, encryption, retention, access control, tenant isolation, audit logs, and deletion/export. Minimize CNIC exposure in spatial indexes. A compact grid token is not anonymization.

### P3: Broaden CI quality gates

**Files:** `.github/workflows/tests.yml`, `.github/workflows/checksums.yml`, `pyproject.toml`.

The repository already has valuable pytest/pyflakes checks on several Python versions and platforms, plus full cross-platform checksum builds. Additional static type checks, JS linting, packaging smoke tests and performance checks are not shown.

**Fix:** Add Ruff/formatter and type checking for public interfaces, frontend linting, pip wheel/install smoke tests, performance budgets, and checks that generated fixtures are reproducible.

## Component-by-component assessment

| Component | Assessment | Next action |
|---|---|---|
| Grid IDs and geometry | Clear deterministic grid and global IDs | Publish a formal numeric specification and boundary-property tests |
| SQLite builder | Hashes and build metadata support reproducibility | Harden crash recovery and snapshot publication |
| Verifier | Detailed checks, but iterator-length gap | Explicitly check all record streams |
| Geospatial/places | Structured provenance and hierarchy | Validate data version, attribution and contested-border caveats |
| User localities | Separate writable store, audit history and flexible shapes | Fix reference-only updates; test concurrency and recovery |
| Navigation | Python/JS parity design and useful guidance | Reject bad sensor data and test noisy/out-of-order samples |
| Web viewer | Functional mapping and selection interface | Optimize loading, accessibility and offline behaviour |
| CLI | Broad build/query/import/export command coverage | Test malformed input and stable error contracts |
| Tests/CI | Meaningful cross-platform, conformance, checksum coverage | Add adversarial, browser-performance and packaging gates |
| Documentation | Extensive, but includes obsolete ID design | Consolidate current spec and operational deployment guidance |

## Implementation plan

1. Fix `UserPlaces.update` decimal-degree vs E7 error with regression tests.
2. Replace verifier `zip` operations with exhaustive stream comparisons and corruption tests.
3. Standardize and test navigation sensor validation across Python and JavaScript.
4. Correct the README identifier widths and authoritative storage schema.
5. Document safe immutable database rollovers and privacy boundaries.
6. Profile and optimize viewer load, add static-analysis and package smoke-test gates.

## Acceptance criteria

- Reference-only edits return correct distances and directions while preserving geometry.
- Verification explicitly fails on missing/extra sub-records, including under `--full`.
- Invalid GPS inputs behave consistently in both implementations.
- Documentation accurately describes 22-bit cell and 27-bit sub-cell IDs.
- Safe database replacement while readers are active is documented and tested.
- Mobile viewer performance is measured against a declared target.
- CI includes additional quality checks without weakening checksum reproducibility.

## Limitations

This was a static inspection, not an executed regression, security or performance audit. The tree, core Python modules, selected source ranges, viewer code/configuration, CI and representative tests were inspected; binary/compressed geography datasets, the PDF diagram, all generated JavaScript assets and every test assertion were not exhaustively analyzed. Findings indicate review priorities, not necessarily reproduced production failures. No project source code was changed.
