# pylint: disable=missing-module-docstring
from datetime import datetime
from unittest.mock import Mock, patch  # lint-amnesty, pylint: disable=wrong-import-order

from django.test import RequestFactory
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import GlobalStaffFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from openedx.features.content_type_gating.helpers import CONTENT_GATING_PARTITION_ID, FULL_ACCESS, LIMITED_ACCESS
from openedx.features.content_type_gating.models import ContentTypeGatingConfig
from openedx.features.content_type_gating.partitions import create_content_gating_partition
from xmodule.partitions.partitions import UserPartitionError  # lint-amnesty, pylint: disable=wrong-import-order


class TestContentTypeGatingPartition(CacheIsolationTestCase):  # pylint: disable=missing-class-docstring
    def setUp(self):  # pylint: disable=super-method-not-called
        self.course_key = CourseKey.from_string('course-v1:test+course+key')
        CourseOverviewFactory.create(id=self.course_key)

    def test_create_content_gating_partition_happy_path(self):

        mock_course = Mock(id=self.course_key, user_partitions={})
        CourseModeFactory.create(course_id=mock_course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=mock_course.id, mode_slug='verified')
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        with patch(
            'openedx.features.content_type_gating.partitions.ContentTypeGatingPartitionScheme.create_user_partition'
        ) as mock_create:
            partition = create_content_gating_partition(mock_course)
            assert partition == mock_create.return_value

    def test_create_content_gating_partition_override_only(self):
        mock_course = Mock(id=self.course_key, user_partitions={})
        ContentTypeGatingConfig.objects.create(enabled=False, studio_override_enabled=True)

        partition = create_content_gating_partition(mock_course)
        assert partition is not None

    def test_create_content_gating_partition_disabled(self):
        mock_course = Mock(id=self.course_key, user_partitions={})
        ContentTypeGatingConfig.objects.create(enabled=False, studio_override_enabled=False)

        partition = create_content_gating_partition(mock_course)
        assert partition is None

    def test_create_content_gating_partition_no_scheme_installed(self):
        mock_course = Mock(id=self.course_key, user_partitions={})
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        with patch(
            'openedx.features.content_type_gating.partitions.UserPartition.get_scheme', side_effect=UserPartitionError
        ):
            partition = create_content_gating_partition(mock_course)

        assert partition is None

    def test_create_content_gating_partition_partition_id_used(self):
        mock_course = Mock(
            id=self.course_key, user_partitions={Mock(name='partition', id=CONTENT_GATING_PARTITION_ID): object()}
        )
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))

        with patch('openedx.features.content_type_gating.partitions.LOG') as mock_log:
            partition = create_content_gating_partition(mock_course)
            mock_log.warning.assert_called()
        assert partition is None

    def test_access_denied_fragment_for_masquerading(self):
        """
        Test that a global staff sees gated content flag when viewing course as `Learner in Limited Access`
        Note: Global staff doesn't require to be enrolled in course.
        """
        mock_request = RequestFactory().get('/')
        mock_course = Mock(id=self.course_key, user_partitions={})
        mock_block = Mock(scope_ids=Mock(usage_id=Mock(course_key=mock_course.id)))
        mock_course_masquerade = Mock(
            role='student',
            user_partition_id=CONTENT_GATING_PARTITION_ID,
            group_id=LIMITED_ACCESS.id,
            user_name=None
        )
        CourseModeFactory.create(course_id=mock_course.id, mode_slug='verified')

        global_staff = GlobalStaffFactory.create()
        ContentTypeGatingConfig.objects.create(enabled=False, studio_override_enabled=True)

        partition = create_content_gating_partition(mock_course)

        with patch(
            'crum.get_current_request',
            return_value=mock_request
        ):
            fragment = partition.access_denied_fragment(mock_block, global_staff, LIMITED_ACCESS, [FULL_ACCESS])

        assert fragment is not None

    def test_access_denied_fragment_for_full_access_users(self):
        """
        Test that Full Access users do not see the access_denied_fragment or access_denied_message
        """
        mock_request = RequestFactory().get('/')
        mock_course = Mock(id=self.course_key, user_partitions={})
        mock_block = Mock(scope_ids=Mock(usage_id=Mock(course_key=mock_course.id)))

        CourseModeFactory.create(course_id=mock_course.id, mode_slug='verified')

        global_staff = GlobalStaffFactory.create()
        ContentTypeGatingConfig.objects.create(enabled=False, studio_override_enabled=True)

        partition = create_content_gating_partition(mock_course)

        with patch(
            'crum.get_current_request',
            return_value=mock_request
        ):
            fragment = partition.access_denied_fragment(mock_block, global_staff, FULL_ACCESS, 'test_allowed_group')
            assert fragment is None
            message = partition.access_denied_message(
                mock_block.scope_ids.usage_id, global_staff, FULL_ACCESS, 'test_allowed_group'
            )
            assert message is None

    def test_access_denied_fragment_for_null_request(self):
        """
        Verifies the access denied fragment is visible when HTTP request is not available.

        Given the HTTP request instance is None
        Then set the mobile_app context variable to False
        And the fragment should be created successfully
        """
        mock_request = None
        mock_course = Mock(id=self.course_key, user_partitions={})
        mock_block = Mock(scope_ids=Mock(usage_id=Mock(course_key=mock_course.id)))
        CourseModeFactory.create(course_id=mock_course.id, mode_slug='audit')
        CourseModeFactory.create(course_id=mock_course.id, mode_slug='verified')
        global_staff = GlobalStaffFactory.create()
        ContentTypeGatingConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1))
        partition = create_content_gating_partition(mock_course)

        with patch(
            'crum.get_current_request',
            return_value=mock_request
        ):
            fragment = partition.access_denied_fragment(mock_block, global_staff, LIMITED_ACCESS, [FULL_ACCESS])

        assert fragment is not None
