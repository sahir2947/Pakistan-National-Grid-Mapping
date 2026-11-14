"""A ready Django app for walkthroughs (pakgrid.walkthrough): upload, photos, approval, riders.

Install with `pip install "pakgrid[django]"` (Django and Django REST framework), then:

    INSTALLED_APPS += ["rest_framework", "pakgrid.contrib.walkthroughs"]
    PAKGRID_DB = "/srv/pakgrid/pakgrid_v1_pakistan.sqlite"
    PAKGRID_WALKTHROUGHS = {
        "JOB": "celery",                                  # or "thread", "command", "sync"
        "CAN_VIEW": "shop.walkthroughs.rider_can_view",   # who may see a live walkthrough
        "CAN_UPLOAD": "shop.walkthroughs.can_upload",     # who may record one for an address
    }
    urlpatterns += [path("api/", include("pakgrid.contrib.walkthroughs.urls"))]

then `python manage.py migrate`. The flow:

1. The shop app uploads the video and the GPS track recorded with it (POST walkthroughs/). The
   walkthrough is made at once, so a bad recording is refused while the owner is still there.
2. A background job takes the photos from the video with ffmpeg, stores them in Django's
   storage (local files, S3, ...) and deletes the video. Status: processing -> review.
3. The owner looks at the photos and approves (live) or rejects them. A newly approved
   walkthrough replaces the address's previous live one.
4. A rider asks for the address's walkthrough (GET walkthroughs/for/<place_ref>/) and gets its
   JSON with each photo's URL, if CAN_VIEW allows (for example: an active order to that
   address). The app runs the rider rule on the phone (conformance/WALKTHROUGH.md).

Settings are in pakgrid.contrib.walkthroughs.conf; see docs/django.md for the full guide.
"""
