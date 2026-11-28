"""
Tests for choosing which backend stores each course's Files.
"""

import ddt
import pytest
from django.urls import reverse
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CourseRun
from openedx_content import api as content_api
from openedx_content.models_api import CourseContent

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.contentstore_shim import api
from openedx.core.djangoapps.contentstore_shim.models import CourseAssetBackend
from openedx.core.djangoapps.contentstore_shim.store import VersionedContentStoreShim
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from xmodule.contentstore.content import StaticContent

Status = CourseAssetBackend.Status

COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+Backend+Run1")
RERUN_KEY = CourseKey.from_string("course-v1:OpenedX+Backend+Run2")


class StatusTestCase(CacheIsolationTestCase):
    """
    Base class for tests that change course statuses.

    The statuses are cached in the default cache, so these tests need to clear
    it (as ``CacheIsolationTestCase`` does), or the statuses they set could leak
    into later tests.
    """

    def setUp(self):
        super().setUp()
        self.course_run = catalog_api.create_course_run_for_modulestore_course_with(COURSE_KEY, title="Backend Test")

    def get_status_uncached(self, course_key=COURSE_KEY):
        self.clear_caches()
        return api.get_course_asset_backend_status(course_key)


@ddt.ddt
class GetSetStatusTest(StatusTestCase):
    """
    Tests for getting and setting course statuses.
    """

    # Use a real default cache, even under LMS test settings (where it's a DummyCache), to test the caching.
    ENABLED_CACHES = ["default"]

    def test_default_is_legacy(self):
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY
        # Including for courses with no CourseRun:
        assert api.get_course_asset_backend_status(RERUN_KEY) == Status.LEGACY

    @ddt.data(Status.LEGACY, Status.IN_PROGRESS, Status.MIGRATED)
    def test_set_status(self, status):
        api.set_course_asset_backend_status(COURSE_KEY, status)
        assert api.get_course_asset_backend_status(COURSE_KEY) == status
        assert self.get_status_uncached() == status
        assert CourseAssetBackend.objects.get(course_run=self.course_run).status == status
        # Only migrated courses get a Learning Package up front:
        assert CourseContent.objects.filter(course_run=self.course_run).exists() == (status == Status.MIGRATED)

    def test_set_status_no_course_run(self):
        with pytest.raises(CourseRun.DoesNotExist):
            api.set_course_asset_backend_status(RERUN_KEY, Status.MIGRATED)

    def test_migrate_and_roll_back(self):
        """
        Switching back to legacy keeps the Learning Package, so switching to
        migrated again uses the same one.
        """
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        learning_package = content_api.get_learning_package_for_course_run(COURSE_KEY)
        api.set_course_asset_backend_status(COURSE_KEY, Status.LEGACY)
        assert content_api.get_learning_package_for_course_run(COURSE_KEY) == learning_package
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        assert content_api.get_learning_package_for_course_run(COURSE_KEY) == learning_package

    def test_status_is_cached(self):
        with self.assertNumQueries(1):
            assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY
        with self.assertNumQueries(0):
            assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY
        # It's still cached in the default cache when the request ends:
        RequestCache.clear_all_namespaces()
        with self.assertNumQueries(0):
            assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY

    def test_cache_cleared_on_change(self):
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY
        # Changing the status by any means clears the cache:
        CourseAssetBackend.objects.create(course_run=self.course_run, status=Status.IN_PROGRESS)
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.IN_PROGRESS
        backend = CourseAssetBackend.objects.get(course_run=self.course_run)
        backend.status = Status.MIGRATED
        backend.save()
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.MIGRATED
        backend.delete()
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY

    def test_cache_cleared_on_course_run_delete(self):
        """
        If a course run is deleted and then re-created with the same key, the
        new one doesn't get the old one's status.
        """
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.MIGRATED
        catalog_api.delete_course_run(COURSE_KEY)
        assert not CourseAssetBackend.objects.exists()
        assert api.get_course_asset_backend_status(COURSE_KEY) == Status.LEGACY


