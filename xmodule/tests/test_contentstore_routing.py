"""
Tests for the RoutingContentStore
"""

from unittest import mock

import ddt
import pytest
from django.test import SimpleTestCase, override_settings
from opaque_keys.edx.keys import CourseKey

from xmodule.contentstore import django as contentstore_django
from xmodule.contentstore.content import ContentStore, ContentStoreReadOnlyError, StaticContent
from xmodule.contentstore.routing import RoutingContentStore

COURSE_A = CourseKey.from_string("course-v1:OpenedX+Routing+A")
COURSE_B = CourseKey.from_string("course-v1:OpenedX+Routing+B")
ASSET_A = COURSE_A.make_asset_key("asset", "a.png")
ASSET_B = COURSE_B.make_asset_key("asset", "b.png")
CONTENT_A = StaticContent(ASSET_A, "a.png", "image/png", b"a")
CONTENT_B = StaticContent(ASSET_B, "b.png", "image/png", b"b")


class FakeContentStore(ContentStore):
    """A ContentStore that does nothing, for testing the contentstore() factory."""

    def __init__(self, **kwargs):
        super().__init__()
        self.options = kwargs


def mock_store():
    return mock.Mock(spec=ContentStore)


# (method name, args, kwargs, the args and kwargs we expect the backend method to receive)
DELEGATED_CALLS = [
    ("save", (CONTENT_A,), {}, (CONTENT_A,), {}),
    ("find", (ASSET_A,), {}, (ASSET_A,), {"throw_on_not_found": True, "as_stream": False}),
    (
        "find",
        (ASSET_A,),
        {"throw_on_not_found": False, "as_stream": True},
        (ASSET_A,),
        {"throw_on_not_found": False, "as_stream": True},
    ),
    ("delete", (ASSET_A,), {}, (ASSET_A,), {}),
    ("get_attrs", (ASSET_A,), {}, (ASSET_A,), {}),
    ("set_attrs", (ASSET_A, {"locked": True}), {}, (ASSET_A, {"locked": True}), {}),
    ("get_attr", (ASSET_A, "locked", False), {}, (ASSET_A, "locked", False), {}),
    ("set_attr", (ASSET_A, "locked", False), {}, (ASSET_A, "locked", False), {}),
    ("export", (ASSET_A, "/tmp/out"), {}, (ASSET_A, "/tmp/out"), {}),
    (
        "generate_thumbnail",
        (CONTENT_A,),
        {"dimensions": (10, 10)},
        (CONTENT_A,),
        {"tempfile_path": None, "dimensions": (10, 10)},
    ),
    (
        "get_all_content_for_course",
        (COURSE_A,),
        {"start": 10, "maxresults": 5, "sort": [("displayname", 1)]},
        (COURSE_A,),
        {"start": 10, "maxresults": 5, "sort": [("displayname", 1)], "filter_params": None},
    ),
    ("get_all_content_thumbnails_for_course", (COURSE_A,), {}, (COURSE_A,), {}),
    ("export_all_for_course", (COURSE_A, "/tmp/out", "/tmp/a.json"), {}, (COURSE_A, "/tmp/out", "/tmp/a.json"), {}),
    ("delete_all_course_assets", (COURSE_A,), {}, (COURSE_A,), {}),
    ("copy_all_course_assets", (COURSE_A, COURSE_B), {}, (COURSE_A, COURSE_B), {}),
]
RETURNS_NONE = {
    "delete",
    "set_attrs",
    "set_attr",
    "export",
    "export_all_for_course",
    "delete_all_course_assets",
    "copy_all_course_assets",
}


class RouterTestCase(SimpleTestCase):
    """
    Base class for tests of a RoutingContentStore with mock backends.

    ``self.statuses`` holds the ``CourseAssetBackend`` status of each course;
    courses that aren't in it are "legacy".
    """

    def setUp(self):
        super().setUp()
        self.legacy = mock_store()
        self.versioned = mock_store()
        self.router = RoutingContentStore(legacy_store=self.legacy, versioned_store=self.versioned)
        self.statuses: dict[CourseKey, str] = {}
        patcher = mock.patch.object(
            RoutingContentStore, "_get_backend_status", side_effect=lambda key: self.statuses.get(key, "legacy")
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            "openedx.core.djangoapps.contentstore_shim.api.inherit_course_asset_backend_status", autospec=True
        )
        self.mock_inherit = patcher.start()
        self.addCleanup(patcher.stop)


