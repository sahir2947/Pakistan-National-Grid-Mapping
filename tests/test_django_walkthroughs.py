"""The Django app for walkthroughs (pakgrid.contrib.walkthroughs): upload, photo job, review, riders."""

import json
import os
import shutil
import subprocess
import tempfile
import time

import pytest

django = pytest.importorskip("django")
pytest.importorskip("rest_framework")

from django.conf import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="pakgrid-django-")
# PAKGRID_TEST_POSTGRES=host:port runs these tests on that PostgreSQL server (database "postgres",
# user "postgres") instead of SQLite: the one-live-walkthrough rule is a constraint there too.
_PG = os.environ.get("PAKGRID_TEST_POSTGRES")
_DB = ({"ENGINE": "django.db.backends.postgresql", "NAME": "postgres", "USER": "postgres",
        "HOST": _PG.rsplit(":", 1)[0], "PORT": _PG.rsplit(":", 1)[1]} if _PG else
       {"ENGINE": "django.db.backends.sqlite3", "NAME": os.path.join(_TMP, "db.sqlite3")})
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "django.contrib.admin",
                        "django.contrib.sessions", "django.contrib.messages", "rest_framework",
                        "pakgrid.contrib.walkthroughs"],
        DATABASES={"default": _DB},
        MEDIA_ROOT=os.path.join(_TMP, "media"), MEDIA_URL="/media/", ROOT_URLCONF=__name__, USE_TZ=True,
        SECRET_KEY="test", DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", ALLOWED_HOSTS=["testserver"],
        PAKGRID_WALKTHROUGHS={},
    )
    django.setup()

from django.contrib.auth.models import User  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test.utils import override_settings  # noqa: E402
from django.urls import include, path  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from test_walkthrough import REF, walk  # noqa: E402

from pakgrid.contrib.walkthroughs import services  # noqa: E402
from pakgrid.contrib.walkthroughs.models import Walkthrough, WalkthroughPhoto  # noqa: E402

urlpatterns = [path("api/", include("pakgrid.contrib.walkthroughs.urls"))]
TRACK, PIN = walk()
REAL_RUN = subprocess.run


def rider_can_view(user, wt):
    """An app's rider rule, e.g. an active order to the address: here, users named rider*."""
    return user.username.startswith("rider")


def anyone_signed_in(user, place_ref):
    """A permissive CAN_UPLOAD rule for these tests; an app sets its own (see test_upload_rule_fails_closed)."""
    return user.is_authenticated


ALLOWED = {"CAN_UPLOAD": f"{__name__}.anyone_signed_in"}


def only_shop_owners(user, place_ref):
    return place_ref == f"shop-of-{user.username}"


def fake_ffmpeg(cmd, *args, **kwargs):
    """Stands in for ffmpeg: writes a small 'photo' where the command would."""
    if cmd[0] == "fake-ffmpeg":
        with open(cmd[-1], "wb") as f:
            f.write(b"\xff\xd8 photo at " + cmd[cmd.index("-ss") + 1].encode())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    if cmd[0] == "broken-ffmpeg":
        return subprocess.CompletedProcess(cmd, 1, b"", b"Invalid data found when processing input")
    return REAL_RUN(cmd, *args, **kwargs)


@pytest.fixture(scope="module", autouse=True)
def database(lahore_db):
    settings.PAKGRID_DB = str(lahore_db)
    call_command("migrate", verbosity=0)
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg"}):
        yield
    Walkthrough.objects.all().delete()
    User.objects.all().delete()


def client(username=None, **kw):
    c = APIClient()
    if username:
        user = User.objects.get_or_create(username=username, defaults=kw)[0]
        c.force_authenticate(user)
    return c


def upload(c, place_ref="shop-42", **fields):
    data = {"video": SimpleUploadedFile("walk.mp4", b"not really a video", content_type="video/mp4"),
            "track": json.dumps([{"t": t, "lat": lat, "lon": lon, "accuracy": a} for t, lat, lon, a in TRACK]),
            "pin_lat": PIN[0], "pin_lon": PIN[1], "start_lat": REF[0], "start_lon": REF[1], "place_ref": place_ref}
    data.update(fields)
    return c.post("/api/walkthroughs/", {k: v for k, v in data.items() if v is not None}, format="multipart")