@ddt.ddt
class InheritStatusTest(StatusTestCase):
    """
    Tests for ``inherit_course_asset_backend_status()``, used for reruns.
    """

    def test_migrated_source(self):
        """
        A rerun of a migrated course is migrated too, with its own Learning
        Package, and a CourseRun is created for it.
        """
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        api.inherit_course_asset_backend_status(COURSE_KEY, RERUN_KEY)
        assert self.get_status_uncached(RERUN_KEY) == Status.MIGRATED
        rerun = catalog_api.get_course_run(RERUN_KEY)
        assert rerun.title == "Backend Test"
        assert rerun.catalog_course == self.course_run.catalog_course
        assert (
            content_api.get_learning_package_for_course_run(RERUN_KEY)
            != content_api.get_learning_package_for_course_run(COURSE_KEY)
        )

    def test_migrated_source_existing_course_run(self):
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        rerun = catalog_api.create_course_run_for_modulestore_course_with(RERUN_KEY, title="Rerun")
        api.inherit_course_asset_backend_status(COURSE_KEY, RERUN_KEY)
        assert self.get_status_uncached(RERUN_KEY) == Status.MIGRATED
        assert catalog_api.get_course_run(RERUN_KEY) == rerun

    @ddt.data(None, Status.LEGACY, Status.IN_PROGRESS)
    def test_source_files_in_legacy_store(self, source_status):
        """
        If the source course's Files are in the legacy store, the rerun stays
        legacy, and no CourseRun is created for it.
        """
        if source_status:
            api.set_course_asset_backend_status(COURSE_KEY, source_status)
        api.inherit_course_asset_backend_status(COURSE_KEY, RERUN_KEY)
        assert self.get_status_uncached(RERUN_KEY) == Status.LEGACY
        assert not CourseRun.objects.filter(course_key=RERUN_KEY).exists()

    def test_dest_status_is_kept(self):
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        catalog_api.create_course_run_for_modulestore_course_with(RERUN_KEY, title="Rerun")
        api.set_course_asset_backend_status(RERUN_KEY, Status.LEGACY)
        api.inherit_course_asset_backend_status(COURSE_KEY, RERUN_KEY)
        assert self.get_status_uncached(RERUN_KEY) == Status.LEGACY


class CourseAssetBackendAdminTest(StatusTestCase):
    """
    Tests for the Django admin.
    """

    def setUp(self):
        super().setUp()
        superuser = UserFactory(is_staff=True, is_superuser=True)
        self.client.login(username=superuser.username, password="Password1234")

    def test_add_migrated(self):
        """Adding a course as migrated creates its Learning Package."""
        response = self.client.get(reverse("admin:contentstore_shim_courseassetbackend_add"))
        assert response.status_code == 200
        response = self.client.post(
            reverse("admin:contentstore_shim_courseassetbackend_add"),
            {"course_run": self.course_run.pk, "status": Status.MIGRATED},
        )
        assert response.status_code == 302, response.content
        assert self.get_status_uncached() == Status.MIGRATED
        assert CourseContent.objects.filter(course_run=self.course_run).exists()

    def test_list_and_change(self):
        api.set_course_asset_backend_status(COURSE_KEY, Status.MIGRATED)
        location = StaticContent.compute_location(COURSE_KEY, "photo.png")
        VersionedContentStoreShim().save(StaticContent(location, "photo.png", "image/png", b"data"))

        response = self.client.get(reverse("admin:contentstore_shim_courseassetbackend_changelist"))
        self.assertContains(response, str(COURSE_KEY))

        url = reverse("admin:contentstore_shim_courseassetbackend_change", args=[self.course_run.pk])
        response = self.client.get(url)
        self.assertContains(response, "Files in openedx_content")
        readonly = {
            field.field["name"]: field.contents()
            for fieldset in response.context["adminform"]
            for line in fieldset
            for field in line
            if field.is_readonly
        }
        assert readonly["course_run"]  # can't be changed
        learning_package = CourseContent.objects.get(course_run=self.course_run).learning_package
        assets_url = reverse("admin:openedx_content_asset_changelist")
        assert readonly["openedx_content_file_count"] == (
            f'<a href="{assets_url}?learning_package__id__exact={learning_package.pk}">1</a>'
        )
        package_url = reverse("admin:openedx_content_learningpackage_change", args=[learning_package.pk])
        assert readonly["learning_package"] == f'<a href="{package_url}">{learning_package}</a>'
        assert readonly["legacy_file_count"] == "0"

        response = self.client.post(url, {"status": Status.LEGACY})
        assert response.status_code == 302, response.content
        assert self.get_status_uncached() == Status.LEGACY
