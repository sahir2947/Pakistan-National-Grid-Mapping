"""The REST API (Django REST framework). Mount it with
`path("api/", include("pakgrid.contrib.walkthroughs.urls"))`:

    POST   walkthroughs/                  upload: multipart video, track (JSON or GPX text) or track_file,
                                          pin_lat, pin_lon, [start_lat, start_lon], [place_ref], [video_start]
    GET    walkthroughs/?place_ref=...    the walkthroughs you may manage (yours; staff: all)
    GET    walkthroughs/<id>/             one, with its photos' URLs, to review
    DELETE walkthroughs/<id>/
    POST   walkthroughs/<id>/approve/     {"note": "..."}: goes live (replacing the address's live one)
    POST   walkthroughs/<id>/reject/      {"note": "..."}
    POST   walkthroughs/<id>/retry/       a failed one, again
    GET    walkthroughs/for/<place_ref>/  for a rider: the address's live walkthrough, if CAN_VIEW allows

Walkthroughs the user may not see answer 404, so their existence is not given away.
"""

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from pakgrid.walkthrough import WalkthroughError

from . import services
from .models import Walkthrough


def _summary(wt, request):
    d = wt.data
    out = {"id": str(wt.pk), "place_ref": wt.place_ref, "status": wt.status, "status_text": wt.get_status_display(),
           "error": wt.error or None, "length_m": wt.length_m, "duration_s": d.get("duration_s"),
           "photos": len(d.get("frames", [])), "turns": [t["turn"] for t in d.get("turns", [])],
           "pin_sub_cell": wt.pin_sub_cell or None, "author": d.get("author"),
           "created_at": wt.created_at.isoformat() if wt.created_at else None,
           "reviewed_at": wt.reviewed_at.isoformat() if wt.reviewed_at else None, "review_note": wt.review_note or None}
    return out


def _number(data, key, required=True):
    v = data.get(key)
    if v in (None, ""):
        if required:
            raise WalkthroughError(f"{key} is required")
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise WalkthroughError(f"{key} must be a number, got {v!r}") from None


def _managed(request, pk):
    wt = get_object_or_404(Walkthrough, pk=pk)
    if not services.can_manage(request.user, wt):
        raise _NotFound()
    return wt


class _NotFound(Exception):
    pass


class _Base(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, _NotFound):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if isinstance(exc, services.PermissionDenied):
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        if isinstance(exc, WalkthroughError):
            return Response({"detail": str(exc), "code": "walkthrough"}, status=status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


class WalkthroughListCreate(_Base):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        qs = Walkthrough.objects.all()
        if request.GET.get("place_ref"):
            qs = qs.filter(place_ref=request.GET["place_ref"])
        if not request.user.is_staff:
            qs = qs.filter(owner=request.user)
        return Response([_summary(wt, request) for wt in qs[:200] if services.can_manage(request.user, wt)])

    def post(self, request):
        d = request.data
        start = (_number(d, "start_lat", False), _number(d, "start_lon", False))
        track = request.FILES.get("track_file") or d.get("track")
        if track in (None, ""):
            raise WalkthroughError("send the GPS track as track (JSON or GPX text) or track_file")
        wt = services.create(request.user, video=request.FILES.get("video"), track=track,
                             pin=(_number(d, "pin_lat"), _number(d, "pin_lon")),
                             start=start if None not in start else None, place_ref=d.get("place_ref", ""),
                             video_start=d.get("video_start") or None)
        wt.refresh_from_db()
        return Response(_summary(wt, request), status=status.HTTP_201_CREATED)


class WalkthroughDetail(_Base):
    def get(self, request, pk):
        wt = _managed(request, pk)
        return Response({**_summary(wt, request),
                         **services.payload(wt, request.build_absolute_uri)})

    def delete(self, request, pk):
        services.delete(request.user, _managed(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class WalkthroughReview(_Base):
    action = None

    def post(self, request, pk):
        wt = _managed(request, pk)
        note = request.data.get("note", "") if hasattr(request.data, "get") else ""
        if self.action == "approve":
            services.approve(request.user, wt, note)
        elif self.action == "reject":
            services.reject(request.user, wt, note)
        else:
            services.retry(wt)
        wt.refresh_from_db()
        return Response(_summary(wt, request))


class RiderWalkthrough(_Base):
    def get(self, request, place_ref):
        wt = services.live_for(place_ref)
        if wt is None or not services.can_view(request.user, wt):
            raise _NotFound()
        return Response(services.payload(wt, request.build_absolute_uri))