@ddt.ddt
class TestRoutingContentStore(RouterTestCase):
    """
    Tests for RoutingContentStore with only legacy courses.
    """

    def test_stores(self):
        assert self.router.legacy_store is self.legacy
        assert self.router.versioned_store is self.versioned

    def test_default_versioned_store(self):
        # pylint: disable=import-outside-toplevel
        from openedx.core.djangoapps.contentstore_shim.store import VersionedContentStoreShim

        router = RoutingContentStore(legacy_store=self.legacy)
        assert isinstance(router.versioned_store, VersionedContentStoreShim)
        # It's only created once:
        assert router.versioned_store is router.versioned_store

    @ddt.data(*DELEGATED_CALLS)
    @ddt.unpack
    def test_delegates_to_legacy(self, method_name, args, kwargs, expected_args, expected_kwargs):
        """Each method is passed through to the legacy store, and returns its result (if any)."""
        result = getattr(self.router, method_name)(*args, **kwargs)
        backend_method = getattr(self.legacy, method_name)
        backend_method.assert_called_once_with(*expected_args, **expected_kwargs)
        if method_name in RETURNS_NONE:
            assert result is None
        else:
            assert result is backend_method.return_value
        assert not self.versioned.method_calls

    def test_delete_by_legacy_id(self):
        """Deleting by a MongoContentStore internal ID (not an AssetKey) goes to the legacy store."""
        self.router.delete("some-mongo-id")
        self.legacy.delete.assert_called_once_with("some-mongo-id")

    def test_global_methods_use_all_backends(self):
        self.legacy.remove_redundant_content_for_courses.return_value = 3
        self.versioned.remove_redundant_content_for_courses.return_value = 2
        assert self.router.remove_redundant_content_for_courses() == 5
        for store in (self.legacy, self.versioned):
            self.router.ensure_indexes()
            store.ensure_indexes.assert_called_with()
            self.router.close_connections()
            store.close_connections.assert_called_with()
            self.router._drop_database(database=False)  # pylint: disable=protected-access
            store._drop_database.assert_called_with(False, True, True)  # pylint: disable=protected-access


@ddt.ddt
class TestRoutingBetweenBackends(RouterTestCase):
    """
    Tests that each request goes to the backend for the course's status.
    """

    def setUp(self):
        super().setUp()
        self.statuses[COURSE_B] = "migrated"

    @ddt.data(
        ("save", (CONTENT_B,)),
        ("find", (ASSET_B,)),
        ("delete", (ASSET_B,)),
        ("get_attrs", (ASSET_B,)),
        ("set_attr", (ASSET_B, "locked", True)),
        ("export", (ASSET_B, "/tmp/out")),
        ("generate_thumbnail", (CONTENT_B,)),
        ("get_all_content_for_course", (COURSE_B,)),
        ("get_all_content_thumbnails_for_course", (COURSE_B,)),
        ("export_all_for_course", (COURSE_B, "/tmp/out", "/tmp/a.json")),
        ("delete_all_course_assets", (COURSE_B,)),
    )
    @ddt.unpack
    def test_migrated_course_uses_versioned_store(self, method_name, args):
        """Requests for the migrated COURSE_B go to the versioned store, and nothing goes to the legacy store."""
        getattr(self.router, method_name)(*args)
        getattr(self.versioned, method_name).assert_called_once()
        assert not self.legacy.method_calls

    def test_legacy_course_uses_legacy_store(self):
        """Requests for COURSE_A still go to the legacy store."""
        self.router.find(ASSET_A)
        self.legacy.find.assert_called_once()
        assert not self.versioned.method_calls

    def test_copy_within_backend(self):
        self.statuses[COURSE_A] = "migrated"
        self.router.copy_all_course_assets(COURSE_A, COURSE_B)
        self.versioned.copy_all_course_assets.assert_called_once_with(COURSE_A, COURSE_B)
        assert not self.legacy.method_calls

    @ddt.data(
        # (source course, dest course): legacy -> migrated, and migrated -> legacy
        (COURSE_A, COURSE_B),
        (COURSE_B, COURSE_A),
    )
    @ddt.unpack
    def test_copy_between_backends(self, source_course, dest_course):
        """
        Copying between courses in different backends copies each file with the
        generic ContentStore API.
        """
        source_store, dest_store = (
            (self.legacy, self.versioned) if source_course == COURSE_A else (self.versioned, self.legacy)
        )
        source_asset = source_course.make_asset_key("asset", "images_photo.png")
        source_content = StaticContent(
            source_asset,
            "photo.png",
            "image/png",
            b"photo",
            import_path="images/photo.png",
            locked=True,
            thumbnail_location=source_course.make_asset_key("thumbnail", "images_photo.jpg"),
        )
        source_store.get_all_content_for_course.return_value = ([{"asset_key": source_asset}], 1)
        source_store.find.return_value = source_content

        self.router.copy_all_course_assets(source_course, dest_course)

        source_store.find.assert_called_once_with(source_asset)
        dest_store.save.assert_called_once()
        saved = dest_store.save.call_args.args[0]
        assert saved.location == dest_course.make_asset_key("asset", "images_photo.png")
        assert (saved.name, saved.content_type, saved.data) == ("photo.png", "image/png", b"photo")
        assert (saved.import_path, saved.locked) == ("images/photo.png", True)
        assert saved.thumbnail_location is None
        assert not source_store.copy_all_course_assets.called
        assert not dest_store.copy_all_course_assets.called

    def test_copy_inherits_status_first(self):
        """
        Copying a course first lets the destination inherit the source's status,
        and then copies the files to the destination's (new) backend.
        """
        dest_course = CourseKey.from_string("course-v1:OpenedX+Routing+B_rerun")

        def inherit(source_course_key, dest_course_key):
            assert (source_course_key, dest_course_key) == (COURSE_B, dest_course)
            self.statuses[dest_course] = self.statuses[source_course_key]

        self.mock_inherit.side_effect = inherit
        self.router.copy_all_course_assets(COURSE_B, dest_course)
        self.mock_inherit.assert_called_once_with(COURSE_B, dest_course)
        self.versioned.copy_all_course_assets.assert_called_once_with(COURSE_B, dest_course)
        assert not self.legacy.method_calls


