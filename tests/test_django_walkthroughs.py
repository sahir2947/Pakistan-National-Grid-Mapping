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
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "django.contrib.admin",
                        "django.contrib.sessions", "django.contrib.messages", "rest_framework",
                        "pakgrid.contrib.walkthroughs"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.path.join(_TMP, "db.sqlite3")}},
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

from pakgrid.contrib.walkthroughs import services  # noqa: E402
from pakgrid.contrib.walkthroughs.models import Walkthrough, WalkthroughPhoto  # noqa: E402
from test_walkthrough import REF, walk  # noqa: E402

urlpatterns = [path("api/", include("pakgrid.contrib.walkthroughs.urls"))]
TRACK, PIN = walk()
REAL_RUN = subprocess.run


def rider_can_view(user, wt):
    """An app's rider rule, e.g. an active order to the address: here, users named rider*."""
    return user.username.startswith("rider")


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
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg"}):
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
    with override_settings(PAKGRID_WALKTHROUGHS={"CAN_VIEW": f"{__name__}.rider_can_view"}):
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
    with override_settings(PAKGRID_WALKTHROUGHS={"MAX_VIDEO_MB": 0}):
        assert "at most 0 MB" in upload(owner).json()["detail"]
    assert upload(client()).status_code in (401, 403)
    with override_settings(PAKGRID_WALKTHROUGHS={"CAN_UPLOAD": f"{__name__}.only_shop_owners"}):
        assert upload(owner, place_ref="shop-42").status_code == 403
        assert upload(owner, place_ref="shop-of-ali").status_code == 201
    assert Walkthrough.objects.count() == 1


def test_sync_job_auto_approve_and_replacing():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg", "JOB": "sync", "AUTO_APPROVE": True,
                                                 "CAN_VIEW": f"{__name__}.rider_can_view"}):
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
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg", "JOB": "thread"}):
        pk = upload(client("ali")).json()["id"]
        for _ in range(100):
            if Walkthrough.objects.get(pk=pk).status != "processing":
                break
            time.sleep(0.05)
    assert Walkthrough.objects.get(pk=pk).status == "review"


def test_failed_job_and_retry():
    owner = client("ali")
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "broken-ffmpeg", "JOB": "sync"}):
        s = upload(owner).json()
    wt = Walkthrough.objects.get(pk=s["id"])
    assert wt.status == "failed" and "Invalid data found" in wt.error and wt.video     # the video is kept
    assert owner.get(f"/api/walkthroughs/{wt.pk}/").json()["error"].startswith("ffmpeg could not take frame-000.jpg")
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg", "JOB": "sync"}):
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
              f"testsrc=size=320x240:rate=5:duration={TRACK[-1][0] + 1}", "-pix_fmt", "yuv420p", str(video)], check=True)
    with override_settings(PAKGRID_WALKTHROUGHS={"JOB": "sync", "PHOTO_WIDTH": 160}):
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
    with override_settings(PAKGRID_WALKTHROUGHS={"FFMPEG": "fake-ffmpeg", "JOB": "sync",
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
