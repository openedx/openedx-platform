"""
Tests for the CCX Coach API v2 endpoints.
"""

from unittest.mock import patch

from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.overrides import get_override_for_ccx, override_field_for_ccx
from lms.djangoapps.ccx.tests.utils import CcxTestCase

CCX_COACH_MFE_URL = 'http://localhost:2003/ccx-coach'


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2MetadataViewTest(CcxTestCase):
    """Tests for `GET /api/ccx_coach/v2/courses/{course_id}/metadata`."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.coach)

    def _url(self, course_id):
        return reverse('ccx_coach_api_v2:metadata', kwargs={'course_id': str(course_id)})

    def test_master_course_without_ccx_returns_empty(self):
        """A master course with no CCX yet yields empty ccx_course_id and tabs."""
        response = self.api_client.get(self._url(self.course.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['course_id'] == str(self.course.id)
        assert response.data['ccx_course_id'] == ''
        assert response.data['tabs'] == []

    @override_settings(CCX_COACH_MICROFRONTEND_URL=CCX_COACH_MFE_URL)
    def test_master_course_with_ccx_returns_tabs(self):
        """When the coach has a CCX, the master id resolves to it (legacy behavior)."""
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))

        response = self.api_client.get(self._url(self.course.id))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['course_id'] == str(self.course.id)
        assert response.data['ccx_course_id'] == str(ccx_key)
        self._assert_tabs(response.data['tabs'], ccx_key)

    @override_settings(CCX_COACH_MICROFRONTEND_URL=CCX_COACH_MFE_URL)
    def test_ccx_course_id_returns_tabs(self):
        """Passing the CCX id directly resolves and returns its tabs."""
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))

        response = self.api_client.get(self._url(ccx_key))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['course_id'] == str(self.course.id)
        assert response.data['ccx_course_id'] == str(ccx_key)
        self._assert_tabs(response.data['tabs'], ccx_key)

    def _assert_tabs(self, tabs, ccx_key):
        """Assert the four CCX tabs, their order, ids and URLs."""
        assert [tab['tab_id'] for tab in tabs] == [
            'enrollments', 'schedule', 'student_grades', 'grading_policy',
        ]
        assert [tab['sort_order'] for tab in tabs] == [10, 20, 30, 40]
        for tab in tabs:
            assert set(tab.keys()) == {'tab_id', 'title', 'url', 'sort_order'}
            assert tab['url'] == f'/ccx-coach/{ccx_key}/{tab["tab_id"]}'

    def test_tabs_without_mfe_url_setting(self):
        """With the MFE URL unset, tabs are still returned as relative links."""
        self.make_ccx()
        with override_settings(CCX_COACH_MICROFRONTEND_URL=None):
            response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['tabs']) == 4
        assert all(tab['url'].startswith('/') for tab in response.data['tabs'])

    def test_nonexistent_master_course_returns_404(self):
        response = self.api_client.get(self._url('course-v1:edX+Missing+Missing'))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_requires_authentication(self):
        self.api_client.force_authenticate(user=None)
        response = self.api_client.get(self._url(self.course.id))
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_allowed(self):
        staff = UserFactory.create()
        CourseStaffRole(self.course.id).add_users(staff)
        self.api_client.force_authenticate(user=staff)
        response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_200_OK

    def test_feature_flag_off_forbidden(self):
        with override_settings(CUSTOM_COURSES_EDX=False):
            response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2CreateViewTest(CcxTestCase):
    """Tests for `POST /api/ccx_coach/v2/courses/{course_id}/create_ccx`."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.coach)

    def _url(self, course_id):
        return reverse('ccx_coach_api_v2:create_ccx', kwargs={'course_id': str(course_id)})

    @override_settings(CCX_COACH_MICROFRONTEND_URL=CCX_COACH_MFE_URL)
    def test_create_returns_full_payload_not_redirect(self):
        """Create returns 201 with the full metadata payload, not a 302 redirect."""
        response = self.api_client.post(self._url(self.course.id), {'name': 'My CCX'}, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        ccx = CustomCourseForEdX.objects.get()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        assert ccx.display_name == 'My CCX'
        assert response.data['course_id'] == str(self.course.id)
        assert response.data['ccx_course_id'] == str(ccx_key)
        assert [tab['tab_id'] for tab in response.data['tabs']] == [
            'enrollments', 'schedule', 'student_grades', 'grading_policy',
        ]
        # The coach is enrolled and granted staff on the new CCX.
        assert CourseEnrollment.is_enrolled(self.coach, ccx_key)
        assert CourseStaffRole(ccx_key).has_user(self.coach)

    def test_create_missing_name_returns_400(self):
        response = self.api_client.post(self._url(self.course.id), {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get('error_code') == 'invalid_request'
        assert 'name' in response.data.get('field_errors', {})
        assert not CustomCourseForEdX.objects.exists()

    def test_create_rejects_ccx_id(self):
        """A CCX can only be created from a master course id."""
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))
        response = self.api_client.post(self._url(ccx_key), {'name': 'Nope'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_requires_authentication(self):
        self.api_client.force_authenticate(user=None)
        response = self.api_client.post(self._url(self.course.id), {'name': 'X'}, format='json')
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_create_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.post(self._url(self.course.id), {'name': 'X'}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_failure_returns_json_error(self):
        """An unexpected failure during creation returns a structured JSON error, not a 500 HTML page."""
        with patch(
            'lms.djangoapps.ccx.api.v2.views.create_ccx_course',
            side_effect=RuntimeError('boom'),
        ):
            response = self.api_client.post(self._url(self.course.id), {'name': 'My CCX'}, format='json')
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data.get('error_code') == 'ccx_creation_failed'

    def test_create_failure_rolls_back_partial_ccx(self):
        """
        A failure part-way through creation leaves no CCX behind.

        The view catches the exception to return JSON, which suppresses the
        ATOMIC_REQUESTS rollback, so creation runs inside an explicit atomic
        block. This guards that rollback.
        """
        def create_then_fail(course, coach, display_name):
            """Persist a CCX (as the real service does) and then fail."""
            CustomCourseForEdX(course_id=course.id, coach=coach, display_name=display_name).save()
            raise RuntimeError('boom')

        with patch('lms.djangoapps.ccx.api.v2.views.create_ccx_course', side_effect=create_then_fail):
            response = self.api_client.post(self._url(self.course.id), {'name': 'My CCX'}, format='json')

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.data.get('error_code') == 'ccx_creation_failed'
        assert not CustomCourseForEdX.objects.exists()

@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2GradingPolicyViewTest(CcxTestCase):
    """Tests for `GET /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy`."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.coach)

    def _url(self, course_id):
        return reverse('ccx_coach_api_v2:grading_policy', kwargs={'course_id': str(course_id)})

    def test_returns_master_course_policy_when_no_override(self):
        """Without an override, the master course policy is returned as fallback."""
        response = self.api_client.get(self._url(self.ccx_key))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == self.course.grading_policy

    def test_returns_ccx_override_when_present(self):
        """When an override is set for the CCX, the override is returned."""
        custom_policy = {
            'GRADER': [
                {
                    'type': 'Homework',
                    'min_count': 5,
                    'drop_count': 1,
                    'short_label': 'HW',
                    'weight': 1.0,
                },
            ],
            'GRADE_CUTOFFS': {'Pass': 0.75},
        }
        override_field_for_ccx(self.ccx, self.course, 'grading_policy', custom_policy)

        response = self.api_client.get(self._url(self.ccx_key))

        assert response.status_code == status.HTTP_200_OK
        assert response.data == custom_policy

    def test_master_course_id_rejected(self):
        """The grading policy endpoint requires a CCX course id."""
        response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error_code'] == 'course_id_not_valid_ccx_id'

    def test_nonexistent_ccx_returns_404(self):
        missing_ccx_key = CCXLocator.from_course_locator(self.course.id, '99999')
        response = self.api_client.get(self._url(missing_ccx_key))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_requires_authentication(self):
        self.api_client.force_authenticate(user=None)
        response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_allowed(self):
        staff = UserFactory.create()
        CourseStaffRole(self.course.id).add_users(staff)
        self.api_client.force_authenticate(user=staff)
        response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code == status.HTTP_200_OK

    def test_feature_flag_off_forbidden(self):
        with override_settings(CUSTOM_COURSES_EDX=False):
            response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code == status.HTTP_403_FORBIDDEN


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2GradingPolicyPutViewTest(CcxTestCase):
    """Tests for `PUT /api/ccx_coach/v2/courses/{ccx_course_id}/grading_policy`."""

    NEW_POLICY = {
        'GRADER': [
            {
                'type': 'Homework',
                'min_count': 5,
                'drop_count': 1,
                'short_label': 'HW',
                'weight': 1.0,
            },
        ],
        'GRADE_CUTOFFS': {'Pass': 0.75},
    }

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.coach)

    def _url(self, course_id):
        return reverse('ccx_coach_api_v2:grading_policy', kwargs={'course_id': str(course_id)})

    def test_put_replaces_policy_and_returns_it(self):
        response = self.api_client.put(
            self._url(self.ccx_key), {'policy': self.NEW_POLICY}, format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data == self.NEW_POLICY
        stored = get_override_for_ccx(self.ccx, self.course, 'grading_policy')
        assert stored == self.NEW_POLICY

    def test_put_missing_policy_returns_400(self):
        response = self.api_client.put(self._url(self.ccx_key), {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_invalid_grader_type_returns_400(self):
        bad = {'GRADER': 'not-a-list', 'GRADE_CUTOFFS': {'Pass': 0.5}}
        response = self.api_client.put(
            self._url(self.ccx_key), {'policy': bad}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_invalid_grade_cutoffs_type_returns_400(self):
        bad = {'GRADER': [], 'GRADE_CUTOFFS': 'not-a-dict'}
        response = self.api_client.put(
            self._url(self.ccx_key), {'policy': bad}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_put_master_course_id_rejected(self):
        response = self.api_client.put(
            self._url(self.course.id), {'policy': self.NEW_POLICY}, format='json'
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error_code'] == 'course_id_not_valid_ccx_id'

    def test_put_nonexistent_ccx_returns_404(self):
        missing_ccx_key = CCXLocator.from_course_locator(self.course.id, '99999')
        response = self.api_client.put(
            self._url(missing_ccx_key), {'policy': self.NEW_POLICY}, format='json'
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_put_requires_authentication(self):
        self.api_client.force_authenticate(user=None)
        response = self.api_client.put(
            self._url(self.ccx_key), {'policy': self.NEW_POLICY}, format='json'
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_put_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.put(
            self._url(self.ccx_key), {'policy': self.NEW_POLICY}, format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_put_feature_flag_off_forbidden(self):
        with override_settings(CUSTOM_COURSES_EDX=False):
            response = self.api_client.put(
                self._url(self.ccx_key), {'policy': self.NEW_POLICY}, format='json'
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN
