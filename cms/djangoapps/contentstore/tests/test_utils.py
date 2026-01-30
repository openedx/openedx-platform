""" Tests for utils. """
import collections
from datetime import datetime, timedelta
from unittest import mock
from unittest.mock import Mock, patch
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from openedx_events.tests.utils import OpenEdxEventsTestMixin
from path import Path as path
from pytz import UTC
from rest_framework import status
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore import utils
from cms.djangoapps.contentstore.tasks import ALL_ALLOWED_XBLOCKS, validate_course_olx
from cms.djangoapps.contentstore.tests.utils import TEST_DATA_DIR, CourseTestCase
from cms.djangoapps.contentstore.utils import send_course_update_notification
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import GlobalStaffFactory, InstructorFactory, UserFactory
from openedx.core.djangoapps.notifications.config.waffle import ENABLE_NOTIFICATIONS
from openedx.core.djangoapps.notifications.models import Notification
from openedx.core.djangoapps.site_configuration.tests.test_util import with_site_configuration_context
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    SharedModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import (
    BlockFactory,
    CourseFactory,
)
from xmodule.partitions.partitions import Group, UserPartition


class LMSLinksTestCase(TestCase):
    """ Tests for LMS links. """

    def lms_link_test(self):
        """ Tests get_lms_link_for_item. """
        course_key = CourseLocator('mitX', '101', 'test')
        location = course_key.make_usage_key('vertical', 'contacting_us')
        link = utils.get_lms_link_for_item(location, False)
        self.assertEqual(link, "//localhost:8000/courses/course-v1:mitX+101+test/jump_to/block-v1:mitX+101+test+type"
                               "@vertical+block@contacting_us")

        # test preview
        link = utils.get_lms_link_for_item(location, True)
        self.assertEqual(
            link,
            "//preview.localhost/courses/course-v1:mitX+101+test/jump_to/block-v1:mitX+101+test+type@vertical+block"
            "@contacting_us "
        )

        # now test with the course' location
        location = course_key.make_usage_key('course', 'test')
        link = utils.get_lms_link_for_item(location)
        self.assertEqual(link, "//localhost:8000/courses/course-v1:mitX+101+test/jump_to/block-v1:mitX+101+test+type"
                               "@course+block@test")

    def lms_link_for_certificate_web_view_test(self):
        """ Tests get_lms_link_for_certificate_web_view. """
        course_key = CourseLocator('mitX', '101', 'test')
        dummy_user = ModuleStoreEnum.UserID.test
        mode = 'professional'

        self.assertEqual(
            utils.get_lms_link_for_certificate_web_view(course_key, mode),
            "//localhost:8000/certificates/course/{course_key}?preview={mode}".format(
                course_key=course_key,
                mode=mode
            )
        )

        with with_site_configuration_context(configuration={"course_org_filter": "mitX", "LMS_BASE": "dummyhost:8000"}):
            self.assertEqual(
                utils.get_lms_link_for_certificate_web_view(course_key, mode),
                "//dummyhost:8000/certificates/course/{course_key}?preview={mode}".format(
                    course_key=course_key,
                    mode=mode
                )
            )


class ExtraPanelTabTestCase(TestCase):
    """ Tests adding and removing extra course tabs. """

    def get_tab_type_dicts(self, tab_types):
        """ Returns an array of tab dictionaries. """
        if tab_types:
            return [{'tab_type': tab_type} for tab_type in tab_types.split(',')]
        else:
            return []

    def get_course_with_tabs(self, tabs=None):
        """ Returns a mock course object with a tabs attribute. """
        if tabs is None:
            tabs = []
        course = collections.namedtuple('MockCourse', ['tabs'])
        if isinstance(tabs, str):
            course.tabs = self.get_tab_type_dicts(tabs)
        else:
            course.tabs = tabs
        return course


