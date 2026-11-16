# Pakgrid

Local review record for the Pakistan National Grid Mapping library.

Upstream repository: [sahir2947/Pakistan-National-Grid-Mapping](https://github.com/sahir2947/Pakistan-National-Grid-Mapping)

## Codex Review

**Reviewed:** 16 November 2026  
**Commit:** [`e2b9b5a7387c90d2f508b8c79d6f7585a62241fc`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/tree/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc) (`main`)  
**Scope:** Python grid core, SQLite builder/store/verifier, identifiers and addresses,
places, zones, GeoJSON, nearest search, user places/roads/access, navigation,
walkthrough processing, optional Django/DRF app, CLI, packaging, browser viewer,
tests, CI, security policy, and integration documentation.

### Executive assessment

The deterministic grid core is thoughtfully designed and substantially stronger
than the earlier October review suggested. Its integer-coordinate layout,
permanent IDs, compact records, official checksums, exhaustive geometry tests,
Python/JavaScript conformance design, standard-library-only core, type coverage,
and clear separation of personal data from the public grid are sound foundations.

The project is **not ready for a production v0.1 release as a whole**, however.
The optional Django walkthrough feature has unsafe default authorization,
unbounded input and processing paths, and concurrency invariants that are not
enforced by the database. The core also has a destructive overwrite path and a
reproducible navigation crash on an invalid timestamp. Those P1 findings should
be closed before release or before Dukaandaari exposes the affected features.

No upstream source code was changed during this review.

### Verification performed

| Check | Result |
|---|---|
| Full local pytest suite | **380 passed, 27 skipped** in 80â€“86 seconds |
| Ruff 0.17.5 | **Passed** |
| mypy 2.4.1 | **Passed: 44 source files** |
| Wheel build | **Passed**, `pakgrid-0.1.0-py3-none-any.whl` |
| Clean wheel install | **Passed** |
| Installed import and CLI smoke test | **Passed**, version `0.1.0`, full `pakgrid --help` |
| Targeted navigation probe | **Failed as reproduced:** `time=float("nan")` raises `ZeroDivisionError` |
| Malformed GPX probe | **Failed as reproduced:** malformed XML escapes as `ElementTree.ParseError` |

The 27 skips were caused by unavailable local Node.js, ffmpeg and Celery, the
absence of `os.fork` on Windows, and one permitted platform-last-digit
conformance difference. GitHub CI is configured to run Node-based parity tests
on Linux, macOS and Windows, but this review did not independently execute those
skipped paths.

### Prioritized findings

#### CR-01 â€” P1 High: default walkthrough permissions allow authenticated content takeover

**Evidence:**

- [`can_upload`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L36-L38)
  allows any authenticated user when `CAN_UPLOAD` is not configured.
- [`can_manage`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L41-L45)
  lets the uploader approve their own record by default.
- Approval makes it live, and
  [`_replace_others`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L229-L261)
  replaces every live walkthrough with the same caller-supplied `place_ref`.

An authenticated attacker who knows a shop/address reference can upload and
approve a walkthrough for that reference, replacing the legitimate live
walkthrough. If an app configures only `CAN_VIEW` for assigned riders but omits
`CAN_UPLOAD`, riders can then receive attacker-controlled directions and photos.
The integration guide demonstrates a safe hook, but the runtime fails open if
the integrator forgets it.

**Recommendation:** fail closed for non-empty `place_ref` unless an explicit
`CAN_UPLOAD` hook is configured; add a Django system check that blocks unsafe
production configuration; scope references by tenant/owner; and add an
attacker-versus-owner regression test. Recheck authorization inside the same
locked transaction that publishes the walkthrough.

#### CR-02 â€” P1 High: unbounded track input and quadratic smoothing enable authenticated denial of service

**Evidence:**

- [`parse_track`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L56-L72)
  reads an uploaded track file completely into memory with no byte limit.
- [`walkthrough.build`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/walkthrough.py#L208-L245)
  materializes and sorts all readings, then performs a nested scan over every
  point for smoothing: O(nÂ²) time.
- The default upload rule permits every authenticated user, and there is no
  feature-specific throttle or point-count limit.
- Photo processing launches one ffmpeg process per frame, each with its own
  timeout, further amplifying CPU work.

A small account can submit a very large JSON/GPX track independently of the
video-size limit, consuming worker memory and quadratic CPU before the request
is rejected or queued.

**Recommendation:** enforce `MAX_TRACK_BYTES`, `MAX_TRACK_POINTS`, a sensible
sampling rate and early duration bounds before sorting; replace the nested
smoothing scan with a two-pointer/sliding-window algorithm; apply DRF upload
throttles and per-user quotas; and impose a total job deadline and queue-level
resource limits. Add adversarial size and complexity tests.

#### CR-03 â€” P1 High: the â€œone live walkthrough per addressâ€ invariant is race-prone

**Evidence:**

- The model has only a normal `(place_ref, status)` index and no conditional
  uniqueness constraint
  ([`models.py`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/models.py#L77-L105)).
- [`approve`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L229-L239)
  saves the selected row and then bulk-replaces visible competitors without
  locking the reference or enforcing a database invariant.
- The auto-approval photo path uses the same replacement pattern.

Under PostgreSQL `READ COMMITTED`, two approvals for the same `place_ref` can
each fail to see the other uncommitted row and both commit as `live`.
`live_for()` then silently selects whichever sorts first instead of reporting
the broken invariant.

**Recommendation:** add a conditional unique constraint for non-empty
`place_ref` where `status="live"`; lock a stable address/ownership row or use an
advisory lock; handle the uniqueness collision deterministically; and test
simultaneous manual and automatic approvals on PostgreSQL, not only SQLite.

#### CR-04 â€” P1 High: overwrite deletes the good database before validation or rebuilding

[`build_database`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/builder.py#L146-L161)
unlinks the destination before parsing the requested quadrants/rows and before
opening the temporary build. An invalid selection, disk-full error, interrupt,
places-layer failure, or process crash therefore destroys the previous good
database. The final `os.replace()` does not make the operation atomic because
the old file was already removed.

**Recommendation:** validate all arguments first, build and verify a uniquely
named sibling temporary file, flush/fsync it as appropriate, and only then use
`os.replace()` over the intact destination. On every failure, delete only the
temporary file and prove the original bytes are unchanged with regression tests.

#### CR-05 â€” P2 Medium: a non-finite navigation timestamp crashes Python and poisons JavaScript parity

The earlier sensor-validation fix covers coordinates, accuracy, speed and
heading, but not time. Python's [`_now`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/navigate.py#L285-L299)
accepts `NaN`/infinity after `float()`. In
[`add_fix`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/navigate.py#L196-L224),
the non-finite subtraction removes every fix and division by zero follows.

Reproduced:

```python
NavigationSession(31.52, 74.35).add_fix(
    31.51, 74.34, time=float("nan")
)
# ZeroDivisionError at self.position = (slat / sw, slon / sw)
```

The JavaScript copy converts time with unary `+` but does not check finiteness,
so it can produce a `NaN` position instead of raising.

**Recommendation:** define one timestamp contract across both ports. Reject or
ignore non-finite/non-numeric timestamps, decide how out-of-order readings are
handled, validate navigation options such as the smoothing window, and add
Python/JavaScript conformance cases for NaN, infinity, strings and time
regression.

#### CR-06 â€” P2 Medium: malformed client track data can become a 500 instead of a controlled 400

Malformed XML raises `xml.etree.ElementTree.ParseError`; invalid GPX numeric or
time fields raise `TypeError`/`ValueError`; invalid UTF-8 raises
`UnicodeDecodeError`. [`parse_track`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L56-L72)
does not normalize those errors, while the API handler maps only
`WalkthroughError` to HTTP 400
([`api.py`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/api.py#L73-L83)).
The malformed-GPX probe reproduced an uncaught `ElementTree.ParseError`.

`place_ref` is also stripped but not length-checked before saving into a
100-character `CharField`, which can behave differently on SQLite and
PostgreSQL.

**Recommendation:** wrap every parse/decode/date/number failure as a stable
`WalkthroughError`; explicitly validate `place_ref` type and length; return
machine-readable error codes; and add malformed JSON, XML, encoding, timestamp,
coordinate and overlong-reference API tests.

#### CR-07 â€” P2 Medium: custom `CAN_MANAGE` grants are ignored by the list endpoint

The documented hook is supposed to decide who may manage a walkthrough, and
detail actions use it. The list endpoint first hard-filters every non-staff
user to `owner=request.user`, then calls the hook
([`api.py`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/api.py#L86-L95)).
A team manager granted access by `CAN_MANAGE` can open a known UUID but cannot
discover the same record in the management list. Because IDs are intentionally
unguessable, the hook cannot practically grant shared management.

**Recommendation:** provide a permission-aware queryset hook, or apply the
owner-only filter only when no custom hook exists. Authorize before pagination
so the first 200 database rows do not truncate the permitted result set. Test
owner, delegated manager, staff and denied-user list/detail parity.

#### CR-08 â€” P2 Medium: photo files and database rows are not transactionally consistent

The photo job deletes existing `WalkthroughPhoto` rows/files and saves new
files inside a database transaction
([`services.py`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L170-L180)).
Django transactions cannot roll back filesystem or object-storage side effects.
If storage fails after several frames, database rows roll back while uploaded
objects remain orphaned; on a retry, deleted old files can be referenced again
after rollback but no longer exist. The current test covers failure on the
first write only.

**Recommendation:** upload a complete generation under a temporary/versioned
prefix, commit references only after all objects exist, and delete the old
generation with `transaction.on_commit`. Track and clean staged objects on
failure. Add fault-injection tests failing after frame N, during replacement,
and during cleanup.

#### CR-09 â€” P2 Medium: stale-job recovery is a timestamp reset, not a safe lease

[`process_pending`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/contrib/walkthroughs/services.py#L214-L224)
clears `photos_at` after 30 minutes and allows another worker to claim the same
record. There is no lease owner/token or heartbeat. A valid long-running worker
can therefore overlap its replacement. This is plausible because the job may
run roughly 100 frame extractions and `FFMPEG_TIMEOUT_S` applies separately to
each frame.

**Recommendation:** use a lease token and expiry with compare-and-swap,
heartbeat long jobs, verify the token before publishing, and cap total job
runtime. A stale worker must be unable to publish or delete the winning
worker's generation.

#### CR-10 â€” P2 Medium: `Zone` claims immutability but exposes a mutable, hash-defining dictionary

`Zone` is a frozen dataclass documented as immutable, but `__post_init__`
stores an ordinary public dictionary
([`zones.py`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/zones.py#L223-L233)).
Its hash is calculated from that dictionary. Mutation therefore corrupts set
and dictionary membership and leaves any prebuilt `ZoneIndex` stale.

Reproduced:

```python
z = Zone({1: 1})
seen = {z}
z.masks[2] = 1
assert z not in seen  # true: its hash changed after insertion
```

**Recommendation:** keep masks private and expose a read-only mapping, use an
actually immutable/hash-stable representation, or remove hashing and the
immutability claim. `ZoneIndex` should copy/normalize its inputs. Add mutation,
hash-stability and index-consistency tests.

#### CR-11 â€” P2 Medium: places-layer replacement is not failure-atomic

[`write_places`](https://github.com/sahir2947/Pakistan-National-Grid-Mapping/blob/e2b9b5a7387c90d2f508b8c79d6f7585a62241fc/pakgrid/places.py#L726-L751)
drops the live tables before recreating and filling them. Python SQLite
`executescript()` commits a pending transaction before executing the script, so
an insertion failure, disk exhaustion or interruption can leave the original
grid file with missing or partial place tables. The Django guide wisely says to
run this on a copy, but the library/CLI do not enforce that safe workflow.

**Recommendation:** build the layer in new tables or a copied database,
validate its checksum and counts, then atomically publish the new database.
Add failure-injection tests showing the original layer remains readable.

#### CR-12 â€” P3 Low: viewer delivery remains heavy for mobile and partly network-dependent

The static viewer ships approximately **9.4 MB** of `places.js` plus **0.95 MB**
of `boundary.js` before transport compression, and depends on externally hosted
Leaflet/map tiles. The code uses SRI for Leaflet and escapes displayed text,
which is good, but loading and parsing the full places payload can still hurt
low-end phones and the viewer is not fully offline.

**Recommendation:** publish compressed immutable assets, lazy-load/index place
data by region or search need, measure parse/interaction time on a low-end
Android device, document map-tile usage limits, and consider self-hosting pinned
Leaflet assets. Establish a viewer performance budget in release checks.

### Strengths confirmed

- Grid v1 IDs and geometry are deterministic and integer-based at 1e-7 degree.
- The verifier now uses `zip_longest`, so missing or trailing paired records are
  detected; the earlier truncation finding is fixed.
- Official full/Pakistan edition checksums and cross-platform checksum CI are a
  strong reproducibility mechanism.
- SQLite records are compact, and read access is isolated per thread and fork.
- The core package has no runtime dependencies and ships `py.typed`.
- Ruff, mypy and the Python suite pass; wheel build/install and CLI smoke tests
  also pass.
- Geometry, edge rules, identifiers, places, GeoJSON, zones, navigation and
  Python/JavaScript conformance have unusually broad tests.
- Personal data is explicitly kept outside the grid database.
- The README now labels its original proposal as superseded and points to the
  implemented 22-bit/27-bit design.
- Immutable-database deployment rollover is documented in the Django guide.
- The October review's claimed E7 bug in reference-only user-locality updates
  is **not a bug**: `_references` intentionally accepts E7 coordinates and
  divides by `SCALE`; dedicated regression tests pass.

### Test and release gaps

- Add PostgreSQL integration and true concurrent transaction tests for the
  optional Django app; SQLite cannot prove the publication invariant.
- Add wheel/sdist build, clean install, import, packaged-data and CLI smoke
  tests to CI. The manual wheel smoke test passed during this review.
- Add malformed/unbounded input, storage-failure-after-N, job-lease and
  permission-matrix tests.
- Keep Node parity tests mandatory; they were skipped locally because Node was
  unavailable.
- Exercise real ffmpeg, Celery and fork behavior in dedicated CI jobs or a
  release qualification environment.
- Run the full official 327 MB/837 MB builds and verification as a required
  release gate, in addition to the existing workflow.
- Publish the reviewed `LICENSE` before distribution. Until then the repository
  says all rights are reserved, and `pyproject.toml` has no final licence
  metadata.

### Recommended remediation order

1. Fail closed on walkthrough ownership (`CAN_UPLOAD`) and add abuse limits.
2. Bound and linearize track processing.
3. Enforce one live walkthrough per non-empty reference in PostgreSQL.
4. Make database overwrite and places-layer publication failure-atomic.
5. Fix navigation timestamp validation in both Python and JavaScript.
6. Normalize malformed track errors and validate all API field lengths/types.
7. Repair permission-aware listing, photo staging and job leases.
8. Make `Zone` truly immutable or unhashable.
9. Add missing concurrency, adversarial-input, packaging and external-tool
   release gates.
10. Profile and optimize viewer delivery, then finalize the licence.

### Release acceptance criteria

- An authenticated user cannot create, approve, replace or view a walkthrough
  for a resource they do not own or have an active authorization for.
- Large tracks are rejected before full parsing/processing, and accepted track
  processing is O(n) or O(n log n) within a declared bound.
- A database constraint guarantees at most one live walkthrough for each
  non-empty scoped reference under concurrent approvals.
- Failed `--overwrite` and places rebuilds preserve the prior database byte for
  byte.
- Invalid timestamps cannot crash Python or create non-finite JavaScript state;
  both ports pass the same conformance cases.
- Every malformed upload returns a bounded, stable 4xx response without a
  traceback or partially created record.
- Delegated `CAN_MANAGE` users see the same permitted records in list and detail
  APIs.
- Storage failures and stale workers leave no orphan files, broken references
  or duplicate publishers.
- `Zone` values cannot change after hashing/indexing.
- PostgreSQL concurrency, Node parity, ffmpeg, Celery, fork, packaging and full
  checksum gates pass for the release commit.

### Review limitations

This was a source, test and local execution review, not a formal penetration
test or geodetic certification. The compressed boundary/place datasets were
used by tests but not independently audited against their upstream geographic
sources. The full official databases were not rebuilt locally. Node, real
ffmpeg, Celery and fork tests were skipped for the environment reasons listed
above. Production PostgreSQL/S3 concurrency and mobile viewer performance were
reasoned about from code and documentation, not exercised against live
infrastructure.