@ddt.ddt
class TestRoutingInProgress(RouterTestCase):
    """
    Tests for courses whose files are being migrated: they're read from the
    legacy store, and can't be changed.
    """

    def setUp(self):
        super().setUp()
        self.statuses[COURSE_A] = "in_progress"

    @ddt.data(
        ("find", (ASSET_A,)),
        ("get_attrs", (ASSET_A,)),
        ("get_attr", (ASSET_A, "locked")),
        ("export", (ASSET_A, "/tmp/out")),
        ("get_all_content_for_course", (COURSE_A,)),
        ("get_all_content_thumbnails_for_course", (COURSE_A,)),
        ("export_all_for_course", (COURSE_A, "/tmp/out", "/tmp/a.json")),
    )
    @ddt.unpack
    def test_reads_use_legacy_store(self, method_name, args):
        getattr(self.router, method_name)(*args)
        getattr(self.legacy, method_name).assert_called_once()
        assert not self.versioned.method_calls

    @ddt.data(
        ("save", (CONTENT_A,)),
        ("delete", (ASSET_A,)),
        ("set_attr", (ASSET_A, "locked", True)),
        ("set_attrs", (ASSET_A, {"locked": True})),
        ("generate_thumbnail", (CONTENT_A,)),
        ("delete_all_course_assets", (COURSE_A,)),
        # Copying files into the course:
        ("copy_all_course_assets", (COURSE_B, COURSE_A)),
    )
    @ddt.unpack
    def test_writes_are_blocked(self, method_name, args):
        with pytest.raises(ContentStoreReadOnlyError, match="being migrated"):
            getattr(self.router, method_name)(*args)
        assert not self.legacy.method_calls
        assert not self.versioned.method_calls

    def test_copy_from_in_progress_course(self):
        """Files can still be copied out of a course that's being migrated."""
        self.router.copy_all_course_assets(COURSE_A, COURSE_B)
        self.legacy.copy_all_course_assets.assert_called_once_with(COURSE_A, COURSE_B)


@override_settings(CONTENTSTORE={
    "ENGINE": f"{__name__}.FakeContentStore",
    "DOC_STORE_CONFIG": {"db": "test"},
    "ADDITIONAL_OPTIONS": {"trashcan": {"bucket": "trash_fs"}},
})
@mock.patch.dict(contentstore_django._CONTENTSTORE, clear=True)  # pylint: disable=protected-access
class TestContentstoreFactory(SimpleTestCase):
    """
    Tests for the contentstore() function.
    """

    def test_default_is_router(self):
        store = contentstore_django.contentstore()
        assert isinstance(store, RoutingContentStore)
        assert isinstance(store.legacy_store, FakeContentStore)
        assert store.legacy_store.options == {"db": "test"}
        # It's cached:
        assert contentstore_django.contentstore() is store

    def test_trashcan_is_legacy(self):
        store = contentstore_django.contentstore("trashcan")
        assert isinstance(store, FakeContentStore)
        assert store.options == {"db": "test", "bucket": "trash_fs"}
