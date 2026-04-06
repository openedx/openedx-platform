"""
Test for course live app views
"""
import json

import ddt
from django.test import RequestFactory
from django.urls import reverse
from lti_consumer.models import CourseAllowPIISharingInLTIFlag, LtiConfiguration
from markupsafe import Markup
from rest_framework.test import APITestCase

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import CourseUserType, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..models import CourseLiveConfiguration
from ..providers import ProviderManager


@ddt.ddt
class TestCourseLiveConfigurationView(ModuleStoreTestCase, APITestCase):
    """
    Unit tests for the CourseLiveConfigurationView.
    """
    password = 'test'

    def setUp(self):
        super().setUp()
        store = ModuleStoreEnum.Type.split
        self.course = CourseFactory.create(default_store=store)
        self.user = self.create_user_for_course(self.course, user_type=CourseUserType.GLOBAL_STAFF)

    @property
    def url(self):
        """Returns the course live API url. """
        return reverse(
            'course_live', kwargs={'course_id': str(self.course.id)}
        )

    def _get(self):
        return self.client.get(self.url)

    def _post(self, data):
        return self.client.post(self.url, data, format='json')

    def create_course_live_config(self, provider='zoom'):
        """
        creates a courseLiveConfiguration
        """
        providers = ProviderManager().get_enabled_providers()
        if providers.get(provider).requires_pii_sharing():
            CourseAllowPIISharingInLTIFlag.objects.create(course_id=self.course.id, enabled=True)

        lti_config = {
            'lti_1p1_client_key': 'this_is_key',
            'lti_1p1_client_secret': 'this_is_secret',
            'lti_1p1_launch_url': 'example.com',
            'lti_config': {
                'additional_parameters': {
                    'custom_instructor_email': "email@example.com"
                }
            },
        }
        if not providers.get(provider).additional_parameters:
            lti_config.pop('lti_config')

        course_live_config_data = {
            'enabled': True,
            'provider_type': provider,
            'lti_configuration': lti_config
        }
        response = self._post(course_live_config_data)
        return lti_config, course_live_config_data, response

    def test_pii_sharing_not_allowed(self):
        """
        Test response if PII sharing is not allowed
        """
        response = self._get()
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        expected_data = {
            'course_key': None,
            'provider_type': '',
            'enabled': True,
            'lti_configuration': {
                'lti_1p1_client_key': '',
                'lti_1p1_launch_url': '',
                'version': 'lti_1p1',
                'lti_config': {}
            },
            'free_tier': False,
            'pii_sharing_allowed': False
        }
        self.assertEqual(response.data, expected_data)  # noqa: PT009

    def test_pii_sharing_is_allowed(self):
        """
        Test response if PII sharing is  allowed
        """
        CourseAllowPIISharingInLTIFlag.objects.create(course_id=self.course.id, enabled=True)
        response = self._get()
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        expected_data = {
            'enabled': True,
            'course_key': None,
            'pii_sharing_allowed': True,
            'lti_configuration': {
                'lti_1p1_client_key': '',
                'lti_1p1_launch_url': '',
                'lti_config': {},
                'version': 'lti_1p1'
            },
            'free_tier': False,
            'provider_type': ''
        }
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(content, expected_data)  # noqa: PT009

    @ddt.data(('zoom', False, False), ('big_blue_button', False, True))
    @ddt.unpack
    def test_create_configurations_data(self, provider, share_email, share_username):
        """
        Create and test courseLiveConfiguration data in database
        """
        lti_config, data, response = self.create_course_live_config(provider)
        course_live_configurations = CourseLiveConfiguration.get(self.course.id)
        lti_configuration = course_live_configurations.get(self.course.id).lti_configuration
        self.assertEqual(self.course.id, course_live_configurations.course_key)  # noqa: PT009
        self.assertEqual(data['enabled'], course_live_configurations.enabled)  # noqa: PT009
        self.assertEqual(data['provider_type'], course_live_configurations.provider_type)  # noqa: PT009

        self.assertEqual(lti_config['lti_1p1_client_key'], lti_configuration.lti_1p1_client_key)  # noqa: PT009
        self.assertEqual(lti_config['lti_1p1_client_secret'], lti_configuration.lti_1p1_client_secret)  # noqa: PT009
        self.assertEqual(lti_config['lti_1p1_launch_url'], lti_configuration.lti_1p1_launch_url)  # noqa: PT009

        provider_instance = ProviderManager().get_enabled_providers().get(provider)
        additional_param = {'additional_parameters': {}}
        if provider_instance.additional_parameters:
            additional_param = {'additional_parameters': {'custom_instructor_email': 'email@example.com'}}

        self.assertEqual({  # noqa: PT009
            'pii_share_username': share_username,
            'pii_share_email': share_email,
            **additional_param
        }, lti_configuration.lti_config)

        self.assertEqual(response.status_code, 200)  # noqa: PT009

    @ddt.data(('zoom', False, False), ('big_blue_button', False, True))
    @ddt.unpack
    def test_update_configurations_data(self, provider, share_email, share_username):
        """
        Create and test courseLiveConfiguration data in database
        """
        lti_config, data, response = self.create_course_live_config(provider)
        updated_lti_config = {
            'lti_1p1_client_key': 'new_key',
            'lti_1p1_client_secret': '',
            'lti_1p1_launch_url': 'example01.com',
            'lti_config': {
                'additional_parameters': {
                    'custom_instructor_email': 'new_email@example.com'
                },
            },
        }
        updated_data = {
            'enabled': False,
            'provider_type': provider,
            'lti_configuration': updated_lti_config
        }
        response = self._post(updated_data)

        live_configurations = CourseLiveConfiguration.get(self.course.id)
        lti_configuration = live_configurations.get(self.course.id).lti_configuration

        self.assertEqual(self.course.id, live_configurations.course_key)  # noqa: PT009
        self.assertEqual(updated_data['enabled'], live_configurations.enabled)  # noqa: PT009
        self.assertEqual(updated_data['provider_type'], live_configurations.provider_type)  # noqa: PT009

        self.assertEqual(updated_lti_config.get('lti_1p1_client_key'), lti_configuration.lti_1p1_client_key)  # noqa: PT009
        self.assertEqual(lti_config.get('lti_1p1_client_secret'), lti_configuration.lti_1p1_client_secret)  # noqa: PT009
        self.assertEqual(updated_lti_config.get('lti_1p1_launch_url'), lti_configuration.lti_1p1_launch_url)  # noqa: PT009

        provider_instance = ProviderManager().get_enabled_providers().get(provider)
        additional_param = {'additional_parameters': {}}
        if provider_instance.additional_parameters:
            additional_param = updated_lti_config.get('lti_config')

        self.assertEqual({  # noqa: PT009
            'pii_share_username': share_username,
            'pii_share_email': share_email,
            **additional_param
        }, lti_configuration.lti_config)

        self.assertEqual(response.status_code, 200)  # noqa: PT009

    @ddt.data(('zoom', False, False), ('big_blue_button', False, True))
    @ddt.unpack
    def test_create_configurations_response(self, provider, share_email, share_username):
        """
        Create and test POST request response data
        """
        lti_config, course_live_config_data, response = self.create_course_live_config(provider)

        provider_instance = ProviderManager().get_enabled_providers().get(provider)
        additional_param = {'additional_parameters': {}}
        if provider_instance.additional_parameters:
            additional_param = {'additional_parameters': {'custom_instructor_email': 'email@example.com'}}

        expected_data = {
            'course_key': str(self.course.id),
            'enabled': True,
            'pii_sharing_allowed': share_email or share_username,
            'provider_type': provider,
            'free_tier': False,
            'lti_configuration': {
                'lti_1p1_client_key': 'this_is_key',
                'lti_1p1_launch_url': 'example.com',
                'version': 'lti_1p1',
                'lti_config': {
                    'pii_share_email': share_email,
                    'pii_share_username': share_username,
                    **additional_param
                },
            },
        }

        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertEqual(content, expected_data)  # noqa: PT009

    @ddt.data(('zoom', False, False), ('big_blue_button', False, True))
    @ddt.unpack
    def test_update_configurations_response(self, provider, share_email, share_username):
        """
        Create, update & test POST request response data
        """
        self.create_course_live_config(provider)
        updated_data = {
            'enabled': False,
            'provider_type': provider,
            'lti_configuration': {
                'lti_1p1_client_key': 'new_key',
                'lti_1p1_client_secret': 'new_secret',
                'lti_1p1_launch_url': 'example01.com',
                'lti_config': {
                    'additional_parameters': {
                        'custom_instructor_email': 'new_email@example.com'
                    },
                },
            },
        }
        response = self._post(updated_data)
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response.status_code, 200)  # noqa: PT009

        provider_instance = ProviderManager().get_enabled_providers().get(provider)
        additional_param = {'additional_parameters': {}}
        if provider_instance.additional_parameters:
            additional_param = {'additional_parameters': {'custom_instructor_email': 'new_email@example.com'}}

        expected_data = {
            'course_key': str(self.course.id),
            'provider_type': provider,
            'enabled': False,
            'free_tier': False,
            'lti_configuration': {
                'lti_1p1_client_key': 'new_key',
                'lti_1p1_launch_url': 'example01.com',
                'version': 'lti_1p1',
                'lti_config': {
                    'pii_share_username': share_username,
                    'pii_share_email': share_email,
                    **additional_param
                }
            },
            'pii_sharing_allowed': share_email or share_username
        }
        self.assertEqual(content, expected_data)  # noqa: PT009

    def test_post_error_messages(self):
        """
        Test all related validation messages are recived
        """
        CourseAllowPIISharingInLTIFlag.objects.create(course_id=self.course.id, enabled=True)
        response = self._post({})
        content = json.loads(response.content.decode('utf-8'))
        expected_data = {
            'provider_type': ['This field is required.'],
        }
        self.assertEqual(content, expected_data)  # noqa: PT009
        self.assertEqual(response.status_code, 400)  # noqa: PT009

    def test_non_staff_user_access(self):
        """
        Test non staff user has no access to API
        """
        self.user = self.create_user_for_course(self.course, user_type=CourseUserType.UNENROLLED)
        response = self._get()
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response.status_code, 403)  # noqa: PT009
        self.assertEqual(content, {'detail': 'You do not have permission to perform this action.'})  # noqa: PT009

        response = self._post({})
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response.status_code, 403)  # noqa: PT009
        self.assertEqual(content, {'detail': 'You do not have permission to perform this action.'})  # noqa: PT009

    def test_courseware_api_has_live_tab(self):
        """
        Test if courseware api has live-tab
        """
        self.create_course_live_config()
        url = reverse('course-home:course-metadata', args=[self.course.id])
        response = self.client.get(url)
        content = json.loads(response.content.decode('utf-8'))
        data = next((tab for tab in content['tabs'] if tab['tab_id'] == 'lti_live'), None)
        self.assertEqual(data, {  # noqa: PT009
            'tab_id': 'lti_live',
            'title': 'Live',
            'url': f'http://learning-mfe/course/{self.course.id}/live'
        })

    @ddt.data(('big_blue_button', False, True))
    @ddt.unpack
    def test_create_configurations_response_free_tier(self, provider, share_email, share_username):
        """
        Create and test POST request response data
        """
        self.create_course_live_config()
        providers = ProviderManager().get_enabled_providers()
        if providers.get(provider).requires_pii_sharing():
            CourseAllowPIISharingInLTIFlag.objects.create(course_id=self.course.id, enabled=True)

        course_live_config_data = {
            'free_tier': True,
            'enabled': True,
            'provider_type': provider,
        }
        response = self._post(course_live_config_data)

        expected_data = {
            'course_key': str(self.course.id),
            'enabled': True,
            'pii_sharing_allowed': share_email or share_username,
            'provider_type': provider,
            'free_tier': True,
            'lti_configuration': None
        }

        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertEqual(content, expected_data)  # noqa: PT009


