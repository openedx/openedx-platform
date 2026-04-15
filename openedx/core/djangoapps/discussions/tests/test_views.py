"""
Test app view logic
"""
# pylint: disable=test-inherits-tests
import itertools
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import ddt
from django.core.exceptions import ValidationError
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from lti_consumer.models import CourseAllowPIISharingInLTIFlag
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.discussion.django_comment_client.tests.factories import RoleFactory
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import CourseUserType, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from ..models import AVAILABLE_PROVIDER_MAP, DEFAULT_CONFIG_ENABLED, Provider, get_default_provider_type
from ..permissions import HasPagesAndResourcesAccess

DATA_LEGACY_COHORTS = {
    'divided_inline_discussions': [],
    'divided_course_wide_discussions': [],
    'always_divide_inline_discussions': True,
    'division_scheme': 'none',
}
DATA_LEGACY_CONFIGURATION = {
    'allow_anonymous': True,
    'allow_anonymous_to_peers': True,
    'discussion_blackouts': [],
    'discussion_topics': {
        'General': {
            'id': 'course',
        },
    },
}
DEFAULT_LEGACY_CONFIGURATION = {
    **DATA_LEGACY_CONFIGURATION,
    'allow_anonymous_to_peers': False,
    'always_divide_inline_discussions': False,
    'divided_inline_discussions': [],
    'divided_course_wide_discussions': [],
    'division_scheme': 'none',
    'available_division_schemes': [],
    'reported_content_email_notifications': False,
}
DEFAULT_LTI_CONFIGURATION = {
    'lti_1p1_client_key': '',
    'lti_1p1_client_secret': '',
    'lti_1p1_launch_url': '',
    'version': None,
    'pii_sharing_allowed': False,
    'pii_share_email': False,
    'pii_share_username': False,
}

DATA_POST_LTI_CONFIGURATION = {
    'lti_1p1_client_key': 'KEY',
    'lti_1p1_client_secret': 'SECRET',
    'lti_1p1_launch_url': 'https://localhost',
    'version': 'lti_1p1'
}
DATA_LTI_CONFIGURATION_DISABLED_PII = {
    **DATA_POST_LTI_CONFIGURATION,
    'pii_sharing_allowed': False,
    'pii_share_email': False,
    'pii_share_username': False,
}

DATA_LTI_CONFIGURATION_ENABLED_PII = {
    **DATA_POST_LTI_CONFIGURATION,
    'pii_sharing_allowed': True,
    'pii_share_email': False,
    'pii_share_username': False,
}


class ApiTest(ModuleStoreTestCase, APITestCase):
    """
    Test basic API operations
    """
    CREATE_USER = True
    USER_TYPE = None

    def setUp(self):
        super().setUp()
        store = ModuleStoreEnum.Type.split
        self.course = CourseFactory.create(default_store=store)
        if self.USER_TYPE:
            self.user = self.create_user_for_course(self.course, user_type=self.USER_TYPE)

    @property
    def url(self):
        """Returns the discussion API url. """
        return reverse(
            'discussions', kwargs={'course_key_string': str(self.course.id)}
        )

    def _get(self):
        return self.client.get(self.url)

    def _post(self, data):
        return self.client.post(self.url, data, format='json')


class UnauthorizedApiTest(ApiTest):
    """
    Logged-out users should _not_ have any access
    """

    expected_response_code = status.HTTP_401_UNAUTHORIZED

    def test_access_get(self):
        response = self._get()
        assert response.status_code == self.expected_response_code

    def test_access_patch(self):
        response = self.client.patch(self.url)
        assert response.status_code == self.expected_response_code

    def test_access_post(self):
        response = self._post({})
        assert response.status_code == self.expected_response_code

    def test_access_put(self):
        response = self.client.put(self.url)
        assert response.status_code == self.expected_response_code


class AuthenticatedApiTest(UnauthorizedApiTest):
    """
    Logged-in users should _not_ have any access
    """

    expected_response_code = status.HTTP_403_FORBIDDEN
    USER_TYPE = CourseUserType.ENROLLED