class XBlockVisibilityTestCase(SharedModuleStoreTestCase):
    """Tests for xblock visibility for students."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.dummy_user = ModuleStoreEnum.UserID.test
        cls.past = datetime(1970, 1, 1, tzinfo=UTC)
        cls.future = datetime.now(UTC) + timedelta(days=1)
        cls.course = CourseFactory.create()

    def test_private_unreleased_xblock(self):
        """Verifies that a private unreleased xblock is not visible"""
        self._test_visible_to_students(False, 'private_unreleased', self.future)

    def test_private_released_xblock(self):
        """Verifies that a private released xblock is not visible"""
        self._test_visible_to_students(False, 'private_released', self.past)

    def test_public_unreleased_xblock(self):
        """Verifies that a public (published) unreleased xblock is not visible"""
        self._test_visible_to_students(False, 'public_unreleased', self.future, publish=True)

    def test_public_released_xblock(self):
        """Verifies that public (published) released xblock is visible if staff lock is not enabled."""
        self._test_visible_to_students(True, 'public_released', self.past, publish=True)

    def test_private_no_start_xblock(self):
        """Verifies that a private xblock with no start date is not visible"""
        self._test_visible_to_students(False, 'private_no_start', None)

    def test_public_no_start_xblock(self):
        """Verifies that a public (published) xblock with no start date is visible unless staff lock is enabled"""
        self._test_visible_to_students(True, 'public_no_start', None, publish=True)

    def test_draft_released_xblock(self):
        """Verifies that a xblock with an unreleased draft and a released published version is visible"""
        vertical = self._create_xblock_with_start_date('draft_released', self.past, publish=True)

        # Create an unreleased draft version of the xblock
        vertical.start = self.future
        modulestore().update_item(vertical, self.dummy_user)

        self.assertTrue(utils.is_currently_visible_to_students(vertical))

    def _test_visible_to_students(self, expected_visible_without_lock, name, start_date, publish=False):
        """
        Helper method that checks that is_xblock_visible_to_students returns the correct value both
        with and without visible_to_staff_only set.
        """
        no_staff_lock = self._create_xblock_with_start_date(name, start_date, publish, visible_to_staff_only=False)
        self.assertEqual(expected_visible_without_lock, utils.is_currently_visible_to_students(no_staff_lock))

        # any xblock with visible_to_staff_only set to True should not be visible to students.
        staff_lock = self._create_xblock_with_start_date(
            name + "_locked", start_date, publish, visible_to_staff_only=True
        )
        self.assertFalse(utils.is_currently_visible_to_students(staff_lock))

    def _create_xblock_with_start_date(self, name, start_date, publish=False, visible_to_staff_only=False):
        """Helper to create an xblock with a start date, optionally publishing it"""

        vertical = modulestore().create_item(
            self.dummy_user, self.course.location.course_key, 'vertical', name,
            fields={'start': start_date, 'visible_to_staff_only': visible_to_staff_only}
        )

        if publish:
            modulestore().publish(vertical.location, self.dummy_user)

        return vertical


class ReleaseDateSourceTest(CourseTestCase):
    """Tests for finding the source of an xblock's release date."""

    def setUp(self):
        super().setUp()

        self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        self.sequential = BlockFactory.create(category='sequential', parent_location=self.chapter.location)
        self.vertical = BlockFactory.create(category='vertical', parent_location=self.sequential.location)

        # Read again so that children lists are accurate
        self.chapter = self.store.get_item(self.chapter.location)
        self.sequential = self.store.get_item(self.sequential.location)
        self.vertical = self.store.get_item(self.vertical.location)

        self.date_one = datetime(1980, 1, 1, tzinfo=UTC)
        self.date_two = datetime(2020, 1, 1, tzinfo=UTC)

    def _update_release_dates(self, chapter_start, sequential_start, vertical_start):
        """Sets the release dates of the chapter, sequential, and vertical"""
        self.chapter.start = chapter_start
        self.chapter = self.store.update_item(self.chapter, ModuleStoreEnum.UserID.test)
        self.sequential.start = sequential_start
        self.sequential = self.store.update_item(self.sequential, ModuleStoreEnum.UserID.test)
        self.vertical.start = vertical_start
        self.vertical = self.store.update_item(self.vertical, ModuleStoreEnum.UserID.test)

    def _verify_release_date_source(self, item, expected_source):
        """Helper to verify that the release date source of a given item matches the expected source"""
        source = utils.find_release_date_source(item)
        self.assertEqual(source.location, expected_source.location)
        self.assertEqual(source.start, expected_source.start)

    def test_chapter_source_for_vertical(self):
        """Tests a vertical's release date being set by its chapter"""
        self._update_release_dates(self.date_one, self.date_one, self.date_one)
        self._verify_release_date_source(self.vertical, self.chapter)

    def test_sequential_source_for_vertical(self):
        """Tests a vertical's release date being set by its sequential"""
        self._update_release_dates(self.date_one, self.date_two, self.date_two)
        self._verify_release_date_source(self.vertical, self.sequential)

    def test_chapter_source_for_sequential(self):
        """Tests a sequential's release date being set by its chapter"""
        self._update_release_dates(self.date_one, self.date_one, self.date_one)
        self._verify_release_date_source(self.sequential, self.chapter)

    def test_sequential_source_for_sequential(self):
        """Tests a sequential's release date being set by itself"""
        self._update_release_dates(self.date_one, self.date_two, self.date_two)
        self._verify_release_date_source(self.sequential, self.sequential)


