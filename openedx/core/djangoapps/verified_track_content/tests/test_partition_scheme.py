"""
Tests for verified_track_content/partition_scheme.py.
"""


from datetime import datetime, timedelta

from zoneinfo import ZoneInfo
import pytest
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.partitions.partitions import MINIMUM_UNUSED_PARTITION_ID, UserPartition, ReadOnlyUserPartitionError  # lint-amnesty, pylint: disable=wrong-import-order

from ..partition_scheme import ENROLLMENT_GROUP_IDS, EnrollmentTrackPartitionScheme, EnrollmentTrackUserPartition


class EnrollmentTrackUserPartitionTest(SharedModuleStoreTestCase):
    """
    Tests for the custom EnrollmentTrackUserPartition (dynamic groups).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_only_default_mode(self):
        partition = create_enrollment_track_partition(self.course)
        groups = partition.groups
        assert 1 == len(groups)
        assert 'Audit' == groups[0].name

    def test_multiple_groups(self):
        create_mode(self.course, CourseMode.AUDIT, "Audit Enrollment Track", min_price=0)
        # Note that the verified mode is expired-- this is intentional.
        create_mode(
            self.course, CourseMode.VERIFIED, "Verified Enrollment Track", min_price=1,
            expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=-1)
        )
        # Note that the credit mode is not selectable-- this is intentional so we
        # can test that it is filtered out.
        create_mode(self.course, CourseMode.CREDIT_MODE, "Credit Mode", min_price=2)

        partition = create_enrollment_track_partition(self.course)
        groups = partition.groups
        assert 2 == len(groups)
        assert self.get_group_by_name(partition, 'Audit Enrollment Track') is not None
        assert self.get_group_by_name(partition, 'Verified Enrollment Track') is not None

    def test_to_json_supported(self):
        user_partition_json = create_enrollment_track_partition(self.course).to_json()
        assert 'Test Enrollment Track Partition' == user_partition_json['name']
        assert 'enrollment_track' == user_partition_json['scheme']
        assert 'Test partition for segmenting users by enrollment track' == user_partition_json['description']

    def test_from_json_not_supported(self):
        user_partition_json = create_enrollment_track_partition(self.course).to_json()
        with pytest.raises(ReadOnlyUserPartitionError):
            UserPartition.from_json(user_partition_json)

    def test_group_ids(self):
        """
        Test that group IDs are all less than MINIMUM_UNUSED_PARTITION_ID (to avoid overlapping
        with group IDs associated with cohort and random user partitions).
        """
        for mode in ENROLLMENT_GROUP_IDS:
            assert ENROLLMENT_GROUP_IDS[mode]['id'] < MINIMUM_UNUSED_PARTITION_ID

    @staticmethod
    def get_group_by_name(partition, name):
        """
        Return the group in the EnrollmentTrackUserPartition with the given name.
        If no such group exists, returns `None`.
        """
        for group in partition.groups:
            if group.name == name:
                return group
        return None


class EnrollmentTrackPartitionSchemeTest(SharedModuleStoreTestCase):
    """
    Tests for EnrollmentTrackPartitionScheme.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.student = UserFactory()

    def test_get_scheme(self):
        """
        Ensure that the scheme extension is correctly plugged in (via entry point in setup.py)
        """
        assert UserPartition.get_scheme('enrollment_track') == EnrollmentTrackPartitionScheme

    def test_create_user_partition(self):
        user_partition = UserPartition.get_scheme('enrollment_track').create_user_partition(
            301, "partition", "test partition", parameters={"course_id": str(self.course.id)}
        )
        assert isinstance(user_partition, EnrollmentTrackUserPartition)
        assert user_partition.name == 'partition'

        groups = user_partition.groups
        assert 1 == len(groups)
        assert 'Audit' == groups[0].name

    def test_not_enrolled(self):
        assert self._get_user_group() is None

    def test_default_enrollment(self):
        CourseEnrollment.enroll(self.student, self.course.id)
        assert 'Audit' == self._get_user_group().name

    def test_enrolled_in_nonexistent_mode(self):
        CourseEnrollment.enroll(self.student, self.course.id, mode=CourseMode.VERIFIED)
        assert 'Audit' == self._get_user_group().name

    def test_enrolled_in_verified(self):
        create_mode(self.course, CourseMode.VERIFIED, "Verified Enrollment Track", min_price=1)
        CourseEnrollment.enroll(self.student, self.course.id, mode=CourseMode.VERIFIED)
        assert 'Verified Enrollment Track' == self._get_user_group().name

    def test_enrolled_in_expired(self):
        create_mode(
            self.course, CourseMode.VERIFIED, "Verified Enrollment Track",
            min_price=1, expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=-1)
        )
        CourseEnrollment.enroll(self.student, self.course.id, mode=CourseMode.VERIFIED)
        assert 'Verified Enrollment Track' == self._get_user_group().name

    def test_enrolled_in_non_selectable(self):
        create_mode(self.course, CourseMode.CREDIT_MODE, "Credit Enrollment Track", min_price=1)
        CourseEnrollment.enroll(self.student, self.course.id, mode=CourseMode.CREDIT_MODE)

        # The default mode is returned because Credit mode is filtered out, and no verified mode exists.
        assert 'Audit' == self._get_user_group().name

        # Now create a verified mode and check that it is returned for the learner enrolled in Credit.
        create_mode(self.course, CourseMode.VERIFIED, "Verified Enrollment Track", min_price=1)
        assert 'Verified Enrollment Track' == self._get_user_group().name

    def test_credit_after_upgrade_deadline(self):
        create_mode(self.course, CourseMode.CREDIT_MODE, "Credit Enrollment Track", min_price=1)
        CourseEnrollment.enroll(self.student, self.course.id, mode=CourseMode.CREDIT_MODE)

        # Create a verified mode and check that it is returned for the learner enrolled in Credit.
        # Make the mode "expired" to ensure that credit users can still see verified-only content after
        # the upgrade deadline has passed (see EDUCATOR-1511 for why this matters).
        create_mode(
            self.course, CourseMode.VERIFIED, "Verified Enrollment Track", min_price=1,
            expiration_datetime=datetime.now(ZoneInfo("UTC")) + timedelta(days=-1)
        )
        assert 'Verified Enrollment Track' == self._get_user_group().name

    def _get_user_group(self):
        """
        Gets the group the user is assigned to.
        """
        user_partition = create_enrollment_track_partition(self.course)
        return user_partition.scheme.get_group_for_user(self.course.id, self.student, user_partition)


def create_enrollment_track_partition(course):
    """
    Create an EnrollmentTrackUserPartition instance for the given course.
    """
    enrollment_track_scheme = UserPartition.get_scheme("enrollment_track")
    partition = enrollment_track_scheme.create_user_partition(
        id=1,
        name="Test Enrollment Track Partition",
        description="Test partition for segmenting users by enrollment track",
        parameters={"course_id": str(course.id)}
    )
    return partition


def create_mode(course, mode_slug, mode_name, min_price=0, expiration_datetime=None):
    """
    Create a new course mode for the given course.
    """
    return CourseMode.objects.get_or_create(
        course_id=course.id,
        mode_display_name=mode_name,
        mode_slug=mode_slug,
        min_price=min_price,
        suggested_prices='',
        _expiration_datetime=expiration_datetime,
        currency='usd'
    )
