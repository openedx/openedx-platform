"""
Tests for the CCX Coach API v2 schedule endpoints.
"""

from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase


class ScheduleTestMixin:
    """Shared setup for the schedule endpoint tests."""

    endpoint_name = None

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.coach)

    def _url(self, course_id):
        return reverse(f'ccx_coach_api_v2:{self.endpoint_name}', kwargs={'course_id': str(course_id)})


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2ScheduleGetViewTest(ScheduleTestMixin, CcxTestCase):
    """Tests for `GET /api/ccx_coach/v2/courses/{ccxId}/schedule`."""

    endpoint_name = 'schedule'

    def test_returns_schedule_tree(self):
        response = self.api_client.get(self._url(self.ccx_key))

        assert response.status_code == status.HTTP_200_OK
        # one node per master-course section, each with subsection children
        assert len(response.data) == len(self.chapters)
        first = response.data[0]
        assert {'location', 'display_name', 'category', 'start', 'hidden'} <= set(first.keys())
        assert 'children' in first

    def test_master_course_id_rejected(self):
        response = self.api_client.get(self._url(self.course.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_requires_authentication(self):
        self.api_client.force_authenticate(user=None)
        response = self.api_client.get(self._url(self.ccx_key))
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2RemoveScheduleViewTest(ScheduleTestMixin, CcxTestCase):
    """Tests for `POST /api/ccx_coach/v2/courses/{ccxId}/remove_schedule`."""

    endpoint_name = 'remove_schedule'

    def test_remove_hides_block_and_descendants(self):
        location = str(self.chapters[0].location)

        response = self.api_client.post(self._url(self.ccx_key), {'location': location}, format='json')

        assert response.status_code == status.HTTP_200_OK
        node = next(n for n in response.data if n['location'] == location)
        assert node['hidden'] is True
        for child in node.get('children', []):
            assert child['hidden'] is True

    def test_missing_location_returns_400(self):
        response = self.api_client.post(self._url(self.ccx_key), {}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_location_returns_400(self):
        bogus = str(self.course.id.make_usage_key('chapter', 'does_not_exist'))
        response = self.api_client.post(self._url(self.ccx_key), {'location': bogus}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get('error_code') == 'schedule_block_not_found'

    def test_non_coach_forbidden(self):
        self.api_client.force_authenticate(user=UserFactory.create())
        response = self.api_client.post(
            self._url(self.ccx_key), {'location': str(self.chapters[0].location)}, format='json'
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@override_settings(CUSTOM_COURSES_EDX=True)
class CCXCoachV2SaveScheduleViewTest(ScheduleTestMixin, CcxTestCase):
    """Tests for `POST /api/ccx_coach/v2/courses/{ccxId}/save_schedule`."""

    endpoint_name = 'save_schedule'

    def test_save_hides_section_and_returns_payload(self):
        location = str(self.chapters[0].location)
        payload = [{'location': location, 'hidden': True, 'start': ''}]

        response = self.api_client.post(self._url(self.ccx_key), payload, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert 'schedule' in response.data
        assert 'grading_policy' in response.data
        node = next(n for n in response.data['schedule'] if n['location'] == location)
        assert node['hidden'] is True

    def test_invalid_payload_returns_400(self):
        response = self.api_client.post(self._url(self.ccx_key), {'not': 'a list'}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get('error_code') == 'invalid_schedule_payload'

    def test_unknown_location_returns_json_400(self):
        bogus = str(self.course.id.make_usage_key('chapter', 'does_not_exist'))
        payload = [{'location': bogus, 'hidden': True, 'start': ''}]
        response = self.api_client.post(self._url(self.ccx_key), payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get('error_code') == 'invalid_schedule_payload'
