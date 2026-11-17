"""Settings, read when used (so tests and settings overrides take effect).

PAKGRID_DB             the grid database file (or PAKGRID_GRID: a dotted path to a function that
                       returns your app's shared Grid). Optional: without either, walkthroughs
                       are made without the grid, and their sub-cell fields are empty
PAKGRID_WALKTHROUGHS   a dict of any of:

    JOB           how the photo job runs after an upload:
                  "command"  (default) a cron job runs `manage.py process_walkthroughs`
                  "celery"   a Celery task (pakgrid.contrib.walkthroughs.tasks)
                  "thread"   a background thread in the web process (small sites)
                  "sync"     during the upload request (tests, development)
    CAN_UPLOAD    dotted path to f(user, place_ref) -> bool: may this user record (and publish)
                  a walkthrough for this address? Set it to your app's rule, e.g. the user owns
                  the shop. Without it, only staff may for an address (any signed-in user may
                  record one with no place_ref): otherwise anyone could replace the directions
                  to someone else's door
    CAN_MANAGE    dotted path to f(user, walkthrough) -> bool (see the photos, approve, reject,
                  delete); default: its owner (the uploader) or staff
    CAN_VIEW      dotted path to f(user, walkthrough) -> bool, for a live walkthrough; default:
                  CAN_MANAGE only. Set it to your rider rule, e.g. an active order to the address
    AUTO_APPROVE  go live without the owner's approval (default False)
    KEEP_VIDEO    keep the video after the photos are taken (default False: it is deleted)
    PHOTO_WIDTH   photo width in pixels (default 1280)
    PHOTO_QUALITY ffmpeg JPEG quality, 2 (best) to 31 (default 3)
    MAX_VIDEO_MB  the largest video accepted (default 200)
    MAX_TRACK_BYTES  the largest GPS track accepted, JSON or GPX (default 5,000,000); a track
                  also has at most pakgrid.walkthrough.MAX_READINGS readings
    UPLOAD_RATE   uploads allowed per user, e.g. "30/hour" (the default) or "5/minute"; None for
                  no limit. Counted in Django's cache
    FFMPEG        the ffmpeg program (default "ffmpeg")
    FFMPEG_TIMEOUT_S  seconds allowed per photo (default 60)
    STORAGE       where videos and photos are kept: a dotted path to a Django Storage class (or a
                  function returning a storage); default: Django's default storage. Its url()
                  gives the links in the API, so a private bucket's storage can give links
                  that expire (e.g. django-storages' S3 storage with querystring auth)
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from django.core.files.storage import Storage

    from pakgrid import Grid

DEFAULTS = {
    "JOB": "command", "CAN_UPLOAD": None, "CAN_MANAGE": None, "CAN_VIEW": None, "AUTO_APPROVE": False,
    "KEEP_VIDEO": False, "PHOTO_WIDTH": 1280, "PHOTO_QUALITY": 3, "MAX_VIDEO_MB": 200, "FFMPEG": "ffmpeg",
    "FFMPEG_TIMEOUT_S": 60, "STORAGE": None, "MAX_TRACK_BYTES": 5_000_000, "UPLOAD_RATE": "30/hour",
}
JOBS = ("command", "celery", "thread", "sync")


def get(name: str) -> Any:
    value = {**DEFAULTS, **getattr(settings, "PAKGRID_WALKTHROUGHS", {})}[name]
    if name == "JOB" and value not in JOBS:
        raise ValueError(f"PAKGRID_WALKTHROUGHS['JOB'] must be one of {', '.join(JOBS)}, not {value!r}")
    return value


def hook(name: str) -> Callable[..., Any] | None:
    """The function named by a CAN_* setting, or None."""
    path = get(name)
    return import_string(path) if isinstance(path, str) else path


_grid_lock = Lock()
_grids: dict[str, Grid] = {}


def get_grid() -> Grid | None:
    """The app's Grid: PAKGRID_GRID's function, or PAKGRID_DB opened once per process; None
    when neither is set (walkthroughs are then made without the grid)."""
    fn = getattr(settings, "PAKGRID_GRID", None)
    if fn:
        return (import_string(fn) if isinstance(fn, str) else fn)()
    if not getattr(settings, "PAKGRID_DB", None):
        return None
    path = str(settings.PAKGRID_DB)
    with _grid_lock:
        if path not in _grids:
            from pakgrid import Grid
            _grids[path] = Grid.open(path)
        return _grids[path]


def get_storage() -> Storage:
    """The storage for videos and photos (STORAGE, or Django's default storage)."""
    from django.core.files.storage import default_storage
    value = get("STORAGE")
    if not value:
        return default_storage
    obj = import_string(value) if isinstance(value, str) else value
    return obj() if callable(obj) else obj
