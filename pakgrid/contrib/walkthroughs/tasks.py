"""The photo job as a Celery task (with PAKGRID_WALKTHROUGHS['JOB'] = "celery")."""

from __future__ import annotations

from celery import shared_task

from . import services


@shared_task(name="pakgrid.walkthroughs.take_photos", ignore_result=True)
def take_photos_task(pk: str) -> None:
    services.take_photos(pk)
