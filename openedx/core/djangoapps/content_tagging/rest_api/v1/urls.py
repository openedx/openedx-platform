"""
Taxonomies API v1 URLs.
"""

from django.urls.conf import include, path
from openedx_tagging.rest_api.v1 import views as oel_tagging_views
from openedx_tagging.rest_api.v1 import views_import as oel_tagging_views_import
from openedx_tagging.rest_api.v1.views import ObjectTagCountsView
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("taxonomies", views.TaxonomyOrgView, basename="taxonomy")
router.register("object_tags", views.ObjectTagOrgView, basename="object_tag")
router.register("object_tag_counts", ObjectTagCountsView, basename="object_tag_counts")

urlpatterns = [
    path(
        "taxonomies/<str:pk>/tags/",
        oel_tagging_views.TaxonomyTagsView.as_view(),
        name="taxonomy-tags",
    ),
    path(
        "taxonomies/import/template.<str:file_ext>",
        oel_tagging_views_import.TemplateView.as_view(),
        name="taxonomy-import-template",
    ),
    path(
        "object_tags/<str:context_id>/export/",
        views.ObjectTagExportView.as_view(),
    ),
    path('', include(router.urls))
]
