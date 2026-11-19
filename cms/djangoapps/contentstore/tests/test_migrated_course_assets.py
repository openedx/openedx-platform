"""
Tests for Studio features that use course Files, for courses whose Files are
stored in openedx_content instead of the legacy contentstore.

See also the ``Migrated*`` test cases in ``views/tests/test_assets.py``.
"""

import json
import os
import shutil
import tempfile
from io import BytesIO

import pytest
from opaque_keys.edx.locator import CourseLocator
from openedx_catalog import api as catalog_api
from rest_framework.test import APIClient

from cms.djangoapps.contentstore.tasks import rerun_course
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.course_action_state.models import CourseRerunState
from openedx.core.djangoapps.contentstore_shim import api as backend_api
from openedx.core.djangoapps.contentstore_shim.models import CourseAssetBackend
from openedx.core.djangoapps.contentstore_shim.tests.utils import set_course_asset_backend_status_for_test
from openedx.core.lib.courses import create_course_image_thumbnail
from xmodule.contentstore.content import ContentStoreReadOnlyError, StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.modulestore import EdxJSONEncoder
from xmodule.modulestore.tests.django_utils import upload_file_to_course
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, ToyCourseFactory
from xmodule.modulestore.xml_exporter import export_course_to_xml
from xmodule.modulestore.xml_importer import import_course_from_xml

Status = CourseAssetBackend.Status


class MigratedCourseAssetsTestCase(CourseTestCase):
    """
    Base class with helpers for checking which backend has a course's Files.
    """

    def migrate(self, course_key, status=Status.MIGRATED):
        set_course_asset_backend_status_for_test(course_key, status)

    def save_file(self, course_key, path, data=b"data", content_type="image/png", locked=False):
        """
        Save a file with the given path (e.g. ``"images/photo.png"``), the way
        the course importer does.
        """
        location = StaticContent.compute_location(course_key, path)
        content = StaticContent(
            location, os.path.basename(path), content_type, data, import_path=path, locked=locked
        )
        contentstore().save(content)
        return location

    def get_file_names(self, store, course_key):
        assets, _count = store.get_all_content_for_course(course_key)
        return sorted(asset["asset_key"].path for asset in assets)

    def assert_files_in_versioned_store(self, course_key, expected_names):
        """
        Check that the course's Files are all in openedx_content, and none are
        in the legacy contentstore.
        """
        assert self.get_file_names(contentstore().versioned_store, course_key) == sorted(expected_names)
        assert not self.get_file_names(contentstore().legacy_store, course_key)
        # And contentstore() finds them:
        assert self.get_file_names(contentstore(), course_key) == sorted(expected_names)


