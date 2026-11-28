"""
Tests that ``VersionedContentStoreShim`` behaves the same as the legacy
``MongoContentStore``.

The same tests run against both backends. They focus on the behavior that the
rest of the platform relies on, particularly the asset handlers used by the
Authoring MFE's "Files" page, the contentserver, and course import/export.
"""

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from io import BytesIO
from tempfile import mkdtemp
from uuid import uuid4

import ddt
import pytest
from django.test import TestCase
from freezegun import freeze_time
from opaque_keys.edx.keys import CourseKey
from openedx_catalog import api as catalog_api

from openedx.core.djangoapps.contentstore_shim.store import VersionedContentStoreShim
from xmodule.contentstore.content import ContentStore, StaticContent, StaticContentStream
from xmodule.contentstore.mongo import MongoContentStore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.tests.mongo_connection import MONGO_HOST, MONGO_PORT_NUM

COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+Contract+Run1")
OTHER_COURSE_KEY = CourseKey.from_string("course-v1:OpenedX+Contract+Run2")

ASCENDING = 1
DESCENDING = -1

# A subset of the FILES_AND_UPLOAD_TYPE_FILTERS setting from cms/envs/common.py
TYPE_FILTERS = {
    "Images": ["image/png", "image/jpeg", "image/jpg", "image/gif", "image/svg+xml"],
    "Documents": ["application/pdf", "text/plain", "text/csv"],
    "Code": ["application/json", "text/html", "text/javascript", "application/javascript", "text/css"],
}


# These functions build the same filters as cms/djangoapps/contentstore/asset_storage_handlers.py (which can't be
# imported here, because these tests also run in the LMS).


def content_type_filter(asset_types: str) -> dict:
    """Like _get_content_type_filter_for_mongo()"""
    requested = asset_types.split(",")
    type_filter: dict = {"$or": []}
    if "OTHER" in requested:
        all_types = [content_type for content_types in TYPE_FILTERS.values() for content_type in content_types]
        type_filter["$or"].append({"contentType": {"$nin": all_types}})
        requested.remove("OTHER")
    content_types = [content_type for asset_type in requested for content_type in TYPE_FILTERS[asset_type]]
    type_filter["$or"].append({"contentType": {"$in": content_types}})
    return type_filter


def displayname_filter(displaynames: list[str]) -> dict:
    """Like _get_displayname_filter_for_mongo()"""
    return {"$or": [{"displayname": {"$eq": displayname}} for displayname in displaynames]}


def displayname_search_filter(text_search: str) -> dict:
    """Like _get_displayname_search_filter_for_mongo()"""
    return {"$and": [{"displayname": {"$regex": re.escape(token), "$options": "i"}} for token in text_search.split()]}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