class AuthorizedApiTest(AuthenticatedApiTest):
    """
    Global Staff should have access to all supported methods
    """

    expected_response_code = status.HTTP_200_OK
    USER_TYPE = CourseUserType.GLOBAL_STAFF

    def test_access_patch(self):
        response = self.client.patch(self.url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_access_put(self):
        response = self.client.put(self.url)
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class CourseStaffAuthorizedTest(AuthorizedApiTest):
    """
    Course Staff should have the same access as Global Staff
    """

    USER_TYPE = CourseUserType.UNENROLLED_STAFF


class CourseInstructorAuthorizedTest(AuthorizedApiTest):
    """
    Course instructor should have the same access as Global Staff.
    """

    USER_TYPE = CourseUserType.COURSE_INSTRUCTOR


class CourseDiscussionRoleAuthorizedTests(ApiTest):
    """Test cases for discussion api for users with discussion privileges."""

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.student_role = RoleFactory(name='Student', course_id=self.course.id)
        self.moderator_role = RoleFactory(name='Moderator', course_id=self.course.id)
        self.community_ta_role = RoleFactory(name='Community TA', course_id=self.course.id)
        self.student_user = UserFactory(password=self.TEST_PASSWORD)
        self.moderator_user = UserFactory(password=self.TEST_PASSWORD)
        self.community_ta_user = UserFactory(password=self.TEST_PASSWORD)
        self.student_role.users.add(self.student_user)
        self.moderator_role.users.add(self.moderator_user)
        self.community_ta_role.users.add(self.community_ta_user)

    def login(self, user):
        """Login the given user."""
        self.client.login(username=user.username, password=self.TEST_PASSWORD)

    def test_student_role_access_get(self):
        """Tests that student role does not have access to the API"""
        self.login(self.student_user)
        response = self._get()
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_student_role_access_post(self):
        """Tests that student role does not have access to the API"""
        self.login(self.student_user)
        response = self._post({})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_moderator_role_access_get(self):
        """Tests that discussion moderator role have access to the API"""
        self.login(self.moderator_user)
        response = self._get()
        assert response.status_code == status.HTTP_200_OK

    def test_moderator_role_access_post(self):
        """Tests that discussion moderator role have access to the API"""
        self.login(self.moderator_user)
        response = self._post({})
        assert response.status_code == status.HTTP_200_OK

    def test_community_ta_role_access_get(self):
        """Tests that discussion community TA role have access to the API"""
        self.login(self.community_ta_user)
        response = self._get()
        assert response.status_code == status.HTTP_200_OK

    def test_community_ta_role_access_post(self):
        """Tests that discussion community TA role have access to the API"""
        self.login(self.community_ta_user)
        response = self._post({})
        assert response.status_code == status.HTTP_200_OK


class DataTestMixin:
    """
    Mixin for common test methods for testing API responses.
    """

    def get(self):
        """
        Makes a get request and returns json data.
        """
        response = self._get()
        return response.json()

    def get_and_assert_defaults(self):
        """
        Assert that course has default discussion configuration
        """
        response = self._get()
        self._assert_defaults(response)

    def _assert_defaults(self, response):
        """
        Check for default values
        """
        data = response.json()
        assert response.status_code == self.expected_response_code
        assert data['enabled'] == DEFAULT_CONFIG_ENABLED
        assert data['provider_type'] == get_default_provider_type()
        assert data['providers']['available']['legacy'] == AVAILABLE_PROVIDER_MAP['legacy']
        assert not [
            name for name, spec in data['providers']['available'].items()
            if "messages" not in spec
        ], "Found available providers without messages field"

        assert data['lti_configuration'] == DEFAULT_LTI_CONFIGURATION
        assert data['plugin_configuration'] == DEFAULT_LEGACY_CONFIGURATION

    def _configure_lti_discussion_provider(self, provider=Provider.PIAZZA):
        """
        Configure an LTI-based discussion provider for a course.
        """
        payload = {
            'enabled': True,
            'provider_type': provider,
            'lti_configuration': DATA_POST_LTI_CONFIGURATION,
            'plugin_configuration': {}
        }
        response = self._post(payload)
        data = response.json()
        assert response.status_code == self.expected_response_code
        return data

    def _configure_legacy_discussion_provider(self, configuration):
        """
        Configure legacy discussion provider for a course.
        """
        payload = {
            'enabled': True,
            'provider_type': Provider.LEGACY,
            'plugin_configuration': configuration,
        }
        response = self._post(payload)
        assert response
        assert response.status_code == self.expected_response_code
        return response.json()


@ddt.ddt
class ProviderDataTest(CourseInstructorAuthorizedTest, DataTestMixin):
    """
    Tests for provider data when user is not global admin.
    """

    @ddt.data(
        # If the legacy provider is selected always show the legacy provider,
        # and only show the new one if the toggle is enabled
        (Provider.LEGACY, [Provider.LEGACY], Provider.OPEN_EDX, False),
        (Provider.LEGACY, [Provider.LEGACY], 'dummy', True),
        # If the new provider is selected show the legacy provider only
        # if the new discussions toggle is disabled
        (Provider.OPEN_EDX, [Provider.OPEN_EDX], 'dummy', False),
        (Provider.OPEN_EDX, [Provider.OPEN_EDX], Provider.LEGACY, True),
        # If some other provider is selected show only legacy provider if the toggle is false
        # and only the new provider if the toggle is enabled
        (Provider.PIAZZA, [], Provider.OPEN_EDX, False),
        (Provider.PIAZZA, [Provider.OPEN_EDX], Provider.LEGACY, True),
    )
    @ddt.unpack
    def test_available_providers(
        self, current_provider, visible_providers, hidden_provider, new_structure_enabled
    ):
        """
        Tests that providers available depending on the course.
        """
        self._configure_lti_discussion_provider(provider=current_provider)
        with override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, new_structure_enabled):
            response = self._get()
            data = response.json()
            for visible_provider in visible_providers:
                assert visible_provider in data['providers']['available'].keys()
            assert hidden_provider not in data['providers']['available'].keys()


