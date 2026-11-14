"""The API's URLs (see pakgrid.contrib.walkthroughs.api); mount with
`path("api/", include("pakgrid.contrib.walkthroughs.urls"))`."""

from django.urls import path

from . import api

app_name = "pakgrid_walkthroughs"

urlpatterns = [
    path("walkthroughs/", api.WalkthroughListCreate.as_view(), name="list"),
    path("walkthroughs/for/<str:place_ref>/", api.RiderWalkthrough.as_view(), name="for-place"),
    path("walkthroughs/<uuid:pk>/", api.WalkthroughDetail.as_view(), name="detail"),
    path("walkthroughs/<uuid:pk>/approve/", api.WalkthroughReview.as_view(action="approve"), name="approve"),
    path("walkthroughs/<uuid:pk>/reject/", api.WalkthroughReview.as_view(action="reject"), name="reject"),
    path("walkthroughs/<uuid:pk>/retry/", api.WalkthroughReview.as_view(action="retry"), name="retry"),
]
