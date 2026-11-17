from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pakgrid_walkthroughs", "0003_one_live_walkthrough_per_place"),
    ]

    operations = [
        migrations.AddField(
            model_name="walkthrough",
            name="job_token",
            field=models.CharField(blank=True, editable=False, max_length=32),
        ),
    ]