@ddt.ddt
class DataTest(AuthorizedApiTest, DataTestMixin):
    """
    Check API-data correctness
    """

    def test_get_non_configured_provider_for_course(self):
        """
        Tests that if no provider is configured for a course, default configuration
        is returned.
        """
        self.get_and_assert_defaults()

    def test_post_invalid_key(self):
        """
        Tests that unsupported keys should be gracefully ignored.
        """
        payload = {
            'non-existent-key': 'value',
        }
        response = self._post(payload)
        assert response.status_code == status.HTTP_200_OK

    @ddt.data(
        Provider.ED_DISCUSS,
        Provider.INSCRIBE,
        Provider.PIAZZA,
        Provider.YELLOWDIG,
    )
    def test_add_valid_configuration(self, provider_type):
        """
        Test that basic configuration is set & retrieved successfully.
        """
        payload = {
            'enabled': True,
            'provider_type': provider_type,
            'plugin_configuration': {
                'key': 'value',
            },
        }
        self._post(payload)

        data = self.get()
        assert data['enabled']
        assert data['provider_type'] == provider_type
        assert data['plugin_configuration'] == {'key': 'value'}
        assert data['lti_configuration'] == DEFAULT_LTI_CONFIGURATION

    @ddt.data(
        True,
        False,
    )
    def test_enabled_configuration(self, enabled):
        """
        Test that setting the "enabled" property for Discussions shows the Discussions tab.
        """
        payload = {
            "provider_type": Provider.PIAZZA,
            "enabled": enabled,
        }
        self._post(payload)

        data = self.get()
        for tab in self.store.get_course(self.course.id).tabs:
            if tab.tab_id == "discussion":
                assert data["enabled"] == (not tab.is_hidden)
                break

    def test_change_plugin_configuration(self):
        """
        Tests custom config values persist that when changing discussion
        provider from edx provider to other provider.
        """
        payload = {
            'provider_type': Provider.PIAZZA,
            'plugin_configuration': {
                'allow_anonymous': False,
                'custom_field': 'custom_value',
                'reported_content_email_notifications': True,
            },
        }
        response = self._post(payload)
        data = response.json()
        assert data['plugin_configuration'] == {
            'allow_anonymous': False,
            'custom_field': 'custom_value',
            'reported_content_email_notifications': True,
        }

        course = self.store.get_course(self.course.id)
        # Only configuration fields not stored in the course, or
        # directly in the model should be stored here.
        assert course.discussions_settings['piazza'] == {'custom_field': 'custom_value'}

    @ddt.data(*itertools.product(
        (Provider.LEGACY, Provider.OPEN_EDX, Provider.PIAZZA),
        (True, False)
    ))
    @ddt.unpack
    def test_available_providers_staff(self, current_provider, new_structure_enabled):
        self._configure_lti_discussion_provider(provider=current_provider)
        with override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, new_structure_enabled):
            response = self._get()
            data = response.json()
            visible_providers = []
            if current_provider == Provider.LEGACY:
                visible_providers = [Provider.LEGACY]
            if current_provider != Provider.LEGACY and new_structure_enabled:
                visible_providers = [Provider.OPEN_EDX]
            for visible_provider in visible_providers:
                assert visible_provider in data['providers']['available'].keys()

    @ddt.data(
        {'enabled': 3},
    )
    def test_configuration_invalid(self, payload):
        """
        Test that invalid data raises validation error.
        """
        response = self._post(payload)
        assert status.is_client_error(response.status_code)

        errors = response.json()
        assert 'enabled' in errors
        self.get_and_assert_defaults()

    @ddt.data(
        *DATA_POST_LTI_CONFIGURATION.items()
    )
    @ddt.unpack
    def test_post_lti_valid(self, key, value):
        """
        Test that we can set & retrieve LTI configuration.
        """
        payload = {
            'enabled': True,
            'provider_type': Provider.PIAZZA,
            'lti_configuration': {
                key: value,
            }
        }
        self._post(payload)

        data = self.get()
        assert data['enabled']
        assert data['provider_type'] == Provider.PIAZZA
        assert data['lti_configuration'][key] == value

    def test_post_lti_invalid(self):
        """
        Check validation of LTI configuration ignores unsupported values.

        The fields are all open-ended strings and will accept any values.
        """
        for key, value in DATA_POST_LTI_CONFIGURATION.items():
            payload = {
                'enabled': True,
                'provider_type': Provider.PIAZZA,
                'lti_configuration': {
                    key: value,
                    'ignored-key': 'ignored value',
                }
            }
            response = self._post(payload)
            assert response.status_code == status.HTTP_200_OK
            data = self.get()
            assert data['enabled']
            assert data['provider_type'] == Provider.PIAZZA
            assert data['lti_configuration'][key] == value
            assert 'ignored-key' not in data['lti_configuration']

    def test_post_legacy_valid(self):
        """
        Test that we can set & retrieve edx provider configuration.
        """
        for key, value in DATA_LEGACY_CONFIGURATION.items():
            self._configure_legacy_discussion_provider(configuration={key: value})

            data = self.get()
            assert data['enabled']
            assert data['provider_type'] == Provider.LEGACY
            assert data['plugin_configuration'][key] == value

    @ddt.data(
        {'allow_anonymous': 3},
        {'allow_anonymous_to_peers': 3},
        {'discussion_blackouts': 3},
        {'discussion_topics': 3},
    )
    def test_post_legacy_invalid(self, plugin_configuration):
        """
        Check validation of legacy settings configuration
        """
        payload = {
            'enabled': True,
            'provider_type': Provider.LEGACY,
            'plugin_configuration': plugin_configuration,
        }
        with self.assertRaises(ValidationError):  # noqa: PT027
            response = self._post(payload)
            if status.is_client_error(response.status_code):
                raise ValidationError(str(response.status_code))
        self.get_and_assert_defaults()

    @ddt.data(*DATA_LEGACY_COHORTS.items())
    @ddt.unpack
    def test_post_cohorts_valid(self, key, value):
        """
        Test that we can set & retrieve legacy cohorts configuration.
        """
        self._configure_legacy_discussion_provider(configuration={key: value})

        data = self.get()
        assert data['enabled']
        assert data['provider_type'] == Provider.LEGACY
        assert data['plugin_configuration'][key] == value

    @ddt.data(*DATA_LEGACY_COHORTS.items())
    @ddt.unpack
    def test_post_cohorts_invalid(self, key, value):
        """
        Check validation of legacy cohorts configuration
        """
        if isinstance(value, str):
            # For the string value, we can only fail here if it's blank
            value = ''
        else:
            # Otherwise, submit a string when non-string is required
            value = str(value)

        payload = {
            'enabled': True,
            'provider_type': Provider.LEGACY,
            'plugin_configuration': {
                key: value,
            }
        }
        with self.assertRaises(ValidationError):  # noqa: PT027
            response = self._post(payload)
            if status.is_client_error(response.status_code):
                raise ValidationError(str(response.status_code))
        self.get_and_assert_defaults()

    def test_change_to_lti(self):
        """
        Test that switching to an LTI-backed provider from a default provider works as expected.

        When switching provider to LTI, the API should return both LTI & legacy data.
        """
        plugin_configuration = {'allow_anonymous': False}
        self._configure_legacy_discussion_provider(configuration=plugin_configuration)
        self._configure_lti_discussion_provider(provider=Provider.ED_DISCUSS)

        data = self.get()
        assert data['enabled']
        assert data['provider_type'] == Provider.ED_DISCUSS
        assert data['plugin_configuration'] == {}
        assert data['lti_configuration'] == DATA_LTI_CONFIGURATION_DISABLED_PII

    def test_change_from_lti(self):
        """
        Test that switching from an LTI-backed provider to a non-LTI provider works as expected.

        When switching provider to LTI, the API should return both LTI & legacy data.
        """
        self._configure_lti_discussion_provider()

        plugin_configuration = {'allow_anonymous': False}
        data = self._configure_legacy_discussion_provider(configuration=plugin_configuration)
        assert data['enabled']
        assert data['provider_type'] == Provider.LEGACY
        assert not data['plugin_configuration']['allow_anonymous']

    @ddt.data(
        *itertools.product(
            ["enable_in_context", "enable_graded_units", "unit_level_visibility"],
            [True, False],
        ),
        ("provider_type", Provider.PIAZZA),
    )
    @ddt.unpack
    def test_change_course_fields(self, field, value):
        """
        Test changing fields that are saved to the course
        """
        payload = {
            field: value
        }
        response = self._post(payload)
        data = response.json()
        assert data[field] == value
        course = self.store.get_course(self.course.id)
        assert course.discussions_settings[field] == value

    @ddt.data(*[
        user_type.name for user_type in CourseUserType
        if user_type not in {  # pylint: disable=undefined-variable
            CourseUserType.ANONYMOUS,
            CourseUserType.GLOBAL_STAFF
        }
    ])
    def test_unable_to_change_provider_for_running_course(self, user_type):
        """
        Ensure that certain users cannot change provider for a running course.
        """
        self.course.start = datetime.now(timezone.utc) - timedelta(days=5)  # noqa: UP017
        self.course = self.update_course(self.course, self.user.id)

        # use the global staff user to do the initial config
        # so we're sure to not get permissions errors
        response = self._post({
            'enabled': True,
            'provider_type': 'legacy',
        })
        assert response.status_code == status.HTTP_200_OK

        self.create_user_for_course(self.course, CourseUserType[user_type])

        response = self._post({
            'enabled': True,
            'provider_type': 'piazza',
        })
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_global_staff_can_change_provider_for_running_course(self):
        """
        Ensure that global staff can change provider for a running course.
        """
        self.course.start = datetime.now(timezone.utc) - timedelta(days=5)  # noqa: UP017
        self.course = self.update_course(self.course, self.user.id)

        # use the global staff user to do the initial config
        # so we're sure to not get permissions errors
        response = self._post({
            'enabled': True,
            'provider_type': 'legacy',
        })
        assert response.status_code == status.HTTP_200_OK

        response = self._post({
            'enabled': True,
            'provider_type': 'piazza',
        })
        assert response.status_code == status.HTTP_200_OK