class TestCourseLiveProvidersView(ModuleStoreTestCase, APITestCase):
    """
    Tests for course live provider view
    """

    def setUp(self):
        super().setUp()
        store = ModuleStoreEnum.Type.split
        self.course = CourseFactory.create(default_store=store)
        self.user = self.create_user_for_course(self.course, user_type=CourseUserType.GLOBAL_STAFF)

    @property
    def url(self):
        """
        Returns the live providers API url.
        """
        return reverse(
            'live_providers', kwargs={'course_id': str(self.course.id)}
        )

    def test_response_has_correct_data(self):
        providers = ProviderManager().get_enabled_providers()
        expected_data = {
            'providers': {
                'active': '',
                'available': {key: provider.__dict__() for (key, provider) in providers.items()}
            }
        }
        response = self.client.get(self.url)
        content = json.loads(response.content.decode('utf-8'))
        self.assertEqual(content, expected_data)  # noqa: PT009


class TestCourseLiveIFrameView(ModuleStoreTestCase, APITestCase):
    """
    Unit tests for course live iframe view
    """

    def setUp(self):
        super().setUp()
        store = ModuleStoreEnum.Type.split
        self.course = CourseFactory.create(default_store=store)
        self.user = self.create_user_for_course(self.course, user_type=CourseUserType.GLOBAL_STAFF)

    @property
    def url(self):
        """
        Returns the course live iframe API url.
        """
        return reverse(
            'live_iframe', kwargs={'course_id': str(self.course.id)}
        )

    def test_api_returns_live_iframe(self):
        request = RequestFactory().get(self.url)
        request.user = self.user
        live_config = CourseLiveConfiguration.objects.create(
            course_key=self.course.id,
            enabled=True,
            provider_type="zoom",
        )
        live_config.lti_configuration = LtiConfiguration.objects.create(
            config_store=LtiConfiguration.CONFIG_ON_DB,
            lti_config={
                "pii_share_username": 'true',
                "pii_share_email": 'true',
                "additional_parameters": {
                    "custom_instructor_email": "test@gmail.com"
                }
            },
            lti_1p1_launch_url='http://test.url',
            lti_1p1_client_key='test_client_key',
            lti_1p1_client_secret='test_client_secret',
        )
        live_config.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)  # noqa: PT009
        self.assertIsInstance(response.data['iframe'], Markup)  # noqa: PT009
        self.assertIn('iframe', str(response.data['iframe']))  # noqa: PT009

    def test_non_authenticated_user(self):
        """
        Verify that 401 is returned if user is not authenticated.
        """
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)  # noqa: PT009

    def test_not_enrolled_user(self):
        """
        Verify that 403 is returned if user is not enrolled.
        """
        self.user = self.create_user_for_course(self.course, user_type=CourseUserType.UNENROLLED)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)  # noqa: PT009

    def test_live_configuration_disabled(self):
        """
        Verify that proper error message is returned if live configuration is disabled.
        """
        CourseLiveConfiguration.objects.create(
            course_key=self.course.id,
            enabled=False,
            provider_type="zoom",
        )
        response = self.client.get(self.url)
        self.assertEqual(response.data['developer_message'], 'Course live is not enabled for this course.')  # noqa: PT009
