from django.contrib import admin
from django.utils.html import format_html_join

from . import services
from .models import Walkthrough, WalkthroughPhoto


class PhotoInline(admin.TabularInline):
    model = WalkthroughPhoto
    extra = 0
    readonly_fields = ("index", "image")
    can_delete = False


@admin.register(Walkthrough)
class WalkthroughAdmin(admin.ModelAdmin):
    list_display = ("place_ref", "status", "length_m", "owner", "created_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("place_ref", "pin_sub_cell", "owner__username")
    readonly_fields = ("id", "owner", "pin_sub_cell", "length_m", "status", "error", "created_at", "photos_at",
                       "reviewed_at", "reviewed_by", "steps", "data")
    exclude = ("video",)
    inlines = [PhotoInline]
    actions = ["approve", "reject"]

    @admin.display(description="Steps")
    def steps(self, obj):
        return format_html_join("", "<div>{}. {} ({} s)</div>",
                                ((f["index"], f.get("text", ""), f["t_s"]) for f in obj.data.get("frames", [])))

    @admin.action(description="Approve: go live")
    def approve(self, request, queryset):
        self._review(request, queryset.filter(status=Walkthrough.REVIEW), services.approve, "approved")

    @admin.action(description="Reject")
    def reject(self, request, queryset):
        self._review(request, queryset.filter(status__in=[Walkthrough.REVIEW, Walkthrough.LIVE]), services.reject,
                     "rejected")

    def _review(self, request, queryset, step, done):
        n = 0
        for wt in queryset:
            try:
                step(request.user, wt)
                n += 1
            except services.PermissionDenied as exc:
                self.message_user(request, f"{wt}: {exc}", level="error")
        self.message_user(request, f"{n} walkthrough{'s' if n != 1 else ''} {done}")