@ddt.ddt
class PIISettingsAPITests(DataTest):
    """
    Test PII sharing setting for course.
    """

    @contextmanager
    def _pii_sharing_for_course(self, enabled):
        instance = CourseAllowPIISharingInLTIFlag.objects.create(course_id=self.course.id, enabled=enabled)
        yield
        instance.delete()

    def _assert_pii_flag_for_course(self, enabled):
        """
        Asserts pii flag has given state.
        """
        try:
            course_pii = CourseAllowPIISharingInLTIFlag.objects.filter(course_id=self.course.id).latest('change_date')
            assert course_pii.enabled == enabled
        except CourseAllowPIISharingInLTIFlag.DoesNotExist:
            assert enabled is False

    def test_pii_sharing_disabled(self):
        """
        Tests that pii settings are not enabled by default when configuring LTI provider.
        """
        self._configure_lti_discussion_provider()
        self._assert_pii_flag_for_course(enabled=False)

        with self._pii_sharing_for_course(enabled=False):
            self._assert_pii_flag_for_course(enabled=False)

    def test_pii_sharing_enabled(self):
        """
        Tests that pii sharing settings can be enabled after configuring lti provider.
        """
        self._configure_lti_discussion_provider()
        with self._pii_sharing_for_course(enabled=True):
            self._assert_pii_flag_for_course(enabled=True)

    @ddt.data(
        {"pii_share_username": True},
        {"pii_share_email": True},
        {"pii_share_email": True, "pii_share_username": True},
    )
    def test_post_pii_fields_with_non_configured_pii(self, pii_configuration):
        """
        Tests that if PII sharing is *not configured* for the course, the api call
        to update PII settings for the course fails.
        """
        data = self._configure_lti_discussion_provider()
        data['lti_configuration'].update(pii_configuration)
        response = self._post(data)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @ddt.data(
        {"pii_share_username": True},
        {"pii_share_email": True},
        {"pii_share_email": True, "pii_share_username": True},
    )
    def test_post_pii_fields_with_pii_disabled(self, pii_configuration):
        """
        Test that when PII sharing is *disabled* for the course, user is not able
        update PII settings for a course.
        """
        data = self._configure_lti_discussion_provider()
        data['lti_configuration'].update(pii_configuration)
        with self._pii_sharing_for_course(enabled=False):
            response = self._post(data)
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            lti_configuration = self.get()['lti_configuration']
            no_pii_fields_updated = [
                lti_configuration.get(pii_field) != pii_value
                for pii_field, pii_value in pii_configuration.items()
            ]
            assert all(no_pii_fields_updated)

    @ddt.data(
        {"pii_share_username": True},
        {"pii_share_email": True},
        {"pii_share_email": True, "pii_share_username": True},
        {"pii_share_email": False, "pii_share_username": True},
        {"pii_share_email": True, "pii_share_username": False},
        {"pii_share_email": False, "pii_share_username": False},
    )
    def test_post_pii_fields_with_pii_enabled(self, pii_configuration):
        """
        Test that when PII sharing is enabled for the course, user is able
        update PII settings for the course.
        """
        data = self._configure_lti_discussion_provider()
        data['lti_configuration'].update(pii_configuration)
        with self._pii_sharing_for_course(enabled=True):
            response = self._post(data)
            assert response.status_code == status.HTTP_200_OK

            lti_configuration = self.get()['lti_configuration']
            all_pii_fields_updated = [
                lti_configuration[pii_field] == pii_value
                for pii_field, pii_value in pii_configuration.items()
            ]
            assert all(all_pii_fields_updated)

    @ddt.data(
        True,
        False
    )
    def test_get_pii_fields(self, pii_sharing):
        """
        Tests that when PII sharing is included in the API regardless of course pii settings.
        """
        self._configure_lti_discussion_provider()
        with self._pii_sharing_for_course(enabled=pii_sharing):
            lti_configuration = self.get()['lti_configuration']
            # If pii_sharing is true, then the fields should be present, and absent otherwise
            assert lti_configuration['pii_sharing_allowed'] == pii_sharing
            assert 'pii_share_email' in lti_configuration
            assert 'pii_share_username' in lti_configuration

    @ddt.data(
        Provider.ED_DISCUSS,
        Provider.INSCRIBE,
        Provider.PIAZZA,
        Provider.YELLOWDIG,
    )
    def test_post_everything_with_pii_disabled(self, provider):
        """
        Test posting all data returns expected response when course PII flag is disabled.
        """
        data = self._configure_lti_discussion_provider(provider=provider)
        self._assert_pii_flag_for_course(enabled=False)
        expected_providers = AVAILABLE_PROVIDER_MAP[provider]
        assert data['enabled']
        assert data['provider_type'] == provider
        assert data['providers']['available'][provider] == expected_providers
        assert data['plugin_configuration'] == {}
        assert data['lti_configuration'] == DATA_LTI_CONFIGURATION_DISABLED_PII

        response_data = self.get()
        # the GET should pull back the same data as the POST
        assert response_data == data

    @ddt.data(
        Provider.ED_DISCUSS,
        Provider.INSCRIBE,
        Provider.PIAZZA,
        Provider.YELLOWDIG,
    )
    def test_post_everything_with_pii_enabled(self, provider):
        """
        Test posting all data returns expected response when course PII flag is disabled.
        """
        with self._pii_sharing_for_course(enabled=True):
            self._assert_pii_flag_for_course(enabled=True)
            data = self._configure_lti_discussion_provider(provider=provider)
            expected_providers = AVAILABLE_PROVIDER_MAP[provider]
            assert data['enabled']
            assert data['provider_type'] == provider
            assert data['providers']['available'][provider] == expected_providers
            assert data['plugin_configuration'] == {}
            assert data['lti_configuration'] == DATA_LTI_CONFIGURATION_ENABLED_PII

            response_data = self.get()
            # the GET should pull back the same data as the POST
            assert response_data == data


