"""
Django admin for the contentstore shim.
"""

from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from openedx_content.models_api import LearningPackage

from xmodule.contentstore.content import ContentStore
from xmodule.contentstore.django import contentstore
from xmodule.contentstore.routing import RoutingContentStore

from .models import CourseAssetBackend
from .store import VersionedContentStoreShim


@admin.register(CourseAssetBackend)
class CourseAssetBackendAdmin(admin.ModelAdmin):
    """
    Admin for choosing which backend stores each course's Files.

    Course runs without a row here use the legacy contentstore. Changing the
    status doesn't copy any Files, so the file counts are shown to make it clear
    what the course will see after switching.
    """

    list_display = ["course_key", "status"]
    list_filter = ["status"]
    search_fields = ["course_run__course_key"]
    autocomplete_fields = ["course_run"]
    readonly_fields = ["learning_package", "legacy_file_count", "openedx_content_file_count"]

    def get_queryset(self, request):
        """Load each row's course run in the same query."""
        return super().get_queryset(request).select_related("course_run")

    def get_readonly_fields(self, request, obj=None):
        """Make the course run read-only once the row has been created."""
        if obj is not None:
            return ["course_run", *self.readonly_fields]
        return self.readonly_fields

    @admin.display(description=_("Course key"), ordering="course_run__course_key")
    def course_key(self, obj: CourseAssetBackend) -> str:
        return str(obj.course_run.course_key)

    @admin.display(description=_("Files in the legacy contentstore"))
    def legacy_file_count(self, obj: CourseAssetBackend) -> int | None:
        """The number of Files the course has in the legacy contentstore."""
        return self._count_files(obj, legacy=True)

    @admin.display(description=_("Files in openedx_content"))
    def openedx_content_file_count(self, obj: CourseAssetBackend) -> str | int | None:
        """
        The number of Files the course has in openedx_content, linked to the
        list of its Assets.
        """
        count = self._count_files(obj, legacy=False)
        learning_package = self._get_learning_package(obj)
        if count is None or learning_package is None:
            return count
        url = reverse("admin:openedx_content_asset_changelist")
        query = urlencode({"learning_package__id__exact": learning_package.id})
        return format_html('<a href="{}?{}">{}</a>', url, query, count)

    @admin.display(description=_("Learning Package"))
    def learning_package(self, obj: CourseAssetBackend) -> str | None:
        """The course's Learning Package, linked to its admin page."""
        learning_package = self._get_learning_package(obj)
        if learning_package is None:
            return None
        url = reverse("admin:openedx_content_learningpackage_change", args=[learning_package.id])
        return format_html('<a href="{}">{}</a>', url, learning_package)

    @staticmethod
    def _get_learning_package(obj: CourseAssetBackend) -> LearningPackage | None:
        """The course's Learning Package, or ``None`` if it doesn't have one yet."""
        if obj.pk is None:
            return None
        try:
            return obj.course_run.content.learning_package
        except ObjectDoesNotExist:
            return None

    @staticmethod
    def _count_files(obj: CourseAssetBackend, *, legacy: bool) -> int | None:
        """Count the course's Files in the legacy store or openedx_content (``None`` for a new row)."""
        if obj.pk is None:
            return None
        store: ContentStore
        if legacy:
            router = contentstore()
            assert isinstance(router, RoutingContentStore)
            store = router.legacy_store
        else:
            store = VersionedContentStoreShim()
        _assets, count = store.get_all_content_for_course(obj.course_run.course_key, maxresults=1)
        return count
