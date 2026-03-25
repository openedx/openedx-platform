"""
Defines the URL routes for this app.
"""


from django.urls import path  # noqa: I001
from django.contrib.auth.decorators import login_required

from .views import TeamsDashboardView

urlpatterns = [
    path('', login_required(TeamsDashboardView.as_view()), name="teams_dashboard")
]