@ddt.ddt
class ContentStoreContractTestMixin:
    """
    Tests for the behavior of a ``ContentStore`` that callers rely on.
    Subclasses must implement ``make_store()``.
    """

    store: ContentStore

    def make_store(self) -> ContentStore:
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.store = self.make_store()

    def save(self, filename, data=b"data", content_type="image/png", course_key=COURSE_KEY, **kwargs):
        """
        Save an asset (like the upload handler does) and return its
        StaticContent.
        """
        location = StaticContent.compute_location(course_key, kwargs.get("import_path") or filename)
        content = StaticContent(location, filename, content_type, data, **kwargs)
        self.store.save(content)
        return content

    # Single assets

    def test_save_and_find(self):
        content = self.save("photo.png", b"PNG data", locked=True)
        found = self.store.find(content.location)
        assert found.location == content.location
        assert found.name == "photo.png"
        assert found.content_type == "image/png"
        assert found.data == b"PNG data"
        assert found.length == len(b"PNG data")
        assert found.locked is True
        assert found.content_digest == md5(b"PNG data")
        assert found.import_path is None
        assert found.thumbnail_location is None
        assert isinstance(found.last_modified_at, datetime)
        assert found.last_modified_at.tzinfo is not None

    def test_find_as_stream(self):
        content = self.save("notes.txt", b"0123456789" * 300, content_type="text/plain")
        found = self.store.find(content.location, as_stream=True)
        assert isinstance(found, StaticContentStream)
        assert found.length == 3000
        assert found.content_digest == md5(b"0123456789" * 300)
        assert b"".join(found.stream_data()) == b"0123456789" * 300
        # Used by the contentserver for HTTP range requests:
        assert b"".join(found.stream_data_in_range(5, 14)) == b"5678901234"
        in_mem = found.copy_to_in_mem()
        assert in_mem.data == b"0123456789" * 300
        found.close()

    def test_find_not_found(self):
        location = COURSE_KEY.make_asset_key("asset", "missing.png")
        with pytest.raises(NotFoundError):
            self.store.find(location)
        with pytest.raises(NotFoundError):
            self.store.find(location, as_stream=True)
        assert self.store.find(location, throw_on_not_found=False) is None

    def test_find_in_other_course(self):
        content = self.save("photo.png")
        other_location = OTHER_COURSE_KEY.make_asset_key("asset", content.location.block_id)
        assert self.store.find(other_location, throw_on_not_found=False) is None

    def test_find_thumbnail_not_found(self):
        """
        Neither store has thumbnails unless generate_thumbnail() creates them.
        """
        location = COURSE_KEY.make_asset_key("thumbnail", "photo-png.jpg")
        assert self.store.find(location, throw_on_not_found=False) is None

    @ddt.data(
        lambda: b"some data",
        lambda: "some data",
        lambda: iter([b"some ", b"data"]),
        lambda: BytesIO(b"some data"),
    )
    def test_save_data_types(self, make_data):
        """
        Callers provide data as bytes, str, an iterator of chunks (uploads), or
        a file (thumbnails).
        """
        content = self.save("data.txt", make_data(), content_type="text/plain")
        found = self.store.find(content.location)
        assert found.data == b"some data"
        assert found.content_digest == md5(b"some data")

    def test_save_replaces(self):
        content = self.save("photo.png", b"version 1", locked=True)
        self.store.save(StaticContent(content.location, "New name.png", "image/png", b"version 2"))
        found = self.store.find(content.location)
        assert found.data == b"version 2"
        assert found.name == "New name.png"
        assert found.locked is False
        assert found.content_digest == md5(b"version 2")
        assert self.store.get_all_content_for_course(COURSE_KEY)[1] == 1

    def test_save_returns_content(self):
        content = StaticContent(COURSE_KEY.make_asset_key("asset", "a.png"), "a.png", "image/png", b"a")
        assert self.store.save(content) is content

    def test_import_path(self):
        content = self.save("photo.png", import_path="images/photo.png")
        assert content.location.block_id == "images_photo.png"
        found = self.store.find(content.location)
        assert found.import_path == "images/photo.png"
        assets, _count = self.store.get_all_content_for_course(COURSE_KEY)
        assert assets[0]["import_path"] == "images/photo.png"

    def test_delete(self):
        content = self.save("photo.png")
        self.store.delete(content.location)
        assert self.store.find(content.location, throw_on_not_found=False) is None
        assert self.store.get_all_content_for_course(COURSE_KEY) == ([], 0)
        # Deleting again (or deleting something that never existed) is not an error:
        self.store.delete(content.location)
        self.store.delete(COURSE_KEY.make_asset_key("asset", "missing.png"))

    def test_save_after_delete(self):
        content = self.save("photo.png", b"version 1")
        self.store.delete(content.location)
        self.save("photo.png", b"version 2")
        assert self.store.find(content.location).data == b"version 2"

    def test_set_and_get_attr(self):
        content = self.save("photo.png")
        assert self.store.get_attr(content.location, "locked") is False
        self.store.set_attr(content.location, "locked", True)
        assert self.store.get_attr(content.location, "locked") is True
        assert self.store.find(content.location).locked is True
        self.store.set_attrs(content.location, {"locked": False})
        assert self.store.find(content.location).locked is False
        assert self.store.get_attr(content.location, "nonexistent", "default") == "default"

    def test_set_displayname(self):
        content = self.save("photo.png")
        self.store.set_attr(content.location, "displayname", "Renamed.png")
        assert self.store.find(content.location).name == "Renamed.png"

    def test_get_attrs(self):
        content = self.save("photo.png", b"1234", locked=True)
        attrs = self.store.get_attrs(content.location)
        assert attrs["displayname"] == "photo.png"
        assert attrs["contentType"] == "image/png"
        assert attrs["length"] == 4
        assert attrs["locked"] is True
        assert isinstance(attrs["uploadDate"], datetime)

    @ddt.data("_id", "md5", "uploadDate", "length")
    def test_set_protected_attr(self, attr):
        content = self.save("photo.png")
        with pytest.raises(AttributeError):
            self.store.set_attr(content.location, attr, "value")

    def test_attrs_not_found(self):
        location = COURSE_KEY.make_asset_key("asset", "missing.png")
        with pytest.raises(NotFoundError):
            self.store.get_attrs(location)
        with pytest.raises(NotFoundError):
            self.store.set_attr(location, "locked", True)

    # Listing assets

    def save_listing_assets(self):
        """Save some assets, each with a different uploadDate."""
        with freeze_time("2026-01-01 12:00:00", auto_tick_seconds=10):
            self.save("b-image.png", b"1", content_type="image/png")
            self.save("A Document.pdf", b"22", content_type="application/pdf", locked=True)
            self.save("c-other.xyz", b"333", content_type="application/octet-stream")
            self.save("d-image.JPG", b"4444", content_type="image/jpeg")
            self.save("e-Code.js", b"55555", content_type="application/javascript")
        self.save("other-course.png", course_key=OTHER_COURSE_KEY)

    def listed_names(self, **kwargs):
        """
        Get the displaynames of the assets returned by
        get_all_content_for_course(**kwargs).
        """
        assets, count = self.store.get_all_content_for_course(COURSE_KEY, **kwargs)
        names = [asset["displayname"] for asset in assets]
        if kwargs.get("maxresults", -1) <= 0 and not kwargs.get("start"):
            assert count == len(names)
        return names

    def test_listing_format(self):
        self.save_listing_assets()
        assets, count = self.store.get_all_content_for_course(COURSE_KEY, sort=[("displayname", ASCENDING)])
        assert count == 5
        doc = assets[0]
        assert doc["asset_key"] == COURSE_KEY.make_asset_key("asset", "A_Document.pdf")
        assert doc["displayname"] == "A Document.pdf"
        assert doc["contentType"] == "application/pdf"
        assert doc["length"] == 2
        assert doc["locked"] is True
        assert isinstance(doc["uploadDate"], datetime)
        assert not doc.get("thumbnail_location")
        assert not doc.get("import_path")

    def test_listing_default_order(self):
        """With no sort, assets are listed in the order they were saved."""
        self.save_listing_assets()
        assert self.listed_names() == [
            "b-image.png",
            "A Document.pdf",
            "c-other.xyz",
            "d-image.JPG",
            "e-Code.js",
        ]

    @ddt.data(
        ([("uploadDate", DESCENDING)], ["e-Code.js", "d-image.JPG", "c-other.xyz", "A Document.pdf", "b-image.png"]),
        ([("uploadDate", ASCENDING)], ["b-image.png", "A Document.pdf", "c-other.xyz", "d-image.JPG", "e-Code.js"]),
        # Sorting by displayname is case-insensitive:
        ([("displayname", ASCENDING)], ["A Document.pdf", "b-image.png", "c-other.xyz", "d-image.JPG", "e-Code.js"]),
        ([("displayname", DESCENDING)], ["e-Code.js", "d-image.JPG", "c-other.xyz", "b-image.png", "A Document.pdf"]),
    )
    @ddt.unpack
    def test_listing_sort(self, sort, expected):
        self.save_listing_assets()
        assert self.listed_names(sort=sort) == expected

    def test_listing_pagination(self):
        """
        The Files page loads assets 50 at a time, sorted by uploadDate (newest
        first).
        """
        self.save_listing_assets()
        sort = [("uploadDate", DESCENDING)]
        pages = [
            self.store.get_all_content_for_course(COURSE_KEY, start=start, maxresults=2, sort=sort)
            for start in (0, 2, 4, 6)
        ]
        assert [count for _assets, count in pages] == [5, 5, 5, 5]
        assert [[asset["displayname"] for asset in assets] for assets, _count in pages] == [
            ["e-Code.js", "d-image.JPG"],
            ["c-other.xyz", "A Document.pdf"],
            ["b-image.png"],
            [],
        ]

    def test_listing_by_displayname(self):
        """
        After uploading, the Files page loads the new assets using
        ?display_name=...&display_name=...
        """
        self.save_listing_assets()
        filter_params = {
            "user_language": "en",
            **displayname_filter(["c-other.xyz", "A Document.pdf", "nonexistent"]),
        }
        sort = [("uploadDate", DESCENDING)]
        assets, count = self.store.get_all_content_for_course(COURSE_KEY, sort=sort, filter_params=filter_params)
        assert count == 2
        assert [asset["displayname"] for asset in assets] == ["c-other.xyz", "A Document.pdf"]
        # The caller's filter_params are not modified (MongoContentStore removes user_language):
        if not isinstance(self.store, MongoContentStore):
            assert "user_language" in filter_params

    @ddt.data(
        ("Images", ["b-image.png", "d-image.JPG"]),
        ("Documents", ["A Document.pdf"]),
        ("Images,Documents", ["A Document.pdf", "b-image.png", "d-image.JPG"]),
        ("Code", ["e-Code.js"]),
        ("OTHER", ["c-other.xyz"]),
        ("Images,OTHER", ["b-image.png", "c-other.xyz", "d-image.JPG"]),
    )
    @ddt.unpack
    def test_listing_by_type(self, asset_type, expected):
        """The asset_type filter of the asset handler API"""
        self.save_listing_assets()
        filter_params = {"user_language": "en", **content_type_filter(asset_type)}
        assert self.listed_names(sort=[("displayname", ASCENDING)], filter_params=filter_params) == expected

    @ddt.data(
        ("image", ["b-image.png", "d-image.JPG"]),
        ("IMAGE b", ["b-image.png"]),
        ("e-", ["e-Code.js"]),
        ("a.b", []),  # "." is not a wildcard
        ("doc", ["A Document.pdf"]),
    )
    @ddt.unpack
    def test_listing_by_text_search(self, text_search, expected):
        """The text_search filter of the asset handler API"""
        self.save_listing_assets()
        filter_params = {"user_language": "en", **displayname_search_filter(text_search)}
        assert self.listed_names(sort=[("displayname", ASCENDING)], filter_params=filter_params) == expected

    def test_listing_empty_course(self):
        assert self.store.get_all_content_for_course(COURSE_KEY) == ([], 0)
        assert self.store.get_all_content_thumbnails_for_course(COURSE_KEY) == []

    # Whole-course operations

    def test_export(self):
        self.save("photo.png", b"photo", import_path="images/photo.png", locked=True)
        self.save("notes.txt", b"notes", content_type="text/plain")
        output_dir = mkdtemp()
        self.addCleanup(shutil.rmtree, output_dir)
        policy_file = os.path.join(output_dir, "assets.json")
        self.store.export_all_for_course(COURSE_KEY, os.path.join(output_dir, "static"), policy_file)

        with open(os.path.join(output_dir, "static", "images", "photo.png"), "rb") as f:
            assert f.read() == b"photo"
        with open(os.path.join(output_dir, "static", "notes.txt"), "rb") as f:
            assert f.read() == b"notes"
        with open(policy_file) as f:
            policy = json.load(f)
        assert set(policy) == {"images_photo.png", "notes.txt"}
        # The import code uses these fields from the policy file:
        assert policy["images_photo.png"]["displayname"] == "photo.png"
        assert policy["images_photo.png"]["locked"] is True
        assert policy["images_photo.png"]["contentType"] == "image/png"
        assert policy["images_photo.png"]["import_path"] == "images/photo.png"
        assert policy["notes.txt"]["locked"] is False

    def test_copy_all_course_assets(self):
        self.save("photo.png", b"photo", locked=True)
        self.save("image.png", b"image", import_path="images/image.png")
        self.store.copy_all_course_assets(COURSE_KEY, OTHER_COURSE_KEY)

        photo = self.store.find(OTHER_COURSE_KEY.make_asset_key("asset", "photo.png"))
        assert photo.data == b"photo"
        assert photo.locked is True
        image = self.store.find(OTHER_COURSE_KEY.make_asset_key("asset", "images_image.png"))
        assert image.data == b"image"
        assert image.import_path == "images/image.png"
        assert self.store.get_all_content_for_course(COURSE_KEY)[1] == 2

    def test_delete_all_course_assets(self):
        self.save("photo.png")
        self.save("notes.txt", content_type="text/plain")
        other = self.save("photo.png", course_key=OTHER_COURSE_KEY)
        self.store.delete_all_course_assets(COURSE_KEY)
        assert self.store.get_all_content_for_course(COURSE_KEY) == ([], 0)
        assert self.store.find(other.location).data == b"data"

    def test_remove_redundant_content_for_courses(self):
        self.save(".DS_Store")
        self.save("._photo.png")
        self.save("photo.png")
        self.save(".DS_Store", course_key=OTHER_COURSE_KEY)
        assert self.store.remove_redundant_content_for_courses() == 3
        assert self.listed_names() == ["photo.png"]
        assert self.store.get_all_content_for_course(OTHER_COURSE_KEY) == ([], 0)

    def test_generate_thumbnail_not_image(self):
        """
        No thumbnail is created for other files, but the location is returned.
        """
        content = self.save("notes.txt", content_type="text/plain")
        thumbnail, location = self.store.generate_thumbnail(content, dimensions=(10, 10))
        assert thumbnail is None
        assert location == COURSE_KEY.make_asset_key("thumbnail", "notes-txt-10x10.jpg")


class MongoContentStoreContractTest(ContentStoreContractTestMixin, TestCase):
    """
    Run the contract tests against the legacy ``MongoContentStore``.
    """

    def make_store(self):
        store = MongoContentStore(MONGO_HOST, f"test_contract_{uuid4().hex[:8]}", port=MONGO_PORT_NUM)
        self.addCleanup(store._drop_database)  # pylint: disable=protected-access
        return store


class VersionedContentStoreShimContractTest(ContentStoreContractTestMixin, TestCase):
    """
    Run the contract tests against ``VersionedContentStoreShim``.
    """

    def make_store(self):
        for course_key in (COURSE_KEY, OTHER_COURSE_KEY):
            catalog_api.create_course_run_for_modulestore_course_with(course_key, title=f"Course {course_key.run}")
        return VersionedContentStoreShim()
