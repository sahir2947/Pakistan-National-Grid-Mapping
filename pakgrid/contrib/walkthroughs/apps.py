"""The app's configuration (label `pakgrid_walkthroughs`)."""

from django.apps import AppConfig


class WalkthroughsConfig(AppConfig):
    name = "pakgrid.contrib.walkthroughs"
    label = "pakgrid_walkthroughs"
    verbose_name = "Walkthroughs"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import checks  # noqa: F401  (registers the system checks)
