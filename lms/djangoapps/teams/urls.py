"""
Defines the URL routes for this app.
"""


from django.contrib.auth.decorators import login_required
from django.urls import path

from .views import TeamsDashboardView

urlpatterns = [
    path('', login_required(TeamsDashboardView.as_view()), name="teams_dashboard")
]
