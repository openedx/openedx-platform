"""
Unit tests covering the program discussion iframe API.
"""

from uuid import uuid4

import ddt
from django.urls import reverse_lazy
from edx_toggles.toggles.testutils import override_waffle_flag
from lti_consumer.models import LtiConfiguration
from markupsafe import Markup

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.learner_dashboard.config.waffle import ENABLE_MASTERS_PROGRAM_TAB_VIEW, ENABLE_PROGRAM_TAB_VIEW
from lms.djangoapps.program_enrollments.rest_api.v1.tests.test_views import ProgramCacheMixin
from lms.djangoapps.program_enrollments.tests.factories import ProgramEnrollmentFactory
from openedx.core.djangoapps.catalog.tests.factories import CourseFactory, CourseRunFactory, ProgramFactory
from openedx.core.djangoapps.programs.models import ProgramDiscussionsConfiguration
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory as ModuleStoreCourseFactory


@ddt.ddt
@override_waffle_flag(ENABLE_PROGRAM_TAB_VIEW, active=True)
@override_waffle_flag(ENABLE_MASTERS_PROGRAM_TAB_VIEW, active=True)
class TestProgramDiscussionIframeView(SharedModuleStoreTestCase, ProgramCacheMixin):
    """Unit tests for the program details page."""
    program_uuid = str(uuid4())
    url = reverse_lazy('program_discussion', kwargs={'program_uuid': program_uuid})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        modulestore_course = ModuleStoreCourseFactory()
        course_run = CourseRunFactory(key=str(modulestore_course.id))
        course = CourseFactory(course_runs=[course_run])
        cls.program = ProgramFactory(uuid=cls.program_uuid, courses=[course])

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        self.set_program_in_catalog_cache(self.program_uuid, self.program)
        ProgramEnrollmentFactory.create(
            user=self.user,
            program_uuid=self.program_uuid,
            external_user_key='0001',
        )

    def test_program_discussion_not_configured(self):
        """
        Verify API returns proper response in case ProgramDiscussions is not Configured.
        """
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        expected_data = {
            'tab_view_enabled': True,
            'discussion': {
                'iframe': "",
                'configured': False
            }
        }
        self.assertEqual(response.data, expected_data)

    def test_if_user_is_not_authenticated(self):
        """
        Verify that 401 is returned if user is not authenticated.
        """
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_program_discussion_disabled(self):
        """
        Tests that API does not return discussion iframe when discussion
        configuration is disabled.
        """
        __ = ProgramDiscussionsConfiguration.objects.create(
            program_uuid=self.program_uuid,
            enabled=False,
            provider_type="piazza",
        )
        expected_data = {
            'tab_view_enabled': True,
            'discussion': {
                'iframe': "",
                'configured': False
            }
        }

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected_data)

    @ddt.data(True, False)
    def test_api_returns_discussions_iframe(self, staff):
        """
        Test if API returns iframe in case ProgramDiscussionsConfiguration model contains proper data for staff
        and non staff. In case of non staff user must be be enrolled in program.
        """
        if staff:
            self.user = UserFactory(is_staff=True)
            self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        discussion_config = ProgramDiscussionsConfiguration.objects.create(
            program_uuid=self.program_uuid,
            enabled=True,
            provider_type="piazza",
        )
        discussion_config.lti_configuration = LtiConfiguration.objects.create(
            config_store=LtiConfiguration.CONFIG_ON_DB,
            lti_1p1_launch_url='http://test.url',
            lti_1p1_client_key='test_client_key',
            lti_1p1_client_secret='test_client_secret',
        )
        discussion_config.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data['discussion']['iframe'], Markup)
        self.assertIn('iframe', str(response.data['discussion']['iframe']), )

    def test_program_without_enrollment(self):
        """
        Test if API returns default response in case a non staff user has no enrollment in the program
        """
        self.user = UserFactory()
        self.client.login(username=self.user.username, password=self.TEST_PASSWORD)
        response = self.client.get(self.url)
        default_response = {
            'tab_view_enabled': False,
            'discussion': {
                'configured': False,
                'iframe': ''
            }
        }
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, default_response)
