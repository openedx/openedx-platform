"""
Test the partitions and partitions service

"""


from unittest.mock import patch
import django.test

from lms.djangoapps.courseware.tests.test_masquerade import StaffMasqueradeTestCase
from openedx.core.djangoapps.user_api.partition_schemes import RandomUserPartitionScheme
from openedx.core.djangolib.testing.utils import skip_unless_lms
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import ToyCourseFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import Group, UserPartition, UserPartitionError  # lint-amnesty, pylint: disable=wrong-import-order

from ..cohorts import add_user_to_cohort, get_course_cohorts, remove_user_from_cohort
from ..models import CourseUserGroupPartitionGroup
from ..partition_scheme import CohortPartitionScheme, get_cohorted_user_partition
from ..views import link_cohort_to_partition_group, unlink_cohort_partition_group
from .helpers import CohortFactory, config_course_cohorts


class TestCohortPartitionScheme(ModuleStoreTestCase):
    """
    Test the logic for linking a user to a partition group based on their cohort.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Regenerate a course with cohort configuration, partition and groups,
        and a student for each test.
        """
        super().setUp()

        self.course_key = ToyCourseFactory.create().id
        self.course = modulestore().get_course(self.course_key)
        config_course_cohorts(self.course, is_cohorted=True)

        self.groups = [Group(10, 'Group 10'), Group(20, 'Group 20')]
        self.user_partition = UserPartition(
            0,
            'Test Partition',
            'for testing purposes',
            self.groups,
            scheme=CohortPartitionScheme
        )
        self.student = UserFactory.create()

    def assert_student_in_group(self, group, partition=None):
        """
        Utility for checking that our test student comes up as assigned to the
        specified partition (or, if None, no partition at all)
        """
        assert CohortPartitionScheme.get_group_for_user(self.course_key,
                                                        self.student,
                                                        (partition or self.user_partition),
                                                        use_cached=False) == group

    def test_student_cohort_assignment(self):
        """
        Test that the CohortPartitionScheme continues to return the correct
        group for a student as the student is moved in and out of different
        cohorts.
        """
        first_cohort, second_cohort = [
            CohortFactory(course_id=self.course_key) for _ in range(2)
        ]

        # place student 0 into first cohort
        add_user_to_cohort(first_cohort, self.student.username)
        self.assert_student_in_group(None)

        # link first cohort to group 0 in the partition
        link_cohort_to_partition_group(
            first_cohort,
            self.user_partition.id,
            self.groups[0].id,
        )
        # link second cohort to to group 1 in the partition
        link_cohort_to_partition_group(
            second_cohort,
            self.user_partition.id,
            self.groups[1].id,
        )
        self.assert_student_in_group(self.groups[0])

        # move student from first cohort to second cohort
        add_user_to_cohort(second_cohort, self.student.username)
        self.assert_student_in_group(self.groups[1])

        # move the student out of the cohort
        remove_user_from_cohort(second_cohort, self.student.username)
        self.assert_student_in_group(None)

    def test_cohort_partition_group_assignment(self):
        """
        Test that the CohortPartitionScheme returns the correct group for a
        student in a cohort when the cohort link is created / moved / deleted.
        """
        test_cohort = CohortFactory(course_id=self.course_key)

        # assign user to cohort (but cohort isn't linked to a partition group yet)
        add_user_to_cohort(test_cohort, self.student.username)
        # scheme should not yet find any link
        self.assert_student_in_group(None)

        # link cohort to group 0
        link_cohort_to_partition_group(
            test_cohort,
            self.user_partition.id,
            self.groups[0].id,
        )
        # now the scheme should find a link
        self.assert_student_in_group(self.groups[0])

        # link cohort to group 1 (first unlink it from group 0)
        unlink_cohort_partition_group(test_cohort)
        link_cohort_to_partition_group(
            test_cohort,
            self.user_partition.id,
            self.groups[1].id,
        )
        # scheme should pick up the link
        self.assert_student_in_group(self.groups[1])

        # unlink cohort from anywhere
        unlink_cohort_partition_group(
            test_cohort,
        )
        # scheme should now return nothing
        self.assert_student_in_group(None)

    def test_student_lazily_assigned(self):
        """
        Test that the lazy assignment of students to cohorts works
        properly when accessed via the CohortPartitionScheme.
        """
        # don't assign the student to any cohort initially
        self.assert_student_in_group(None)

        # get the default cohort, which is automatically created
        # during the `get_course_cohorts` API call if it doesn't yet exist
        cohort = get_course_cohorts(self.course)[0]

        # map that cohort to a group in our partition
        link_cohort_to_partition_group(
            cohort,
            self.user_partition.id,
            self.groups[0].id,
        )

        # The student will be lazily assigned to the default cohort
        # when CohortPartitionScheme.get_group_for_user makes its internal
        # call to cohorts.get_cohort.
        self.assert_student_in_group(self.groups[0])

    def setup_student_in_group_0(self):
        """
        Utility to set up a cohort, add our student to the cohort, and link
        the cohort to self.groups[0]
        """
        test_cohort = CohortFactory(course_id=self.course_key)

        # link cohort to group 0
        link_cohort_to_partition_group(
            test_cohort,
            self.user_partition.id,
            self.groups[0].id,
        )
        # place student into cohort
        add_user_to_cohort(test_cohort, self.student.username)
        # check link is correct
        self.assert_student_in_group(self.groups[0])

    def test_partition_changes_nondestructive(self):
        """
        If the name of a user partition is changed, or a group is added to the
        partition, links from cohorts do not break.

        If the name of a group is changed, links from cohorts do not break.
        """
        self.setup_student_in_group_0()

        # to simulate a non-destructive configuration change on the course, create
        # a new partition with the same id and scheme but with groups renamed and
        # a group added
        new_groups = [Group(10, 'New Group 10'), Group(20, 'New Group 20'), Group(30, 'New Group 30')]
        new_user_partition = UserPartition(
            0,  # same id
            'Different Partition',
            'dummy',
            new_groups,
            scheme=CohortPartitionScheme,
        )
        # the link should still work
        self.assert_student_in_group(new_groups[0], new_user_partition)

    def test_missing_group(self):
        """
        If the group is deleted (or its id is changed), there's no referential
        integrity enforced, so any references from cohorts to that group will be
        lost.  A warning should be logged when links are found from cohorts to
        groups that no longer exist.
        """
        self.setup_student_in_group_0()

        # to simulate a destructive change on the course, create a new partition
        # with the same id, but different group ids.
        new_user_partition = UserPartition(
            0,  # same id
            'Another Partition',
            'dummy',
            [Group(11, 'Not Group 10'), Group(21, 'Not Group 20')],  # different ids
            scheme=CohortPartitionScheme,
        )
        # the partition will be found since it has the same id, but the group
        # ids aren't present anymore, so the scheme returns None (and logs a
        # warning)
        with patch('openedx.core.djangoapps.course_groups.partition_scheme.log') as mock_log:
            self.assert_student_in_group(None, new_user_partition)
            assert mock_log.warning.called
            self.assertRegex(mock_log.warning.call_args[0][0], 'group not found')

    def test_missing_partition(self):
        """
        If the user partition is deleted (or its id is changed), there's no
        referential integrity enforced, so any references from cohorts to that
        partition's groups will be lost.  A warning should be logged when links
        are found from cohorts to partitions that do not exist.
        """
        self.setup_student_in_group_0()

        # to simulate another destructive change on the course, create a new
        # partition with a different id, but using the same groups.
        new_user_partition = UserPartition(
            1,  # different id
            'Moved Partition',
            'dummy',
            [Group(10, 'Group 10'), Group(20, 'Group 20')],  # same ids
            scheme=CohortPartitionScheme,
        )
        # the partition will not be found even though the group ids match, so the
        # scheme returns None (and logs a warning).
        with patch('openedx.core.djangoapps.course_groups.partition_scheme.log') as mock_log:
            self.assert_student_in_group(None, new_user_partition)
            assert mock_log.warning.called
            self.assertRegex(mock_log.warning.call_args[0][0], 'partition mismatch')


