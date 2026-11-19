"""
Tests for the RoutingContentStore
"""

from unittest import mock

import ddt
import pytest
from django.test import SimpleTestCase, override_settings
from opaque_keys.edx.keys import CourseKey

from xmodule.contentstore import django as contentstore_django
from xmodule.contentstore.content import ContentStore, StaticContent
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


class TwoBackendRoutingContentStore(RoutingContentStore):
    """
    A router with a second backend, which stores the files for ``COURSE_B``.
    """

    def __init__(self, legacy_store, other_store):
        super().__init__(legacy_store)
        self.other_store = other_store

    @property
    def _all_stores(self):
        return [self.legacy_store, self.other_store]

    def _store_for_course(self, course_key):
        return self.other_store if course_key == COURSE_B else self.legacy_store


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


@ddt.ddt
class TestRoutingContentStore(SimpleTestCase):
    """
    Tests for RoutingContentStore with only the legacy backend.
    """

    def setUp(self):
        super().setUp()
        self.legacy = mock_store()
        self.router = RoutingContentStore(legacy_store=self.legacy)

    def test_legacy_store(self):
        assert self.router.legacy_store is self.legacy

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

    def test_delete_by_legacy_id(self):
        """Deleting by a MongoContentStore internal ID (not an AssetKey) goes to the legacy store."""
        self.router.delete("some-mongo-id")
        self.legacy.delete.assert_called_once_with("some-mongo-id")

    def test_global_methods(self):
        self.legacy.remove_redundant_content_for_courses.return_value = 3
        assert self.router.remove_redundant_content_for_courses() == 3
        self.router.ensure_indexes()
        self.legacy.ensure_indexes.assert_called_once_with()
        self.router.close_connections()
        self.legacy.close_connections.assert_called_once_with()
        self.router._drop_database(database=False)  # pylint: disable=protected-access
        self.legacy._drop_database.assert_called_once_with(False, True, True)  # pylint: disable=protected-access


@ddt.ddt
class TestRoutingBetweenBackends(SimpleTestCase):
    """
    Tests that a router with more than one backend sends each request to the right one.
    """

    def setUp(self):
        super().setUp()
        self.legacy = mock_store()
        self.other = mock_store()
        self.router = TwoBackendRoutingContentStore(self.legacy, self.other)

    @ddt.data(
        ("save", (CONTENT_B,)),
        ("find", (ASSET_B,)),
        ("delete", (ASSET_B,)),
        ("set_attr", (ASSET_B, "locked", True)),
        ("generate_thumbnail", (CONTENT_B,)),
        ("get_all_content_for_course", (COURSE_B,)),
        ("export_all_for_course", (COURSE_B, "/tmp/out", "/tmp/a.json")),
        ("delete_all_course_assets", (COURSE_B,)),
    )
    @ddt.unpack
    def test_routes_by_course(self, method_name, args):
        """Requests for COURSE_B go to the other store, and nothing goes to the legacy store."""
        getattr(self.router, method_name)(*args)
        getattr(self.other, method_name).assert_called_once()
        assert not self.legacy.method_calls

    def test_routes_to_legacy(self):
        """Requests for COURSE_A still go to the legacy store."""
        self.router.find(ASSET_A)
        self.legacy.find.assert_called_once()
        assert not self.other.method_calls

    def test_copy_within_backend(self):
        self.router.copy_all_course_assets(COURSE_B, COURSE_B)
        self.other.copy_all_course_assets.assert_called_once_with(COURSE_B, COURSE_B)

    def test_copy_between_backends(self):
        with pytest.raises(NotImplementedError):
            self.router.copy_all_course_assets(COURSE_A, COURSE_B)

    def test_global_methods_use_all_backends(self):
        self.legacy.remove_redundant_content_for_courses.return_value = 3
        self.other.remove_redundant_content_for_courses.return_value = 2
        assert self.router.remove_redundant_content_for_courses() == 5
        self.router.ensure_indexes()
        self.legacy.ensure_indexes.assert_called_once_with()
        self.other.ensure_indexes.assert_called_once_with()


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