def media(name):
    return os.path.join(settings.MEDIA_ROOT, name)


def test_the_whole_flow():
    owner, rider, stranger = client("ali"), client("rider1"), client("bilal")
    r = upload(owner)
    assert r.status_code == 201, r.content
    s = r.json()
    assert s["status"] == "processing" and s["turns"] == ["turn right", "turn left"] and s["photos"] > 10
    wt = Walkthrough.objects.get(pk=s["id"])
    assert os.path.exists(media(wt.video.name)) and wt.data["properties"] == {"place_ref": "shop-42"}
    assert wt.pin_sub_cell == wt.data["pin_sub_cell"] and wt.data["author"] == "ali"
    assert rider.get("/api/walkthroughs/for/shop-42/").status_code == 404           # nothing live yet

    # the photo job (cron runs this command)
    out = __import__("io").StringIO()
    call_command("process_walkthroughs", stdout=out)
    assert "1 walkthrough processed" in out.getvalue()
    wt.refresh_from_db()
    assert wt.status == "review" and wt.photos.count() == len(wt.data["frames"]) and not wt.video
    assert not os.path.exists(media(f"walkthroughs/{wt.pk}/video.mp4"))                # the video is deleted
    first = wt.photos.get(index=0)
    assert open(media(first.image.name), "rb").read().startswith(b"\xff\xd8")
    call_command("process_walkthroughs", stdout=out)                                   # nothing twice
    assert WalkthroughPhoto.objects.count() == len(wt.data["frames"])

    # the owner reviews and approves; nobody else can
    d = owner.get(f"/api/walkthroughs/{wt.pk}/").json()
    assert d["status"] == "review" and d["walkthrough"]["frames"][0]["url"] == \
        f"http://testserver/media/walkthroughs/{wt.pk}/frame-000.jpg"
    assert stranger.get(f"/api/walkthroughs/{wt.pk}/").status_code == 404
    assert stranger.post(f"/api/walkthroughs/{wt.pk}/approve/").status_code == 404
    assert rider.get("/api/walkthroughs/for/shop-42/").status_code == 404
    r = owner.post(f"/api/walkthroughs/{wt.pk}/approve/", {"note": "looks right"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "live" and r.json()["review_note"] == "looks right"
    assert owner.post(f"/api/walkthroughs/{wt.pk}/approve/").status_code == 400       # already live

    # riders: only those the app's rule allows
    assert rider.get("/api/walkthroughs/for/shop-42/").status_code == 404            # default: owner only
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "CAN_VIEW": f"{__name__}.rider_can_view"}):
        r = rider.get("/api/walkthroughs/for/shop-42/")
        assert r.status_code == 200
        p = r.json()
        assert p["id"] == str(wt.pk) and p["walkthrough"]["length_m"] == wt.length_m
        assert all(f["url"].endswith(f["image"]) for f in p["walkthrough"]["frames"])
        assert stranger.get("/api/walkthroughs/for/shop-42/").status_code == 404
        assert client().get("/api/walkthroughs/for/shop-42/").status_code in (401, 403)
    # the payload drives the rider rule
    from pakgrid import WalkthroughGuide
    assert WalkthroughGuide(p["walkthrough"]).update(*PIN).arrived

    # delete: record, photos and files
    assert owner.delete(f"/api/walkthroughs/{wt.pk}/").status_code == 204
    assert not Walkthrough.objects.exists() and not os.path.exists(media(first.image.name))


