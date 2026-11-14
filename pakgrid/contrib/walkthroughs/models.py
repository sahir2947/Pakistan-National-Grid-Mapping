import os
import uuid

from django.conf import settings
from django.core.files.storage import Storage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver


def video_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()[:8] or ".mp4"
    return f"walkthroughs/{instance.id}/video{ext}"


class _ConfiguredStorage(Storage):
    """Passes every call to PAKGRID_WALKTHROUGHS['STORAGE'], looked up at that moment (Django
    reads a field's storage once, at start-up; this keeps settings overrides working)."""

    def _s(self):
        from .conf import get_storage
        return get_storage()

    def _open(self, name, mode="rb"):
        return self._s().open(name, mode)

    def _save(self, name, content):
        return self._s().save(name, content)

    def generate_filename(self, filename):
        return self._s().generate_filename(filename)

    def get_available_name(self, name, max_length=None):
        return self._s().get_available_name(name, max_length=max_length)

    def get_valid_name(self, name):
        return self._s().get_valid_name(name)

    def delete(self, name):
        return self._s().delete(name)

    def exists(self, name):
        return self._s().exists(name)

    def url(self, name):
        return self._s().url(name)

    def size(self, name):
        return self._s().size(name)

    def path(self, name):
        return self._s().path(name)

    def listdir(self, path):
        return self._s().listdir(path)


_STORAGE = _ConfiguredStorage()


def walkthrough_storage():
    """The storage of videos and photos: PAKGRID_WALKTHROUGHS['STORAGE'] (see conf)."""
    return _STORAGE


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
    video = models.FileField(upload_to=video_path, storage=walkthrough_storage, blank=True)
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
    image = models.FileField(upload_to=photo_path, storage=walkthrough_storage)

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