class AssetHandlerTest(MigratedCourseAssetsTestCase):
    """
    Tests for the Studio Files page handlers that aren't covered by the
    ``Migrated*`` test cases in ``views/tests/test_assets.py``.
    """

    def test_lock_and_unlock(self):
        self.migrate(self.course.id)
        location = self.save_file(self.course.id, "photo.png")
        url = reverse_course_url("assets_handler", self.course.id, kwargs={"asset_key_string": str(location)})

        for locked in (True, False):
            response = self.client.post(url, json.dumps({"locked": locked}), "application/json")
            assert response.status_code == 201
            assert contentstore().find(location).locked is locked
            assets, _count = contentstore().get_all_content_for_course(self.course.id)
            assert assets[0]["locked"] is locked
        self.assert_files_in_versioned_store(self.course.id, ["photo.png"])

    def test_course_image_thumbnail(self):
        """
        Migrated courses don't have thumbnails, so the course image "thumbnail"
        URL is the URL of the full image, rather than one that doesn't exist.
        """
        self.migrate(self.course.id)
        self.save_file(self.course.id, self.course.course_image, b"image data", content_type="image/jpeg")
        url = create_course_image_thumbnail(self.course, (100, 100))
        assert url == f"/{self.course.id.make_asset_key('asset', self.course.course_image)}"
        response = self.client.get(url)
        assert response.status_code == 200
        content = b"".join(response.streaming_content) if response.streaming else response.content
        assert content == b"image data"

    def test_in_progress_course_is_read_only(self):
        """
        While a course's Files are being migrated, they can be read, but not
        changed.
        """
        location = self.save_file(self.course.id, "photo.png")
        self.migrate(self.course.id, Status.IN_PROGRESS)

        response = self.client.get(self.url_for_assets(), HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert [asset["display_name"] for asset in response.json()["assets"]] == ["photo.png"]

        with pytest.raises(ContentStoreReadOnlyError):
            self.client.post(self.url_for_assets(), {"file": self.make_upload("new.txt")})
        url = reverse_course_url("assets_handler", self.course.id, kwargs={"asset_key_string": str(location)})
        with pytest.raises(ContentStoreReadOnlyError):
            self.client.delete(url, HTTP_ACCEPT="application/json")
        assert self.get_file_names(contentstore(), self.course.id) == ["photo.png"]

    def url_for_assets(self):
        return reverse_course_url("assets_handler", self.course.id)

    def make_upload(self, name):
        upload = BytesIO(b"new file")
        upload.name = name
        return upload


class ImportExportTest(MigratedCourseAssetsTestCase):
    """
    Tests that course import and export work with migrated courses.
    """

    def setUp(self):
        super().setUp()
        self.root_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root_dir)

    def test_round_trip(self):
        """
        Export a migrated course, and import it into another migrated course.
        """
        self.migrate(self.course.id)
        self.save_file(self.course.id, "images/photo.png", b"photo", locked=True)
        self.save_file(self.course.id, "handout.txt", b"handout", content_type="text/plain")

        export_course_to_xml(self.store, contentstore(), self.course.id, self.root_dir, "exported")

        static_dir = os.path.join(self.root_dir, "exported", "static")
        with open(os.path.join(static_dir, "images", "photo.png"), "rb") as f:
            assert f.read() == b"photo"
        with open(os.path.join(static_dir, "handout.txt"), "rb") as f:
            assert f.read() == b"handout"
        with open(os.path.join(self.root_dir, "exported", "policies", "assets.json")) as f:
            policy = json.load(f)
        assert policy["images_photo.png"]["locked"] is True

        dest_key = CourseLocator("edX", "imported", "2026")
        self.migrate(dest_key)
        import_course_from_xml(
            self.store,
            self.user.id,
            self.root_dir,
            ["exported"],
            static_content_store=contentstore(),
            target_id=dest_key,
            create_if_not_present=True,
        )

        self.assert_files_in_versioned_store(dest_key, ["handout.txt", "images_photo.png"])
        photo = contentstore().find(dest_key.make_asset_key("asset", "images_photo.png"))
        assert (photo.data, photo.import_path, photo.locked) == (b"photo", "images/photo.png", True)

    def test_import_legacy_export(self):
        """
        A course exported from the legacy contentstore (the toy course) can be
        imported into a migrated course.
        """
        dest_key = CourseLocator("edX", "toy_imported", "2026")
        self.migrate(dest_key)
        import_course_from_xml(
            self.store,
            self.user.id,
            "common/test/data",
            ["toy"],
            static_content_store=contentstore(),
            target_id=dest_key,
            create_if_not_present=True,
        )
        names = self.get_file_names(contentstore(), dest_key)
        assert "python_lib.zip" in names
        assert "handouts_sample_handout.txt" in names
        self.assert_files_in_versioned_store(dest_key, names)
        handout = contentstore().find(dest_key.make_asset_key("asset", "handouts_sample_handout.txt"))
        assert handout.import_path == "handouts/sample_handout.txt"


