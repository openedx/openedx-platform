"""
Testing indexing of the courseware as it is changed
"""
import json
import time
from datetime import datetime
from unittest import skip
from unittest.mock import patch, Mock

import ddt
import pytest
from django.conf import settings
from lazy.lazy import lazy
from pytz import UTC
from search.search_engine_base import SearchEngine

from cms.djangoapps.contentstore.courseware_index import (
    CourseAboutSearchIndexer,
    CoursewareSearchIndexer,
    LibrarySearchIndexer,
    SearchIndexingError
)
from cms.djangoapps.contentstore.signals.handlers import listen_for_course_publish, listen_for_library_update
from cms.djangoapps.contentstore.tasks import update_search_index
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url, reverse_usage_url
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from openedx.core.djangoapps.models.course_details import CourseDetails
from xmodule.library_tools import normalize_key_for_search  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import SignalHandler, modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    ModuleStoreTestCase,
    TEST_DATA_SPLIT_MODULESTORE,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory, LibraryFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import UserPartition  # lint-amnesty, pylint: disable=wrong-import-order

COURSE_CHILD_STRUCTURE = {
    "course": "chapter",
    "chapter": "sequential",
    "sequential": "vertical",
    "vertical": "html",
}


def create_children(store, parent, category, load_factor):
    """ create load_factor children within the given parent; recursively call to insert children when appropriate """
    created_count = 0
    for child_index in range(load_factor):
        child_object = BlockFactory.create(
            parent_location=parent.location,
            category=category,
            display_name=f"{category} {child_index} {time.clock()}",  # lint-amnesty, pylint: disable=no-member
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        created_count += 1

        if category in COURSE_CHILD_STRUCTURE:
            created_count += create_children(store, child_object, COURSE_CHILD_STRUCTURE[category], load_factor)

    return created_count


def create_large_course(store, load_factor):
    """
    Create a large course, note that the number of blocks created will be
    load_factor ^ 4 - e.g. load_factor of 10 => 10 chapters, 100
    sequentials, 1000 verticals, 10000 html blocks
    """
    course = CourseFactory.create(modulestore=store, start=datetime(2015, 3, 1, tzinfo=UTC))
    with store.bulk_operations(course.id):
        child_count = create_children(store, course, COURSE_CHILD_STRUCTURE["course"], load_factor)
    return course, child_count


class MixedWithOptionsTestCase(ModuleStoreTestCase):
    """ Base class for test cases within this file """
    CREATE_USER = False
    INDEX_NAME = None

    def setup_course_base(self, store):
        """ base version of setup_course_base is a no-op """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @lazy
    def searcher(self):
        """ Centralized call to getting the search engine for the test """
        return SearchEngine.get_search_engine(self.INDEX_NAME)

    def _get_default_search(self):
        """ Returns field_dictionary for default search """
        return {}

    def search(self, field_dictionary=None, query_string=None):
        """ Performs index search according to passed parameters """
        fields = field_dictionary if field_dictionary else self._get_default_search()
        return self.searcher.search(query_string=query_string, field_dictionary=fields)

    def _perform_test_using_store(self, store_type, test_to_perform):
        """ Helper method to run a test function that uses a specific store """
        store = modulestore()
        with store.default_store(store_type):
            self.setup_course_base(store)
            test_to_perform(store)

    def publish_item(self, store, item_location):
        """ publish the item at the given location """
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            store.publish(item_location, ModuleStoreEnum.UserID.test)

    def delete_item(self, store, item_location):
        """ delete the item at the given location """
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            store.delete_item(item_location, ModuleStoreEnum.UserID.test)

    def update_item(self, store, item):
        """ update the item at the given location """
        with store.branch_setting(ModuleStoreEnum.Branch.draft_preferred):
            store.update_item(item, ModuleStoreEnum.UserID.test)


@pytest.mark.django_db
@ddt.ddt
class TestCoursewareSearchIndexer(MixedWithOptionsTestCase):
    """ Tests the operation of the CoursewareSearchIndexer """

    ENABLED_SIGNALS = ['course_deleted']

    def setUp(self):
        super().setUp()

        self.course = None
        self.chapter = None
        self.sequential = None
        self.vertical = None
        self.html_unit = None

        self.setup_course_base(self.store)

    def setup_course_base(self, store):
        """
        Set up the for the course outline tests.
        """
        self.course = CourseFactory.create(
            modulestore=store,
            start=datetime(2015, 3, 1, tzinfo=UTC),
            display_name="Search Index Test Course"
        )

        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name="Week 1",
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name="Lesson 1",
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location,
            category='vertical',
            display_name='Subsection 1',
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        # unspecified start - should inherit from container
        self.html_unit = BlockFactory.create(
            parent_location=self.vertical.location,
            category="html",
            display_name="Html Content",
            modulestore=store,
            publish_item=False,
        )

    INDEX_NAME = CoursewareSearchIndexer.INDEX_NAME

    def reindex_course(self, store):
        """ kick off complete reindex of the course """
        return CoursewareSearchIndexer.do_course_reindex(store, self.course.id)

    def index_recent_changes(self, store, since_time):
        """ index course using recent changes """
        trigger_time = datetime.now(UTC)
        return CoursewareSearchIndexer.index(
            store,
            self.course.id,
            triggered_at=trigger_time,
            reindex_age=(trigger_time - since_time)
        )

    def _get_default_search(self):
        return {"course": str(self.course.id)}

    def _test_indexing_course(self, store):
        """ indexing course tests """
        # Only published blocks should be in the index
        added_to_index = self.reindex_course(store)  # This reindex may not be necessary (it may already be indexed)
        self.assertEqual(added_to_index, 3)
        response = self.search()
        self.assertEqual(response["total"], 3)

        # Publish the vertical as is, and any unpublished children should now be available
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

    def _test_not_indexing_unpublished_content(self, store):
        """ add a new one, only appers in index once added """
        # Publish the vertical to start with
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

        # Now add a new unit to the existing vertical
        BlockFactory.create(
            parent_location=self.vertical.location,
            category="html",
            display_name="Some other content",
            publish_item=False,
            modulestore=store,
        )
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

        # Now publish it and we should find it
        # Publish the vertical as is, and everything should be available
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 5)

    def _test_delete_course_from_search_index_after_course_deletion(self, store):  # pylint: disable=invalid-name
        """
        Test that course will also be delete from search_index after course deletion.
        """
        self.searcher = SearchEngine.get_search_engine(CourseAboutSearchIndexer.INDEX_NAME)
        # index the course in search_index (it may already be indexed)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 1)

        # delete the course and look course in search_index
        store.delete_course(self.course.id, ModuleStoreEnum.UserID.test)
        self.assertIsNone(store.get_course(self.course.id))
        # Now, because of contentstore.signals.handlers.listen_for_course_delete, the index should already be updated:
        response = self.search()
        self.assertEqual(response["total"], 0)

    def _test_deleting_item(self, store):
        """ test deleting an item """
        # Publish the vertical to start with
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

        # just a delete should not change anything
        self.delete_item(store, self.html_unit.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

        # but after publishing, we should no longer find the html_unit
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 3)

    def _test_start_date_propagation(self, store):
        """ make sure that the start date is applied at the right level """
        early_date = self.course.start
        later_date = self.vertical.start

        # Publish the vertical
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search()
        self.assertEqual(response["total"], 4)

        results = response["results"]
        date_map = {
            str(self.chapter.location): early_date,
            str(self.sequential.location): early_date,
            str(self.vertical.location): later_date,
            str(self.html_unit.location): later_date,
        }
        for result in results:
            self.assertEqual(result["data"]["start_date"], date_map[result["data"]["id"]])

    @patch('django.conf.settings.SEARCH_ENGINE', None)
    def _test_search_disabled(self, store):
        """ if search setting has it as off, confirm that nothing is indexed """
        indexed_count = self.reindex_course(store)
        self.assertFalse(indexed_count)

    def _test_time_based_index(self, store):
        """ Make sure that a time based request to index does not index anything too old """
        self.publish_item(store, self.vertical.location)
        indexed_count = self.reindex_course(store)
        self.assertEqual(indexed_count, 4)

        # Add a new sequential
        sequential2 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name='Section 2',
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )

        # add a new vertical
        vertical2 = BlockFactory.create(
            parent_location=sequential2.location,
            category='vertical',
            display_name='Subsection 2',
            modulestore=store,
            publish_item=True,
        )
        BlockFactory.create(
            parent_location=vertical2.location,
            category="html",
            display_name="Some other content",
            publish_item=False,
            modulestore=store,
        )

        before_time = datetime.now(UTC)
        self.publish_item(store, vertical2.location)
        # index based on time, will include an index of the origin sequential
        # because it is in a common subtree but not of the original vertical
        # because the original sequential's subtree is too old
        new_indexed_count = self.index_recent_changes(store, before_time)
        self.assertEqual(new_indexed_count, 5)

        # full index again
        indexed_count = self.reindex_course(store)
        self.assertEqual(indexed_count, 7)

    def _test_course_about_property_index(self, store):
        """
        Test that informational properties in the course object end up in the course_info index.
        """
        self.searcher = SearchEngine.get_search_engine(CourseAboutSearchIndexer.INDEX_NAME)
        display_name = "Help, I need somebody!"
        self.course.display_name = display_name
        self.update_item(store, self.course)
        self.reindex_course(store)
        response = self.searcher.search(
            field_dictionary={"course": str(self.course.id)}
        )
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["results"][0]["data"]["content"]["display_name"], display_name)

    def _test_course_about_store_index(self, store):
        """
        Test that informational properties in the about store end up in
        the course_info index.
        """
        self.searcher = SearchEngine.get_search_engine(CourseAboutSearchIndexer.INDEX_NAME)
        short_description = "Not just anybody"
        CourseDetails.update_about_item(
            self.course, "short_description", short_description, ModuleStoreEnum.UserID.test, store
        )
        self.reindex_course(store)
        response = self.searcher.search(
            field_dictionary={"course": str(self.course.id)}
        )
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["results"][0]["data"]["content"]["short_description"], short_description)

    def _test_course_about_mode_index(self, store):
        """
        Test that informational properties in the course modes store end up in
        the course_info index.
        """
        self.searcher = SearchEngine.get_search_engine(CourseAboutSearchIndexer.INDEX_NAME)
        honour_mode = CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.HONOR,
            mode_display_name=CourseMode.HONOR
        )
        honour_mode.save()
        verified_mode = CourseModeFactory(
            course_id=self.course.id,
            mode_slug=CourseMode.VERIFIED,
            mode_display_name=CourseMode.VERIFIED,
            min_price=1
        )
        verified_mode.save()
        self.reindex_course(store)

        response = self.searcher.search(
            field_dictionary={"course": str(self.course.id)}
        )
        self.assertEqual(response["total"], 1)
        self.assertIn(CourseMode.HONOR, response["results"][0]["data"]["modes"])
        self.assertIn(CourseMode.VERIFIED, response["results"][0]["data"]["modes"])

    def _test_course_location_info(self, store):
        """ Test that course location information is added to index """
        self.publish_item(store, self.vertical.location)
        self.reindex_course(store)
        response = self.search(query_string="Html Content")
        self.assertEqual(response["total"], 1)

        result = response["results"][0]["data"]
        self.assertEqual(result["course_name"], "Search Index Test Course")
        self.assertEqual(result["location"], ["Week 1", "Lesson 1", "Subsection 1"])

    def _test_course_location_null(self, store):
        """ Test that course location information is added to index """
        sequential2 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name=None,
            modulestore=store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        # add a new vertical
        vertical2 = BlockFactory.create(
            parent_location=sequential2.location,
            category='vertical',
            display_name='Subsection 2',
            modulestore=store,
            publish_item=True,
        )
        BlockFactory.create(
            parent_location=vertical2.location,
            category="html",
            display_name="Find Me",
            publish_item=True,
            modulestore=store,
        )
        self.reindex_course(store)
        response = self.search(query_string="Find Me")
        self.assertEqual(response["total"], 1)

        result = response["results"][0]["data"]
        self.assertEqual(result["course_name"], "Search Index Test Course")
        self.assertEqual(result["location"], ["Week 1", CoursewareSearchIndexer.UNNAMED_MODULE_NAME, "Subsection 2"])

    @patch('django.conf.settings.SEARCH_ENGINE', 'search.tests.utils.ErroringIndexEngine')
    def _test_exception(self, store):
        """ Test that exception within indexing yields a SearchIndexingError """
        self.publish_item(store, self.vertical.location)
        with self.assertRaises(SearchIndexingError):
            self.reindex_course(store)

    def test_indexing_course(self):
        self._test_indexing_course(self.store)

    def test_not_indexing_unpublished_content(self):
        self._test_not_indexing_unpublished_content(self.store)

    def test_deleting_item(self):
        self._test_deleting_item(self.store)

    def test_start_date_propagation(self):
        self._test_start_date_propagation(self.store)

    def test_search_disabled(self):
        self._test_search_disabled(self.store)

    def test_time_based_index(self):
        self._test_time_based_index(self.store)

    def test_exception(self):
        self._test_exception(self.store)

    def test_course_about_property_index(self):
        self._test_course_about_property_index(self.store)

    def test_course_about_store_index(self):
        self._test_course_about_store_index(self.store)

    def test_course_about_mode_index(self):
        self._test_course_about_mode_index(self.store)

    def test_course_location_info(self):
        self._test_course_location_info(self.store)

    def test_course_location_null(self):
        self._test_course_location_null(self.store)

    def test_delete_course_from_search_index_after_course_deletion(self):
        """ Test for removing course from CourseAboutSearchIndexer """
        self._test_delete_course_from_search_index_after_course_deletion(self.store)

    def test_empty_course(self):
        empty_course = CourseFactory.create(modulestore=self.store, start=datetime(2015, 3, 1, tzinfo=UTC))
        added_to_index = CoursewareSearchIndexer.do_course_reindex(self.store, empty_course.id)
        assert added_to_index == 0

    def test_demo_course_reindexing(self):
        """
        Test that demo courses can be reindexed successfully without errors.
        
        This test verifies fix for issue #37496 where demo course reindexing fails.
        Demo courses may have missing metadata or incomplete structure, but indexing
        should still complete successfully.
        """
        # Create a demo course with typical demo course structure
        # Demo courses typically have org="edX", course="DemoX", run="Demo_Course"
        demo_course = CourseFactory.create(
            modulestore=self.store,
            org="edX",
            course="DemoX",
            run="Demo_Course",
            start=datetime(2015, 3, 1, tzinfo=UTC),
            display_name="Demo Course"
        )
        
        # Add some content to the demo course
        chapter = BlockFactory.create(
            parent_location=demo_course.location,
            category='chapter',
            display_name="Demo Chapter",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        sequential = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            display_name="Demo Sequential",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        vertical = BlockFactory.create(
            parent_location=sequential.location,
            category='vertical',
            display_name="Demo Vertical",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        html_unit = BlockFactory.create(
            parent_location=vertical.location,
            category="html",
            display_name="Demo Content",
            modulestore=self.store,
            publish_item=True,
        )
        
        # Reindex the demo course - this should complete without errors
        # even if some metadata is missing
        try:
            added_to_index = CoursewareSearchIndexer.do_course_reindex(self.store, demo_course.id)
            # Verify that indexing completed successfully
            self.assertIsNotNone(added_to_index)
            self.assertGreaterEqual(added_to_index, 0)
            
            # Verify that the course content was indexed
            response = self.search(field_dictionary={"course": str(demo_course.id)})
            # Should have at least the published blocks indexed
            self.assertGreaterEqual(response["total"], 3)
            
            # Verify that specific blocks are in the index
            indexed_ids = [result["data"]["id"] for result in response["results"]]
            self.assertIn(str(chapter.location), indexed_ids)
            self.assertIn(str(sequential.location), indexed_ids)
            self.assertIn(str(vertical.location), indexed_ids)
            self.assertIn(str(html_unit.location), indexed_ids)
            
        except SearchIndexingError as e:
            # If indexing fails, that's the bug we're fixing
            self.fail(f"Demo course reindexing failed with SearchIndexingError: {e}")
        except Exception as e:
            # Any other exception is also a failure
            self.fail(f"Demo course reindexing failed with exception: {e}")

    def test_demo_course_reindexing_with_missing_metadata(self):
        """
        Test that demo courses can be reindexed even when metadata is missing.
        
        This test simulates the scenario where demo courses might have missing
        about blocks or other metadata that could cause indexing to fail.
        """
        # Create a demo course
        demo_course = CourseFactory.create(
            modulestore=self.store,
            org="edX",
            course="DemoX",
            run="Demo_Course",
            start=datetime(2015, 3, 1, tzinfo=UTC),
            display_name="Demo Course"
        )
        
        # Add minimal content
        chapter = BlockFactory.create(
            parent_location=demo_course.location,
            category='chapter',
            display_name="Demo Chapter",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        
        # Mock get_items to simulate missing about blocks (common in demo courses)
        # This simulates the scenario where about blocks haven't been initialized yet
        original_get_items = self.store.get_items
        
        def mock_get_items(course_id, qualifiers=None):
            # If querying for about blocks, return empty list (simulating missing blocks)
            if qualifiers and qualifiers.get("category") == "about":
                return []
            # Otherwise, use the original method
            return original_get_items(course_id, qualifiers=qualifiers)
        
        # Test that indexing still works even with missing about blocks
        with patch.object(self.store, 'get_items', side_effect=mock_get_items):
            try:
                added_to_index = CoursewareSearchIndexer.do_course_reindex(self.store, demo_course.id)
                # Verify that indexing completed successfully despite missing metadata
                self.assertIsNotNone(added_to_index)
                self.assertGreaterEqual(added_to_index, 0)
                
                # Verify that the course content was still indexed
                response = self.search(field_dictionary={"course": str(demo_course.id)})
                self.assertGreaterEqual(response["total"], 1)
                
            except SearchIndexingError as e:
                # If indexing fails, that's the bug we're fixing
                self.fail(f"Demo course reindexing with missing metadata failed with SearchIndexingError: {e}")
            except Exception as e:
                # Any other exception is also a failure
                self.fail(f"Demo course reindexing with missing metadata failed with exception: {e}")

    def test_normal_course_indexing_unaffected(self):
        """
        Test that normal course indexing is unaffected by the demo course fixes.
        
        This test verifies that the error handling added for demo courses doesn't
        break normal course indexing functionality.
        """
        # Create a normal course (not a demo course)
        normal_course = CourseFactory.create(
            modulestore=self.store,
            org="TestOrg",
            course="TestCourse",
            run="TestRun",
            start=datetime(2015, 3, 1, tzinfo=UTC),
            display_name="Normal Test Course"
        )
        
        # Add content to the normal course
        chapter = BlockFactory.create(
            parent_location=normal_course.location,
            category='chapter',
            display_name="Test Chapter",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        sequential = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            display_name="Test Sequential",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        vertical = BlockFactory.create(
            parent_location=sequential.location,
            category='vertical',
            display_name="Test Vertical",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        html_unit = BlockFactory.create(
            parent_location=vertical.location,
            category="html",
            display_name="Test Content",
            modulestore=self.store,
            publish_item=True,
        )
        
        # Reindex the normal course - should work exactly as before
        added_to_index = CoursewareSearchIndexer.do_course_reindex(self.store, normal_course.id)
        
        # Verify that indexing completed successfully
        self.assertIsNotNone(added_to_index)
        self.assertGreaterEqual(added_to_index, 3)
        
        # Verify that the course content was indexed
        response = self.search(field_dictionary={"course": str(normal_course.id)})
        self.assertGreaterEqual(response["total"], 3)
        
        # Verify that specific blocks are in the index
        indexed_ids = [result["data"]["id"] for result in response["results"]]
        self.assertIn(str(chapter.location), indexed_ids)
        self.assertIn(str(sequential.location), indexed_ids)
        self.assertIn(str(vertical.location), indexed_ids)
        self.assertIn(str(html_unit.location), indexed_ids)


@patch('django.conf.settings.SEARCH_ENGINE', 'search.tests.utils.ForceRefreshElasticSearchEngine')
@ddt.ddt
class TestLargeCourseDeletions(MixedWithOptionsTestCase):
    """ Tests to excerise deleting items from a course """

    def _clean_course_id(self):
        """
        Clean all documents from the index that have a specific course provided.
        """
        if self.course_id:

            response = self.searcher.search(field_dictionary={"course": self.course_id})
            while response["total"] > 0:
                for item in response["results"]:
                    self.searcher.remove(item["data"]["id"])
                response = self.searcher.search(field_dictionary={"course": self.course_id})
        self.course_id = None

    def setUp(self):
        super().setUp()
        self.course_id = None
        self.setup_course_base(self.store)

    def tearDown(self):
        super().tearDown()
        self._clean_course_id()

    def assert_search_count(self, expected_count):
        """ Check that the search within this course will yield the expected number of results """

        response = self.searcher.search(field_dictionary={"course": self.course_id})
        self.assertEqual(response["total"], expected_count)

    def _do_test_large_course_deletion(self, store, load_factor):
        """ Test that deleting items from a course works even when present within a very large course """
        def id_list(top_parent_object):
            """ private function to get ids from object down the tree """
            list_of_ids = [str(top_parent_object.location)]
            for child in top_parent_object.get_children():
                list_of_ids.extend(id_list(child))
            return list_of_ids

        course, course_size = create_large_course(store, load_factor)
        self.course_id = str(course.id)

        # index full course
        CoursewareSearchIndexer.do_course_reindex(store, course.id)

        self.assert_search_count(course_size)

        # reload course to allow us to delete one single unit
        course = store.get_course(course.id, depth=1)

        # delete the first chapter
        chapter_to_delete = course.get_children()[0]
        self.delete_item(store, chapter_to_delete.location)

        # index and check correctness
        CoursewareSearchIndexer.do_course_reindex(store, course.id)
        deleted_count = 1 + load_factor + (load_factor ** 2) + (load_factor ** 3)
        self.assert_search_count(course_size - deleted_count)

    def _test_large_course_deletion(self, store):
        """ exception catch-ing wrapper around large test course test with deletions """
        # load_factor of 6 (1296 items) takes about 5 minutes to run on devstack on a laptop
        # load_factor of 7 (2401 items) takes about 70 minutes to run on devstack on a laptop
        # load_factor of 8 (4096 items) takes just under 3 hours to run on devstack on a laptop
        load_factor = 6
        try:
            self._do_test_large_course_deletion(store, load_factor)
        except:  # pylint: disable=bare-except
            # Catch any exception here to see when we fail
            print(f"Failed with load_factor of {load_factor}")

    @skip("This test is to see how we handle very large courses, to ensure that the delete"
          "procedure works smoothly - too long to run during the normal course of things")
    def test_large_course_deletion(self):
        self._test_large_course_deletion(self.store)


@patch('cms.djangoapps.contentstore.signals.handlers.transaction.on_commit',
       new=Mock(side_effect=lambda func: func()),)  # run right away
class TestTaskExecution(SharedModuleStoreTestCase):
    """
    Set of tests to ensure that the task code will do the right thing when
    executed directly. The test course and library gets created without the listeners
    being present, which allows us to ensure that when the listener is
    executed, it is done as expected.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        SignalHandler.course_published.disconnect(listen_for_course_publish)
        SignalHandler.library_updated.disconnect(listen_for_library_update)
        cls.course = CourseFactory.create(start=datetime(2015, 3, 1, tzinfo=UTC))

        cls.chapter = BlockFactory.create(
            parent_location=cls.course.location,
            category='chapter',
            display_name="Week 1",
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        cls.sequential = BlockFactory.create(
            parent_location=cls.chapter.location,
            category='sequential',
            display_name="Lesson 1",
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        cls.vertical = BlockFactory.create(
            parent_location=cls.sequential.location,
            category='vertical',
            display_name='Subsection 1',
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        # unspecified start - should inherit from container
        cls.html_unit = BlockFactory.create(
            parent_location=cls.vertical.location,
            category="html",
            display_name="Html Content",
            publish_item=False,
        )

        cls.library = LibraryFactory.create()

        cls.library_block1 = BlockFactory.create(
            parent_location=cls.library.location,
            category="html",
            display_name="Html Content",
            publish_item=False,
        )

        cls.library_block2 = BlockFactory.create(
            parent_location=cls.library.location,
            category="html",
            display_name="Html Content 2",
            publish_item=False,
        )

    @classmethod
    def tearDownClass(cls):
        SignalHandler.course_published.connect(listen_for_course_publish)
        SignalHandler.library_updated.connect(listen_for_library_update)
        super().tearDownClass()

    def test_task_indexing_course(self):
        """
        Making sure that the receiver correctly fires off the task when invoked
        by signal.
        """
        searcher = SearchEngine.get_search_engine(CoursewareSearchIndexer.INDEX_NAME)
        response = searcher.search(
            field_dictionary={"course": str(self.course.id)}
        )
        self.assertEqual(response["total"], 0)

        listen_for_course_publish(self, self.course.id)

        # Note that this test will only succeed if celery is working in inline mode
        response = searcher.search(
            field_dictionary={"course": str(self.course.id)}
        )
        self.assertEqual(response["total"], 3)

    def test_task_library_update(self):
        """ Making sure that the receiver correctly fires off the task when invoked by signal """
        searcher = SearchEngine.get_search_engine(LibrarySearchIndexer.INDEX_NAME)
        library_search_key = str(normalize_key_for_search(self.library.location.library_key))
        response = searcher.search(field_dictionary={"library": library_search_key})
        self.assertEqual(response["total"], 0)

        listen_for_library_update(self, self.library.location.library_key)

        # Note that this test will only succeed if celery is working in inline mode
        response = searcher.search(field_dictionary={"library": library_search_key})
        self.assertEqual(response["total"], 2)

    def test_ignore_ccx(self):
        """Test that we ignore CCX courses (it's too slow now)."""
        # We're relying on our CCX short circuit to just stop execution as soon
        # as it encounters a CCX key. If that isn't working properly, it will
        # fall through to the normal indexing and raise an exception because
        # there is no data or backing course behind the course key.
        with patch('cms.djangoapps.contentstore.courseware_index.CoursewareSearchIndexer.index') as mock_index:
            self.assertIsNone(
                update_search_index(
                    "ccx-v1:OpenEdX+FAKECOURSE+FAKERUN+ccx@1", "2020-09-28T16:41:57.150796"
                )
            )
            self.assertFalse(mock_index.called)


@pytest.mark.django_db
@ddt.ddt
class TestLibrarySearchIndexer(MixedWithOptionsTestCase):
    """ Tests the operation of the CoursewareSearchIndexer """

    # libraries work only with split, so do library indexer
    WORKS_WITH_STORES = (ModuleStoreEnum.Type.split, )

    def setUp(self):
        super().setUp()

        self.library = None
        self.html_unit1 = None
        self.html_unit2 = None

    def setup_course_base(self, store):
        """
        Set up the for the course outline tests.
        """
        self.library = LibraryFactory.create(modulestore=store)

        self.html_unit1 = BlockFactory.create(
            parent_location=self.library.location,
            category="html",
            display_name="Html Content",
            modulestore=store,
            publish_item=False,
        )

        self.html_unit2 = BlockFactory.create(
            parent_location=self.library.location,
            category="html",
            display_name="Html Content 2",
            modulestore=store,
            publish_item=False,
        )

    INDEX_NAME = LibrarySearchIndexer.INDEX_NAME

    def _get_default_search(self):
        """ Returns field_dictionary for default search """
        return {"library": str(self.library.location.library_key.replace(version_guid=None, branch=None))}

    def reindex_library(self, store):
        """ kick off complete reindex of the course """
        return LibrarySearchIndexer.do_library_reindex(store, self.library.location.library_key)

    def _get_contents(self, response):
        """ Extracts contents from search response """
        return [item['data']['content'] for item in response['results']]

    def _test_indexing_library(self, store):
        """ indexing course tests """
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 2)

        added_to_index = self.reindex_library(store)
        self.assertEqual(added_to_index, 2)
        response = self.search()
        self.assertEqual(response["total"], 2)

    def _test_creating_item(self, store):
        """ test updating an item """
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 2)

        # updating a library item causes immediate reindexing
        data = "Some data"
        BlockFactory.create(
            parent_location=self.library.location,
            category="html",
            display_name="Html Content 3",
            data=data,
            modulestore=store,
            publish_item=False,
        )

        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 3)
        html_contents = [cont['html_content'] for cont in self._get_contents(response)]
        self.assertIn(data, html_contents)

    def _test_updating_item(self, store):
        """ test updating an item """
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 2)

        # updating a library item causes immediate reindexing
        new_data = "I'm new data"
        self.html_unit1.data = new_data
        self.update_item(store, self.html_unit1)
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 2)
        html_contents = [cont['html_content'] for cont in self._get_contents(response)]
        self.assertIn(new_data, html_contents)

    def _test_deleting_item(self, store):
        """ test deleting an item """
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 2)

        # deleting a library item causes immediate reindexing
        self.delete_item(store, self.html_unit1.location)
        self.reindex_library(store)
        response = self.search()
        self.assertEqual(response["total"], 1)

    @patch('django.conf.settings.SEARCH_ENGINE', None)
    def _test_search_disabled(self, store):
        """ if search setting has it as off, confirm that nothing is indexed """
        indexed_count = self.reindex_library(store)
        self.assertFalse(indexed_count)

    @patch('django.conf.settings.SEARCH_ENGINE', 'search.tests.utils.ErroringIndexEngine')
    def _test_exception(self, store):
        """ Test that exception within indexing yields a SearchIndexingError """
        with self.assertRaises(SearchIndexingError):
            self.reindex_library(store)

    @ddt.data(*WORKS_WITH_STORES)
    def test_indexing_library(self, store_type):
        self._perform_test_using_store(store_type, self._test_indexing_library)

    @ddt.data(*WORKS_WITH_STORES)
    def test_updating_item(self, store_type):
        self._perform_test_using_store(store_type, self._test_updating_item)

    @ddt.data(*WORKS_WITH_STORES)
    def test_creating_item(self, store_type):
        self._perform_test_using_store(store_type, self._test_creating_item)

    @ddt.data(*WORKS_WITH_STORES)
    def test_deleting_item(self, store_type):
        self._perform_test_using_store(store_type, self._test_deleting_item)

    @ddt.data(*WORKS_WITH_STORES)
    def test_search_disabled(self, store_type):
        self._perform_test_using_store(store_type, self._test_search_disabled)

    @ddt.data(*WORKS_WITH_STORES)
    def test_exception(self, store_type):
        self._perform_test_using_store(store_type, self._test_exception)