class StaffLockTest(CourseTestCase):
    """Base class for testing staff lock functions."""

    def setUp(self):
        super().setUp()

        self.chapter = BlockFactory.create(category='chapter', parent_location=self.course.location)
        self.sequential = BlockFactory.create(category='sequential', parent_location=self.chapter.location)
        self.vertical = BlockFactory.create(category='vertical', parent_location=self.sequential.location)
        self.orphan = BlockFactory.create(category='vertical', parent_location=self.sequential.location)

        # Read again so that children lists are accurate
        self.chapter = self.store.get_item(self.chapter.location)
        self.sequential = self.store.get_item(self.sequential.location)
        self.vertical = self.store.get_item(self.vertical.location)

        # Orphan the orphaned xblock
        self.sequential.children = [self.vertical.location]
        self.sequential = self.store.update_item(self.sequential, ModuleStoreEnum.UserID.test)

    def _set_staff_lock(self, xblock, is_locked):
        """If is_locked is True, xblock is staff locked. Otherwise, the xblock staff lock field is removed."""
        field = xblock.fields['visible_to_staff_only']
        if is_locked:
            field.write_to(xblock, True)
        else:
            field.delete_from(xblock)
        return self.store.update_item(xblock, ModuleStoreEnum.UserID.test)

    def _update_staff_locks(self, chapter_locked, sequential_locked, vertical_locked):
        """
        Sets the staff lock on the chapter, sequential, and vertical
        If the corresponding argument is False, then the field is deleted from the xblock
        """
        self.chapter = self._set_staff_lock(self.chapter, chapter_locked)
        self.sequential = self._set_staff_lock(self.sequential, sequential_locked)
        self.vertical = self._set_staff_lock(self.vertical, vertical_locked)


class StaffLockSourceTest(StaffLockTest):
    """Tests for finding the source of an xblock's staff lock."""

    def _verify_staff_lock_source(self, item, expected_source):
        """Helper to verify that the staff lock source of a given item matches the expected source"""
        source = utils.find_staff_lock_source(item)
        self.assertEqual(source.location, expected_source.location)
        self.assertTrue(source.visible_to_staff_only)

    def test_chapter_source_for_vertical(self):
        """Tests a vertical's staff lock being set by its chapter"""
        self._update_staff_locks(True, False, False)
        self._verify_staff_lock_source(self.vertical, self.chapter)

    def test_sequential_source_for_vertical(self):
        """Tests a vertical's staff lock being set by its sequential"""
        self._update_staff_locks(True, True, False)
        self._verify_staff_lock_source(self.vertical, self.sequential)
        self._update_staff_locks(False, True, False)
        self._verify_staff_lock_source(self.vertical, self.sequential)

    def test_vertical_source_for_vertical(self):
        """Tests a vertical's staff lock being set by itself"""
        self._update_staff_locks(True, True, True)
        self._verify_staff_lock_source(self.vertical, self.vertical)
        self._update_staff_locks(False, True, True)
        self._verify_staff_lock_source(self.vertical, self.vertical)
        self._update_staff_locks(False, False, True)
        self._verify_staff_lock_source(self.vertical, self.vertical)

    def test_orphan_has_no_source(self):
        """Tests that a orphaned xblock has no staff lock source"""
        self.assertIsNone(utils.find_staff_lock_source(self.orphan))

    def test_no_source_for_vertical(self):
        """Tests a vertical with no staff lock set anywhere"""
        self._update_staff_locks(False, False, False)
        self.assertIsNone(utils.find_staff_lock_source(self.vertical))


class InheritedStaffLockTest(StaffLockTest):
    """Tests for determining if an xblock inherits a staff lock."""

    def test_no_inheritance(self):
        """Tests that a locked or unlocked vertical with no locked ancestors does not have an inherited lock"""
        self._update_staff_locks(False, False, False)
        self.assertFalse(utils.ancestor_has_staff_lock(self.vertical))
        self._update_staff_locks(False, False, True)
        self.assertFalse(utils.ancestor_has_staff_lock(self.vertical))

    def test_inheritance_in_locked_section(self):
        """Tests that a locked or unlocked vertical in a locked section has an inherited lock"""
        self._update_staff_locks(True, False, False)
        self.assertTrue(utils.ancestor_has_staff_lock(self.vertical))
        self._update_staff_locks(True, False, True)
        self.assertTrue(utils.ancestor_has_staff_lock(self.vertical))

    def test_inheritance_in_locked_subsection(self):
        """Tests that a locked or unlocked vertical in a locked subsection has an inherited lock"""
        self._update_staff_locks(False, True, False)
        self.assertTrue(utils.ancestor_has_staff_lock(self.vertical))
        self._update_staff_locks(False, True, True)
        self.assertTrue(utils.ancestor_has_staff_lock(self.vertical))

    def test_no_inheritance_for_orphan(self):
        """Tests that an orphaned xblock does not inherit staff lock"""
        self.assertFalse(utils.ancestor_has_staff_lock(self.orphan))


