"""Settings, read when used (so tests and settings overrides take effect).

PAKGRID_DB             the grid database file (or PAKGRID_GRID: a dotted path to a function that
                       returns your app's shared Grid)
PAKGRID_WALKTHROUGHS   a dict of any of:

    JOB           how the photo job runs after an upload:
                  "command"  (default) a cron job runs `manage.py process_walkthroughs`
                  "celery"   a Celery task (pakgrid.contrib.walkthroughs.tasks)
                  "thread"   a background thread in the web process (small sites)
                  "sync"     during the upload request (tests, development)
    CAN_UPLOAD    dotted path to f(user, place_ref) -> bool; default: any signed-in user
    CAN_MANAGE    dotted path to f(user, walkthrough) -> bool (see the photos, approve, reject,
                  delete); default: its owner (the uploader) or staff
    CAN_VIEW      dotted path to f(user, walkthrough) -> bool, for a live walkthrough; default:
                  CAN_MANAGE only. Set it to your rider rule, e.g. an active order to the address
    AUTO_APPROVE  go live without the owner's approval (default False)
    KEEP_VIDEO    keep the video after the photos are taken (default False: it is deleted)
    PHOTO_WIDTH   photo width in pixels (default 1280)
    PHOTO_QUALITY ffmpeg JPEG quality, 2 (best) to 31 (default 3)
    MAX_VIDEO_MB  the largest video accepted (default 200)
    FFMPEG        the ffmpeg program (default "ffmpeg")
    FFMPEG_TIMEOUT_S  seconds allowed per photo (default 60)
"""

from threading import Lock

from django.conf import settings
from django.utils.module_loading import import_string

DEFAULTS = {
    "JOB": "command", "CAN_UPLOAD": None, "CAN_MANAGE": None, "CAN_VIEW": None, "AUTO_APPROVE": False,
    "KEEP_VIDEO": False, "PHOTO_WIDTH": 1280, "PHOTO_QUALITY": 3, "MAX_VIDEO_MB": 200, "FFMPEG": "ffmpeg",
    "FFMPEG_TIMEOUT_S": 60,
}
JOBS = ("command", "celery", "thread", "sync")


def get(name):
    value = {**DEFAULTS, **getattr(settings, "PAKGRID_WALKTHROUGHS", {})}[name]
    if name == "JOB" and value not in JOBS:
        raise ValueError(f"PAKGRID_WALKTHROUGHS['JOB'] must be one of {', '.join(JOBS)}, not {value!r}")
    return value


def hook(name):
    """The function named by a CAN_* setting, or None."""
    path = get(name)
    return import_string(path) if isinstance(path, str) else path


_grid_lock = Lock()
_grids = {}


def get_grid():
    """The app's Grid: PAKGRID_GRID's function, or PAKGRID_DB opened once per process."""
    fn = getattr(settings, "PAKGRID_GRID", None)
    if fn:
        return (import_string(fn) if isinstance(fn, str) else fn)()
    path = str(settings.PAKGRID_DB)
    with _grid_lock:
        if path not in _grids:
            from pakgrid import Grid
            _grids[path] = Grid.open(path)
        return _grids[path]
