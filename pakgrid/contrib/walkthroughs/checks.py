"""Django system checks (`manage.py check`, and at start-up) for the walkthrough settings."""

from __future__ import annotations

from typing import Any

from django.core.checks import Warning, register

from . import conf


@register()
def check_upload_rule(app_configs: Any = None, **kwargs: Any) -> list[Warning]:
    """Warn when CAN_UPLOAD is not set: uploads for an address are then refused to all but staff."""
    if conf.get("CAN_UPLOAD"):
        return []
    return [Warning(
        "PAKGRID_WALKTHROUGHS['CAN_UPLOAD'] is not set, so only staff can record walkthroughs for an "
        "address (place_ref).",
        hint="Set it to a dotted path to f(user, place_ref) -> bool: your app's rule for who may record "
             "and publish directions to an address, e.g. the user owns the shop.",
        id="pakgrid_walkthroughs.W001")]
