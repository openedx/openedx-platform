"""
Defines URLs for theming views.
"""
from django.urls import path

from . import helpers, views

app_name = "openedx.core.djangoapps.theming"

urlpatterns = [
    path(
        "asset/<path:path>",
        views.themed_asset,
        name="openedx.theming.asset",
    ),
]

if helpers.is_comprehensive_theming_enabled():
    urlpatterns += [
        path('admin', views.ThemingAdministrationFragmentView.as_view(),
             name="openedx.theming.update_theme_fragment_view",
             ),
    ]
