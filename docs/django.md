# Using pakgrid in a Django REST Framework backend

This guide sets up `pakgrid` inside a Django + DRF backend, with a Flutter (or
any other) app talking to that backend's API. It is how Dukaandari uses the
grid, but nothing here is specific to Dukaandari.

```
pakgrid (GitHub)              Your backend (Django DRF, container)                Your app (Flutter, …)
────────────────              ─────────────────────────────────────               ─────────────────────
library + grid rules ──pip──▶ pakgrid + the grid database (read-only SQLite) ◀─API── your screens
official checksums            your endpoints: locate a pin, save an address,      + live navigation on
                              batch orders by cell, find neighbouring cells          the phone (§7)
```

- The **backend** is the single source of truth. It decides which grid cell
  and sub-cell a pin belongs to, and stores the result with your own data.
- The **app** never opens the grid database. It calls your API.
- The **grid database** is built once, from the grid rules, and checked
  against the official checksum. It is never edited.

All the code below comes from a working project that was tested with
Django's test runner, under gunicorn with several worker processes and
threads, and as a Docker image built from the Dockerfile in §3.

Contents: [1. Install](#1-install) · [2. Build the database](#2-build-and-verify-the-database) ·
[3. Docker](#3-docker) · [4. Connect Django](#4-connect-django) · [5. Store locations](#5-store-locations) ·
[6. API endpoints](#6-api-endpoints) · [7. What the app does](#7-what-the-app-does) ·
[8. Errors](#8-errors) · [9. Testing](#9-testing) · [10. Performance](#10-performance) ·
[11. Upgrading](#11-upgrading-pakgrid) · [12. Checklist](#12-checklist)

---

## 1. Install

`pakgrid` needs Python 3.10 or later and has no dependencies. It is installed
straight from GitHub's download archive, which needs no `git`. Pin a release
tag or a commit, so every build of your backend gets exactly the same library:

```text
# requirements.txt
django
djangorestframework
gunicorn
pakgrid @ https://github.com/sahir2947/Pakistan-National-Grid-Mapping/archive/<tag or commit>.tar.gz
```

For example `…/archive/v0.1.0.tar.gz` for a release, or
`…/archive/<40-character commit SHA>.tar.gz`. (If you prefer git,
`pakgrid @ git+https://github.com/sahir2947/Pakistan-National-Grid-Mapping@<tag or commit>`
works too, but needs `git` installed.)

## 2. Build and verify the database

Choose an edition. Both have the same IDs; the Pakistan edition just leaves
out cells wholly outside Pakistan.

| Edition | Command | Size | Build time* | Use when |
|---|---|---|---|---|
| **Pakistan** (recommended) | `pakgrid build --pakistan-only -o pakgrid_v1_pakistan.sqlite` | ~327 MB | ~1.5 min | Your service only works inside Pakistan |
| Full | `pakgrid build -o pakgrid_v1.sqlite` | ~837 MB | ~2 min | You also need cells outside Pakistan's border |

\* Measured on one CPU core; a build needs about 650 MB of memory. Both
editions include the places layer (locality, tehsil, district and province
of every block, §6); `--no-places` leaves it out.

Then verify it:

```console
$ pakgrid verify --db pakgrid_v1_pakistan.sqlite
PASS  grid version  (database 1, library 1)
PASS  schema version  (database 2, library 2)
PASS  layout matches grid rules
PASS  row boundaries match grid rules
PASS  stored border matches its checksum  (86,591 vertices)
PASS  stored border is the one packaged with this library
PASS  record count  (872,146 cells)
PASS  border flag counts  (860,775 inside, 11,371 border, 0 outside)
PASS  content checksum  (6c5c2ec492a38f93…)
PASS  matches the official pakistan edition (grid v1)
PASS  places layer version  (database 2, library 2)
PASS  places checksum  (03ff666089c56b32…)
PASS  places layer matches the official places version 2
content sha256: 6c5c2ec492a38f93020b593c554e9d437b39d859948a2cc82e94e6605c30cb01
OK
```

`verify` recomputes a checksum over every stored record and compares it with
the **official checksum** published in the library (`pakgrid/checksums.py`).
The build is deterministic, so a passing database is identical in content to
the official one, built on any machine. It exits with status 0 when
everything passes and 2 otherwise, so scripts and Docker builds stop on a bad
database. `pakgrid verify --full` also regenerates every record from the
rules and compares them one by one (about as long as a build).

| Edition | Official content checksum (grid v1) |
|---|---|
| Pakistan | `6c5c2ec492a38f93020b593c554e9d437b39d859948a2cc82e94e6605c30cb01` |
| Full | `f8da541f3865862a18ac25dded339c2a84eeea4b5ff26e59d27676a4309e0109` |
| Places layer, version 2 (both editions) | `03ff666089c56b32c31348902d8ab87ff5276072ed74eb30c64bb94f17892ba7` |

The checksum covers the content (the layout, every cell and sub-cell, the
border flags and the border itself), not the file's bytes, which can differ
between SQLite versions.

**Build it once, not on every container start.** Either build it into the
image (§3, recommended) or build it once onto a persistent volume. Rebuild
only when you move to a `pakgrid` version with a different grid version
(§11).

## 3. Docker

A two-stage Dockerfile: the first stage builds and verifies the database, the
second is your app with the finished file copied in. If verification fails,
the image build fails.

```dockerfile
# ---- Stage 1: build and verify the grid database ----
FROM python:3.12-slim AS grid
# Use the same tag or commit as in requirements.txt.
ARG PAKGRID="pakgrid @ https://github.com/sahir2947/Pakistan-National-Grid-Mapping/archive/<tag or commit>.tar.gz"
RUN pip install --no-cache-dir "$PAKGRID"
RUN pakgrid build --pakistan-only -o /grid/pakgrid_v1_pakistan.sqlite \
    && pakgrid verify --db /grid/pakgrid_v1_pakistan.sqlite

# ---- Stage 2: the Django app ----
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=grid /grid/pakgrid_v1_pakistan.sqlite /app/data/pakgrid_v1_pakistan.sqlite
ENV PAKGRID_DB=/app/data/pakgrid_v1_pakistan.sqlite
EXPOSE 8000
CMD ["gunicorn", "shop.wsgi", "--preload", "--workers", "3", "--threads", "8", \
     "--worker-class", "gthread", "--bind", "0.0.0.0:8000"]
```

Add `data/` and `*.sqlite` to `.dockerignore` so a local copy is never sent
into the image. The database is copied *after* `COPY . .` so nothing in your
source tree can replace it.

If you prefer a volume, run the two commands from stage 1 once against the
volume (for example in a one-off job), and point `PAKGRID_DB` at it.

## 4. Connect Django

**Settings:**

```python
# settings.py
import os

INSTALLED_APPS += ["rest_framework", "locations"]

PAKGRID_DB = os.environ.get("PAKGRID_DB", str(BASE_DIR / "data" / "pakgrid_v1_pakistan.sqlite"))

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "locations.grid.exception_handler",
}
```

**One shared grid per process** (`locations/grid.py`):

```python
from functools import cache

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from pakgrid import Grid, InvalidIdError, NotInDatabaseError, OutsideGridError


@cache
def get_grid():
    """Open the grid database once per process; safe to share between threads."""
    return Grid.open(settings.PAKGRID_DB)


def exception_handler(exc, context):
    """Turn pakgrid errors into clean API errors; everything else as DRF normally does."""
    if isinstance(exc, (OutsideGridError, InvalidIdError)):
        return Response({"detail": str(exc), "code": "invalid_location"}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, NotInDatabaseError):
        return Response({"detail": "This location is outside Pakistan.", "code": "outside_pakistan"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return drf_exception_handler(exc, context)
```

**Fail at startup, not on the first request** (`locations/apps.py`):

```python
from django.apps import AppConfig


class LocationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "locations"

    def ready(self):
        # Fail at startup, not on the first request, if the database is missing or wrong.
        from .grid import get_grid
        get_grid()
```

`Grid.open` checks the grid version, the database schema and the layout
against the library's rules, and raises `GridMismatchError` if they differ.
It takes 10–20 ms; the full checksum check belongs in the image
build (§3), not at every start. Because `ready()` runs for every management
command, the database must exist wherever `manage.py` runs, including CI.

**Threads and worker processes.** One `Grid` can be shared by all threads of
a process: each thread gets its own read-only SQLite connection, opened on
first use and closed when the thread ends. It is also safe to open the grid
before gunicorn forks its workers (`--preload`): each worker opens fresh
connections instead of reusing the parent's. Async views can call it through
`sync_to_async`, which runs it on a thread. The database is opened read-only
and immutable, so there is no locking between readers; this also means the
file must never be changed while a server has it open (see §11 for how to
roll out a new one).

## 5. Store locations

Store the grid result next to your own data, and keep the original pin too:

```python
# locations/models.py
from django.db import models


class CustomerAddress(models.Model):
    customer_id = models.IntegerField()
    label = models.CharField(max_length=100, blank=True)
    lat = models.FloatField()                     # the pin, as the customer placed it
    lon = models.FloatField()
    accuracy_m = models.FloatField(null=True, blank=True)
    grid_address = models.CharField(max_length=32)            # '11-0140-0471-00 W23 S18'
    sub_cell_id = models.IntegerField(db_index=True)          # 105265888 (fits 27 bits)
    cell_id = models.IntegerField(db_index=True)              # 3289559 (= sub_cell_id >> 5)
    in_pakistan = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

| Field | Why |
|---|---|
| `sub_cell_id` | The ~200 m block. Fits a normal 32-bit integer column |
| `cell_id` | The ~1 km cell: group, batch and count by it with plain SQL. Always `sub_cell_id >> 5`, stored so it can be indexed |
| `grid_address` | Readable text for riders, support staff and printed labels; turns back into the exact pin with no database |
| `lat`, `lon`, `accuracy_m` | The original evidence; keep it |

Grid IDs never change for grid version 1, so stored IDs stay valid for as
long as you use it.

**Don't store place names; look them up.** A cell's locality, tehsil,
district and province come from the places layer, which is updated over time
(new districts, renamed tehsils, newly mapped neighbourhoods) while grid IDs
stay the same. Store the
`sub_cell_id` and ask `get_grid().place(sub_cell_id)` when you need the names,
so they are always current. For zones and reports, `grid.cells_in_place(...)`
gives every cell of a province, district, tehsil or locality, to match against your
indexed `cell_id` column.

## 6. API endpoints

These are examples; design your own API around your app. The serializers:

```python
# locations/serializers.py
from rest_framework import serializers

from .grid import get_grid
from .models import CustomerAddress


class PointSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lon = serializers.FloatField(min_value=-180, max_value=180)
    accuracy_m = serializers.FloatField(min_value=0, required=False, allow_null=True)


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        fields = ["id", "customer_id", "label", "lat", "lon", "accuracy_m",
                  "grid_address", "sub_cell_id", "cell_id", "in_pakistan", "created_at"]
        read_only_fields = ["grid_address", "sub_cell_id", "cell_id", "in_pakistan", "created_at"]

    def create(self, data):
        loc = get_grid().locate(data["lat"], data["lon"], data.get("accuracy_m"))
        if not loc.in_pakistan:
            raise serializers.ValidationError({"detail": "This pin is outside Pakistan."})
        data.update(grid_address=loc.address.text, sub_cell_id=loc.sub_cell.id,
                    cell_id=loc.sub_cell.cell_id, in_pakistan=loc.in_pakistan)
        return super().create(data)
```

The views:

```python
# locations/views.py
from django.db.models import Count
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from pakgrid import parse_address

from .grid import get_grid
from .models import CustomerAddress
from .serializers import CustomerAddressSerializer, PointSerializer


class LocateView(APIView):
    """POST {lat, lon, accuracy_m?} -> the sub-cell, grid address, confidence, border flag."""

    def post(self, request):
        p = PointSerializer(data=request.data)
        p.is_valid(raise_exception=True)
        loc = get_grid().locate(p.validated_data["lat"], p.validated_data["lon"],
                                p.validated_data.get("accuracy_m"))
        return Response(loc.to_dict())


class ResolveAddressView(APIView):
    """GET ?address=11-0140-0471-00 W23 S18 -> the GPS point (no database lookup needed)."""

    def get(self, request):
        a = parse_address(request.query_params.get("address", ""))
        lat, lon = a.point()
        return Response({"address": a.text, "lat": lat, "lon": lon})


class CustomerAddressList(generics.ListCreateAPIView):
    queryset = CustomerAddress.objects.all()
    serializer_class = CustomerAddressSerializer


class CellLoadView(APIView):
    """GET -> how many saved addresses fall in each ~1 km cell (for batching)."""

    def get(self, request):
        rows = CustomerAddress.objects.values("cell_id").annotate(n=Count("id")).order_by("-n")
        return Response([{"cell_id": r["cell_id"], "addresses": r["n"],
                          "neighbours": get_grid().neighbours(r["cell_id"])} for r in rows])
```

```python
# urls.py
from django.urls import path

from locations import views

urlpatterns = [
    path("api/locate", views.LocateView.as_view()),
    path("api/resolve", views.ResolveAddressView.as_view()),
    path("api/addresses", views.CustomerAddressList.as_view()),
    path("api/cell-load", views.CellLoadView.as_view()),
]
```

`POST /api/locate` with `{"lat": 31.5204, "lon": 74.3587, "accuracy_m": 5}`
returns `Location.to_dict()`:

```json
{
  "grid_version": 1,
  "address": "11-0140-0471-00 W23 S18",
  "point": [31.5204, 74.3587],
  "sub_cell": {
    "id": 105265888, "code": "11-0140-0471-00", "cell_id": 3289559, "cell_code": "11-0140-0471",
    "index": 0, "centroid": [31.5205657, 74.3589474],
    "bounds": {"south": 31.5196645, "west": 74.3578947, "north": 31.5214668, "east": 74.36},
    "pakistan": "inside"
  },
  "cell": {"id": 3289559, "code": "11-0140-0471"},
  "offset": {"east_m": -23.5, "north_m": -18.4, "distance_m": 29.8, "bearing_deg": 232.0, "text": "W23 S18"},
  "accuracy_m": 5,
  "confident": true,
  "edge_distance_m": 76.5,
  "candidates": [],
  "in_pakistan": true,
  "place": {
    "label": "Lahore Cant, Lahore Cantt, Lahore, Punjab",
    "locality": {"id": 20135, "key": "punjab/lahore/lahore-cantt/lahore-cant", "name": "Lahore Cant",
                 "kind": "town", "local_name": "لاہور کینٹ", "near": false, "text": "Lahore Cant",
                 "distance_m": 1916, "direction": "W"},
    "tehsil": {"id": 460, "key": "punjab/lahore/lahore-cantt", "name": "Lahore Cantt"},
    "district": {"id": 87, "key": "punjab/lahore", "name": "Lahore"},
    "province": {"id": 6, "key": "punjab", "name": "Punjab"}
  }
}
```

`place` is `null` outside Pakistan. `locality` is `null` where no named
place is near (open country). When `near` is true the block is outside the
locality itself, and `text` says so: `"near Kharochan (1.2 km S)"`;
`distance_m` and `direction` run from the locality's point to the block's
centre (`null` for a locality mapped as an outline). Show `label` or
`locality.text` to riders, and `local_name` in Urdu-language screens. Sources
and coverage: [PLACES_SOURCE.md](../pakgrid/data/PLACES_SOURCE.md).

Other library calls that are useful behind an API (see
[project.md §7.1](../project.md#71-python-library-16-functions)):
`grid.cells_in_area(...)` for a delivery zone, `grid.cells_within(lat, lon, radius_m)`
for "shops that deliver here" or "riders near this order" (match the IDs against
your indexed `cell_id` column), `grid.distance(a, b)` for the straight-line
distance and direction between two blocks, `grid.neighbours(...)` for
touching blocks, `grid.cell(...)` and `grid.sub_cell(...)` for a block's
bounds and centre, `grid.pakistan_flags(...)`, `direction(...)`, and for
places `grid.place(...)`, `grid.find_places("sahiwal")`,
`grid.get_place("punjab/lahore")` and `grid.cells_in_place(...)` (Lahore
district: 1,746 cells in ~16 ms).

**Delivery zones.** Let each shop draw its delivery area in the app, make a
zone from it, and store the zone's data on the shop. Checking an order's pin
is then a few microseconds, and `ZoneIndex` answers "which shops deliver
here?" for many shops at once ([project.md §7.6](../project.md#76-custom-zones)):

```python
from pakgrid import Zone, ZoneIndex

class Shop(models.Model):
    delivery_zone = models.JSONField(null=True)      # Zone.to_dict()

# saving the area the owner drew (polygon: [[lat, lon], ...] from the app)
shop.delivery_zone = get_grid().zone(polygon=polygon, pakistan_only=True).to_dict()

# checking an order
Zone.from_dict(shop.delivery_zone, get_grid()).contains(order.lat, order.lon)

# which shops deliver here? Build the index once (and again when a zone changes)
index = ZoneIndex({s.id: Zone.from_dict(s.delivery_zone, get_grid())
                   for s in Shop.objects.exclude(delivery_zone=None)})
shop_ids = index.at(lat, lon)
```

**Shapes for the app's map (GeoJSON).** Send GeoJSON and the Flutter app
draws it with its map library, with no grid maths on the phone
([project.md §7.7](../project.md#77-geojson)):

```python
from django.core.cache import cache
from django.http import JsonResponse

GEOJSON = "application/geo+json"

def shop_zone_geojson(request, shop_id):                 # GET /api/shops/<id>/zone.geojson
    shop = Shop.objects.get(pk=shop_id)
    zone = Zone.from_dict(shop.delivery_zone, get_grid())
    return JsonResponse(zone.to_geojson({"shop": shop.id}), content_type=GEOJSON)

def place_geojson(request, key):                         # GET /api/places/<path:key>.geojson
    data = cache.get_or_set(f"place-geojson:{key}",       # places change only with a new places version
                            lambda: get_grid().place_geojson(key), None)
    return JsonResponse(data, content_type=GEOJSON)

def block_geojson(request, code):                        # GET /api/blocks/<code>.geojson
    return JsonResponse(get_grid().geojson(code), content_type=GEOJSON)
```

`grid.cells_geojson(ids)` returns a radius search's or zone's blocks one by
one, for example to colour them by order count. GeoJSON puts longitude first:
`[lon, lat]`.

**Localities your users add.** Shop owners and riders know places the
official data misses (a gali, a market). Let trusted users draw them in the
app and send the polygon to an endpoint; your backend checks the permission
and the library stores the locality, located from known localities by
distance and direction ([project.md §5.8](../project.md#58-user-places-localities-drawn-by-an-apps-users)).
Keep the user places file on the same volume as your database backups, not
inside the image.

```python
# settings.py
PAKGRID_USER_PLACES = os.environ.get("PAKGRID_USER_PLACES", "/data/user_places.sqlite")

# grid.py (beside get_grid): attach once, when the grid is first opened
#     _grid.attach_user_places(settings.PAKGRID_USER_PLACES)

# views.py
from pakgrid import UserPlacesError

class UserLocalityCreate(APIView):
    permission_classes = [IsAuthenticated, DjangoModelPermissions]   # or your own "can map" check

    def post(self, request):
        d = request.data          # {"name": "Gali 7", "polygon": [[lat, lon], ...], "nearest": 2}
        try:
            loc = get_grid().user_places.add(
                d["name"], author=request.user.username, polygon=d["polygon"],
                kind=d.get("kind", "neighbourhood"), local_name=d.get("local_name"),
                references=d.get("references", []), nearest=d.get("nearest"))
        except UserPlacesError as exc:
            return Response({"error": str(exc)}, status=400)
        return Response(loc.to_dict(), status=201)
```

From then on `place` in every `locate` response shows the new locality
inside it (`"user": true`, with its `references`), and the OpenStreetMap one
stays in `osm_locality`. Back up with
`pakgrid user-places --file /data/user_places.sqlite export backup.json`.

**Confidence.** When the app sends the phone's GPS accuracy, `confident` says
whether the pin could belong to a neighbouring block, and `candidates` lists
those blocks. For a saved address, ask the customer to confirm the pin on the
map when `confident` is false.

## 7. What the app does

The app sends pins to your API and shows what comes back. Two things it can
do on its own, with no API call and no database, because they are only
formulas:

- **Live navigation to a pin** (distance, arrow, "bear left", "arrived"),
  updated every second from the phone's GPS. Do this on the phone: a request
  per rider per second is needless load, and guidance must keep working when
  the signal drops. The rules are in [project.md §10.6](../project.md#106-the-navigation-engine);
  the exact rules for a port are in
  [conformance/NAVIGATION.md](../conformance/NAVIGATION.md), and your port
  proves it matches by passing
  [conformance/navigation.json](../conformance/navigation.json) (about 2,000
  cases; see [conformance/README.md](../conformance/README.md)). The
  JavaScript copy in `viewer/pakgrid.js` (`NavigationSession`) is a complete
  example port.
- **Showing a grid address as a point**, e.g. a rider opening
  `11-0140-0471-00 W23 S18`. Or ask your API (`/api/resolve` above).

## 8. Errors

| pakgrid error | When | Suggested response |
|---|---|---|
| `OutsideGridError` | The point is outside the grid's rectangle | 400 |
| `InvalidIdError` | A code, ID or grid address that is not in the grid | 400 |
| `NotInDatabaseError` | Pakistan edition: the point is in a cell wholly outside Pakistan | 422 "outside Pakistan" |
| `GridMismatchError` | The database was built with different grid rules or schema | Fails at startup; rebuild the database |

A point can also be *in* the database but just outside the border, in a cell
that the border crosses. Check `location.in_pakistan` for that (the
serializer above does).

## 9. Testing

For your own test suite, a small piece of the grid builds in about a second:

```console
$ pakgrid build --quadrant 11 --rows 135:146 -o tests/data/lahore.sqlite
$ pakgrid verify --db tests/data/lahore.sqlite \
    --expect 8cbb99316c829ddf1d8e7ed2580b53279244bd4318df199a0db8dbaa5707d141
```

It covers central Lahore (about 31.47° N to 31.57° N, the whole width of the
north-east quadrant). Point `PAKGRID_DB` at it in your test settings. A
partial build has no official checksum, so record its checksum and pass it to
`--expect`, as above. In a partial build, a point outside the built rows
raises `NotInDatabaseError` too, so test only with points inside it; the
exception handler above would report such points as "outside Pakistan".

Your CI can also build the real Pakistan edition (~1 min) and run against that.

## 10. Performance

Measured on one CPU core with the Pakistan edition:

| | |
|---|---|
| `grid.locate(lat, lon, accuracy_m)` in Python | ~18 µs |
| Memory per process with the grid open | ~56 MB |
| `POST /api/locate` under gunicorn (3 workers × 8 threads), 3,000 requests from 32 clients | ~3.5 s in total, every answer identical to the library's |

The database is read on demand through SQLite's own cache; only a small
table of rows is kept in memory.

## 11. Upgrading pakgrid

- The **grid version** (`pakgrid.GRID_VERSION`, now 1) fixes every boundary
  and ID. Grid version 1 is frozen; IDs stored in your database stay valid.
- Upgrading the library within grid version 1 does not require rebuilding the
  grid: the official checksum stays the same. `Grid.open` refuses a
  database built for a different grid version or schema (`GridMismatchError`),
  so a mismatch cannot go unnoticed.
- A new **places version** (new names or boundaries) needs the places layer
  rebuilt: `pakgrid build-places --db FILE` on a copy, then `pakgrid verify`.
  Until then the old layer is not used (`grid.has_places` is false and
  `verify` says so). After switching, run `pakgrid user-places refresh` so
  user localities pick up the new tehsils and reference points.
- **Never change a database file that a running server has open.** The grid
  is opened read-only and *immutable* (§4): SQLite then assumes the file never
  changes and skips all locking, which is what makes reads fast, but a file
  rewritten in place under it can give wrong answers or errors. To roll out a
  new database:
  1. build and verify it under a **new name**, e.g.
     `pakgrid_v1_pakistan_places2.sqlite`;
  2. point `PAKGRID_DB` at it and **restart** the workers (a new image does
     both); or, for a volume, replace a symlink to it with `ln -sfn` and
     restart;
  3. delete the old file only after every worker has restarted.

  Replacing the file by renaming a new one over it is also safe for workers
  that restart, because open files keep reading the old copy, but never
  `cp` or rebuild onto the path a worker has open. The user places file (§5)
  is a normal writable database and is not replaced this way: back it up with
  `pakgrid user-places export`.
- Keep the same tag or commit in `requirements.txt` and in the Dockerfile's
  stage 1, so the library and the database are always built together.

## 12. Checklist

- [ ] `pakgrid` pinned to a tag or commit in `requirements.txt`
- [ ] Pakistan edition built and verified in the image build (or once onto a volume)
- [ ] `PAKGRID_DB` set; the grid opened once per process (`get_grid`) and checked in `ready()`
- [ ] `sub_cell_id`, `cell_id`, `grid_address` and the original pin stored with each address
- [ ] pakgrid errors mapped to API errors; `in_pakistan` checked when saving
- [ ] Live navigation done on the phone, not through the API
