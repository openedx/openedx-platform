"""
Tests for helper function provided by site_configuration app.
"""


from django.test import TestCase

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.site_configuration.tests.test_util import (
    with_site_configuration,
    with_site_configuration_context,
)

test_config = {   # pylint: disable=invalid-name
    "university": "Test University",
    "platform_name": "Test Education Program",
    "SITE_NAME": "test.localhost",
    "course_org_filter": "TestX",
    "css_overrides_file": "test/css/site.css",
    "ENABLE_MKTG_SITE": False,
    "ENABLE_THIRD_PARTY_AUTH": False,
    "course_about_show_social_links": False,
    "favicon_path": "/static/test.ico",

    "REGISTRATION_EXTRA_FIELDS": {
        "first_name": "required",
        "last_name": "required",
        "level_of_education": "hidden",
        "gender": "hidden",
        "year_of_birth": "required",
        "mailing_address": "hidden",
        "goals": "hidden",
        "terms_of_service": "required",
        "honor_code": "hidden",
        "state": "required",
        "country": "required"
    },
}

test_config_multi_org = {   # pylint: disable=invalid-name
    "course_org_filter": ["FooOrg", "BarOrg", "FooBarOrg"]
}


class TestHelpers(TestCase):
    """
    Tests for helper function provided by site_configuration.
    """

    @with_site_configuration(configuration=test_config)
    def test_get_value(self):
        """
        Test that get_value returns correct value for any given key.
        """
        # Make sure entry is saved and retrieved correctly
        assert configuration_helpers.get_value('university') == test_config['university']
        assert configuration_helpers.get_value('platform_name') == test_config['platform_name']
        assert configuration_helpers.get_value('SITE_NAME') == test_config['SITE_NAME']
        assert configuration_helpers.get_value('course_org_filter') == test_config['course_org_filter']
        assert configuration_helpers.get_value('css_overrides_file') == test_config['css_overrides_file']
        assert configuration_helpers.get_value('ENABLE_MKTG_SITE') == test_config['ENABLE_MKTG_SITE']
        assert configuration_helpers.get_value('favicon_path') == test_config['favicon_path']
        assert configuration_helpers.get_value('ENABLE_THIRD_PARTY_AUTH') == test_config['ENABLE_THIRD_PARTY_AUTH']
        assert configuration_helpers.get_value('course_about_show_social_links') ==\
               test_config['course_about_show_social_links']

        # Test that the default value is returned if the value for the given key is not found in the configuration
        assert configuration_helpers.get_value('non_existent_name', 'dummy-default-value') == 'dummy-default-value'

        # Test that correct default value is returned
        assert configuration_helpers.get_value('non_existent_name', '') == ''
        assert configuration_helpers.get_value('non_existent_name', None) is None

    @with_site_configuration(configuration=test_config)
    def test_get_dict(self):
        """
        Test that get_dict returns correct value for any given key.
        """
        # Make sure entry is saved and retrieved correctly
        self.assertCountEqual(
            configuration_helpers.get_dict("REGISTRATION_EXTRA_FIELDS"),
            test_config['REGISTRATION_EXTRA_FIELDS'],
        )

        default = {"test1": 123, "first_name": "Test"}
        expected = default
        expected.update(test_config['REGISTRATION_EXTRA_FIELDS'])

        # Test that the default value is returned if the value for the given key is not found in the configuration
        self.assertCountEqual(
            configuration_helpers.get_dict("REGISTRATION_EXTRA_FIELDS", default),
            expected,
        )

    @with_site_configuration(configuration=test_config)
    def test_has_override_value(self):
        """
        Test that has_override_value returns correct value for any given key.
        """

        assert configuration_helpers.has_override_value('university')
        assert configuration_helpers.has_override_value('platform_name')
        assert configuration_helpers.has_override_value('ENABLE_MKTG_SITE')
        assert configuration_helpers.has_override_value('REGISTRATION_EXTRA_FIELDS')

        assert not configuration_helpers.has_override_value('non_existent_key')

    def test_is_site_configuration_enabled(self):
        """
        Test that is_site_configuration_enabled returns True when configuration is enabled.
        """
        with with_site_configuration_context(configuration=test_config):
            assert configuration_helpers.is_site_configuration_enabled()

        # Test without a Site Configuration
        assert not configuration_helpers.is_site_configuration_enabled()

    def test_get_value_for_org(self):
        """
        Test that get_value_for_org returns correct value for any given key.
        """
        test_org = test_config['course_org_filter']
        with with_site_configuration_context(configuration=test_config):
            assert configuration_helpers.get_value_for_org(test_org, 'university') == test_config['university']
            assert configuration_helpers.get_value_for_org(test_org, 'css_overrides_file') ==\
                   test_config['css_overrides_file']

            self.assertCountEqual(
                configuration_helpers.get_value_for_org(test_org, "REGISTRATION_EXTRA_FIELDS"),
                test_config['REGISTRATION_EXTRA_FIELDS']
            )

            # Test default value of key is not present in configuration
            assert configuration_helpers.get_value_for_org(test_org, 'non_existent_key') is None
            assert configuration_helpers.get_value_for_org(test_org, 'non_existent_key', 'default for non existent') ==\
                   'default for non existent'
            assert configuration_helpers.get_value_for_org('missing_org', 'university', 'default for non existent') ==\
                   'default for non existent'

    def test_get_value_for_org_2(self):
        """
        Test that get_value_for_org returns correct value for any given key.
        """
        test_org = test_config['course_org_filter']
        with with_site_configuration_context(configuration=test_config):

            # Make sure if ORG is not present in site configuration then default is used instead
            assert configuration_helpers.get_value_for_org('TestSiteX', 'email_from_address') is None
            # Make sure 'default' is returned if org is present but key is not
            assert configuration_helpers.get_value_for_org(test_org, 'email_from_address') is None

    def test_get_all_orgs(self):
        """
        Test that get_all_orgs returns organizations defined in site configuration
        """
        test_orgs = [test_config['course_org_filter']]
        with with_site_configuration_context(configuration=test_config):
            self.assertCountEqual(
                list(configuration_helpers.get_all_orgs()),
                test_orgs,
            )

    @with_site_configuration(configuration=test_config_multi_org)
    def test_get_current_site_orgs(self):
        test_orgs = test_config_multi_org['course_org_filter']
        self.assertCountEqual(
            list(configuration_helpers.get_current_site_orgs()),
            test_orgs
        )

    def test_get_current_site_configuration_values(self):
        """
        Test get_current_site_configuration_values helper function
        """
        site_values = configuration_helpers.get_current_site_configuration_values()
        self.assertTrue(isinstance(site_values, dict))

        # without any site configuration it should return empty dict
        self.assertEqual(site_values, {})

        with with_site_configuration_context(configuration=test_config):
            site_values = configuration_helpers.get_current_site_configuration_values()
            self.assertEqual(site_values, test_config)