class GroupVisibilityTest(CourseTestCase):
    """
    Test content group access rules.
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        chapter = BlockFactory.create(category='chapter', parent=self.course)
        sequential = BlockFactory.create(category='sequential', parent=chapter)
        vertical = BlockFactory.create(category='vertical', parent=sequential)
        html = BlockFactory.create(category='html', parent=vertical)
        problem = BlockFactory.create(
            category='problem', parent=vertical, data="<problem></problem>"
        )
        self.sequential = self.store.get_item(sequential.location)
        self.vertical = self.store.get_item(vertical.location)
        self.html = self.store.get_item(html.location)
        self.problem = self.store.get_item(problem.location)

        # Add partitions to the course
        self.course.user_partitions = [
            UserPartition(
                id=0,
                name="Partition 0",
                description="Partition 0",
                scheme=UserPartition.get_scheme("random"),
                groups=[
                    Group(id=0, name="Group A"),
                    Group(id=1, name="Group B"),
                ],
            ),
            UserPartition(
                id=1,
                name="Partition 1",
                description="Partition 1",
                scheme=UserPartition.get_scheme("random"),
                groups=[
                    Group(id=0, name="Group C"),
                    Group(id=1, name="Group D"),
                ],
            ),
            UserPartition(
                id=2,
                name="Partition 2",
                description="Partition 2",
                scheme=UserPartition.get_scheme("random"),
                groups=[
                    Group(id=0, name="Group E"),
                    Group(id=1, name="Group F"),
                    Group(id=2, name="Group G"),
                    Group(id=3, name="Group H"),
                ],
            ),
        ]
        self.course = self.store.update_item(self.course, ModuleStoreEnum.UserID.test)

    def set_group_access(self, xblock, value):
        """ Sets group_access to specified value and calls update_item to persist the change. """
        xblock.group_access = value
        self.store.update_item(xblock, self.user.id)

    def test_no_visibility_set(self):
        """ Tests when group_access has not been set on anything. """

        def verify_all_components_visible_to_all():
            """ Verifies when group_access has not been set on anything. """
            for item in (self.sequential, self.vertical, self.html, self.problem):
                self.assertFalse(utils.has_children_visible_to_specific_partition_groups(item))
                self.assertFalse(utils.is_visible_to_specific_partition_groups(item))

        verify_all_components_visible_to_all()

        # Test with group_access set to Falsey values.
        self.set_group_access(self.vertical, {1: []})
        self.set_group_access(self.html, {2: None})

        verify_all_components_visible_to_all()

    def test_sequential_and_problem_have_group_access(self):
        """ Tests when group_access is set on a few different components. """
        self.set_group_access(self.sequential, {1: [0]})
        # This is a no-op.
        self.set_group_access(self.vertical, {1: []})
        self.set_group_access(self.problem, {2: [3, 4]})
        # get updated sequential/vertical/problem
        self.sequential = self.store.get_item(self.sequential.location)
        self.vertical = self.store.get_item(self.vertical.location)
        self.problem = self.store.get_item(self.problem.location)

        # Note that "has_children_visible_to_specific_partition_groups" only checks immediate children.
        self.assertFalse(utils.has_children_visible_to_specific_partition_groups(self.sequential))
        self.assertTrue(utils.has_children_visible_to_specific_partition_groups(self.vertical))
        self.assertFalse(utils.has_children_visible_to_specific_partition_groups(self.html))
        self.assertFalse(utils.has_children_visible_to_specific_partition_groups(self.problem))

        self.assertTrue(utils.is_visible_to_specific_partition_groups(self.sequential))
        self.assertFalse(utils.is_visible_to_specific_partition_groups(self.vertical))
        self.assertFalse(utils.is_visible_to_specific_partition_groups(self.html))
        self.assertTrue(utils.is_visible_to_specific_partition_groups(self.problem))


class GetUserPartitionInfoTest(ModuleStoreTestCase):
    """
    Tests for utility function that retrieves user partition info
    and formats it for consumption by the editing UI.
    """

    def setUp(self):
        """Create a dummy course. """
        super().setUp()
        self.course = CourseFactory()
        self.block = BlockFactory.create(category="problem", parent_location=self.course.location)

        # Set up some default partitions
        self._set_partitions([
            UserPartition(
                id=0,
                name="Cohort user partition",
                scheme=UserPartition.get_scheme("cohort"),
                description="Cohorted user partition",
                groups=[
                    Group(id=0, name="Group A"),
                    Group(id=1, name="Group B"),
                ],
            ),
            UserPartition(
                id=1,
                name="Random user partition",
                scheme=UserPartition.get_scheme("random"),
                description="Random user partition",
                groups=[
                    Group(id=0, name="Group C"),
                ],
            ),
        ])

    def test_retrieves_partition_info_with_selected_groups(self):
        # Initially, no group access is set on the block, so no groups should
        # be marked as selected.
        expected = [
            {
                "id": 0,
                "name": "Cohort user partition",
                "scheme": "cohort",
                "groups": [
                    {
                        "id": 0,
                        "name": "Group A",
                        "selected": False,
                        "deleted": False,
                    },
                    {
                        "id": 1,
                        "name": "Group B",
                        "selected": False,
                        "deleted": False,
                    },
                ]
            },
            {
                "id": 1,
                "name": "Random user partition",
                "scheme": "random",
                "groups": [
                    {
                        "id": 0,
                        "name": "Group C",
                        "selected": False,
                        "deleted": False,
                    },
                ]
            }
        ]
        self.assertEqual(self._get_partition_info(schemes=["cohort", "random"]), expected)

        # Update group access and expect that now one group is marked as selected.
        self._set_group_access({0: [1]})
        expected[0]["groups"][1]["selected"] = True
        self.assertEqual(self._get_partition_info(schemes=["cohort", "random"]), expected)

    def test_deleted_groups(self):
        # Select a group that is not defined in the partition
        self._set_group_access({0: [3]})

        # Expect that the group appears as selected but is marked as deleted
        partitions = self._get_partition_info()
        groups = partitions[0]["groups"]
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[2], {
            "id": 3,
            "name": "Deleted Group",
            "selected": True,
            "deleted": True
        })

    def test_singular_deleted_group(self):
        """
        Verify that a partition with only one deleted group is
        shown in the partition info with the group marked as deleted
        """
        self._set_partitions([
            UserPartition(
                id=0,
                name="Cohort user partition",
                scheme=UserPartition.get_scheme("cohort"),
                description="Cohorted user partition",
                groups=[],
            ),
        ])
        self._set_group_access({0: [1]})
        partitions = self._get_partition_info()
        groups = partitions[0]["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], {
            "id": 1,
            "name": "Deleted Group",
            "selected": True,
            "deleted": True,
        })

    def test_filter_by_partition_scheme(self):
        partitions = self._get_partition_info(schemes=["random"])
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0]["scheme"], "random")

    def test_exclude_inactive_partitions(self):
        # Include an inactive verification scheme
        self._set_partitions([
            UserPartition(
                id=0,
                name="Cohort user partition",
                scheme=UserPartition.get_scheme("cohort"),
                description="Cohorted user partition",
                groups=[
                    Group(id=0, name="Group A"),
                    Group(id=1, name="Group B"),
                ],
            ),
            UserPartition(
                id=1,
                name="Completely random user partition",
                scheme=UserPartition.get_scheme("random"),
                description="Random user partition",
                groups=[
                    Group(id=0, name="Group C"),
                ],
                active=False,
            ),
        ])

        # Expect that the inactive scheme is excluded from the results
        partitions = self._get_partition_info(schemes=["cohort", "verification"])
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0]["scheme"], "cohort")

    def test_exclude_partitions_with_no_groups(self):
        # The cohort partition has no groups defined
        self._set_partitions([
            UserPartition(
                id=0,
                name="Cohort user partition",
                scheme=UserPartition.get_scheme("cohort"),
                description="Cohorted user partition",
                groups=[],
            ),
            UserPartition(
                id=1,
                name="Completely random user partition",
                scheme=UserPartition.get_scheme("random"),
                description="Random user partition",
                groups=[
                    Group(id=0, name="Group C"),
                ],
            ),
        ])

        # Expect that the partition with no groups is excluded from the results
        partitions = self._get_partition_info(schemes=["cohort", "random"])
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0]["scheme"], "random")

    def _set_partitions(self, partitions):
        """Set the user partitions of the course block. """
        self.course.user_partitions = partitions
        self.course = self.store.update_item(self.course, ModuleStoreEnum.UserID.test)

    def _set_group_access(self, group_access):
        """Set group access of the block. """
        self.block.group_access = group_access
        self.block = self.store.update_item(self.block, ModuleStoreEnum.UserID.test)

    def _get_partition_info(self, schemes=None):
        """Retrieve partition info and selected groups. """
        return utils.get_user_partition_info(self.block, schemes=schemes)


@patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=True)
@mock.patch('olxcleaner.validate')
@ddt.ddt
class ValidateCourseOlxTests(CourseTestCase):
    """Tests for olx validation"""

    def setUp(self):
        super().setUp()
        self.LOGGER = 'cms.djangoapps.contentstore.tasks.LOGGER'
        self.data_dir = path(TEST_DATA_DIR)
        self.toy_course_path = self.data_dir / 'course_ignore'
        self.status = UserTaskStatus.objects.create(
            user=self.user, task_id=str(uuid4()), task_class='sample_task', name='CourseImport', total_steps=4
        )

    def test_with_library_locator(self, mock_olxcleaner_validate):
        """
        Tests that olx is validation is skipped with library locator.
        """
        library_key = LibraryLocator(org='TestOrg', library='TestProbs')
        self.assertTrue(validate_course_olx(library_key, self.toy_course_path, self.status))
        self.assertFalse(mock_olxcleaner_validate.called)

    def test_config_settings_enabled(self, mock_olxcleaner_validate):
        """
        Tests olx validation with config setting is disabled.
        """
        with patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=False):
            self.assertTrue(validate_course_olx(self.course.id, self.toy_course_path, self.status))
            self.assertFalse(mock_olxcleaner_validate.called)

    def test_config_settings_disabled(self, mock_olxcleaner_validate):
        """
        Tests olx validation with config setting is enabled.
        """
        with patch.dict(settings.FEATURES, ENABLE_COURSE_OLX_VALIDATION=True):
            self.assertTrue(validate_course_olx(self.course.id, self.toy_course_path, self.status))
            self.assertTrue(mock_olxcleaner_validate.called)

    def test_exception_during_validation(self, mock_olxcleaner_validate):
        """
        Tests olx validation in case of unexpected error.

        In case of any unexpected exception during the olx validation,
         the course import continues and information is logged on the server.
        """
        mock_olxcleaner_validate.side_effect = Exception
        with mock.patch(self.LOGGER) as patched_log:
            self.assertTrue(validate_course_olx(self.course.id, self.toy_course_path, self.status))
            self.assertTrue(mock_olxcleaner_validate.called)
            patched_log.exception.assert_called_once_with(
                f'Course import {self.course.id}: CourseOlx could not be validated')

    def test_no_errors(self, mock_olxcleaner_validate):
        """
        Tests olx validation with no errors.
        Verify that in case for no validation errors, no artifact object is created.
        """
        mock_olxcleaner_validate.return_value = [
            Mock(),
            Mock(errors=[], return_error=Mock(return_value=False)),
            Mock()
        ]
        self.assertTrue(validate_course_olx(self.course.id, self.toy_course_path, self.status))
        task_artifact = UserTaskArtifact.objects.filter(status=self.status, name='OLX_VALIDATION_ERROR').first()
        self.assertIsNone(task_artifact)
        self.assertTrue(mock_olxcleaner_validate.called)

    @mock.patch('cms.djangoapps.contentstore.tasks.report_error_summary')
    @mock.patch('cms.djangoapps.contentstore.tasks.report_errors')
    def test_creates_artifact(self, mock_report_errors, mock_report_error_summary, mock_olxcleaner_validate):
        """
        Tests olx validation in case of errors.
        Verify that in case of olx validation errors, course import does fail & errors
        are logged in task artifact.
        """
        errors = [Mock(description='DuplicateURLNameError', level_val=3)]

        mock_olxcleaner_validate.return_value = [
            Mock(),
            Mock(errors=errors, return_error=Mock(return_value=True)),
            Mock()
        ]
        mock_report_errors.return_value = [f'ERROR {error.description} found in content' for error in errors]
        mock_report_error_summary.return_value = [f'Errors: {len(errors)}']

        with patch(self.LOGGER) as patched_log:
            self.assertFalse(validate_course_olx(self.course.id, self.toy_course_path, self.status))
            patched_log.error.assert_called_once_with(
                f'Course import {self.course.id}: CourseOlx validation failed.')

        task_artifact = UserTaskArtifact.objects.filter(status=self.status, name='OLX_VALIDATION_ERROR').first()
        self.assertIsNotNone(task_artifact)

    def test_validate_calls_with(self, mock_olxcleaner_validate):
        """
        Tests that olx library is called with expected keyword arguments.
        """
        allowed_xblocks = ALL_ALLOWED_XBLOCKS
        steps = 2
        ignore = ['edx-xblock']
        mock_olxcleaner_validate.return_value = [Mock(), Mock(errors=[], return_error=Mock(return_value=False)), Mock()]

        with override_settings(COURSE_OLX_VALIDATION_STAGE=steps, COURSE_OLX_VALIDATION_IGNORE_LIST=ignore):
            validate_course_olx(self.course.id, self.toy_course_path, self.status)
            mock_olxcleaner_validate.assert_called_with(
                filename=self.toy_course_path,
                steps=steps,
                ignore=ignore,
                allowed_xblocks=allowed_xblocks
            )


class DetermineLabelTests(TestCase):
    """Tests for xblock Title"""

    def test_html_replaced_with_text_for_none(self):
        """
        Tests that display names for "html" xblocks are replaced with "Text" when the display name is otherwise unset.
        """
        display_name = None
        block_type = "html"
        result = utils.determine_label(display_name, block_type)
        self.assertEqual(result, "Text")

    def test_html_replaced_with_text_for_empty(self):
        """
        Tests that display names for "html" xblocks are replaced with "Text" when the display name is empty.
        """
        display_name = ""
        block_type = "html"
        result = utils.determine_label(display_name, block_type)
        self.assertEqual(result, "Text")

    def test_set_titles_not_replaced(self):
        """
        Tests that display names for "html" xblocks are not replaced with "Text" when the display name is set.
        """
        display_name = "Something"
        block_type = "html"
        result = utils.determine_label(display_name, block_type)
        self.assertEqual(result, "Something")

    def test_non_html_blocks_titles_not_replaced(self):
        """
        Tests that display names for non-"html" xblocks are not replaced with "Text" when the display name is set.
        """
        display_name = None
        block_type = "something else"
        result = utils.determine_label(display_name, block_type)
        self.assertEqual(result, "something else")


class AuthorizeStaffTestCase():
    """
    Test that only staff roles can access an API endpoint.
    """
    @classmethod
    def get_course_key_string(cls):
        return 'course-v1:edX+ToyX+Toy_Course'

    @classmethod
    def get_other_course_key_string(cls):
        return 'course-v1:edX+ToyX_Other_Course+Toy_Course'

    def setUp(self):
        super().setUp()
        self.course_key = self.get_course_key_string()
        self.other_course_key = self.get_other_course_key_string()
        self.course = self.create_course_from_course_key(CourseKey.from_string(self.course_key))
        self.other_course = self.create_course_from_course_key(CourseKey.from_string(self.other_course_key))
        self.password = 'password'
        self.student = UserFactory.create(username='student', password=self.password)
        self.global_staff = GlobalStaffFactory(
            username='global-staff', password=self.password
        )
        self.course_instructor = InstructorFactory(
            username='instructor',
            password=self.password,
            course_key=self.course.id,
        )
        self.other_course_instructor = InstructorFactory(
            username='other-course-instructor',
            password=self.password,
            course_key=self.other_course.id,
        )

    @classmethod
    def create_course_from_course_key(cls, course_key):
        return CourseFactory.create(
            org=course_key.org,
            course=course_key.course,
            run=course_key.run
        )

    def make_request(self, course_id=None, data=None):
        raise NotImplementedError

    def get_url(self, course_key):
        raise NotImplementedError

    def test_student(self, expect_status=status.HTTP_403_FORBIDDEN):
        self.client.login(username=self.student.username, password=self.password)
        response = self.make_request()
        assert response.status_code == expect_status

    def test_instructor_in_another_course(self, expect_status=status.HTTP_403_FORBIDDEN):
        self.client.login(
            username=self.other_course_instructor.username,
            password=self.password
        )
        response = self.make_request()
        assert response.status_code == expect_status

    def test_global_staff(self, expect_status=status.HTTP_200_OK):
        self.client.login(username=self.global_staff.username, password=self.password)
        response = self.make_request()
        assert response.status_code == expect_status
        return response

    def test_course_instructor(self, expect_status=status.HTTP_200_OK):
        self.client.login(username=self.course_instructor.username, password=self.password)
        response = self.make_request()
        assert response.status_code == expect_status
        return response


class UpdateCourseDetailsTests(ModuleStoreTestCase):
    """
    Unit tests for the `update_course_details` utility function.
    """

    class Request:
        """
        Basic Python class that mocks the required structural components of a WSGIRequest object instance, used in the
        functions under test.
        """
        def __init__(self):
            self.user = UserFactory.create(username="course_staff", password="password")

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @patch.dict("django.conf.settings.FEATURES", {
        "ENABLE_PREREQUISITE_COURSES": False,
    })
    @override_settings(ENTRANCE_EXAMS=False)
    @patch("cms.djangoapps.contentstore.utils.CourseDetails.update_from_json")
    def test_update_course_details_self_paced(self, mock_update):
        """
        This test ensures that expected updates and validation occur on a course update before the settings payload
        is commit to Mongo. This tests checks that we're removing bad certificates display behavior and availability
        settings before we process the settings updates.
        """
        mock_request = self.Request()
        payload = {
            "certificate_available_date": "2024-08-01T00:00:00Z",
            "certificates_display_behavior": "end_with_date",
            "self_paced": True,
        }
        expected_payload = {
            "certificate_available_date": None,
            "certificates_display_behavior": "early_no_info",
            "self_paced": True,
        }

        utils.update_course_details(mock_request, self.course.id, payload, None)
        mock_update.assert_called_once_with(self.course.id, expected_payload, mock_request.user)

    @patch.dict("django.conf.settings.FEATURES", {
        "ENABLE_PREREQUISITE_COURSES": False,
    })
    @override_settings(ENTRANCE_EXAMS=False)
    @patch("cms.djangoapps.contentstore.utils.CourseDetails.update_from_json")
    def test_update_course_details_instructor_paced(self, mock_update):
        """
        This test ensures that expected updates and validation occur on a course update before the settings payload
        is commit to Mongo. This test checks that we don't modify any of the incoming settings when a course is
        instructor-paced.
        """
        mock_request = self.Request()
        payload = {
            "certificate_available_date": "2024-08-01T00:00:00Z",
            "certificates_display_behavior": "end_with_date",
            "self_paced": False,
        }

        utils.update_course_details(mock_request, self.course.id, payload, None)
        mock_update.assert_called_once_with(self.course.id, payload, mock_request.user)


@override_waffle_flag(ENABLE_NOTIFICATIONS, active=True)
class CourseUpdateNotificationTests(OpenEdxEventsTestMixin, ModuleStoreTestCase):
    """
    Unit tests for the course_update notification.
    """
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.learning.course.notification.requested.v1",
    ]

    def setUp(self):
        """
        Setup the test environment.
        """
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory.create(org='testorg', number='testcourse', run='testrun')

    def test_course_update_notification_sent(self):
        """
        Test that the course_update notification is sent.
        """
        user = UserFactory()
        CourseEnrollment.enroll(user=user, course_key=self.course.id)
        assert Notification.objects.all().count() == 0
        content = "<p>content</p><img src='' />"
        send_course_update_notification(self.course.id, content, self.user)
        assert Notification.objects.all().count() == 1
        notification = Notification.objects.first()
        assert notification.content == "<p><strong>content</strong></p>"

    def test_if_content_is_plain_text(self):
        """
        Test that the course_update notification is sent.
        """
        user = UserFactory()
        CourseEnrollment.enroll(user=user, course_key=self.course.id)
        assert Notification.objects.all().count() == 0
        content = "<p>content<p>Sub content</p><h1>heading</h1></p><img src='' />"
        send_course_update_notification(self.course.id, content, self.user)
        assert Notification.objects.all().count() == 1
        notification = Notification.objects.first()
        assert notification.content == "<p><strong>content Sub content heading</strong></p>"

    def test_if_html_unescapes(self):
        """
        Tests if html unescapes when creating content of course update notification
        """
        user = UserFactory()
        CourseEnrollment.enroll(user=user, course_key=self.course.id)
        assert Notification.objects.all().count() == 0
        content = "<p>&lt;p&gt; &amp;nbsp;&lt;/p&gt;<br />"\
                  "&lt;p&gt;abcd&lt;/p&gt;<br />"\
                  "&lt;p&gt;&amp;nbsp;&lt;/p&gt;<br /></p>"
        send_course_update_notification(self.course.id, content, self.user)
        assert Notification.objects.all().count() == 1
        notification = Notification.objects.first()
        assert notification.content == "<p><strong>abcd</strong></p>"