def test_refused_uploads():
    owner = client("ali")
    cases = [(dict(video=None), "video is missing"),
             (dict(track="not a track"), "neither JSON nor GPX"),
             (dict(track=None), "send the GPS track"),
             (dict(pin_lat=31.6), "from the pin"),
             (dict(pin_lat=None), "pin_lat is required"),
             (dict(pin_lon="east"), "must be a number"),
             (dict(track=json.dumps(TRACK[:1])), "at least 2 good readings")]
    for fields, message in cases:
        r = upload(owner, **fields)
        assert r.status_code == 400 and message in r.json()["detail"], (fields, r.content)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "MAX_VIDEO_MB": 0}):
        assert "at most 0 MB" in upload(owner).json()["detail"]
    assert upload(client()).status_code in (401, 403)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "CAN_UPLOAD": f"{__name__}.only_shop_owners"}):
        assert upload(owner, place_ref="shop-42").status_code == 403
        assert upload(owner, place_ref="shop-of-ali").status_code == 201
    assert Walkthrough.objects.count() == 1


def test_sync_job_auto_approve_and_replacing():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync",
                                                 "AUTO_APPROVE": True, "CAN_VIEW": f"{__name__}.rider_can_view"}):
        a = upload(owner).json()
        assert a["status"] == "live"                          # photos taken during the request ...
        assert not Walkthrough.objects.get(pk=a["id"]).video  # ... from the upload: no video stored
        assert not os.path.exists(media(f"walkthroughs/{a['id']}"))  or \
            not any(n.startswith("video") for n in os.listdir(media(f"walkthroughs/{a['id']}")))
        b = upload(owner).json()
        assert Walkthrough.objects.get(pk=a["id"]).status == "replaced"
        assert client("rider2").get("/api/walkthroughs/for/shop-42/").json()["id"] == b["id"]
        other = upload(owner, place_ref="shop-43").json()
        assert Walkthrough.objects.get(pk=b["id"]).status == "live" and other["status"] == "live"


def test_thread_job():
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "thread"}):
        pk = upload(client("ali")).json()["id"]
        for _ in range(100):
            if Walkthrough.objects.get(pk=pk).status != "processing":
                break
            time.sleep(0.05)
    assert Walkthrough.objects.get(pk=pk).status == "review"


def test_failed_job_and_retry():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "broken-ffmpeg", "JOB": "sync"}):
        s = upload(owner).json()
    wt = Walkthrough.objects.get(pk=s["id"])
    assert wt.status == "failed" and "Invalid data found" in wt.error and wt.video     # the video is kept
    assert owner.get(f"/api/walkthroughs/{wt.pk}/").json()["error"].startswith("ffmpeg could not take frame-000.jpg")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync"}):
        r = owner.post(f"/api/walkthroughs/{wt.pk}/retry/")
    assert r.status_code == 200 and r.json()["status"] == "review" and r.json()["error"] is None
    assert owner.post(f"/api/walkthroughs/{wt.pk}/retry/").status_code == 400


def test_reject_list_and_staff():
    ali, sara, admin = client("ali"), client("sara"), client("boss", is_staff=True)
    a = upload(ali).json()["id"]
    upload(sara, place_ref="shop-7")
    services.process_pending()
    assert [w["place_ref"] for w in ali.get("/api/walkthroughs/").json()] == ["shop-42"]
    assert len(admin.get("/api/walkthroughs/").json()) == 2
    assert admin.get("/api/walkthroughs/?place_ref=shop-7").json()[0]["place_ref"] == "shop-7"
    r = admin.post(f"/api/walkthroughs/{a}/reject/", {"note": "blurry"}, format="json")
    assert r.json()["status"] == "rejected" and r.json()["review_note"] == "blurry"
    assert ali.post(f"/api/walkthroughs/{a}/approve/").status_code == 400