class TestExtension(django.test.TestCase):
    """
    Ensure that the scheme extension is correctly plugged in (via entry point
    in pyproject.toml)
    """

    def test_get_scheme(self):
        assert UserPartition.get_scheme('cohort') == CohortPartitionScheme
        with self.assertRaisesRegex(UserPartitionError, 'Unrecognized scheme'):
            UserPartition.get_scheme('other')


class TestGetCohortedUserPartition(ModuleStoreTestCase):
    """
    Test that `get_cohorted_user_partition` returns the first user_partition with scheme `CohortPartitionScheme`.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        """
        Regenerate a course with cohort configuration, partition and groups,
        and a student for each test.
        """
        super().setUp()
        self.course_key = ToyCourseFactory.create().id
        self.course = modulestore().get_course(self.course_key)
        self.student = UserFactory.create()

        self.random_user_partition = UserPartition(
            1,
            'Random Partition',
            'Should not be returned',
            [Group(0, 'Group 0'), Group(1, 'Group 1')],
            scheme=RandomUserPartitionScheme
        )

        self.cohort_user_partition = UserPartition(
            0,
            'Cohort Partition 1',
            'Should be returned',
            [Group(10, 'Group 10'), Group(20, 'Group 20')],
            scheme=CohortPartitionScheme
        )

        self.second_cohort_user_partition = UserPartition(
            2,
            'Cohort Partition 2',
            'Should not be returned',
            [Group(10, 'Group 10'), Group(1, 'Group 1')],
            scheme=CohortPartitionScheme
        )

    def test_returns_first_cohort_user_partition(self):
        """
        Test get_cohorted_user_partition returns first user_partition with scheme `CohortPartitionScheme`.
        """
        self.course.user_partitions.append(self.random_user_partition)
        self.course.user_partitions.append(self.cohort_user_partition)
        self.course.user_partitions.append(self.second_cohort_user_partition)
        assert self.cohort_user_partition == get_cohorted_user_partition(self.course)

    def test_no_cohort_user_partitions(self):
        """
        Test get_cohorted_user_partition returns None when there are no cohorted user partitions.
        """
        self.course.user_partitions.append(self.random_user_partition)
        assert get_cohorted_user_partition(self.course) is None


class TestMasqueradedGroup(StaffMasqueradeTestCase):
    """
    Check for staff being able to masquerade as belonging to a group.
    """
    def setUp(self):
        super().setUp()
        self.user_partition = UserPartition(
            0, 'Test User Partition', '',
            [Group(0, 'Group 1'), Group(1, 'Group 2')],
            scheme_id='cohort'
        )
        self.course.user_partitions.append(self.user_partition)
        modulestore().update_item(self.course, self.test_user.id)

    def _verify_masquerade_for_group(self, group):
        """
        Verify that the masquerade works for the specified group id.
        """
        self.ensure_masquerade_as_group_member(
            self.user_partition.id,
            group.id if group is not None else None
        )

        scheme = self.user_partition.scheme
        assert scheme.get_group_for_user(self.course.id, self.test_user, self.user_partition) == group

    def _verify_masquerade_for_all_groups(self):
        """
        Verify that the staff user can masquerade as being in all groups
        as well as no group.
        """
        self._verify_masquerade_for_group(self.user_partition.groups[0])
        self._verify_masquerade_for_group(self.user_partition.groups[1])
        self._verify_masquerade_for_group(None)

    @skip_unless_lms
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_group_masquerade(self):
        """
        Tests that a staff member can masquerade as being in a particular group.
        """
        self._verify_masquerade_for_all_groups()

    @skip_unless_lms
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_group_masquerade_with_cohort(self):
        """
        Tests that a staff member can masquerade as being in a particular group
        when that staff member also belongs to a cohort with a corresponding
        group.
        """
        self.course.cohort_config = {'cohorted': True}
        modulestore().update_item(self.course, self.test_user.id)
        cohort = CohortFactory.create(course_id=self.course.id, users=[self.test_user])
        CourseUserGroupPartitionGroup(
            course_user_group=cohort,
            partition_id=self.user_partition.id,
            group_id=self.user_partition.groups[0].id
        ).save()

        # When the staff user is masquerading as being in a None group
        # (within an existent UserPartition), we should treat that as
        # an explicit None, not defaulting to the user's cohort's
        # partition group.
        self._verify_masquerade_for_all_groups()
