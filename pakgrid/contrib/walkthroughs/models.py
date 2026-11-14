import os
import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver


def video_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()[:8] or ".mp4"
    return f"walkthroughs/{instance.id}/video{ext}"


def photo_path(instance, filename):
    return f"walkthroughs/{instance.walkthrough_id}/{os.path.basename(filename)}"


class Walkthrough(models.Model):
    """One recorded walk from the road to an address's door, and its photos."""

    PROCESSING, REVIEW, LIVE, REJECTED, FAILED, REPLACED = "processing", "review", "live", "rejected", "failed", "replaced"
    STATUSES = [(PROCESSING, "Taking photos"), (REVIEW, "Waiting for the owner's approval"), (LIVE, "Live"),
                (REJECTED, "Rejected"), (FAILED, "Failed"), (REPLACED, "Replaced by a newer one")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)     # unguessable in URLs
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                              related_name="pakgrid_walkthroughs")
    place_ref = models.CharField(max_length=100, db_index=True, blank=True,
                                 help_text="Your app's own reference for the address or shop")
    pin_sub_cell = models.CharField(max_length=20, db_index=True, blank=True)
    length_m = models.FloatField()
    data = models.JSONField(help_text="pakgrid.Walkthrough.to_dict()")
    video = models.FileField(upload_to=video_path, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=PROCESSING, db_index=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    photos_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["place_ref", "status"])]

    def __str__(self):
        return f"Walkthrough {self.place_ref or self.pin_sub_cell} ({self.get_status_display()}, {self.length_m:.0f} m)"


class WalkthroughPhoto(models.Model):
    walkthrough = models.ForeignKey(Walkthrough, on_delete=models.CASCADE, related_name="photos")
    index = models.PositiveIntegerField()
    image = models.FileField(upload_to=photo_path)

    class Meta:
        ordering = ["walkthrough", "index"]
        constraints = [models.UniqueConstraint(fields=["walkthrough", "index"], name="pakgrid_walkthrough_photo_index")]

    def __str__(self):
        return f"{self.walkthrough_id} photo {self.index}"


# Files go with their records (storage does not delete them by itself).
@receiver(post_delete)
def _delete_files(sender, instance, **kwargs):
    if sender is WalkthroughPhoto and instance.image:
        instance.image.delete(save=False)
    elif sender is Walkthrough and instance.video:
        instance.video.delete(save=False)
