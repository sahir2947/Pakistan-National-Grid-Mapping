"""`manage.py process_walkthroughs`: the photo job run from cron (JOB "command")."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from ... import services


class Command(BaseCommand):
    help = "Take the photos of walkthroughs waiting for them (run it from cron every minute or so)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, help="at most this many")
        parser.add_argument("--stale-minutes", type=int, default=30,
                            help="start again a job that began this long ago and never finished (default 30)")

    def handle(self, *args: Any, limit: int | None = None, stale_minutes: int = 30, **options: Any) -> None:
        done = services.process_pending(limit=limit, stale_minutes=stale_minutes)
        for wt in done:
            line = f"{wt.pk}: {wt.status}" + (f" ({wt.error})" if wt.error else "")
            self.stdout.write(self.style.ERROR(line) if wt.status == wt.FAILED else line)
        self.stdout.write(f"{len(done)} walkthrough{'s' if len(done) != 1 else ''} processed")
