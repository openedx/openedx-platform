"""
Tests for the parts of ``VersionedContentStoreShim`` that are specific to it
(see test_contract.py for the rest).
"""

import hashlib
from unittest import mock

import ddt
import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api
from openedx_content import api as content_api
from openedx_content.models_api import CourseContent

from openedx.core.djangoapps.contentstore_shim.models import MediaHashShim
from openedx.core.djangoapps.contentstore_shim.mongo_query import UnsupportedQuery
from openedx.core.djangoapps.contentstore_shim.store import VersionedContentStoreShim
from xmodule.contentstore.content import StaticContent

COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+Shim+Run1")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+Shim+Run2")

User = get_user_model()


@ddt.ddt
class VersionedContentStoreShimTest(TestCase):
    """
    Tests for how ``VersionedContentStoreShim`` stores files in
    ``openedx_content``.
    """

    def setUp(self):
        super().setUp()
        for course_key in (COURSE_KEY, OTHER_COURSE_KEY):
            catalog_api.create_course_run_for_modulestore_course_with(course_key, title=f"Course {course_key.run}")
        self.store = VersionedContentStoreShim()

    def save(self, filename, data=b"data", content_type="image/png", course_key=COURSE_KEY, **kwargs):
        location = StaticContent.compute_location(course_key, kwargs.get("import_path") or filename)
        content = StaticContent(location, filename, content_type, data, **kwargs)
        self.store.save(content)
        return content

    def get_asset(self, path, course_key=COURSE_KEY):
        learning_package = content_api.get_learning_package_for_course_run(course_key)
        return content_api.get_asset_by_path(learning_package, path)

    def test_creates_learning_package(self):
        """
        The course's Learning Package is created when the first asset is saved,
        not when reading.
        """
        location = COURSE_KEY.make_asset_key("asset", "photo.png")
        assert self.store.find(location, throw_on_not_found=False) is None
        assert self.store.get_all_content_for_course(COURSE_KEY) == ([], 0)
        self.store.delete(location)
        assert not CourseContent.objects.exists()

        self.save("photo.png")
        assert content_api.get_course_content(COURSE_KEY).learning_package.title == "Course Run1"

    def test_asset_stored_and_published(self):
        content = self.save("photo.png", b"PNG data", import_path="images/photo.png")
        asset = self.get_asset("images/photo.png")
        assert asset.versioning.draft == asset.versioning.published
        version = asset.versioning.published
        assert version.title == "photo.png"
        assert version.media.mime_type == "image/png"
        assert version.media.read_file().read() == b"PNG data"
        assert asset.legacy_flat_path == content.location.block_id == "images_photo.png"

    def test_each_save_is_a_new_version(self):
        self.save("photo.png", b"version 1")
        self.save("photo.png", b"version 2")
        asset = self.get_asset("photo.png")
        assert asset.versioning.published.version_num == 2
        assert asset.versioning.published.media.read_file().read() == b"version 2"

    def test_delete_is_soft(self):
        content = self.save("photo.png", b"version 1")
        self.store.delete(content.location)
        asset = self.get_asset("photo.png")
        assert asset.versioning.draft is None
        assert asset.versioning.published is None

        # Saving it again restores it with a new version:
        self.save("photo.png", b"version 2")
        asset = self.get_asset("photo.png")
        assert asset.versioning.published.version_num == 2

    def test_restore_keeps_path(self):
        """
        A restored asset keeps its original path, even if it's saved again
        without an import_path.
        """
        content = self.save("photo.png", import_path="images/photo.png")
        self.store.delete(content.location)
        self.store.save(StaticContent(content.location, "photo.png", "image/png", b"new data"))
        assert self.store.find(content.location).import_path == "images/photo.png"

    @ddt.data(
        (False, content_api.AssetPermissions.PUBLIC),
        (True, content_api.AssetPermissions.ENROLLED),
    )
    @ddt.unpack
    def test_permissions(self, locked, expected_permissions):
        content = self.save("photo.png", locked=locked)
        assert self.get_asset("photo.png").permissions == expected_permissions
        self.store.set_attr(content.location, "locked", not locked)
        assert self.get_asset("photo.png").permissions != expected_permissions

    @ddt.data(False, True)
    def test_python_lib_is_staff_only(self, locked):
        content = self.save("python_lib.zip", content_type="application/zip", locked=locked)
        assert self.get_asset("python_lib.zip").permissions == content_api.AssetPermissions.STAFF
        # It's not reported as locked, so the contentserver applies its own python_lib.zip rules:
        assert self.store.find(content.location).locked is False
        assert self.store.get_all_content_for_course(content.location.course_key)[0][0]["locked"] is False
        self.store.set_attr(content.location, "locked", False)
        assert self.get_asset("python_lib.zip").permissions == content_api.AssetPermissions.STAFF

    def test_displayname_change_is_a_new_version(self):
        content = self.save("photo.png")
        self.store.set_attr(content.location, "displayname", "New name")
        asset = self.get_asset("photo.png")
        assert asset.versioning.published.version_num == 2
        assert asset.versioning.published.title == "New name"

    def test_unsupported_attr(self):
        content = self.save("photo.png")
        with pytest.raises(NotImplementedError):
            self.store.set_attr(content.location, "thumbnail_location", None)

    @ddt.data(
        # (import_path, asset key name, expected Asset.path)
        ("images/photo.png", "images_photo.png", "images/photo.png"),
        ("images/My Photo (1).png", "images_My_Photo__1_.png", "images/My Photo (1).png"),
        # Not a valid Asset.path:
        ("images//photo.png", "images__photo.png", "images__photo.png"),
        ("../photo.png", ".._photo.png", ".._photo.png"),
    )
    @ddt.unpack
    def test_asset_path(self, import_path, expected_name, expected_path):
        content = self.save("photo.png", import_path=import_path)
        assert content.location.block_id == expected_name
        assert self.get_asset(expected_path)
        found = self.store.find(content.location)
        assert found.import_path == (import_path if expected_path == import_path else None)

    def test_import_path_not_matching_asset_key(self):
        """An import_path that doesn't correspond to the AssetKey is ignored."""
        location = COURSE_KEY.make_asset_key("asset", "photo.png")
        self.store.save(StaticContent(location, "photo.png", "image/png", b"data", import_path="other/file.png"))
        assert self.get_asset("photo.png")
        assert self.store.find(location).import_path is None

    def test_invalid_name(self):
        location = COURSE_KEY.make_asset_key("asset", "..")
        with pytest.raises(ValueError, match="not a valid asset path"):
            self.store.save(StaticContent(location, "..", "image/png", b"data"))

    def test_adopts_existing_asset(self):
        """
        If an Asset with the same path was created without using the shim,
        saving adopts it.
        """
        course_content, _created = content_api.get_or_create_course_content(COURSE_KEY)
        content_api.create_asset_and_version(course_content.learning_package, "images/photo.png", media=b"old")
        location = COURSE_KEY.make_asset_key("asset", "images_photo.png")
        # Not visible until adopted:
        assert self.store.find(location, throw_on_not_found=False) is None
        assert self.store.get_all_content_for_course(COURSE_KEY) == ([], 0)

        self.save("photo.png", b"new", import_path="images/photo.png")
        asset = self.get_asset("images/photo.png")
        assert asset.versioning.published.version_num == 2
        assert asset.legacy_flat_path == "images_photo.png"
        assert self.store.find(location).data == b"new"

    def test_md5_stored(self):
        content = self.save("photo.png", b"PNG data")
        media = self.get_asset("photo.png").versioning.published.media
        assert MediaHashShim.objects.get(hash_digest=media.hash_digest).md5 == hashlib.md5(b"PNG data").hexdigest()
        assert self.store.find(content.location).content_digest == hashlib.md5(b"PNG data").hexdigest()

    def test_md5_computed_if_missing(self):
        content = self.save("photo.png", b"PNG data")
        MediaHashShim.objects.all().delete()
        assert self.store.find(content.location).content_digest == hashlib.md5(b"PNG data").hexdigest()
        assert MediaHashShim.objects.count() == 1

    def test_md5_shared_between_courses(self):
        self.save("photo.png", b"PNG data")
        self.save("photo.png", b"PNG data", course_key=OTHER_COURSE_KEY)
        assert MediaHashShim.objects.count() == 1

    @ddt.data(
        ("image/png", "image/png"),
        ("image/svg+xml", "image/svg+xml"),
        ("text/html; charset=utf-8", "text/html"),
        (None, "application/octet-stream"),
        ("invalid", "application/octet-stream"),
        ("a/b+c+d", "application/octet-stream"),
    )
    @ddt.unpack
    def test_content_type(self, content_type, expected):
        content = self.save("file", content_type=content_type)
        assert self.store.find(content.location).content_type == expected
        assets, _count = self.store.get_all_content_for_course(COURSE_KEY)
        assert assets[0]["contentType"] == expected

    @ddt.data("image/png", "image/svg+xml")
    def test_thumbnails_not_supported(self, content_type):
        """
        No thumbnails are created, but for an image, generate_thumbnail()
        returns the image's own location.
        """
        content = self.save("photo.png", content_type=content_type)
        thumbnail, location = self.store.generate_thumbnail(content, dimensions=(10, 10))
        assert thumbnail is None
        assert location == content.location
        assert self.store.find(content.location).thumbnail_location is None
        assets, _count = self.store.get_all_content_for_course(COURSE_KEY)
        assert assets[0]["thumbnail_location"] is None
        assert not self.store.get_all_content_thumbnails_for_course(COURSE_KEY)

        thumbnail_location = COURSE_KEY.make_asset_key("thumbnail", "photo-png.jpg")
        assert self.store.find(thumbnail_location, throw_on_not_found=False) is None
        with pytest.raises(ValueError, match="can only store 'asset' content"):
            self.store.save(StaticContent(thumbnail_location, "photo-png.jpg", "image/jpeg", b"data"))

    def test_generate_thumbnail_not_image(self):
        content = self.save("notes.txt", content_type="text/plain")
        thumbnail, location = self.store.generate_thumbnail(content)
        assert thumbnail is None
        assert location == COURSE_KEY.make_asset_key("thumbnail", "notes-txt.jpg")

    def test_created_by_current_user(self):
        user = User.objects.create(username="uploader")
        with mock.patch("crum.get_current_user", return_value=user):
            self.save("photo.png")
        assert self.get_asset("photo.png").versioning.published.created_by == user

    def test_unsupported_query(self):
        self.save("photo.png")
        with pytest.raises(UnsupportedQuery):
            self.store.get_all_content_for_course(COURSE_KEY, filter_params={"filename": "photo.png"})
        with pytest.raises(UnsupportedQuery):
            self.store.get_all_content_for_course(COURSE_KEY, filter_params={"displayname": {"$exists": True}})

    def test_unknown_sort_is_ignored(self):
        self.save("b.png")
        self.save("a.png")
        assets, _count = self.store.get_all_content_for_course(COURSE_KEY, sort=[("filename", 1)])
        assert [asset["displayname"] for asset in assets] == ["b.png", "a.png"]

    def test_copy_reuses_md5(self):
        self.save("photo.png", b"PNG data")
        self.store.copy_all_course_assets(COURSE_KEY, OTHER_COURSE_KEY)
        copy = self.get_asset("photo.png", course_key=OTHER_COURSE_KEY)
        assert copy.learning_package_id != self.get_asset("photo.png").learning_package_id
        assert MediaHashShim.objects.count() == 1

    def test_query_count(self):
        """Listing the assets of a course doesn't do a query per asset."""
        for i in range(5):
            self.save(f"file{i}.png")
        with self.assertNumQueries(3):  # Learning Package lookup, count, listing
            assets, count = self.store.get_all_content_for_course(COURSE_KEY, start=0, maxresults=50)
        assert count == len(assets) == 5
