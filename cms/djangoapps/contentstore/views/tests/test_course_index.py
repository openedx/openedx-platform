"""
Unit tests for getting the list of courses and the course outline.
"""


import datetime
import json
from unittest import mock, skip

import ddt
import pytz
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext as _
from search.api import perform_search

from cms.djangoapps.contentstore.courseware_index import CoursewareSearchIndexer, SearchIndexingError
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import (
    reverse_course_url,
    reverse_usage_url
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE
from xmodule.modulestore.tests.factories import BlockFactory, check_mongo_calls  # lint-amnesty, pylint: disable=wrong-import-order

from ..course import _deprecated_blocks_info, course_outline_initial_state, reindex_course_and_check_access
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import VisibilityState, create_xblock_info


@ddt.ddt
class TestCourseOutline(CourseTestCase):
    """
    Unit tests for the course outline.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    ENABLED_SIGNALS = ['course_published']

    def setUp(self):
        """
        Set up the for the course outline tests.
        """
        super().setUp()

        self.chapter = BlockFactory.create(
            parent_location=self.course.location, category='chapter', display_name="Week 1"
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location, category='sequential', display_name="Lesson 1"
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location, category='vertical', display_name='Subsection 1'
        )
        self.video = BlockFactory.create(
            parent_location=self.vertical.location, category="video", display_name="My Video"
        )

    @ddt.data(True, False)
    def test_json_responses(self, is_concise):
        """
        Verify the JSON responses returned for the course.

        Arguments:
            is_concise (Boolean) : If True, fetch concise version of course outline.
        """
        outline_url = reverse_course_url('course_handler', self.course.id)
        outline_url = outline_url + '?format=concise' if is_concise else outline_url
        resp = self.client.get(outline_url, HTTP_ACCEPT='application/json')
        if self.course.id.deprecated:
            self.assertEqual(resp.status_code, 404)
            return

        json_response = json.loads(resp.content.decode('utf-8'))

        # First spot check some values in the root response
        self.assertEqual(json_response['category'], 'course')
        self.assertEqual(json_response['id'], str(self.course.location))
        self.assertEqual(json_response['display_name'], self.course.display_name)
        self.assertNotEqual(json_response.get('published', False), is_concise)
        self.assertIsNone(json_response.get('visibility_state'))

        # Now verify the first child
        children = json_response['child_info']['children']
        self.assertGreater(len(children), 0)
        first_child_response = children[0]
        self.assertEqual(first_child_response['category'], 'chapter')
        self.assertEqual(first_child_response['id'], str(self.chapter.location))
        self.assertEqual(first_child_response['display_name'], 'Week 1')
        self.assertNotEqual(json_response.get('published', False), is_concise)
        if not is_concise:
            self.assertEqual(first_child_response['visibility_state'], VisibilityState.unscheduled)
        self.assertGreater(len(first_child_response['child_info']['children']), 0)

        # Finally, validate the entire response for consistency
        self.assert_correct_json_response(json_response, is_concise)

    def assert_correct_json_response(self, json_response, is_concise=False):
        """
        Asserts that the JSON response is syntactically consistent
        """
        self.assertIsNotNone(json_response['display_name'])
        self.assertIsNotNone(json_response['id'])
        self.assertIsNotNone(json_response['category'])
        self.assertNotEqual(json_response.get('published', False), is_concise)
        if json_response.get('child_info', None):
            for child_response in json_response['child_info']['children']:
                self.assert_correct_json_response(child_response, is_concise)

    def test_course_outline_initial_state(self):
        course_block = modulestore().get_item(self.course.location)
        course_structure = create_xblock_info(
            course_block,
            include_child_info=True,
            include_children_predicate=lambda xblock: not xblock.category == 'vertical'
        )

        # Verify that None is returned for a non-existent locator
        self.assertIsNone(course_outline_initial_state('no-such-locator', course_structure))

        # Verify that the correct initial state is returned for the test chapter
        chapter_locator = str(self.chapter.location)
        initial_state = course_outline_initial_state(chapter_locator, course_structure)
        self.assertEqual(initial_state['locator_to_show'], chapter_locator)
        expanded_locators = initial_state['expanded_locators']
        self.assertIn(str(self.sequential.location), expanded_locators)
        self.assertIn(str(self.vertical.location), expanded_locators)

    def _create_test_data(self, course_block, create_blocks=False, publish=True, block_types=None):
        """
        Create data for test.
        """
        if create_blocks:
            for block_type in block_types:
                BlockFactory.create(
                    parent=self.vertical,
                    category=block_type,
                    display_name=f'{block_type} Problem'
                )

            if not publish:
                self.store.unpublish(self.vertical.location, self.user.id)

        # get updated vertical
        self.vertical = modulestore().get_item(self.vertical.location)
        course_block.advanced_modules.extend(block_types)

    def _verify_deprecated_info(self, course_id, advanced_modules, info, deprecated_block_types):
        """
        Verify deprecated info.
        """
        expected_blocks = []
        for block_type in deprecated_block_types:
            expected_blocks.append(
                [
                    reverse_usage_url('container_handler', self.vertical.location),
                    f'{block_type} Problem'
                ]
            )

        self.assertEqual(
            info['deprecated_enabled_block_types'],
            [component for component in advanced_modules if component in deprecated_block_types]
        )

        self.assertCountEqual(info['blocks'], expected_blocks)
        self.assertEqual(
            info['advance_settings_url'],
            reverse_course_url('advanced_settings_handler', course_id)
        )

    @skip('OldMongo Deprecation. HiddenDescriptorWithMixins is not created for split.')
    @ddt.data(
        [{'publish': True}, ['notes']],
        [{'publish': False}, ['notes']],
        [{'publish': True}, ['notes', 'lti']]
    )
    @ddt.unpack
    def test_verify_deprecated_warning_message(self, publish, block_types):
        """
        Verify deprecated warning info.
        """
        course_block = modulestore().get_item(self.course.location)
        self._create_test_data(course_block, create_blocks=True, block_types=block_types, publish=publish)
        # get updated course_block
        course_block = modulestore().get_item(self.course.location)

        info = _deprecated_blocks_info(course_block, block_types)
        self._verify_deprecated_info(
            course_block.id,
            course_block.advanced_modules,
            info,
            block_types
        )

    @skip('OldMongo Deprecation. HiddenDescriptorWithMixins is not created for split.')
    @ddt.data(
        (["a", "b", "c"], ["a", "b", "c"]),
        (["a", "b", "c"], ["a", "b", "d"]),
        (["a", "b", "c"], ["a", "d", "e"]),
        (["a", "b", "c"], ["d", "e", "f"])
    )
    @ddt.unpack
    def test_verify_warn_only_on_enabled_blocks(self, enabled_block_types, deprecated_block_types):
        """
        Verify that we only warn about block_types that are both deprecated and enabled.
        """
        expected_block_types = list(set(enabled_block_types) & set(deprecated_block_types))
        course_block = modulestore().get_item(self.course.location)
        self._create_test_data(course_block, create_blocks=True, block_types=enabled_block_types)
        # get updated course_module
        course_block = modulestore().get_item(self.course.location)
        info = _deprecated_blocks_info(course_block, deprecated_block_types)
        self._verify_deprecated_info(
            course_block.id,
            course_block.advanced_modules,
            info,
            expected_block_types
        )

    def test_number_of_calls_to_db(self):
        """
        Test to check number of queries made to mysql and mongo
        """
        with self.assertNumQueries(21, table_ignorelist=WAFFLE_TABLES):
            with check_mongo_calls(3):
                self.client.get(reverse_course_url('course_handler', self.course.id), content_type="application/json")


class TestCourseReIndex(CourseTestCase):
    """
    Unit tests for the course outline.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    SUCCESSFUL_RESPONSE = _("Course has been successfully reindexed.")

    ENABLED_SIGNALS = ['course_published']

    @mock.patch('cms.djangoapps.contentstore.signals.handlers.transaction.on_commit',
                new=mock.Mock(side_effect=lambda func: func()), )  # run index right away
    def setUp(self):
        """
        Set up the for the course reindex tests.
        """

        super().setUp()

        self.course.start = datetime.datetime(2014, 1, 1, tzinfo=pytz.utc)
        modulestore().update_item(self.course, self.user.id)

        self.chapter = BlockFactory.create(
            parent_location=self.course.location, category='chapter', display_name="Week 1"
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location, category='sequential', display_name="Lesson 1"
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location, category='vertical', display_name='Subsection 1'
        )
        self.video = BlockFactory.create(
            parent_location=self.vertical.location, category="video", display_name="My Video"
        )

        self.html = BlockFactory.create(
            parent_location=self.vertical.location, category="html", display_name="My HTML",
            data="<div>This is my unique HTML content</div>",

        )

    def test_reindex_course(self):
        """
        Verify that course gets reindexed.
        """
        index_url = reverse_course_url('course_search_index_handler', self.course.id)
        response = self.client.get(index_url, {}, HTTP_ACCEPT='application/json')

        # A course with the default release date should display as "Unscheduled"
        self.assertContains(response, self.SUCCESSFUL_RESPONSE)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(index_url, {}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.content, b'')
        self.assertEqual(response.status_code, 405)

        self.client.logout()
        response = self.client.get(index_url, {}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 302)

    def test_negative_conditions(self):
        """
        Test the error conditions for the access
        """
        index_url = reverse_course_url('course_search_index_handler', self.course.id)
        # register a non-staff member and try to delete the course branch
        non_staff_client, _ = self.create_non_staff_authed_user_client()
        response = non_staff_client.get(index_url, {}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)

    def test_empty_content_type(self):
        """
        Test json content type is set if '' is selected
        """
        index_url = reverse_course_url('course_search_index_handler', self.course.id)
        response = self.client.get(index_url, {}, CONTENT_TYPE='')

        # A course with the default release date should display as "Unscheduled"
        self.assertContains(response, self.SUCCESSFUL_RESPONSE)
        self.assertEqual(response.status_code, 200)

    @mock.patch('xmodule.html_block.HtmlBlock.index_dictionary')
    def test_reindex_course_search_index_error(self, mock_index_dictionary):
        """
        Test json response with mocked error data for html
        """

        # set mocked exception response
        err = SearchIndexingError
        mock_index_dictionary.return_value = err

        index_url = reverse_course_url('course_search_index_handler', self.course.id)

        # Start manual reindex and check error in response
        response = self.client.get(index_url, {}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 500)

    def test_reindex_json_responses(self):
        """
        Test json response with real data
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # Start manual reindex
        reindex_course_and_check_access(self.course.id, self.user)

        # Check results remain the same
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

    @mock.patch('xmodule.video_block.VideoBlock.index_dictionary')
    def test_reindex_video_error_json_responses(self, mock_index_dictionary):
        """
        Test json response with mocked error data for video
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = SearchIndexingError
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            reindex_course_and_check_access(self.course.id, self.user)

    @mock.patch('xmodule.html_block.HtmlBlock.index_dictionary')
    def test_reindex_html_error_json_responses(self, mock_index_dictionary):
        """
        Test json response with mocked error data for html
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = SearchIndexingError
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            reindex_course_and_check_access(self.course.id, self.user)

    @mock.patch('xmodule.seq_block.SequenceBlock.index_dictionary')
    def test_reindex_seq_error_json_responses(self, mock_index_dictionary):
        """
        Test json response with mocked error data for sequence
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = Exception
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            reindex_course_and_check_access(self.course.id, self.user)

    @mock.patch('xmodule.modulestore.split_mongo.split.SplitMongoModuleStore.get_course')
    def test_reindex_no_item(self, mock_get_course):
        """
        Test system logs an error if no item found.
        """
        # set mocked exception response
        err = ItemNotFoundError
        mock_get_course.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            reindex_course_and_check_access(self.course.id, self.user)

    def test_reindex_no_permissions(self):
        # register a non-staff member and try to delete the course branch
        user2 = UserFactory()
        with self.assertRaises(PermissionDenied):
            reindex_course_and_check_access(self.course.id, user2)

    def test_indexing_responses(self):
        """
        Test do_course_reindex response with real data
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # Start manual reindex
        CoursewareSearchIndexer.do_course_reindex(modulestore(), self.course.id)

        # Check results are the same following reindex
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

    @mock.patch('xmodule.video_block.VideoBlock.index_dictionary')
    def test_indexing_video_error_responses(self, mock_index_dictionary):
        """
        Test do_course_reindex response with mocked error data for video
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = Exception
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            CoursewareSearchIndexer.do_course_reindex(modulestore(), self.course.id)

    @mock.patch('xmodule.html_block.HtmlBlock.index_dictionary')
    def test_indexing_html_error_responses(self, mock_index_dictionary):
        """
        Test do_course_reindex response with mocked error data for html
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = Exception
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            CoursewareSearchIndexer.do_course_reindex(modulestore(), self.course.id)

    @mock.patch('xmodule.seq_block.SequenceBlock.index_dictionary')
    def test_indexing_seq_error_responses(self, mock_index_dictionary):
        """
        Test do_course_reindex response with mocked error data for sequence
        """
        # results are indexed because they are published from BlockFactory
        response = perform_search(
            "unique",
            user=self.user,
            size=10,
            from_=0,
            course_id=str(self.course.id))
        self.assertEqual(response['total'], 1)

        # set mocked exception response
        err = Exception
        mock_index_dictionary.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            CoursewareSearchIndexer.do_course_reindex(modulestore(), self.course.id)

    @mock.patch('xmodule.modulestore.split_mongo.split.SplitMongoModuleStore.get_course')
    def test_indexing_no_item(self, mock_get_course):
        """
        Test system logs an error if no item found.
        """
        # set mocked exception response
        err = ItemNotFoundError
        mock_get_course.return_value = err

        # Start manual reindex and check error in response
        with self.assertRaises(SearchIndexingError):
            CoursewareSearchIndexer.do_course_reindex(modulestore(), self.course.id)