def test_gpx_upload_and_stale_jobs():
    gpx = "<gpx><trk><trkseg>" + "".join(
        f'<trkpt lat="{lat}" lon="{lon}"><time>2026-11-14T10:00:{int(t):02d}Z</time></trkpt>'
        if t < 60 else f'<trkpt lat="{lat}" lon="{lon}"><time>2026-11-14T10:01:{int(t) - 60:02d}Z</time></trkpt>'
        for t, lat, lon, _ in TRACK) + "</trkseg></trk></gpx>"
    r = upload(client("ali"), track=None, track_file=SimpleUploadedFile("walk.gpx", gpx.encode()))
    assert r.status_code == 201, r.content
    pk = r.json()["id"]
    from django.utils import timezone
    Walkthrough.objects.filter(pk=pk).update(photos_at=timezone.now() - timezone.timedelta(hours=2))
    assert services.process_pending(stale_minutes=None) == []           # claimed by a job that died ...
    assert [w.status for w in services.process_pending()] == ["review"]  # ... started again after 30 min


def test_migrations_are_up_to_date():
    call_command("makemigrations", "pakgrid_walkthroughs", "--check", "--dry-run", verbosity=0)


def test_admin_is_registered():
    import importlib

    from django.contrib import admin
    importlib.import_module("pakgrid.contrib.walkthroughs.admin")
    assert Walkthrough in admin.site._registry


def test_celery_task():
    pytest.importorskip("celery")
    from pakgrid.contrib.walkthroughs.tasks import take_photos_task
    pk = upload(client("ali")).json()["id"]
    take_photos_task(pk)
    assert Walkthrough.objects.get(pk=pk).status == "review"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_real_ffmpeg(tmp_path):
    video = tmp_path / "walk.mp4"
    REAL_RUN(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
              f"testsrc=size=320x240:rate=5:duration={TRACK[-1][0] + 1}", "-pix_fmt", "yuv420p", str(video)],
             check=True)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "JOB": "sync", "PHOTO_WIDTH": 160}):
        r = upload(client("ali"), video=SimpleUploadedFile("walk.mp4", video.read_bytes(), content_type="video/mp4"))
    wt = Walkthrough.objects.get(pk=r.json()["id"])
    assert wt.status == "review", wt.error
    assert all(os.path.getsize(media(p.image.name)) > 500 for p in wt.photos.all())


class PrivateBucket:
    """Stands in for a private bucket's storage: files in their own folder, links that expire."""

    def __new__(cls):
        from django.core.files.storage import FileSystemStorage

        class _Storage(FileSystemStorage):
            def url(self, name):
                return f"https://bucket.example/{name}?X-Amz-Expires=300"
        return _Storage(location=os.path.join(_TMP, "bucket"))


def test_own_storage_with_expiring_links():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync",
                                                 "STORAGE": f"{__name__}.PrivateBucket"}):
        pk = upload(owner).json()["id"]
        frames = owner.get(f"/api/walkthroughs/{pk}/").json()["walkthrough"]["frames"]
        assert frames[0]["url"] == f"https://bucket.example/walkthroughs/{pk}/frame-000.jpg?X-Amz-Expires=300"
        assert os.path.exists(os.path.join(_TMP, "bucket", f"walkthroughs/{pk}/frame-000.jpg"))
        assert not os.path.exists(media(f"walkthroughs/{pk}/frame-000.jpg"))
        assert owner.delete(f"/api/walkthroughs/{pk}/").status_code == 204
        assert not os.path.exists(os.path.join(_TMP, "bucket", f"walkthroughs/{pk}/frame-000.jpg"))


def test_without_the_grid_database():
    with override_settings(PAKGRID_DB=None):
        r = upload(client("ali"))
    assert r.status_code == 201, r.content
    wt = Walkthrough.objects.get(pk=r.json()["id"])
    assert wt.pin_sub_cell == "" and wt.data["sub_cells"] == [] and r.json()["turns"] == ["turn right", "turn left"]


class RefusingBucket:
    """A storage that refuses every write, like a bucket with the wrong credentials."""

    def __new__(cls):
        from django.core.files.storage import InMemoryStorage

        class _Storage(InMemoryStorage):
            def _save(self, name, content):
                raise RuntimeError("AccessDenied")
        return _Storage()


