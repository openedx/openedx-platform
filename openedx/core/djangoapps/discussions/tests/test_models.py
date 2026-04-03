"""
Perform basic validation of the models
"""

from unittest.mock import patch

import ddt
import pytest
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from organizations.models import Organization

from ..config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from ..models import (
    DEFAULT_CONFIG_ENABLED,
    DiscussionsConfiguration,
    PostingRestriction,
    Provider,
    ProviderFilter,
    get_default_provider_type,
)

SUPPORTED_PROVIDERS = [
    'legacy',
    'piazza',
]


class OrganizationFilterTest(TestCase):
    """
    Perform basic validation on the filter model
    """

    def setUp(self):
        """
        Configure shared test data
        """
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:Test+Course+Configured")
        self.course_key_with_defaults = CourseKey.from_string("course-v1:TestX+Course+Configured")
        self.organization = Organization(short_name=self.course_key.org)
        self.organization.save()
        self.provider_allowed = SUPPORTED_PROVIDERS[0]
        self.provider_denied = SUPPORTED_PROVIDERS[1]

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_get_nonexistent(self, _default_providers):
        """
        Assert we retrieve defaults when no configuration set
        """
        providers = ProviderFilter.get_available_providers(self.course_key_with_defaults)
        assert len(providers) == len(SUPPORTED_PROVIDERS)

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_get_allow(self, _default_providers):
        """
        Assert we can set the allow list
        """
        ProviderFilter.objects.create(
            org=self.course_key.org,
            allow=[self.provider_allowed],
        )
        providers = ProviderFilter.get_available_providers(self.course_key)
        assert self.provider_allowed in providers
        assert len(providers) == 1

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_get_deny(self, _default_providers):
        """
        Assert we can set the deny list
        """
        ProviderFilter.objects.create(
            org=self.course_key.org,
            deny=[self.provider_denied],
        )
        providers = ProviderFilter.get_available_providers(self.course_key)
        assert self.provider_denied not in providers

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_get_allow_and_deny(self, _default_providers):
        """
        Assert we can add an item to both allow and deny lists
        """
        ProviderFilter.objects.create(
            org=self.course_key.org,
            allow=[self.provider_allowed, self.provider_denied],
            deny=[self.provider_denied],
        )
        providers = ProviderFilter.get_available_providers(self.course_key)
        assert len(providers) == 1
        assert self.provider_denied not in providers
        assert self.provider_allowed in providers

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_get_allow_or_deny(self, _default_providers):
        """
        Assert we can exclusively add an items to both allow and deny lists
        """
        ProviderFilter.objects.create(
            org=self.course_key.org,
            allow=[self.provider_allowed],
            deny=[self.provider_denied],
        )
        providers = ProviderFilter.get_available_providers(self.course_key)
        assert len(providers) == 1
        assert self.provider_denied not in providers
        assert self.provider_allowed in providers

    @patch('openedx.core.djangoapps.discussions.models.get_supported_providers', return_value=SUPPORTED_PROVIDERS)
    def test_override(self, _default_providers):
        """
        Assert we can override a configuration and get the latest data
        """
        ProviderFilter.objects.create(
            org=self.course_key.org,
            allow=[self.provider_allowed, self.provider_denied],
        )
        ProviderFilter.objects.create(
            org=self.course_key.org,
            allow=[self.provider_allowed],
        )
        providers = ProviderFilter.get_available_providers(self.course_key)
        assert self.provider_allowed in providers
        assert len(providers) == 1


