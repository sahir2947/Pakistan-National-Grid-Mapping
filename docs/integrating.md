# Integrating pakgrid into Dukaandaari: the app team's guide

This page is for **Dukaandaari's developers**. It lists, in order, what the
Dukaandaari backend, its Flutter app and its operations must do to run pakgrid
in production. It also says what pakgrid already guarantees, so you don't
build those things twice. It turns the work items in Codex's
production-readiness assessment (`codexreview.md`, 17 November 2026) into
concrete steps.

**pakgrid stays a standalone library.** Nothing here changes pakgrid for
Dukaandaari. Everything below is written, owned and tested **in Dukaandaari's
own repositories**: settings, permission rules, tests, monitoring and
deployment. pakgrid supplies the grid, the walkthrough app and the hooks; the
app decides who may do what and how much.

The model names in the examples (`ShopProfile`, `Order`, `user.role`) are
placeholders for your own. Rename them to match your code.

| Step | What | Needed for |
|---|---|---|
| [1](#1-install-the-core-and-store-addresses) | Install the core, build the grid database, store addresses | Everything |
| [2](#2-permissions-for-walkthroughs) | Permission rules for walkthroughs, and their tests | Walkthroughs |
| [3](#3-resource-limits) | Upload, job, storage and retention limits | Walkthroughs |
| [4](#4-the-map-viewer-and-the-phone-app) | The map viewer and the phone app | Viewer, rider guidance |
| [5](#5-rolling-out-a-new-grid-database) | Rolling out a new grid database, and rolling back | Everything |
| [6](#6-observability) | Metrics, logs, alerts and runbooks | Everything |
| [7](#7-staging-qualification) | A staging run of the real deployment | Before real users |
| [8](#8-security-and-privacy-review) | A security and privacy review | Before real users |
| [9](#9-controlled-rollout) | A gradual rollout with stop conditions | Before real users |
| [10](#10-acceptance-checklist) | The acceptance checklist | Sign-off |

You can adopt pakgrid in two stages:

- **Stage A, addresses only:** steps 1, 5 and 6. No uploads, no workers, no
  files. This is the low-risk part, and most of the value: sub-cell IDs, grid
  addresses, place names, batching, zones and rider guidance.
- **Stage B, walkthroughs:** add steps 2 and 3. Walkthroughs take uploads
  (video, GPS tracks), run ffmpeg on a worker, and store photos. Turn them on
  only after the permission tests and limits are in place.

Steps 7 to 9 apply to whichever stage goes to real users.

---

## 1. Install the core and store addresses

Follow [docs/django.md](django.md) §1–§6 exactly. It is tested code. In short:

**Python version.** pakgrid needs Python 3.10 or later, and is tested on 3.10
to 3.14 in CI. Dukaandaari's 3.14 is covered by the main test matrix and by
the Django, PostgreSQL, ffmpeg and Celery job. On 3.14, the Pakistan edition
built bit-for-bit identical to the official checksum. When you move to a
newer Python (3.15 and later), run pakgrid's tests and `pakgrid verify` on
it before deploying, and tell the pakgrid repository so it can be added to
CI. Grid IDs cannot shift with the Python version: every row and column count
is far from a rounding boundary, and boundaries are stored as integers. If a
new Python ever changed a single stored value, `pakgrid verify` would fail
against the official checksum, so a bad database never reaches production.

1. Pin pakgrid to a **tag or commit** in `requirements.txt`, and use the same
   one in the Dockerfile's database stage (§1, §3).
2. Build the **Pakistan edition** in the image build and check it with
   `pakgrid verify --expect <official checksum>` (§2–§3). A build that does not
   verify must fail the image build.
3. Set `PAKGRID_DB`. Open the grid **once per process** (`get_grid()`), and
   call it from `AppConfig.ready()` so a missing or wrong database stops
   start-up, not the first request (§4).
4. Map pakgrid's errors to API errors (§4, §8). For every address, store
   `sub_cell_id`, `cell_id`, `grid_address`, the **original pin** and its
   accuracy (§5). Check `in_pakistan` when saving.
5. Run live rider navigation **on the phone**, not through the API (§7).
6. Your CI runs `manage.py check --deploy` and the full test suite. Because
   `ready()` opens the grid, CI needs a database: build the small Lahore slice
   from §9 for tests.

**Already done by pakgrid; don't rebuild these:**

- Grid IDs are frozen (grid version 1). Stored IDs stay valid across pakgrid
  upgrades.
- `Grid.open` refuses a database built for other rules
  (`GridMismatchError`).
- Builds are atomic: a failed build leaves no half-written file.
- The `Grid` is safe across threads and across `gunicorn --preload` forks.

---

## 2. Permissions for walkthroughs

Only needed if you enable walkthroughs (`pakgrid.contrib.walkthroughs`;
[docs/django.md §6](django.md#6-api-endpoints), "Walkthroughs").

### 2.1 Choose a `place_ref` format

`place_ref` is **your** reference for the address a walkthrough leads to (at
most 100 characters). There is at most one live walkthrough per `place_ref`,
and approving a new one replaces it for every rider. So a `place_ref` must
name exactly one address, across all tenants and regions, for good:

```python
# Use a prefix and a primary key that is never reused, e.g. "shop:1234".
def place_ref_for(shop) -> str:
    return f"shop:{shop.pk}"

def shop_for(place_ref: str):
    kind, _, pk = place_ref.partition(":")
    if kind != "shop" or not pk.isdigit():
        return None
    return ShopProfile.objects.filter(pk=int(pk)).first()
```

Never use a shop name, a phone number or anything a user can edit.

### 2.2 Write the four rules

Put them in one module, for example `shops/walkthrough_rules.py`:

```python
from django.db.models import Q

ACTIVE_ORDER = ["ready_for_pickup", "in_transit"]       # your statuses while a rider is on the way


def _ok(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.status == "active")


def can_upload(user, place_ref: str) -> bool:
    """Who may record, and publish, directions to this address: the shop's own verified owner.
    pakgrid calls this on upload, on approval and before AUTO_APPROVE publishes."""
    if not _ok(user) or not place_ref:            # every Dukaandaari walkthrough belongs to a shop
        return False
    shop = shop_for(place_ref)
    return shop is not None and shop.user_id == user.pk and user.role == "shop_owner"


def can_manage(user, walkthrough) -> bool:
    """Review, approve, reject, retry, delete, list: the shop's owner now (not only whoever
    uploaded), and support staff with an explicit permission."""
    if not _ok(user):
        return False
    if user.has_perm("walkthroughs.moderate"):    # a narrow staff permission, not is_staff
        return True
    shop = shop_for(walkthrough.place_ref)
    return shop is not None and shop.user_id == user.pk


def manage_queryset(user, qs):
    """The same rule as can_manage, in the database, so the list never scans other tenants."""
    if not _ok(user):
        return qs.none()
    if user.has_perm("walkthroughs.moderate"):
        return qs
    refs = [place_ref_for(s) for s in ShopProfile.objects.filter(user=user)]
    return qs.filter(place_ref__in=refs)


def can_view(user, walkthrough) -> bool:
    """A live walkthrough: the rider assigned to an active order to this shop, while it is active."""
    if not _ok(user) or user.role != "logistics_provider":
        return False
    shop = shop_for(walkthrough.place_ref)
    return shop is not None and Order.objects.filter(
        shop=shop, rider=user, status__in=ACTIVE_ORDER).exists()
```

```python
# settings.py
PAKGRID_WALKTHROUGHS = {
    "JOB": "celery",
    "JOB_TIMEOUT_S": 1200,
    "CAN_UPLOAD": "shops.walkthrough_rules.can_upload",
    "CAN_MANAGE": "shops.walkthrough_rules.can_manage",
    "MANAGE_QUERYSET": "shops.walkthrough_rules.manage_queryset",
    "CAN_VIEW": "shops.walkthrough_rules.can_view",
}
```

Notes:

- The rules run **on every request**, so a revoked owner, a suspended
  account or a finished order loses access at once. Nothing is cached in
  pakgrid.
- `can_upload` is called with `place_ref=""` when the app sends none.
  Refuse it, as above. Dukaandaari has no use for walkthroughs without an
  address.
- `can_manage` and `manage_queryset` must agree. pakgrid narrows with
  `manage_queryset` first, then checks `can_manage` on each row. A row that
  `manage_queryset` lets through but `can_manage` refuses is merely hidden;
  a row that `manage_queryset` wrongly drops never appears in the list.
- Riders get walkthroughs only through `GET walkthroughs/for/<place_ref>/`.
  Anything a user may not see answers **404**, so its existence is not given
  away.
- Phase 3 builds the rider assignment. Until it exists, keep the default
  `CAN_VIEW` (owner and staff only), or leave walkthroughs off.

### 2.3 Refuse to start without the rules

pakgrid warns (`pakgrid_walkthroughs.W001`) when `CAN_UPLOAD` is missing, and
then fails closed. In production, make every rule **required**:

```python
# shops/checks.py (imported from ShopsConfig.ready())
from django.conf import settings
from django.core.checks import Error, register
from django.utils.module_loading import import_string

REQUIRED = ("CAN_UPLOAD", "CAN_MANAGE", "MANAGE_QUERYSET", "CAN_VIEW")


@register(deploy=True)
def walkthrough_rules(app_configs=None, **kwargs):
    if "pakgrid.contrib.walkthroughs" not in settings.INSTALLED_APPS:
        return []
    conf, errors = getattr(settings, "PAKGRID_WALKTHROUGHS", {}), []
    for name in REQUIRED:
        try:
            import_string(conf[name])
        except (KeyError, ImportError) as exc:
            errors.append(Error(f"PAKGRID_WALKTHROUGHS['{name}'] is missing or cannot be imported: {exc}",
                                id="shops.E101"))
    return errors
```

`deploy=True` makes it run in `manage.py check --deploy`, which your CI
already runs before every deploy. Never add `pakgrid_walkthroughs.W001` to
`SILENCED_SYSTEM_CHECKS`.

### 2.4 The permission-matrix tests

Write these in Dukaandaari's suite, on PostgreSQL, against the real URLs. For
each actor, each call must give exactly the expected answer:

| Actor | Upload for shop A | List | Detail, reject, retry, delete of A's | Approve A's | `for/shop:A/` (live) |
|---|---|---|---|---|---|
| Owner of shop A | 201 | A's only | 200 | 200 | 404, unless also its rider |
| Owner of shop B | 403 | B's only, never A's | 404 | 404 | 404 |
| Delegate of A (if you add delegates) | as your rule says | A's | 200 | as `can_upload` says | 404 |
| Staff with `moderate` | 403 (staff do not record) | all | 200 | **403** | 404 |
| Staff without `moderate` | 403 | none | 404 | 404 | 404 |
| Rider with an active order to A | 403 | none | 404 | 404 | **200** |
| Rider whose order to A is delivered or cancelled | 403 | none | 404 | 404 | 404 |
| Rider with an order to B only | 403 | none | 404 | 404 | 404 |
| Suspended or pending account | 401 or 403 | none | 404 | 404 | 404 |
| Anonymous | 401 | 401 | 401 | 401 | 401 |

Approving publishes directions to the address, so pakgrid requires **both**
`can_manage` and `can_upload` from whoever approves. With the rules above,
staff can review, reject and delete, but only the shop's owner can make a
walkthrough live.

Also test:

- **Revocation:** transfer shop A to another user, suspend the owner, or
  finish the order. The next request is refused; nothing cached lets it
  through.
- **Approval re-checks upload rights:** a walkthrough waiting for review
  when shop A changes hands cannot be approved by the former owner (404),
  and the new owner can approve it only if `can_upload` allows them.
- **A crafted `place_ref`:** `"shop:abc"`, `"shop:"`, `"shop:1 "`, another
  tenant's ID and a 101-character value are all refused.
- **The list never leaks:** with 300 walkthroughs belonging to other shops,
  an owner's list shows only their own and their count is right (the
  200-row limit is applied after the rule).
- **The deploy check:** removing any one rule from settings makes
  `check --deploy` fail.

---

## 3. Resource limits

pakgrid's defaults are safe starting points, not measured values. Set each
limit from your own numbers. Review them after the staging soak (step 7).

### 3.1 pakgrid's settings

| Setting | Default | Recommendation |
|---|---|---|
| `UPLOAD_RATE` | `"30/hour"` per user | A shop records a few walkthroughs, ever: `"5/hour"` is plenty |
| `MAX_VIDEO_MB` | 200 | A 2-minute phone video at 1080p is ~60–120 MB; 150 is a fair cap |
| `MAX_TRACK_BYTES` | 5,000,000 | Keep (a track is also capped at 20,000 readings) |
| `JOB_TIMEOUT_S` | 1,200 | Set it to about 3× the slowest job measured in staging |
| `FFMPEG_TIMEOUT_S` | 60 per photo | Keep unless staging shows otherwise |
| `KEEP_VIDEO` | False | Keep False: the video is deleted once the photos are taken |
| `AUTO_APPROVE` | False | Keep False: the owner sees the photos before riders do |

**`UPLOAD_RATE` needs a shared cache.** It is counted in Django's cache. With
the default per-process memory cache, each worker counts on its own, so the
real limit becomes the setting times the number of processes. Point
`CACHES["default"]` at your Redis:

```python
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache",
                      "LOCATION": os.environ["REDIS_URL"]}}
```

### 3.2 Limits that belong in the app

pakgrid limits each user. Per-shop and global limits are business rules, so
they belong in `can_upload`, which runs before anything is stored:

```python
from datetime import timedelta
from django.utils import timezone
from pakgrid.contrib.walkthroughs.models import Walkthrough

MAX_PROCESSING_PER_SHOP = 1
MAX_PER_SHOP_PER_DAY = 5
MAX_PROCESSING_TOTAL = 50                       # the whole platform: protects the workers


def within_limits(place_ref: str) -> bool:
    if not settings.WALKTHROUGH_UPLOADS_ENABLED:     # the kill switch, see 6.3
        return False
    busy = Walkthrough.objects.filter(status=Walkthrough.PROCESSING)
    if busy.count() >= MAX_PROCESSING_TOTAL or busy.filter(place_ref=place_ref).count() >= MAX_PROCESSING_PER_SHOP:
        return False
    day = timezone.now() - timedelta(days=1)
    return Walkthrough.objects.filter(place_ref=place_ref, created_at__gte=day).count() < MAX_PER_SHOP_PER_DAY


def can_upload(user, place_ref):
    return owns_shop(user, place_ref) and within_limits(place_ref)
```

Here `owns_shop` is the body of `can_upload` from 2.2, renamed. Keep the
ownership check first, so a user who is not the owner never learns whether a
shop is at its limit.

A refusal here answers 403. Keep the reason in your own logs, and have the
app show "try again later".

### 3.3 Nginx, gunicorn and the workers

- **Nginx:** set `client_max_body_size` to `MAX_VIDEO_MB` plus about 10 MB,
  **on the upload route only**, and keep it small everywhere else. Set
  `client_body_timeout` and `proxy_read_timeout` long enough for a 150 MB
  upload on a slow mobile connection (start at 300 s), and
  `proxy_request_buffering on`, so a slow upload ties up Nginx, not a
  gunicorn worker.
- **The upload request** stores the video and answers at once with
  `processing`. The photos are taken on a worker. Never use `JOB: "sync"`
  or `"thread"` in production.
- **Celery:** route the task to its own queue and its own workers, so a
  backlog of photo jobs can never delay order or notification tasks:

  ```python
  PHOTO_TASK = "pakgrid.walkthroughs.take_photos"
  CELERY_TASK_ROUTES = {PHOTO_TASK: {"queue": "walkthroughs"}}
  # a hard stop just after pakgrid's own JOB_TIMEOUT_S, for this task only
  CELERY_TASK_ANNOTATIONS = {PHOTO_TASK: {"time_limit": PAKGRID_WALKTHROUGHS["JOB_TIMEOUT_S"] + 120}}
  ```

  ```console
  celery -A dukaandari worker -Q walkthroughs --concurrency 2 --prefetch-multiplier 1 \
      --max-memory-per-child 600000
  ```

  `--prefetch-multiplier 1` keeps each process to one long job at a time;
  `--max-memory-per-child` is in KiB (about 600 MB here).

  ffmpeg uses a whole core while it works, so keep the concurrency at or
  below the cores you give the container. Set CPU and memory limits on that
  container, and keep it separate from the web containers.
- **A job whose worker died** is restarted by pakgrid after 30 minutes
  without news. With Celery, also schedule `manage.py process_walkthroughs`
  every 10 minutes: it picks up such jobs, and any job whose task was lost
  before it ran.

### 3.4 Storage, retention and clean-up

- Use a **private bucket** for walkthroughs (`STORAGE`, for example
  django-storages' S3 storage with query-string auth). Give links that expire
  in minutes, not hours (`AWS_QUERYSTRING_EXPIRE = 600`). The rider app
  downloads the photos once, when the order is assigned.
- **Retention:** decide how long each status is kept, and delete on a
  schedule. Deleting a record deletes its files once the deletion commits:

  ```python
  # shops/management/commands/expire_walkthroughs.py: run daily
  KEEP_DAYS = {"rejected": 30, "failed": 14, "replaced": 30, "review": 60}

  for status, days in KEEP_DAYS.items():
      old = Walkthrough.objects.filter(status=status, created_at__lt=timezone.now() - timedelta(days=days))
      for wt in old.iterator(chunk_size=100):
          wt.delete()
  ```

  Also delete a shop's walkthroughs when the shop is deleted or asks to be
  removed (see step 8).
- **Stray files:** pakgrid stores new photos under a new
  `walkthroughs/<id>/<generation>/` folder and deletes the old ones after the
  switch commits. It also discards files from a job that fails or loses its
  claim. Only a worker **killed mid-job** (out of memory, `SIGKILL`) can leave
  an unused folder behind. A weekly sweep lists `walkthroughs/` in the
  bucket and deletes any `<id>/<generation>/` folder that no
  `WalkthroughPhoto` points at and that is older than one day. The bucket's
  total size, against the expected size, is your alert (step 6).
- **Storage quota:** set an alert on the bucket's size, and a hard bucket
  quota if your provider supports one.

### 3.5 Load and abuse tests

In staging (step 7), test at and beyond each limit:

- every refusal code at its boundary: `track_too_large`,
  `too_many_readings`, `video_too_large`, `bad_track` (malformed JSON and GPX,
  an XML bomb, non-UTF-8), and 429 past `UPLOAD_RATE`;
- one shop uploading in a loop: refused after the per-shop limit, and other
  shops are not affected;
- fill the `walkthroughs` queue far past what the workers can do in an hour.
  Order placement, the cart and `/api/health/` must stay at their normal
  latency. This is what the separate queue and containers are for.

---

## 4. The map viewer and the phone app

### 4.1 The viewer (`viewer/`)

The viewer is a static page for checking the grid and trying features. It is
**not** Dukaandaari's app. If you serve it (to support staff, say):

- It loads nothing from other hosts except map tiles. Leaflet is in
  `viewer/vendor/leaflet/`, checked against its integrity hashes.
- **Tiles:** edit `viewer/tiles.js`. The default, `tile.openstreetmap.org`,
  allows light use only and is **not** for an app's own users. Point it at a
  provider whose terms allow your traffic (or your own tile server), with the
  attribution that provider requires. Or set its `url` to `""` for no
  background map at all. Re-run the browser check afterwards:
  `node scripts/check_viewer_in_browser.js viewer index.html`.
- Serve it compressed (gzip or brotli), with long cache lifetimes for the
  `.js` files. They change only when pakgrid is upgraded.
- Keep the data attributions it shows (geoBoundaries, UN OCHA,
  OpenStreetMap/HOT).

### 4.2 The Flutter app

The phone app talks only to **your** API, never to pakgrid directly. It runs
two things on the phone, ported from pakgrid's published rules:

- **Rider navigation:** the rules are in `conformance/NAVIGATION.md`. Your
  Dart port must pass all of `conformance/navigation.json`.
- **The walkthrough rider rule:** `conformance/WALKTHROUGH.md`, checked with
  `conformance/walkthrough.json`.

Run both conformance files in the app's CI, and update them whenever you
upgrade pakgrid. A port that passes them gives the same answers as the
library. `viewer/pakgrid.js` is a complete example port in JavaScript.

**Map tiles in the app** are a separate decision from the viewer's.
`flutter_map` with `tile.openstreetmap.org` falls under the same usage policy:
pick a provider for production.

### 4.3 Measure on a real low-end phone

The viewer was measured only in Chromium with the CPU slowed 6× (map painted
after ~1.0 s, places ready after ~1.2 s on a fast network). Before relying on
it, or on the same data in the app, measure on a representative low-end
Android phone (2–3 GB RAM) over a slow 3G/4G connection:

- time to the first map, and time until a tap answers;
- memory while `places.js` (2.8 MB compressed, 9.4 MB raw) is parsed;
- search, drawing and navigation responsiveness;
- what the user sees when tiles fail to load.

Write down the budgets you accept. If the places data is too heavy for
your users' phones, raise it on the pakgrid repository: splitting it by
region is planned but waiting for exactly this measurement.

---

## 5. Rolling out a new grid database

The grid database is opened **read-only and immutable**. SQLite then skips
all locking, which is what makes it fast, but it also means **a file must
never change while a server has it open**. The procedure is in
[docs/django.md §11](django.md#11-upgrading-pakgrid). For Dukaandaari:

1. **Build outside the running path,** in CI or the image build: `pakgrid
   build` (and `build-places` if the places version changed), then `pakgrid
   verify --expect <official checksum>`. A build that does not verify fails
   the pipeline.
2. **Publish under a versioned name** that is never reused, e.g.
   `pakgrid_v1_pakistan_<library version>_<checksum[:12]>.sqlite`, or bake it
   into a versioned image.
3. **Switch deliberately:** deploy the image, or move the `current` symlink
   with `ln -sfn` and restart every worker, Celery included.
4. **Keep the previous verified file** until the next release. Rolling back
   means pointing back at it and restarting.
5. **Never** `cp`, rebuild or edit onto a path a running process has open.

**The deployment receipt.** Record these on every deploy in your deployment
log, and expose them on your health endpoint:

| Field | Where it comes from |
|---|---|
| App commit | your CI |
| pakgrid version | `pakgrid.__version__` |
| Grid database checksum | `get_grid().info()["database"]["content_sha256"]` |
| Places layer | `get_grid().info()["places"]["version"]` and `["places_sha256"]` |
| Rollback target | the previous receipt |

```python
# in your /api/health/ view
import pakgrid
info = get_grid().info()
health["pakgrid"] = {"version": pakgrid.__version__,
                     "grid_sha256": info["database"]["content_sha256"],
                     "places_version": (info["places"] or {}).get("version")}
```

Your deploy check (which already compares the commit) should also compare
`grid_sha256` with what CI verified. A mismatch fails the deploy and rolls
back.

**Migrations.** The walkthrough app's migrations are **forward-only data
changes** in places: `0003` marks duplicate live walkthroughs as `replaced`.
Take a database backup before the deploy that first applies them. Rolling
back the code does not undo them. That is safe, because older pakgrid
versions read the result fine, but it is not a reversal.

---

## 6. Observability

### 6.1 What to measure

| Signal | How | Alert when |
|---|---|---|
| API rate, latency, 4xx/5xx for `/api/locate`-style endpoints and `walkthroughs/` | your request logging or APM | 5xx > 1% for 5 min; p95 latency 2× normal |
| Refused uploads, by `code` | log the `code` from 400 responses | a sudden rise in one code (a client bug, or abuse) |
| 429s and app-limit refusals | log them | sustained, from one user or shop |
| Upload bytes per day | sum of `video.size` | above the budget you set in 3.4 |
| Jobs waiting | `Walkthrough.objects.filter(status="processing").count()` | above your worker capacity for an hour |
| Oldest waiting job | `min(created_at)` of `processing` | older than `JOB_TIMEOUT_S` + 30 min |
| Failed jobs | `status="failed"`, and pakgrid's log lines (below) | more than a few per hour |
| Live and replaced transitions | count of `live` and `replaced` by day | duplicate live per `place_ref` (impossible by constraint: alert if seen) |
| Storage errors and clean-up failures | pakgrid's warnings (below) | any |
| Bucket size | your storage provider | growth beyond expected |
| Grid checksum per instance | the health endpoint (step 5) | instances disagree |
| Viewer and tile errors | browser error reporting, if you serve the viewer | tile failures sustained |

A small management command, run every minute, can export the walkthrough
queue figures as metrics. They are plain queries on indexed columns.

### 6.2 pakgrid's log lines

The walkthrough app logs through the logger
`pakgrid.contrib.walkthroughs.services`. Send it to your log pipeline at
`WARNING`:

- `photos failed` (with the traceback for unexpected errors): a job failed;
  the record is `failed`, with the reason in `error`, and can be retried;
- `another photo job took this one over`: a stale worker woke up and stopped
  without changing anything (normal after a restart; alert if frequent);
- `could not delete the unused file` / `the video could not be deleted` /
  `could not keep the video for a retry`: a storage problem. The data stays
  consistent, but files may be left over: the sweep in 3.4 collects them.

pakgrid logs no personal data, tokens or coordinates. Keep your own request
logs the same: don't log uploaded tracks, pins with the user's identity, or
signed photo links.

### 6.3 Runbooks

Every alert needs an owner, a severity and a written response. At minimum:

- **Turn off walkthrough uploads without touching addresses.** Set
  `WALKTHROUGH_UPLOADS_ENABLED = False` (an environment variable read by
  `can_upload`, see 3.2). Uploads then answer 403, while live walkthroughs
  keep working for riders and grid lookups are untouched. To stop serving
  walkthroughs to riders too, return `False` from `can_view` the same way.
- **Queue backlog:** add workers on the `walkthroughs` queue, or turn off
  uploads. Nothing else is affected, because the queue is separate.
- **A failed job:** read `error`, then `POST walkthroughs/<id>/retry/`, or
  ask the shop to record again.
- **A grid checksum mismatch:** roll back to the previous receipt (step 5).

---

## 7. Staging qualification

Run the real deployment, end to end, before real users:

- **Components:** the production PostgreSQL version and connection pooling,
  Redis as the cache, the real bucket with signed links, real ffmpeg in the
  worker image, Celery on its own queue, Nginx with the limits from 3.3, and
  Dukaandaari's own sign-in, shops, orders and rider assignment.
- **A soak** of at least 24–72 hours at representative traffic: address
  lookups, uploads, approvals, replacements, rider fetches.
- **Fault injection:** kill a worker mid-job (the job is picked up again
  after 30 minutes and finishes once); make the bucket fail during a job (the
  previous photos stay); restart PostgreSQL during an approval; fill the
  queue (3.5).
- **Afterwards, check:** no duplicate live walkthroughs; no files without
  records (the sweep finds nothing older than a day); no cross-tenant answers
  in any log; every failed job explained.
- **Backup and restore:** restore last night's backup into a scratch
  database and bucket, and open a walkthrough from it.
- **Rollout and rollback:** deploy a new grid database (step 5) during the
  soak, then roll it back, with no failed requests.

Record the results, with dates and figures, in your deployment log.

---

## 8. Security and privacy review

The review of pakgrid's source was not a penetration test of Dukaandaari.
Before public rollout:

- **Authorization:** the matrix in 2.4, plus a tester trying other tenants'
  IDs, UUIDs and `place_ref` values on every route.
- **Uploads:** malformed and hostile multipart bodies, JSON, GPX (entity
  expansion, deep nesting) and video files (wrong containers, corrupt
  streams, huge resolutions). pakgrid refuses bad tracks with codes and runs
  ffmpeg with a timeout per photo. The worker container should run as a
  non-root user, with no network access beyond the bucket and the database,
  and with ffmpeg kept up to date.
- **The bucket:** private, no public listing, links that expire in minutes,
  server-side encryption, and only the web and worker roles allowed to write.
- **The web side:** CSRF, CORS (only your app's origins), cookie flags and
  JWT handling, as for the rest of the API.
- **Dependencies:** scan the images and `requirements.txt` (for example
  `pip-audit` and your registry's image scanner) in CI.
- **Personal data:** a walkthrough is video and photos of the route to
  someone's door, plus a GPS track. Decide and document:
  - who the data controller and processors are;
  - how long videos (deleted after the photos by default), photos and tracks
    are kept (3.4);
  - how a shop deletes its walkthroughs, and that deleting a shop deletes
    them;
  - that riders' locations used for navigation stay on the phone (pakgrid's
    navigation does not send them anywhere).
- **Audit:** approvals and rejections are recorded on the walkthrough
  (`reviewed_by`, `reviewed_at`, `review_note`). If you need a full history
  (who deleted what), log your own calls around pakgrid's.

---

## 9. Controlled rollout

Turn features on in steps, each with written stop conditions:

1. Internal staff and synthetic shops.
2. One controlled region (for example Lahore) with a few real shops.
3. A small share of eligible shops and orders.
4. Wider, while watching the alerts from step 6.
5. Everyone, only after the soak and a quiet observation period.

**Stop and roll back** on any authorization anomaly, a duplicate live
walkthrough, a sustained job backlog, storage inconsistency, a map or tile
outage that users notice, or a clear rise in latency or errors.

A per-region switch in `can_upload` (and `can_view`) is enough to stage
walkthroughs. Addresses can be staged the same way in your own views.

---

## 10. Acceptance checklist

**Dukaandaari's side:**

- [ ] pakgrid pinned to a tag or commit; the Pakistan edition built and
      verified in CI; the grid checked in `ready()` (step 1)
- [ ] Addresses store sub-cell ID, cell ID, grid address, the original pin
      and accuracy (step 1)
- [ ] `place_ref` format fixed and tenant-unique (2.1)
- [ ] All four rules set, and `check --deploy` refuses to start without them
      (2.2, 2.3)
- [ ] The permission matrix, revocation and crafted-`place_ref` tests pass on
      PostgreSQL (2.4)
- [ ] Limits set from measured demand; `UPLOAD_RATE` counted in Redis;
      per-shop and global limits in `can_upload` (3.1, 3.2)
- [ ] Nginx, Celery queue, worker concurrency and container limits set (3.3)
- [ ] Private bucket, short-lived links, retention job and stray-file sweep
      running (3.4)
- [ ] Load and abuse tests pass; the API stays responsive with a full queue
      (3.5)
- [ ] Tile provider chosen for the app (and the viewer, if served) (4)
- [ ] The Flutter ports pass `navigation.json` and `walkthrough.json` in CI (4.2)
- [ ] Low-end Android measurements meet written budgets, or an approved
      exception is recorded (4.3)
- [ ] Grid database rollout and rollback rehearsed; the receipt recorded and
      on the health endpoint (5)
- [ ] Metrics, alerts with owners, and runbooks in place, including the
      upload kill switch (6)
- [ ] Staging soak, fault injection, backup restore and rollback passed (7)
- [ ] Security and privacy review done; retention and deletion approved (8)
- [ ] A limited pilot finished with no severity-one or data-integrity
      incident (9)

**pakgrid's side (not Dukaandaari's work):**

- [ ] The lawyer-approved licence published, with matching package metadata
- [ ] Required checks and protected-branch rules enabled on pakgrid's `main`

## What pakgrid already does

So you don't build it twice, and so you know what to rely on:

| Guarantee | Where it is tested |
|---|---|
| Grid IDs, codes and boundaries frozen (grid version 1); official checksums | `tests/test_*`, `checksums.yml` on Linux, macOS and Windows |
| Atomic database builds; a failed build or places rebuild leaves the old file | `tests/test_build_verify.py`, `tests/test_places.py` |
| `Grid` safe across threads and forked workers | `tests/test_concurrency.py` |
| Bad tracks refused with stable codes; size and reading limits enforced while reading | `tests/test_walkthrough.py`, `tests/test_django_walkthroughs.py` |
| `CAN_UPLOAD` fails closed, and is re-checked on approval and auto-approval | `tests/test_django_walkthroughs.py` |
| Hidden records answer 404; IDs are random UUIDs | `tests/test_django_walkthroughs.py` |
| One live walkthrough per `place_ref`, enforced by the database, even under concurrent approvals (on PostgreSQL) | `tests/test_django_walkthroughs.py` |
| A stale worker cannot publish; failed or interrupted jobs leave the previous photos intact and no stray files | `tests/test_django_walkthroughs.py` |
| Python and JavaScript give the same answers (2,016 navigation and 610 walkthrough cases) | `tests/test_viewer_js.py`, `conformance/` |
| The installed wheel and sdist work from outside the source tree | CI job "Installed package" |
| The viewer loads its code from its own files, and works with no tiles | `tests/test_viewer_js.py`, `scripts/check_viewer_in_browser.js` |

Report a problem with any of these on the pakgrid repository (security
problems privately: see [SECURITY.md](../SECURITY.md)).
