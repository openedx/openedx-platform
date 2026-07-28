"""
Tests for serializers for the Mobile Course Info
"""
from typing import Dict, List, Tuple, Union  # noqa: UP035
from unittest.mock import MagicMock, Mock, patch

import ddt
from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.mobile_api.course_info.serializers import CourseAccessSerializer, CourseInfoOverviewSerializer
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory


@ddt.ddt
class TestCourseAccessSerializer(TestCase):
    """
    Tests for the CourseAccessSerializer.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseOverviewFactory()

    @ddt.data(
        ([{'course_id': {}}], True),
        ([], False),
    )
    @ddt.unpack
    @patch('lms.djangoapps.mobile_api.course_info.serializers.get_pre_requisite_courses_not_completed')
    def test_has_unmet_prerequisites(
        self,
        mock_return_value: List[Dict],  # noqa: UP006
        has_unmet_prerequisites: bool,
        mock_get_prerequisites: MagicMock,
    ) -> None:
        mock_get_prerequisites.return_value = mock_return_value

        output_data = CourseAccessSerializer({
            'user': self.user,
            'course': self.course,
            'course_id': self.course.id,
        }).data

        self.assertEqual(output_data['has_unmet_prerequisites'], has_unmet_prerequisites)  # noqa: PT009
        mock_get_prerequisites.assert_called_once_with(self.user, [self.course.id])

    @ddt.data(
        (True, False),
        (False, True),
    )
    @ddt.unpack
    @patch('lms.djangoapps.mobile_api.course_info.serializers.check_course_open_for_learner')
    def test_is_too_early(
        self,
        mock_return_value: bool,
        is_too_early: bool,
        mock_check_course_open: MagicMock,
    ) -> None:
        mock_check_course_open.return_value = mock_return_value

        output_data = CourseAccessSerializer({
            'user': self.user,
            'course': self.course,
            'course_id': self.course.id
        }).data

        self.assertEqual(output_data['is_too_early'], is_too_early)  # noqa: PT009
        mock_check_course_open.assert_called_once_with(self.user, self.course)

    @ddt.data(
        ((False, False, False), False),
        ((True, True, True), True),
        ((True, False, False), True),
    )
    @ddt.unpack
    @patch('lms.djangoapps.mobile_api.course_info.serializers.administrative_accesses_to_course_for_user')
    def test_is_staff(
        self,
        mock_return_value: Tuple[bool],  # noqa: UP006
        is_staff: bool,
        mock_administrative_access: MagicMock,
    ) -> None:
        mock_administrative_access.return_value = mock_return_value

        output_data = CourseAccessSerializer({
            'user': self.user,
            'course': self.course,
            'course_id': self.course.id
        }).data

        self.assertEqual(output_data['is_staff'], is_staff)  # noqa: PT009
        mock_administrative_access.assert_called_once_with(self.user, self.course.id)

    @ddt.data(None, 'mocked_user_course_expiration_date')
    @patch('lms.djangoapps.mobile_api.course_info.serializers.get_user_course_expiration_date')
    def test_get_audit_access_expires(
        self,
        mock_return_value: Union[str, None],  # noqa: UP007
        mock_get_user_course_expiration_date: MagicMock,
    ) -> None:
        mock_get_user_course_expiration_date.return_value = mock_return_value

        output_data = CourseAccessSerializer({
            'user': self.user,
            'course': self.course,
            'course_id': self.course.id
        }).data

        self.assertEqual(output_data['audit_access_expires'], mock_return_value)  # noqa: PT009
        mock_get_user_course_expiration_date.assert_called_once_with(self.user, self.course)

    @patch('lms.djangoapps.mobile_api.course_info.serializers.has_access')
    def test_get_courseware_access(self, mock_has_access: MagicMock) -> None:
        mocked_access = {
            'has_access': True,
            'error_code': None,
            'developer_message': None,
            'user_message': None,
            'additional_context_user_message': None,
            'user_fragment': None
        }
        mock_has_access.return_value = Mock(to_json=Mock(return_value=mocked_access))

        output_data = CourseAccessSerializer({
            'user': self.user,
            'course': self.course,
            'course_id': self.course.id
        }).data

        self.assertDictEqual(output_data['courseware_access'], mocked_access)  # noqa: PT009
        mock_has_access.assert_called_once_with(self.user, 'load_mobile', self.course)
        mock_has_access.return_value.to_json.assert_called_once_with()


class TestCourseInfoOverviewSerializer(TestCase):
    """
    Tests for the CourseInfoOverviewSerializer.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course_overview = CourseOverviewFactory()

    @patch('lms.djangoapps.mobile_api.course_info.serializers.get_assignments_completions')
    def test_get_media(self, get_assignments_completions_mock: MagicMock) -> None:
        output_data = CourseInfoOverviewSerializer(self.course_overview, context={'user': self.user}).data

        self.assertIn('media', output_data)  # noqa: PT009
        self.assertIn('image', output_data['media'])  # noqa: PT009
        self.assertIn('raw', output_data['media']['image'])  # noqa: PT009
        self.assertIn('small', output_data['media']['image'])  # noqa: PT009
        self.assertIn('large', output_data['media']['image'])  # noqa: PT009

    @patch('lms.djangoapps.mobile_api.course_info.serializers.get_assignments_completions')
    @patch(
        'lms.djangoapps.mobile_api.course_info.serializers.get_link_for_about_page',
        return_value='mock_about_link'
    )
    def test_get_course_sharing_utm_parameters(
        self,
        mock_get_link_for_about_page: MagicMock,
        get_assignments_completions_mock: MagicMock,
    ) -> None:
        output_data = CourseInfoOverviewSerializer(self.course_overview, context={'user': self.user}).data

        self.assertEqual(output_data['course_about'], mock_get_link_for_about_page.return_value)  # noqa: PT009
        mock_get_link_for_about_page.assert_called_once_with(self.course_overview)

    @patch('lms.djangoapps.mobile_api.course_info.serializers.get_assignments_completions')
    def test_get_course_modes(self, get_assignments_completions_mock: MagicMock) -> None:
        expected_course_modes = [{'slug': 'audit', 'sku': None, 'android_sku': None, 'ios_sku': None, 'min_price': 0}]

        output_data = CourseInfoOverviewSerializer(self.course_overview, context={'user': self.user}).data

        self.assertListEqual(output_data['course_modes'], expected_course_modes)  # noqa: PT009

    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_get_course_progress_no_assignments(self, get_course_assignment_mock: MagicMock) -> None:
        expected_course_progress = {'total_assignments_count': 0, 'assignments_completed': 0}

        output_data = CourseInfoOverviewSerializer(self.course_overview, context={'user': self.user}).data

        self.assertIn('course_progress', output_data)  # noqa: PT009
        self.assertDictEqual(output_data['course_progress'], expected_course_progress)  # noqa: PT009
        get_course_assignment_mock.assert_called_once_with(
            self.course_overview.id, self.user, include_without_due=True
        )

    @patch('lms.djangoapps.courseware.courses.get_course_assignments')
    def test_get_course_progress_with_assignments(self, get_course_assignment_mock: MagicMock) -> None:
        assignments_mock = [
            Mock(complete=False), Mock(complete=False), Mock(complete=True), Mock(complete=True), Mock(complete=True)
        ]
        get_course_assignment_mock.return_value = assignments_mock
        expected_course_progress = {'total_assignments_count': 5, 'assignments_completed': 3}

        output_data = CourseInfoOverviewSerializer(self.course_overview, context={'user': self.user}).data

        self.assertIn('course_progress', output_data)  # noqa: PT009
        self.assertDictEqual(output_data['course_progress'], expected_course_progress)  # noqa: PT009
        get_course_assignment_mock.assert_called_once_with(
            self.course_overview.id, self.user, include_without_due=True
        )