@ddt.ddt
class DiscussionsConfigurationModelTest(TestCase):
    """
    Perform basic validation on the configuration model
    """

    def setUp(self):
        """
        Configure shared test data (configuration, course_key, etc.)
        """
        super().setUp()
        self.course_key_with_defaults = CourseKey.from_string("course-v1:TestX+Course+Configured")
        self.course_key_without_config = CourseKey.from_string("course-v1:TestX+Course+NoConfig")
        self.course_key_with_values = CourseKey.from_string("course-v1:TestX+Course+Values")
        self.configuration_with_defaults = DiscussionsConfiguration(
            context_key=self.course_key_with_defaults,
        )
        self.configuration_with_defaults.save()
        self.configuration_with_values = DiscussionsConfiguration(
            context_key=self.course_key_with_values,
            posting_restrictions=PostingRestriction.ENABLED,
            enabled=False,
            provider_type=Provider.LEGACY,
            plugin_configuration={
                'url': 'http://localhost',
            },
        )
        self.configuration_with_values.save()

    def test_get_nonexistent(self):
        """
        Assert we can not fetch a non-existent record
        """
        with pytest.raises(DiscussionsConfiguration.DoesNotExist):
            DiscussionsConfiguration.objects.get(
                context_key=self.course_key_without_config,
            )

    def test_get_with_defaults(self):
        """
        Assert we can lookup a record with default values
        """
        configuration = DiscussionsConfiguration.objects.get(context_key=self.course_key_with_defaults)
        assert configuration is not None
        assert configuration.posting_restrictions == PostingRestriction.DISABLED
        assert configuration.enabled  # by default
        assert configuration.lti_configuration is None
        assert len(configuration.plugin_configuration.keys()) == 0
        assert configuration.provider_type == get_default_provider_type()

    def test_get_with_values(self):
        """
        Assert we can lookup a record with custom values
        """
        configuration = DiscussionsConfiguration.objects.get(context_key=self.course_key_with_values)
        assert configuration is not None
        assert configuration.posting_restrictions == PostingRestriction.ENABLED
        assert not configuration.enabled
        assert configuration.lti_configuration is None
        actual_url = configuration.plugin_configuration.get('url')
        expected_url = self.configuration_with_values.plugin_configuration.get('url')  # pylint: disable=no-member
        assert actual_url == expected_url
        assert configuration.provider_type == self.configuration_with_values.provider_type

    def test_update_defaults(self):
        """
        Assert we can update an existing record
        """
        configuration = DiscussionsConfiguration.objects.get(context_key=self.course_key_with_defaults)
        configuration.posting_restrictions = PostingRestriction.DISABLED
        configuration.enabled = False
        configuration.plugin_configuration = {
            'url': 'http://localhost',
        }
        configuration.provider_type = 'legacy'
        configuration.save()
        configuration = DiscussionsConfiguration.objects.get(context_key=self.course_key_with_defaults)
        assert configuration is not None
        assert configuration.posting_restrictions == PostingRestriction.DISABLED
        assert not configuration.enabled
        assert configuration.lti_configuration is None
        assert configuration.plugin_configuration['url'] == 'http://localhost'
        assert configuration.provider_type == 'legacy'

    def test_is_enabled_nonexistent(self):
        """
        Assert that discussions are enabled by default even when no configuration exists
        """
        is_enabled = DiscussionsConfiguration.is_enabled(context_key=self.course_key_without_config)
        assert is_enabled == DEFAULT_CONFIG_ENABLED

    def test_is_enabled_default(self):
        """
        Assert that discussions are enabled by default, when a configuration exists
        """
        is_enabled = DiscussionsConfiguration.is_enabled(self.course_key_with_defaults)
        assert is_enabled

    def test_is_enabled_explicit(self):
        """
        Assert that discussions can be explicitly disabled
        """
        is_enabled = DiscussionsConfiguration.is_enabled(self.course_key_with_values)
        assert not is_enabled

    @ddt.data(
        (True, Provider.OPEN_EDX),
        (False, Provider.LEGACY),
    )
    @ddt.unpack
    def test_get_nonexistent_defaults(self, new_structure_enabled, default_provider_type):
        """
        Assert we get default provider "legacy" and default enabled model instance back for nonexistent records
        """
        with override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=new_structure_enabled):
            configuration = DiscussionsConfiguration.get(self.course_key_without_config)
            assert configuration is not None
            assert configuration.posting_restrictions == PostingRestriction.DISABLED
            assert configuration.enabled == DEFAULT_CONFIG_ENABLED
            assert configuration.provider_type == default_provider_type
            assert not configuration.lti_configuration
            assert not configuration.plugin_configuration

    def test_get_defaults(self):
        """
        Assert we can lookup a record with default values
        """
        configuration = DiscussionsConfiguration.get(self.course_key_with_defaults)
        assert configuration is not None
        assert configuration.posting_restrictions == PostingRestriction.DISABLED
        assert configuration.enabled
        assert not configuration.lti_configuration
        assert not configuration.plugin_configuration
        assert configuration.provider_type == 'legacy'

    def test_get_explicit(self):
        """
        Assert we can lookup a record with explicitly-set values
        """
        configuration = DiscussionsConfiguration.get(self.course_key_with_values)
        assert configuration is not None
        assert configuration.posting_restrictions == PostingRestriction.ENABLED
        assert not configuration.enabled
        assert not configuration.lti_configuration
        assert configuration.plugin_configuration
        assert configuration.provider_type == 'legacy'