class SyncDiscussionTopicsViewTests(ModuleStoreTestCase, APITestCase):
    """
    Tests for SyncDiscussionTopicsView
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.course_key_string = str(self.course.id)
        self.staff_user = UserFactory.create(is_staff=True)
        self.instructor_user = UserFactory.create()
        self.student_user = UserFactory.create()
        self.url = reverse('sync-discussion-topics', kwargs={'course_key_string': self.course_key_string})

        # Mock the permission class for course team checking
        self.original_has_permission = HasPagesAndResourcesAccess.has_permission
        HasPagesAndResourcesAccess.has_permission = Mock(return_value=True)

    def tearDown(self):
        # Restore original permission method
        HasPagesAndResourcesAccess.has_permission = self.original_has_permission
        super().tearDown()

    @patch('openedx.core.djangoapps.discussions.views.update_discussions_settings_from_course_task')
    def test_sync_discussion_topics_staff_user(self, mock_update):
        """
        Test that staff users can sync discussion topics
        """
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['status'], 'success')  # noqa: PT009
        mock_update.assert_called_once_with(self.course_key_string)

    @patch('openedx.core.djangoapps.discussions.views.update_discussions_settings_from_course_task')
    def test_sync_discussion_topics_course_team(self, mock_update):
        """
        Test that course team members can sync discussion topics
        """
        self.client.force_authenticate(user=self.instructor_user)

        # Mock the course team permission check
        HasPagesAndResourcesAccess.has_permission = Mock(return_value=True)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)  # noqa: PT009
        self.assertEqual(response.data['status'], 'success')  # noqa: PT009
        mock_update.assert_called_once()

    def test_sync_discussion_topics_unauthorized(self):
        """
        Test that unauthorized users cannot sync discussion topics
        """
        # Don't authenticate the request
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)  # noqa: PT009

    def test_sync_discussion_topics_forbidden(self):
        """
        Test that authenticated but unauthorized users cannot sync discussion topics
        """
        self.client.force_authenticate(user=self.student_user)

        # Mock the course team permission check to return False
        HasPagesAndResourcesAccess.has_permission = Mock(return_value=False)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)  # noqa: PT009

    def test_invalid_http_method(self):
        """
        Test that only POST method is allowed
        """
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)  # noqa: PT009