class RerunTest(MigratedCourseAssetsTestCase):
    """
    Tests that reruns and clones of courses copy their Files to the right
    backend.
    """

    def test_rerun_migrated_course(self):
        """
        A rerun of a migrated course is migrated too, and gets a copy of the
        source course's Files.
        """
        self.migrate(self.course.id)
        self.save_file(self.course.id, "images/photo.png", b"photo", locked=True)
        rerun_key = CourseLocator(self.course.id.org, self.course.id.course, "rerun")
        fields = {"display_name": "Rerun"}
        CourseRerunState.objects.initiated(self.course.id, rerun_key, self.user, fields["display_name"])

        result = rerun_course.delay(
            str(self.course.id), str(rerun_key), self.user.id, json.dumps(fields, cls=EdxJSONEncoder)
        )

        assert result.get() == "succeeded"
        assert backend_api.get_course_asset_backend_status(rerun_key) == Status.MIGRATED
        assert catalog_api.get_course_run(rerun_key)
        self.assert_files_in_versioned_store(rerun_key, ["images_photo.png"])
        photo = contentstore().find(rerun_key.make_asset_key("asset", "images_photo.png"))
        assert (photo.data, photo.import_path, photo.locked) == (b"photo", "images/photo.png", True)
        # The source course still has its own copy:
        self.assert_files_in_versioned_store(self.course.id, ["images_photo.png"])

    def test_clone_legacy_course(self):
        """
        Cloning a legacy course leaves the clone in the legacy store.
        """
        self.save_file(self.course.id, "photo.png")
        dest_key = CourseLocator(self.course.id.org, self.course.id.course, "clone")
        self.store.clone_course(self.course.id, dest_key, self.user.id)
        assert backend_api.get_course_asset_backend_status(dest_key) == Status.LEGACY
        assert self.get_file_names(contentstore().legacy_store, dest_key) == ["photo.png"]

    def test_clone_between_backends(self):
        """
        If the destination course already uses a different backend, the Files
        are copied between backends.
        """
        self.save_file(self.course.id, "images/photo.png", b"photo", locked=True)
        dest_key = CourseLocator(self.course.id.org, self.course.id.course, "clone")
        self.migrate(dest_key)
        self.store.clone_course(self.course.id, dest_key, self.user.id)
        self.assert_files_in_versioned_store(dest_key, ["images_photo.png"])
        photo = contentstore().find(dest_key.make_asset_key("asset", "images_photo.png"))
        assert (photo.data, photo.import_path, photo.locked) == (b"photo", "images/photo.png", True)


class PasteTest(MigratedCourseAssetsTestCase):
    """
    Tests that pasting a block into a migrated course copies its Files, and
    detects conflicts using the MD5 hashes of the Files.
    """

    def test_paste_with_assets(self):
        dest_course_key = ToyCourseFactory.create().id
        self.migrate(dest_course_key)
        source_course = CourseFactory.create()
        self.migrate(source_course.id)
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)

        for name in ("picture1.jpg", "picture2.jpg"):
            upload_file_to_course(source_course.id, contentstore(), f"./common/test/data/static/{name}", name)
        source_html = BlockFactory.create(
            parent_location=source_course.location,
            category="html",
            display_name="Some HTML",
            data='<p><a href="/static/picture1.jpg">1</a> <a href="/static/picture2.jpg">2</a></p>',
        )
        # A file with the same name but different data:
        upload_file_to_course(
            dest_course_key, contentstore(), "./common/test/data/static/picture3.jpg", "picture2.jpg"
        )

        response = client.post(
            "/api/content-staging/v1/clipboard/", {"usage_key": str(source_html.location)}, format="json"
        )
        assert response.status_code == 200
        response = client.post("/xblock/", {
            "parent_locator": str(dest_course_key.make_usage_key("vertical", "vertical_test")),
            "staged_content": "clipboard",
        }, format="json")
        assert response.status_code == 200
        assert response.json()["static_file_notices"] == {
            "error_files": [],
            "new_files": ["picture1.jpg"],
            "conflicting_files": ["picture2.jpg"],
        }
        self.assert_files_in_versioned_store(dest_course_key, ["picture1.jpg", "picture2.jpg"])
