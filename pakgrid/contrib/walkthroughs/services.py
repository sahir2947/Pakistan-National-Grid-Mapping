"""The walkthrough steps as plain functions: usable from the API views, the admin, a Celery task,
a management command or your own code."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from django.core.files import File
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from pakgrid.walkthrough import MAX_READINGS, WalkthroughError, read_gpx
from pakgrid.walkthrough import Walkthrough as WalkthroughData
from pakgrid.walkthrough import build as build_walkthrough

from . import conf
from .models import Walkthrough, WalkthroughPhoto

log = logging.getLogger(__name__)


class PermissionDenied(Exception):
    """The user may not do this (the API answers 403, or 404 to hide that it exists)."""


def _refused(message: str, code: str) -> WalkthroughError:
    """A WalkthroughError with a machine-readable `code` (the API returns it with a 400)."""
    exc = WalkthroughError(message)
    exc.code = code                                     # type: ignore[attr-defined]
    return exc


PLACE_REF_MAX = 100                                     # Walkthrough.place_ref's max_length


# -- Permissions ----------------------------------------------------------------------

def can_upload(user: Any, place_ref: str) -> bool:
    """May this user record, and publish, a walkthrough for this address? The app's CAN_UPLOAD
    rule; without one, only staff for an address (it fails closed: otherwise any signed-in user
    could replace the directions to someone else's door), and any signed-in user without one."""
    fn = conf.hook("CAN_UPLOAD")
    if fn:
        return bool(fn(user, place_ref))
    if not (user and user.is_authenticated):
        return False
    return not place_ref or bool(user.is_staff)


def can_manage(user: Any, wt: Walkthrough) -> bool:
    fn = conf.hook("CAN_MANAGE")
    if fn:
        return bool(fn(user, wt))
    return bool(user and user.is_authenticated and (user.is_staff or wt.owner_id == user.pk))


def can_view(user: Any, wt: Walkthrough) -> bool:
    """For a live walkthrough: CAN_VIEW, or else whoever can manage it."""
    fn = conf.hook("CAN_VIEW")
    return bool(fn(user, wt)) if fn else can_manage(user, wt)


# -- Upload ----------------------------------------------------------------------------

def parse_track(track: Any, video_start: datetime | str | None = None) -> list[Any]:
    """Readings from a list, JSON text, or GPX text (or bytes / an uploaded file of either).
    WalkthroughError (with a `code`) for a track larger than MAX_TRACK_BYTES, with too many
    readings, or that cannot be read; nothing larger than the limit is read into memory."""
    limit = conf.get("MAX_TRACK_BYTES")
    too_big = _refused(f"the GPS track is larger than {limit:,} bytes", "track_too_large")
    if hasattr(track, "read"):
        if getattr(track, "size", 0) > limit:
            raise too_big
        track = track.read(limit + 1)
    if isinstance(track, (bytes, str)) and len(track) > limit:
        raise too_big
    if isinstance(track, bytes):
        try:
            track = track.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise _refused("the GPS track is not UTF-8 text", "bad_track") from None
    if isinstance(track, str):
        text = track.strip()
        if text.startswith("<"):
            try:
                return read_gpx(text, video_start)
            except WalkthroughError as e:
                raise _refused(str(e), "bad_track") from None
        try:
            track = json.loads(text)
        except ValueError:
            raise _refused("the track is neither JSON nor GPX", "bad_track") from None
    if not isinstance(track, list):
        raise _refused("the track must be a list of readings", "bad_track")
    if len(track) > MAX_READINGS:
        raise _refused(f"the track has more than {MAX_READINGS:,} readings", "too_many_readings")
    return track


def create(user: Any, *, video: Any, track: Any, pin: Sequence[float], start: Sequence[float] | None = None,
           place_ref: str = "", video_start: datetime | str | None = None,
           properties: dict[str, Any] | None = None) -> Walkthrough:
    """Make the walkthrough from the track (refusing a bad recording with WalkthroughError), store
    it with its video, and start the photo job. Returns the Walkthrough record.

    With JOB "sync" the photos are taken straight from the uploaded file, and the video is
    stored only if that fails (so it can be retried) or KEEP_VIDEO is set."""
    if place_ref is None:
        place_ref = ""
    if not isinstance(place_ref, str):
        raise _refused("place_ref must be text", "bad_place_ref")
    place_ref = place_ref.strip()
    if len(place_ref) > PLACE_REF_MAX:
        raise _refused(f"place_ref is longer than {PLACE_REF_MAX} characters", "bad_place_ref")
    if not can_upload(user, place_ref):
        raise PermissionDenied("you may not record a walkthrough for this address")
    limit = conf.get("MAX_VIDEO_MB")
    if video is None:
        raise _refused("the video is missing", "no_video")
    if getattr(video, "size", 0) > limit * 1024 * 1024:
        raise _refused(f"the video is {video.size / 1048576:.0f} MB; at most {limit} MB is allowed", "video_too_large")
    props = {**(properties or {}), **({"place_ref": place_ref} if place_ref else {})}
    grid = conf.get_grid()
    author = user.get_username() if user else "unknown"
    args: dict[str, Any] = dict(pin=pin, start=start, author=author, properties=props)
    track = parse_track(track, video_start)
    data = grid.walkthrough(track, **args) if grid is not None else build_walkthrough(track, **args)
    with transaction.atomic():
        wt = Walkthrough(owner=user if user and user.is_authenticated else None, place_ref=place_ref,
                         pin_sub_cell=data.data["pin_sub_cell"] or "", length_m=data.length_m, data=data.to_dict())
        sync = conf.get("JOB") == "sync"
        if not sync or conf.get("KEEP_VIDEO"):
            wt.video.save(getattr(video, "name", None) or "video.mp4", video, save=False)
        wt.save()
        if sync:
            transaction.on_commit(lambda: take_photos(wt.pk, upload=video))
        else:
            transaction.on_commit(lambda: start_job(wt.pk))
    return wt


def start_job(pk: Any) -> None:
    """Run the photo job as PAKGRID_WALKTHROUGHS['JOB'] says."""
    job = conf.get("JOB")
    if job == "sync":
        take_photos(pk)
    elif job == "thread":
        threading.Thread(target=_in_thread, args=(pk,), name=f"walkthrough-{pk}", daemon=True).start()
    elif job == "celery":
        from .tasks import take_photos_task
        take_photos_task.delay(str(pk))
    # "command": process_walkthroughs picks it up


def _in_thread(pk: Any) -> None:
    close_old_connections()
    try:
        take_photos(pk)
    finally:
        close_old_connections()


# -- The photo job ---------------------------------------------------------------------

def take_photos(pk: Any, upload: Any = None) -> Walkthrough:
    """Take a processing walkthrough's photos from its video with ffmpeg, store them, and move it
    to review (or live, with AUTO_APPROVE). Safe to run twice: only a 'processing' record is
    taken, and claimed first. Returns the record (status 'failed', with `error`, if it failed).

    `upload`: the uploaded video file itself, read instead of the stored video (JOB "sync").
    If the photos then fail, it is stored, so the walkthrough can be retried."""
    with transaction.atomic():
        claimed = Walkthrough.objects.filter(pk=pk, status=Walkthrough.PROCESSING, photos_at__isnull=True) \
            .update(photos_at=timezone.now())
    wt = Walkthrough.objects.get(pk=pk)
    if not claimed:
        return wt
    try:
        if not wt.video and upload is None:
            raise WalkthroughError("the video is missing")
        data = WalkthroughData.from_dict(wt.data)
        with tempfile.TemporaryDirectory(prefix="pakgrid-walk-") as tmp:
            name = wt.video.name if wt.video else (getattr(upload, "name", None) or "video.mp4")
            src = os.path.join(tmp, "video" + (os.path.splitext(name)[1].lower()[:8] or ".mp4"))
            if upload is not None:
                upload.seek(0)
                with open(src, "wb") as out:
                    for chunk in upload.chunks():
                        out.write(chunk)
            else:
                with wt.video.open("rb") as f, open(src, "wb") as out:      # from any storage (local, S3, ...)
                    for chunk in f.chunks():
                        out.write(chunk)
            ffmpeg, timeout = conf.get("FFMPEG"), conf.get("FFMPEG_TIMEOUT_S")
            for cmd in data.ffmpeg_commands(src, tmp, conf.get("PHOTO_WIDTH"), conf.get("PHOTO_QUALITY")):
                cmd[0] = ffmpeg
                done = subprocess.run(cmd, capture_output=True, timeout=timeout)
                if done.returncode != 0 or not os.path.exists(cmd[-1]) or os.path.getsize(cmd[-1]) == 0:
                    tail = (done.stderr or b"").decode("utf-8", "replace").strip()[-300:]
                    raise WalkthroughError(f"ffmpeg could not take {os.path.basename(cmd[-1])}: {tail or 'no output'}")
            with transaction.atomic():
                wt.photos.all().delete()
                for f in data.frames:
                    photo = WalkthroughPhoto(walkthrough=wt, index=f["index"])
                    with open(os.path.join(tmp, f["image"]), "rb") as img:
                        photo.image.save(f["image"], File(img), save=True)
                wt.error = ""
                # AUTO_APPROVE publishes only if the uploader may still publish for the address
                if conf.get("AUTO_APPROVE") and (not wt.place_ref or can_upload(wt.owner, wt.place_ref)):
                    _go_live(wt, ["status", "error"])
                else:
                    wt.status = Walkthrough.REVIEW
                    wt.save(update_fields=["status", "error"])
        if not conf.get("KEEP_VIDEO") and wt.video:
            wt.video.delete(save=False)
            wt.save(update_fields=["video"])
    except Exception as exc:  # noqa: BLE001 - any failure (ffmpeg, storage, ...) fails this job only
        if isinstance(exc, (OSError, subprocess.SubprocessError, WalkthroughError, ValueError)):
            log.warning("walkthrough %s: photos failed: %s", pk, exc)
            reason = str(exc)
        else:                                   # e.g. the storage refused: say what, not a traceback
            log.exception("walkthrough %s: photos failed", pk)
            reason = f"the photos could not be stored ({type(exc).__name__})"
        wt.status, wt.error = Walkthrough.FAILED, reason[:2000]
        fields = ["status", "error"]
        if upload is not None and not wt.video:
            try:                                    # keep it, so the walkthrough can be retried
                upload.seek(0)
                wt.video.save(getattr(upload, "name", None) or "video.mp4", upload, save=False)
                fields.append("video")
            except Exception as keep:  # noqa: BLE001
                log.warning("walkthrough %s: could not keep the video for a retry: %s", pk, keep)
        wt.save(update_fields=fields)
    return wt


def retry(wt: Walkthrough) -> Walkthrough:
    """Put a failed walkthrough back to processing (its video must still be there) and start the job."""
    if wt.status != Walkthrough.FAILED or not wt.video:
        raise WalkthroughError("only a failed walkthrough that still has its video can be retried")
    wt.status, wt.error, wt.photos_at = Walkthrough.PROCESSING, "", None
    wt.save(update_fields=["status", "error", "photos_at"])
    transaction.on_commit(lambda: start_job(wt.pk))
    return wt


def process_pending(limit: int | None = None, stale_minutes: float | None = 30) -> list[Walkthrough]:
    """Take the photos of every walkthrough still processing (the 'command' job). A job that
    started more than stale_minutes ago and never finished (its worker died) is started again.
    Returns the records."""
    if stale_minutes is not None:
        Walkthrough.objects.filter(status=Walkthrough.PROCESSING,
                                   photos_at__lt=timezone.now() - timedelta(minutes=stale_minutes)) \
            .update(photos_at=None)
    pks = list(Walkthrough.objects.filter(status=Walkthrough.PROCESSING, photos_at__isnull=True)
               .order_by("created_at").values_list("pk", flat=True)[:limit])
    return [take_photos(pk) for pk in pks]


# -- Review ----------------------------------------------------------------------------

def approve(user: Any, wt: Walkthrough, note: str = "") -> Walkthrough:
    """The owner approves the photos: the walkthrough goes live, replacing the address's live one."""
    if not can_manage(user, wt):
        raise PermissionDenied("you may not approve this walkthrough")
    if wt.place_ref and not can_upload(user, wt.place_ref):        # approving publishes for the address
        raise PermissionDenied("you may not publish a walkthrough for this address")
    if wt.status != Walkthrough.REVIEW:
        raise WalkthroughError(f"only a walkthrough waiting for approval can be approved (this one is {wt.status})")
    wt.reviewed_by, wt.reviewed_at, wt.review_note = user, timezone.now(), note or ""
    _go_live(wt, ["status", "reviewed_by", "reviewed_at", "review_note"])
    return wt


def reject(user: Any, wt: Walkthrough, note: str = "") -> Walkthrough:
    if not can_manage(user, wt):
        raise PermissionDenied("you may not reject this walkthrough")
    if wt.status not in (Walkthrough.REVIEW, Walkthrough.LIVE):
        raise WalkthroughError(f"this walkthrough is {wt.status}; it cannot be rejected")
    wt.status, wt.reviewed_by, wt.reviewed_at, wt.review_note = Walkthrough.REJECTED, user, timezone.now(), note or ""
    wt.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    return wt


def delete(user: Any, wt: Walkthrough) -> None:
    if not can_manage(user, wt):
        raise PermissionDenied("you may not delete this walkthrough")
    wt.delete()                                     # its photos and video files go too


def _go_live(wt: Walkthrough, fields: list[str]) -> None:
    """Make `wt` live (saving `fields`), replacing its address's live walkthrough.

    At most one walkthrough per address is live: a database constraint guarantees it, even when
    two are published at once. The one published second finds the constraint broken by the first
    (which it could not yet see), so it tries once more, replacing it: the later one wins."""
    wt.status = Walkthrough.LIVE
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                if wt.place_ref:
                    Walkthrough.objects.filter(place_ref=wt.place_ref, status=Walkthrough.LIVE) \
                        .exclude(pk=wt.pk).update(status=Walkthrough.REPLACED)
                wt.save(update_fields=fields)
            return
        except IntegrityError:
            if attempt == 2:
                raise


# -- For riders --------------------------------------------------------------------------

def live_for(place_ref: str) -> Walkthrough | None:
    """The address's live walkthrough, or None."""
    live = Walkthrough.objects.filter(place_ref=place_ref, status=Walkthrough.LIVE)
    return live.order_by("-reviewed_at", "-created_at").first()


def payload(wt: Walkthrough, url: Callable[[str], str] = lambda u: u) -> dict[str, Any]:
    """The walkthrough's JSON for the app, each frame with its photo's `url` (url() can make it
    absolute, e.g. request.build_absolute_uri)."""
    photos = {p.index: p.image.url for p in wt.photos.all()}
    data = json.loads(json.dumps(wt.data))
    for f in data["frames"]:
        f["url"] = url(photos[f["index"]]) if f["index"] in photos else None
    return {"id": str(wt.pk), "place_ref": wt.place_ref, "status": wt.status, "walkthrough": data}
