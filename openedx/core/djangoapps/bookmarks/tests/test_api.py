"""
Tests for bookmarks api.
"""
from unittest.mock import Mock, patch

import ddt
import pytest
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from opaque_keys.edx.keys import UsageKey

from openedx.core.djangoapps.bookmarks.api import BookmarksLimitReachedError
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order

from .. import api
from ..models import Bookmark
from .test_models import BookmarksTestsBase


class BookmarkApiEventTestMixin:
    """ Mixin for verifying that bookmark api events were emitted during a test. """

    def assert_bookmark_event_emitted(self, mock_tracker, event_name, **kwargs):
        """ Assert that an event has been emitted. """
        mock_tracker.assert_any_call(
            event_name,
            kwargs,
        )

    def assert_no_events_were_emitted(self, mock_tracker):
        """
        Assert no events were emitted.
        """
        assert not mock_tracker.called


@ddt.ddt
@skip_unless_lms
class BookmarksAPITests(BookmarkApiEventTestMixin, BookmarksTestsBase):
    """
    These tests cover the parts of the API methods.
    """

    def test_get_bookmark(self):
        """
        Verifies that get_bookmark returns data as expected.
        """
        bookmark_data = api.get_bookmark(user=self.user, usage_key=self.sequential_1.location)
        self.assert_bookmark_data_is_valid(self.bookmark_1, bookmark_data)

        # With Optional fields.
        with self.assertNumQueries(1):
            bookmark_data = api.get_bookmark(
                user=self.user,
                usage_key=self.sequential_1.location,
                fields=self.ALL_FIELDS
            )
        self.assert_bookmark_data_is_valid(self.bookmark_1, bookmark_data, check_optional_fields=True)

    def test_get_bookmark_raises_error(self):
        """
        Verifies that get_bookmark raises error as expected.
        """
        with self.assertNumQueries(1):
            with pytest.raises(ObjectDoesNotExist):
                api.get_bookmark(user=self.other_user, usage_key=self.vertical_1.location)

    @ddt.data(
        1, 10, 20
    )
    def test_get_bookmarks(self, count):
        """
        Verifies that get_bookmarks returns data as expected.
        """
        course, __, bookmarks = self.create_course_with_bookmarks_count(count)

        # Without course key.
        with self.assertNumQueries(1):
            bookmarks_data = api.get_bookmarks(user=self.user)
            assert len(bookmarks_data) == (count + 5)
        # Assert them in ordered manner.
        self.assert_bookmark_data_is_valid(bookmarks[-1], bookmarks_data[0])
        self.assert_bookmark_data_is_valid(self.bookmark_1, bookmarks_data[-1])
        self.assert_bookmark_data_is_valid(self.bookmark_2, bookmarks_data[-2])

        # Without course key, with optional fields.
        with self.assertNumQueries(1):
            bookmarks_data = api.get_bookmarks(user=self.user, fields=self.ALL_FIELDS)
            assert len(bookmarks_data) == (count + 5)
        self.assert_bookmark_data_is_valid(bookmarks[-1], bookmarks_data[0])
        self.assert_bookmark_data_is_valid(self.bookmark_1, bookmarks_data[-1])

        # With course key.
        with self.assertNumQueries(1):
            bookmarks_data = api.get_bookmarks(user=self.user, course_key=course.id)
            assert len(bookmarks_data) == count
        self.assert_bookmark_data_is_valid(bookmarks[-1], bookmarks_data[0])
        self.assert_bookmark_data_is_valid(bookmarks[0], bookmarks_data[-1])

        # With course key, with optional fields.
        with self.assertNumQueries(1):
            bookmarks_data = api.get_bookmarks(user=self.user, course_key=course.id, fields=self.ALL_FIELDS)
            assert len(bookmarks_data) == count
        self.assert_bookmark_data_is_valid(bookmarks[-1], bookmarks_data[0])
        self.assert_bookmark_data_is_valid(bookmarks[0], bookmarks_data[-1])

        # Without Serialized.
        with self.assertNumQueries(1):
            bookmarks = api.get_bookmarks(user=self.user, course_key=course.id, serialized=False)
            assert len(bookmarks) == count
        assert bookmarks.model is Bookmark

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_create_bookmark(self, mock_tracker):
        """
        Verifies that create_bookmark create & returns data as expected.
        """
        assert len(api.get_bookmarks(user=self.user, course_key=self.course.id)) == 4

        with self.assertNumQueries(10):
            bookmark_data = api.create_bookmark(user=self.user, usage_key=self.vertical_2.location)

        self.assert_bookmark_event_emitted(
            mock_tracker,
            event_name='edx.bookmark.added',
            course_id=str(self.course_id),
            bookmark_id=bookmark_data['id'],
            component_type=self.vertical_2.location.block_type,
            component_usage_id=str(self.vertical_2.location),
        )

        assert len(api.get_bookmarks(user=self.user, course_key=self.course.id)) == 5

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_create_bookmark_do_not_create_duplicates(self, mock_tracker):
        """
        Verifies that create_bookmark do not create duplicate bookmarks.
        """
        assert len(api.get_bookmarks(user=self.user, course_key=self.course.id)) == 4

        with self.assertNumQueries(10):
            bookmark_data = api.create_bookmark(user=self.user, usage_key=self.vertical_2.location)

        self.assert_bookmark_event_emitted(
            mock_tracker,
            event_name='edx.bookmark.added',
            course_id=str(self.course_id),
            bookmark_id=bookmark_data['id'],
            component_type=self.vertical_2.location.block_type,
            component_usage_id=str(self.vertical_2.location),
        )

        assert len(api.get_bookmarks(user=self.user, course_key=self.course.id)) == 5

        mock_tracker.reset_mock()

        with self.assertNumQueries(5):
            bookmark_data_2 = api.create_bookmark(user=self.user, usage_key=self.vertical_2.location)

        assert len(api.get_bookmarks(user=self.user, course_key=self.course.id)) == 5
        assert bookmark_data == bookmark_data_2

        self.assert_no_events_were_emitted(mock_tracker)

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_create_bookmark_raises_error(self, mock_tracker):
        """
        Verifies that create_bookmark raises error as expected.
        """
        with self.assertNumQueries(0):
            with pytest.raises(ItemNotFoundError):
                api.create_bookmark(user=self.user, usage_key=UsageKey.from_string('i4x://brb/100/html/340ef1771a0940'))

        self.assert_no_events_were_emitted(mock_tracker)

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    @patch('django.conf.settings.MAX_BOOKMARKS_PER_COURSE', 5)
    def bookmark_more_than_limit_raise_error(self, mock_tracker):
        """
        Verifies that create_bookmark raises error when maximum number of units
        allowed to bookmark per course are already bookmarked.
        """

        max_bookmarks = settings.MAX_BOOKMARKS_PER_COURSE
        __, blocks, __ = self.create_course_with_bookmarks_count(max_bookmarks)
        with self.assertNumQueries(1):
            with pytest.raises(BookmarksLimitReachedError):
                api.create_bookmark(user=self.user, usage_key=blocks[-1].location)

        self.assert_no_events_were_emitted(mock_tracker)

        # if user tries to create bookmark in another course it should succeed
        assert len(api.get_bookmarks(user=self.user, course_key=self.other_course.id)) == 1
        api.create_bookmark(user=self.user, usage_key=self.other_chapter_1.location)
        assert len(api.get_bookmarks(user=self.user, course_key=self.other_course.id)) == 2

        # if another user tries to create bookmark it should succeed
        assert len(api.get_bookmarks(user=self.other_user, course_key=blocks[(- 1)].location.course_key)) == 0
        api.create_bookmark(user=self.other_user, usage_key=blocks[-1].location)
        assert len(api.get_bookmarks(user=self.other_user, course_key=blocks[(- 1)].location.course_key)) == 1

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_delete_bookmark(self, mock_tracker):
        """
        Verifies that delete_bookmark removes bookmark as expected.
        """
        assert len(api.get_bookmarks(user=self.user)) == 5

        with self.assertNumQueries(3):
            api.delete_bookmark(user=self.user, usage_key=self.sequential_1.location)

        self.assert_bookmark_event_emitted(
            mock_tracker,
            event_name='edx.bookmark.removed',
            course_id=str(self.course_id),
            bookmark_id=self.bookmark_1.resource_id,
            component_type=self.sequential_1.location.block_type,
            component_usage_id=str(self.sequential_1.location),
        )

        bookmarks_data = api.get_bookmarks(user=self.user)
        assert len(bookmarks_data) == 4
        assert str(self.sequential_1.location) != bookmarks_data[0]['usage_id']
        assert str(self.sequential_1.location) != bookmarks_data[1]['usage_id']

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_delete_bookmarks_with_vertical_block_type(self, mock_tracker):
        assert len(api.get_bookmarks(user=self.user)) == 5

        api.delete_bookmarks(usage_key=self.vertical_3.location)

        self.assert_bookmark_event_emitted(
            mock_tracker,
            event_name='edx.bookmark.removed',
            course_id=str(self.course_id),
            bookmark_id=self.bookmark_3.resource_id,
            component_type=self.vertical_1.location.block_type,
            component_usage_id=str(self.vertical_3.location),
        )

        assert len(api.get_bookmarks(self.user)) == 4

    @patch('openedx.core.djangoapps.bookmarks.api_impl.modulestore')
    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_delete_bookmarks_with_chapter_block_type(self, mock_tracker, mocked_modulestore):
        mocked_modulestore().get_item().get_children = Mock(
            return_value=[Mock(get_children=Mock(return_value=[Mock(
                location=self.chapter_2.location)]))])

        api.delete_bookmarks(usage_key=self.chapter_2.location)

        assert mocked_modulestore.call_count == 2
        assert mocked_modulestore().get_item.call_count == 2
        mocked_modulestore().get_item().get_children.assert_called()
        self.assert_bookmark_event_emitted(
            mock_tracker,
            event_name='edx.bookmark.removed',
            course_id=str(self.course_id),
            bookmark_id=self.bookmark_4.resource_id,
            component_type=self.chapter_2.location.block_type,
            component_usage_id=str(self.chapter_2.location),
        )
        assert len(api.get_bookmarks(self.user)) == 4

    @patch('openedx.core.djangoapps.bookmarks.api_impl.tracker.emit')
    def test_delete_bookmark_raises_error(self, mock_tracker):
        """
        Verifies that delete_bookmark raises error as expected.
        """
        with self.assertNumQueries(1):
            with pytest.raises(ObjectDoesNotExist):
                api.delete_bookmark(user=self.other_user, usage_key=self.vertical_1.location)

        self.assert_no_events_were_emitted(mock_tracker)