class GroupConfigurationSearchSplit(CourseTestCase, MixedWithOptionsTestCase):
    """
    Tests indexing of content groups on course blocks using split modulestore.
    """
    CREATE_USER = True
    INDEX_NAME = CoursewareSearchIndexer.INDEX_NAME
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()

        self._setup_course_with_content()
        self._setup_split_test_block()
        self._setup_content_groups()
        self.reload_course()

    def _setup_course_with_content(self):
        """
        Set up course with html content in it.
        """
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name="Week 1",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )

        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name="Lesson 1",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )

        self.sequential2 = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name="Lesson 2",
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )

        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location,
            category='vertical',
            display_name='Subsection 1',
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )

        self.vertical2 = BlockFactory.create(
            parent_location=self.sequential.location,
            category='vertical',
            display_name='Subsection 2',
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )

        self.vertical3 = BlockFactory.create(
            parent_location=self.sequential2.location,
            category='vertical',
            display_name='Subsection 3',
            modulestore=self.store,
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )

        # unspecified start - should inherit from container
        self.html_unit1 = BlockFactory.create(
            parent_location=self.vertical.location,
            category="html",
            display_name="Html Content 1",
            modulestore=self.store,
            publish_item=True,
        )
        self.html_unit1.parent = self.vertical

        self.html_unit2 = BlockFactory.create(
            parent_location=self.vertical2.location,
            category="html",
            display_name="Html Content 2",
            modulestore=self.store,
            publish_item=True,
        )
        self.html_unit2.parent = self.vertical2

        self.html_unit3 = BlockFactory.create(
            parent_location=self.vertical2.location,
            category="html",
            display_name="Html Content 3",
            modulestore=self.store,
            publish_item=True,
        )
        self.html_unit3.parent = self.vertical2

    def _setup_split_test_block(self):
        """
        Set up split test block.
        """
        c0_url = self.course.id.make_usage_key("vertical", "condition_0_vertical")
        c1_url = self.course.id.make_usage_key("vertical", "condition_1_vertical")
        c2_url = self.course.id.make_usage_key("vertical", "condition_2_vertical")

        self.split_test_unit = BlockFactory.create(
            parent_location=self.vertical3.location,
            category='split_test',
            user_partition_id=0,
            display_name="Test Content Experiment 1",
            group_id_to_child={"2": c0_url, "3": c1_url, "4": c2_url}
        )

        self.condition_0_vertical = BlockFactory.create(
            parent_location=self.split_test_unit.location,
            category="vertical",
            display_name="Group ID 2",
            location=c0_url,
        )
        self.condition_0_vertical.parent = self.vertical3

        self.condition_1_vertical = BlockFactory.create(
            parent_location=self.split_test_unit.location,
            category="vertical",
            display_name="Group ID 3",
            location=c1_url,
        )
        self.condition_1_vertical.parent = self.vertical3

        self.condition_2_vertical = BlockFactory.create(
            parent_location=self.split_test_unit.location,
            category="vertical",
            display_name="Group ID 4",
            location=c2_url,
        )
        self.condition_2_vertical.parent = self.vertical3

        self.html_unit4 = BlockFactory.create(
            parent_location=self.condition_0_vertical.location,
            category="html",
            display_name="Split A",
            publish_item=True,
        )
        self.html_unit4.parent = self.condition_0_vertical

        self.html_unit5 = BlockFactory.create(
            parent_location=self.condition_1_vertical.location,
            category="html",
            display_name="Split B",
            publish_item=True,
        )
        self.html_unit5.parent = self.condition_1_vertical

        self.html_unit6 = BlockFactory.create(
            parent_location=self.condition_2_vertical.location,
            category="html",
            display_name="Split C",
            publish_item=True,
        )
        self.html_unit6.parent = self.condition_2_vertical

    def _setup_content_groups(self):
        """
        Set up cohort and experiment content groups.
        """
        cohort_groups_list = {
            'id': 666,
            'name': 'Test name',
            'scheme': 'cohort',
            'description': 'Test description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 0, 'name': 'Group A', 'version': 1, 'usage': []},
                {'id': 1, 'name': 'Group B', 'version': 1, 'usage': []},
            ],
        }
        experiment_groups_list = {
            'id': 0,
            'name': 'Experiment aware partition',
            'scheme': 'random',
            'description': 'Experiment aware description',
            'version': UserPartition.VERSION,
            'groups': [
                {'id': 2, 'name': 'Group A', 'version': 1, 'usage': []},
                {'id': 3, 'name': 'Group B', 'version': 1, 'usage': []},
                {'id': 4, 'name': 'Group C', 'version': 1, 'usage': []}
            ],
        }

        self.client.put(
            self._group_conf_url(cid=666),
            data=json.dumps(cohort_groups_list),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.client.put(
            self._group_conf_url(cid=0),
            data=json.dumps(experiment_groups_list),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def _group_conf_url(self, cid=-1):
        """
        Return url for the handler.
        """
        return reverse_course_url(
            'group_configurations_detail_handler',
            self.course.id,
            kwargs={'group_configuration_id': cid},
        )

    def _html_group_result(self, html_unit, content_groups):
        """
        Return object with arguments and content group for html_unit.
        """
        return {
            'course_name': self.course.display_name,
            'id': str(html_unit.location),
            'content': {'html_content': '', 'display_name': html_unit.display_name},
            'course': str(self.course.id),
            'location': [
                self.chapter.display_name,
                self.sequential.display_name,
                html_unit.parent.display_name
            ],
            'content_type': 'Text',
            'org': self.course.org,
            'content_groups': content_groups,
            'start_date': datetime(2015, 4, 1, 0, 0, tzinfo=UTC)
        }

    def _html_experiment_group_result(self, html_unit, content_groups):
        """
        Return object with arguments and content group for html_unit.
        """
        return {
            'course_name': self.course.display_name,
            'id': str(html_unit.location),
            'content': {'html_content': '', 'display_name': html_unit.display_name},
            'course': str(self.course.id),
            'location': [
                self.chapter.display_name,
                self.sequential2.display_name,
                self.vertical3.display_name
            ],
            'content_type': 'Text',
            'org': self.course.org,
            'content_groups': content_groups,
            'start_date': datetime(2015, 4, 1, 0, 0, tzinfo=UTC)
        }

    def _vertical_experiment_group_result(self, vertical, content_groups):
        """
        Return object with arguments and content group for split_test vertical.
        """
        return {
            'start_date': datetime(2015, 4, 1, 0, 0, tzinfo=UTC),
            'content': {'display_name': vertical.display_name},
            'course': str(self.course.id),
            'location': [
                self.chapter.display_name,
                self.sequential2.display_name,
                vertical.parent.display_name
            ],
            'content_type': 'Sequence',
            'content_groups': content_groups,
            'id': str(vertical.location),
            'course_name': self.course.display_name,
            'org': self.course.org
        }

    def _html_nogroup_result(self, html_unit):
        """
        Return object with arguments and content group set to empty array for html_unit.
        """
        return {
            'course_name': self.course.display_name,
            'id': str(html_unit.location),
            'content': {'html_content': '', 'display_name': html_unit.display_name},
            'course': str(self.course.id),
            'location': [
                self.chapter.display_name,
                self.sequential.display_name,
                html_unit.parent.display_name
            ],
            'content_type': 'Text',
            'org': self.course.org,
            'content_groups': None,
            'start_date': datetime(2015, 4, 1, 0, 0, tzinfo=UTC)
        }

    def _get_index_values_from_call_args(self, mock_index):
        """
        Return content values from args tuple in a mocked calls list.
        """
        call = mock_index.call_args
        (indexed_content, ), kwargs = call  # pylint: disable=unused-variable
        return indexed_content

    def reindex_course(self, store):
        """ kick off complete reindex of the course """
        return CoursewareSearchIndexer.do_course_reindex(store, self.course.id)

    def test_content_group_gets_indexed(self):
        """
        Indexing course with content groups added test.
        """

        # Only published blocks should be in the index
        added_to_index = self.reindex_course(self.store)
        self.assertEqual(added_to_index, 16)
        response = self.searcher.search(field_dictionary={"course": str(self.course.id)})
        self.assertEqual(response["total"], 16)

        group_access_content = {'group_access': {666: [1]}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit1.location),
            data={'metadata': group_access_content}
        )

        self.publish_item(self.store, self.html_unit1.location)
        self.publish_item(self.store, self.split_test_unit.location)

        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_group_result(self.html_unit1, [1]), indexed_content)
            self.assertIn(self._html_experiment_group_result(self.html_unit4, [str(2)]), indexed_content)
            self.assertIn(self._html_experiment_group_result(self.html_unit5, [str(3)]), indexed_content)
            self.assertIn(self._html_experiment_group_result(self.html_unit6, [str(4)]), indexed_content)
            self.assertNotIn(self._html_experiment_group_result(self.html_unit6, [str(5)]), indexed_content)
            self.assertIn(
                self._vertical_experiment_group_result(self.condition_0_vertical, [str(2)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_1_vertical, [str(2)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_2_vertical, [str(2)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_0_vertical, [str(3)]),
                indexed_content
            )
            self.assertIn(
                self._vertical_experiment_group_result(self.condition_1_vertical, [str(3)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_2_vertical, [str(3)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_0_vertical, [str(4)]),
                indexed_content
            )
            self.assertNotIn(
                self._vertical_experiment_group_result(self.condition_1_vertical, [str(4)]),
                indexed_content
            )
            self.assertIn(
                self._vertical_experiment_group_result(self.condition_2_vertical, [str(4)]),
                indexed_content
            )
            mock_index.reset_mock()

    def test_content_group_not_assigned(self):
        """ indexing course without content groups added test """

        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_nogroup_result(self.html_unit1), indexed_content)
            mock_index.reset_mock()

    def test_content_group_not_indexed_on_delete(self):
        """ indexing course with content groups deleted test """

        group_access_content = {'group_access': {666: [1]}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit1.location),
            data={'metadata': group_access_content}
        )

        self.publish_item(self.store, self.html_unit1.location)

        # Checking group indexed correctly
        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_group_result(self.html_unit1, [1]), indexed_content)
            mock_index.reset_mock()

        empty_group_access = {'group_access': {}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit1.location),
            data={'metadata': empty_group_access}
        )

        self.publish_item(self.store, self.html_unit1.location)

        # Checking group removed and not indexed any more
        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_nogroup_result(self.html_unit1), indexed_content)
            mock_index.reset_mock()

    def test_group_indexed_only_on_assigned_html_block(self):
        """ indexing course with content groups assigned to one of multiple html units """
        group_access_content = {'group_access': {666: [1]}}
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit1.location),
            data={'metadata': group_access_content}
        )

        self.publish_item(self.store, self.html_unit1.location)

        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_group_result(self.html_unit1, [1]), indexed_content)
            self.assertIn(self._html_nogroup_result(self.html_unit2), indexed_content)
            mock_index.reset_mock()

    def test_different_groups_indexed_on_assigned_html_blocks(self):
        """ indexing course with different content groups assigned to each of multiple html units """
        group_access_content_1 = {'group_access': {666: [1]}}
        group_access_content_2 = {'group_access': {666: [0]}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit1.location),
            data={'metadata': group_access_content_1}
        )
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit2.location),
            data={'metadata': group_access_content_2}
        )

        self.publish_item(self.store, self.html_unit1.location)
        self.publish_item(self.store, self.html_unit2.location)

        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_group_result(self.html_unit1, [1]), indexed_content)
            self.assertIn(self._html_group_result(self.html_unit2, [0]), indexed_content)
            mock_index.reset_mock()

    def test_different_groups_indexed_on_same_vertical_html_blocks(self):
        """
        Indexing course with different content groups assigned to each of multiple html units
        on same vertical

        """
        group_access_content_1 = {'group_access': {666: [1]}}
        group_access_content_2 = {'group_access': {666: [0]}}

        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit2.location),
            data={'metadata': group_access_content_1}
        )
        self.client.ajax_post(
            reverse_usage_url("xblock_handler", self.html_unit3.location),
            data={'metadata': group_access_content_2}
        )

        self.publish_item(self.store, self.html_unit2.location)
        self.publish_item(self.store, self.html_unit3.location)

        with patch(settings.SEARCH_ENGINE + '.index') as mock_index:
            self.reindex_course(self.store)
            self.assertTrue(mock_index.called)
            indexed_content = self._get_index_values_from_call_args(mock_index)
            self.assertIn(self._html_group_result(self.html_unit2, [1]), indexed_content)
            self.assertIn(self._html_group_result(self.html_unit3, [0]), indexed_content)
            mock_index.reset_mock()
