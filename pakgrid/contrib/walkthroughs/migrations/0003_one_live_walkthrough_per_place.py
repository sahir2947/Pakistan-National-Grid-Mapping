from typing import Any

from django.db import migrations, models


def keep_one_live_per_place(apps: Any, schema_editor: Any) -> None:
    """Before the constraint: where an address has several live walkthroughs (possible before
    it), keep the most recently approved one live and mark the others replaced."""
    Walkthrough = apps.get_model("pakgrid_walkthroughs", "Walkthrough")
    live = Walkthrough.objects.filter(status="live").exclude(place_ref="")
    seen = set()
    for wt in live.order_by("place_ref", models.F("reviewed_at").desc(nulls_last=True), "-created_at"):
        if wt.place_ref in seen:
            Walkthrough.objects.filter(pk=wt.pk).update(status="replaced")
        seen.add(wt.place_ref)


class Migration(migrations.Migration):

    dependencies = [
        ("pakgrid_walkthroughs", "0002_configurable_storage"),
    ]

    operations = [
        migrations.RunPython(keep_one_live_per_place, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="walkthrough",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "live"), models.Q(("place_ref", ""), _negated=True)),
                fields=("place_ref",), name="pakgrid_one_live_walkthrough_per_place"),
        ),
    ]