def test_storage_failure_fails_the_job_cleanly():
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync",
                                                 "STORAGE": f"{__name__}.RefusingBucket"}):
        r = upload(client("ali"))
    assert r.status_code == 201 and r.json()["status"] == "failed"
    assert r.json()["error"] == "the photos could not be stored (RuntimeError)"
    assert WalkthroughPhoto.objects.count() == 0


# -- Codex review fixes: permissions fail closed, bounded tracks, one live per address ------

def approve_now(c, place_ref, **settings_kw):
    """Upload for place_ref, take the photos and approve: returns the upload's id."""
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync", **settings_kw}):
        wid = upload(c, place_ref=place_ref).json()["id"]
        assert c.post(f"/api/walkthroughs/{wid}/approve/").json()["status"] == "live"
    return wid


def test_upload_rule_fails_closed():
    from django.core import checks
    attacker, staff = client("sara"), client("boss", is_staff=True)
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg"}):            # no CAN_UPLOAD rule
        r = upload(attacker, place_ref="shop-42")
        assert r.status_code == 403 and not Walkthrough.objects.exists()
        assert upload(attacker, place_ref="").status_code == 201        # no address: nothing to take over
        assert upload(staff, place_ref="shop-42").status_code == 201    # staff are trusted
        assert any(w.id == "pakgrid_walkthroughs.W001" for w in checks.run_checks())
    with override_settings(PAKGRID_WALKTHROUGHS=ALLOWED):
        assert not any(w.id == "pakgrid_walkthroughs.W001" for w in checks.run_checks())


def test_an_attacker_cannot_replace_the_owners_walkthrough():
    rule = {"CAN_UPLOAD": f"{__name__}.only_shop_owners"}
    owner, attacker, boss = client("ali"), client("sara"), client("boss", is_staff=True)
    live = approve_now(owner, "shop-of-ali", **rule)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync", **rule}):
        assert upload(attacker, place_ref="shop-of-ali").status_code == 403
        # approving publishes for the address, so it needs the address too: staff may manage
        # the walkthrough, but the app's rule says this address is not theirs
        own = upload(attacker, place_ref="shop-of-sara").json()["id"]
        Walkthrough.objects.filter(pk=own).update(place_ref="shop-of-ali")       # e.g. changed in the admin
        assert attacker.post(f"/api/walkthroughs/{own}/approve/").status_code == 403
        assert boss.post(f"/api/walkthroughs/{own}/approve/").status_code == 403
    assert Walkthrough.objects.get(pk=live).status == "live"


def test_auto_approve_checks_the_rule_again():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "AUTO_APPROVE": True}):
        wid = upload(owner, place_ref="shop-of-ali").json()["id"]
    # by the time the photo job runs, the app's rule no longer lets ali publish for shop-of-ali
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg", "AUTO_APPROVE": True,
                                                 "CAN_UPLOAD": f"{__name__}.only_shop_owners_of_bilal"}):
        call_command("process_walkthroughs", stdout=__import__("io").StringIO())
    assert Walkthrough.objects.get(pk=wid).status == "review"             # waits for a person instead


def only_shop_owners_of_bilal(user, place_ref):
    return user is not None and user.username == "bilal"


def test_one_live_walkthrough_per_address_is_a_database_rule():
    from django.db import IntegrityError, transaction
    owner = client("ali")
    first = approve_now(owner, "shop-42")
    second = Walkthrough.objects.get(pk=first)
    second.pk, second.status = None, "review"
    second.save()
    with pytest.raises(IntegrityError), transaction.atomic():
        Walkthrough.objects.filter(pk=second.pk).update(status="live")
    # addresses with no place_ref are not limited
    for _ in range(2):
        Walkthrough.objects.create(place_ref="", length_m=1, data={}, status="live")


def test_publishing_tries_again_when_it_hits_the_rule(monkeypatch):
    """Two approvals at once: the second cannot see the first until it commits, so its save hits
    the constraint, and it tries again. One connection cannot show the race itself (the other
    approval made here is rolled back with the failed try); a real PostgreSQL race was run when
    this was written, with the later approval live and the other replaced."""
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "FFMPEG": "fake-ffmpeg", "JOB": "sync"}):
        a = Walkthrough.objects.get(pk=upload(owner, place_ref="shop-42").json()["id"])
        b = Walkthrough.objects.get(pk=upload(owner, place_ref="shop-42").json()["id"])
    real_save, calls = Walkthrough.save, []

    def save(self, *args, **kwargs):
        if self.pk == b.pk:
            calls.append(1)
            if len(calls) == 1:                            # the other approval lands first
                Walkthrough.objects.filter(pk=a.pk).update(status="live")
        return real_save(self, *args, **kwargs)
    monkeypatch.setattr(Walkthrough, "save", save)
    services.approve(User.objects.get(username="ali"), b)
    monkeypatch.undo()
    assert len(calls) == 2                                 # the save hit the rule, and was tried again
    assert Walkthrough.objects.get(pk=b.pk).status == "live"
    assert Walkthrough.objects.filter(place_ref="shop-42", status="live").count() == 1
    assert services.live_for("shop-42").pk == b.pk


def test_the_migration_keeps_the_newest_live_walkthrough():
    from django.utils import timezone
    call_command("migrate", "pakgrid_walkthroughs", "0002", verbosity=0)    # before the constraint
    try:
        now = timezone.now()
        old, new, newest = (Walkthrough.objects.create(place_ref="shop-9", length_m=1, data={}, status="live",
                                                       reviewed_at=now - timezone.timedelta(days=d))
                            for d in (3, 2, 1))
        other = Walkthrough.objects.create(place_ref="shop-10", length_m=1, data={}, status="live")
    finally:
        call_command("migrate", "pakgrid_walkthroughs", verbosity=0)
    status = dict(Walkthrough.objects.values_list("pk", "status"))
    assert (status[old.pk], status[new.pk], status[newest.pk], status[other.pk]) == \
        ("replaced", "replaced", "live", "live")


def test_bad_uploads_get_a_400_with_a_code_and_create_nothing():
    owner = client("ali")
    gpx = '<gpx><trk><trkseg><trkpt lat="31.5" lon="74.3"><time>{}</time></trkpt></trkseg></trk></gpx>'
    cases = [(dict(track="<gpx><trk"), "bad_track"),                              # broken XML
             (dict(track=gpx.format("not a time")), "bad_track"),
             (dict(track=gpx.replace('lat="31.5"', 'lat="north"').format("2026-01-01T00:00:00Z")), "bad_track"),
             (dict(track=None, track_file=SimpleUploadedFile("t.gpx", b"\xff\xfe<gpx")), "bad_track"),   # not UTF-8
             (dict(track=json.dumps({"t": 1})), "bad_track"),
             (dict(track=json.dumps([[0, 31.5, 74.3]] * 20_001)), "too_many_readings"),
             (dict(place_ref="x" * 101), "bad_place_ref"),
             (dict(video=None), "no_video")]
    for fields, code in cases:
        r = upload(owner, **fields)
        assert r.status_code == 400 and r.json()["code"] == code, (code, r.content)
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "MAX_TRACK_BYTES": 1000}):
        assert upload(owner).json()["code"] == "track_too_large"
        big = SimpleUploadedFile("t.json", b"[" + b"[0, 31.5, 74.3]," * 200 + b"[1, 31.5, 74.3]]")
        assert upload(owner, track=None, track_file=big).json()["code"] == "track_too_large"
    assert not Walkthrough.objects.exists()


def test_uploads_are_rate_limited():
    from django.core.cache import cache
    cache.clear()
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "UPLOAD_RATE": "2/minute"}):
        assert [upload(owner).status_code for _ in range(3)] == [201, 201, 429]
        assert owner.get("/api/walkthroughs/").status_code == 200              # reading is not limited
    cache.clear()
    with override_settings(PAKGRID_WALKTHROUGHS={**ALLOWED, "UPLOAD_RATE": None}):
        assert all(upload(owner).status_code == 201 for _ in range(4))
