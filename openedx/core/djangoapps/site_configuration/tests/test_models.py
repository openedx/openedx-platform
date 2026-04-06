"""
Tests for site configuration's django models.
"""
from unittest.mock import patch

import pytest
from django.contrib.sites.models import Site
from django.db import IntegrityError, transaction
from django.test import TestCase

from openedx.core.djangoapps.site_configuration.models import (
    SiteConfiguration,
    SiteConfigurationHistory,
    save_siteconfig_without_historical_record,
)
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory


class SiteConfigurationTests(TestCase):
    """
    Tests for SiteConfiguration and its signals/receivers.
    """
    domain = 'site_configuration_post_save_receiver_example.com'
    name = 'site_configuration_post_save_receiver_example'

    test_config1 = {
        "university": "Test University",
        "platform_name": "Test Education Program",
        "SITE_NAME": "test.localhost",
        "course_org_filter": "TestX",
        "css_overrides_file": "test/css/site.css",
        "ENABLE_MKTG_SITE": False,
        "ENABLE_THIRD_PARTY_AUTH": False,
        "course_about_show_social_links": False,
        "favicon_path": "/static/test.ico",
    }

    test_config2 = {
        "university": "Test Another University",
        "platform_name": "Test Another Education Program",
        "SITE_NAME": "test-another.localhost",
        "course_org_filter": "TestAnotherX",
        "css_overrides_file": "test-another/css/site.css",
        "ENABLE_MKTG_SITE": True,
        "ENABLE_THIRD_PARTY_AUTH": True,
        "course_about_show_social_links": False,
        "favicon_path": "/static/test-another.ico",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.site, _ = Site.objects.get_or_create(domain=cls.domain, name=cls.domain)
        cls.site2, _ = Site.objects.get_or_create(
            domain=cls.test_config2['SITE_NAME'],
            name=cls.test_config2['SITE_NAME'],
        )

    def test_site_configuration_post_save_receiver(self):
        """
        Test that and entry is added to SiteConfigurationHistory model each time a new
        SiteConfiguration is added.
        """
        # add SiteConfiguration to database
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
        )

        # Verify an entry to SiteConfigurationHistory was added.
        site_configuration_history = SiteConfigurationHistory.objects.filter(
            site=site_configuration.site,
        ).all()

        # Make sure an entry (and only one entry) is saved for SiteConfiguration
        assert len(site_configuration_history) == 1

    def test_site_configuration_post_update_receiver(self):
        """
        Test that and entry is added to SiteConfigurationHistory each time a
        SiteConfiguration is updated.
        """
        # add SiteConfiguration to database
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
        )

        site_configuration.site_values = {'test': 'test'}
        site_configuration.save()

        # Verify an entry to SiteConfigurationHistory was added.
        site_configuration_history = SiteConfigurationHistory.objects.filter(
            site=site_configuration.site,
        ).all()

        # Make sure two entries (one for create and one for update) are saved for SiteConfiguration
        assert len(site_configuration_history) == 2

    def test_site_configuration_post_update_receiver_with_skip(self):
        """
        Test that and entry is NOT added to SiteConfigurationHistory each time a
        SiteConfiguration is updated with save_siteconfig_without_historical_record().
        """
        # Add SiteConfiguration to database.  By default, the site_valutes field contains only "{}".
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
        )

        # Update the SiteConfiguration we just created.
        site_configuration.site_values = {"test": "test"}
        save_siteconfig_without_historical_record(site_configuration)  # Instead of .save().

        # Verify that the SiteConfiguration has been updated.
        assert site_configuration.get_value('test') == 'test'

        # Verify an entry to SiteConfigurationHistory was NOT added.
        # Make sure one entry (one for create and NONE for update) is saved for SiteConfiguration.
        site_configuration_history = SiteConfigurationHistory.objects.filter(
            site=site_configuration.site,
        ).all()
        assert len(site_configuration_history) == 1

    def test_no_entry_is_saved_for_errors(self):
        """
        Test that and entry is not added to SiteConfigurationHistory if there is an error while
        saving SiteConfiguration.
        """
        # add SiteConfiguration to database
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
        )

        # Verify an entry to SiteConfigurationHistory was added.
        site_configuration_history = SiteConfigurationHistory.objects.filter(
            site=site_configuration.site,
        ).all()

        # Make sure entry is saved if there is no error
        assert len(site_configuration_history) == 1

        with transaction.atomic():
            with pytest.raises(IntegrityError):
                # try to add a duplicate entry
                site_configuration = SiteConfigurationFactory.create(
                    site=self.site,
                )
        site_configuration_history = SiteConfigurationHistory.objects.filter(
            site=site_configuration.site,
        ).all()

        # Make sure no entry is saved if there an error
        assert len(site_configuration_history) == 1

    def test_get_value(self):
        """
        Test that get_value returns correct value for any given key.
        """
        # add SiteConfiguration to database
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
            site_values=self.test_config1
        )

        # Make sure entry is saved and retrieved correctly
        assert site_configuration.get_value('university') == self.test_config1['university']
        assert site_configuration.get_value('platform_name') == self.test_config1['platform_name']
        assert site_configuration.get_value('SITE_NAME') == self.test_config1['SITE_NAME']
        assert site_configuration.get_value('course_org_filter') == self.test_config1['course_org_filter']
        assert site_configuration.get_value('css_overrides_file') == self.test_config1['css_overrides_file']
        assert site_configuration.get_value('ENABLE_MKTG_SITE') == self.test_config1['ENABLE_MKTG_SITE']
        assert site_configuration.get_value('favicon_path') == self.test_config1['favicon_path']
        assert site_configuration.get_value('ENABLE_THIRD_PARTY_AUTH') == self.test_config1['ENABLE_THIRD_PARTY_AUTH']
        assert site_configuration.get_value('course_about_show_social_links') == \
               self.test_config1['course_about_show_social_links']

        # Test that the default value is returned if the value for the given key is not found in the configuration
        assert site_configuration.get_value('non_existent_name', 'dummy-default-value') == 'dummy-default-value'

        # Test that the default value is returned if Site configuration is not enabled
        site_configuration.enabled = False
        site_configuration.save()

        assert site_configuration.get_value('university') is None
        assert site_configuration.get_value('platform_name', 'Default Platform Name') == 'Default Platform Name'
        assert site_configuration.get_value('SITE_NAME', 'Default Site Name') == 'Default Site Name'

    def test_invalid_data_error_on_get_value(self):
        """
        Test that get_value logs an error if json data is not valid.
        """
        # import logger, for patching
        from openedx.core.djangoapps.site_configuration.models import logger
        invalid_data = [self.test_config1]

        # add SiteConfiguration to database
        site_configuration = SiteConfigurationFactory.create(
            site=self.site,
            site_values=invalid_data
        )

        # make sure get_value logs an error for invalid json data
        with patch.object(logger, "exception") as mock_logger:
            assert site_configuration.get_value('university') is None
            assert mock_logger.called

        # make sure get_value returns default_value for invalid json data
        with patch.object(logger, "exception") as mock_logger:
            value = site_configuration.get_value("platform_name", "Default Platform Name")
            assert mock_logger.called
            assert value == 'Default Platform Name'

    def test_get_value_for_org(self):
        """
        Test that get_value_for_org returns correct value for any given key.
        """
        # add SiteConfiguration to database
        SiteConfigurationFactory.create(
            site=self.site,
            site_values=self.test_config1
        )
        SiteConfigurationFactory.create(
            site=self.site2,
            site_values=self.test_config2
        )

        # Make sure entry is saved and retrieved correctly
        assert SiteConfiguration.get_value_for_org(self.test_config1['course_org_filter'], 'university') ==\
               self.test_config1['university']
        assert SiteConfiguration.get_value_for_org(self.test_config1['course_org_filter'], 'platform_name') ==\
               self.test_config1['platform_name']
        assert SiteConfiguration.get_value_for_org(self.test_config1['course_org_filter'], 'SITE_NAME') ==\
               self.test_config1['SITE_NAME']
        assert SiteConfiguration.get_value_for_org(self.test_config1['course_org_filter'], 'css_overrides_file') ==\
               self.test_config1['css_overrides_file']
        assert SiteConfiguration.get_value_for_org(self.test_config1['course_org_filter'], 'ENABLE_MKTG_SITE') ==\
               self.test_config1['ENABLE_MKTG_SITE']

        # Make sure entry is saved and retrieved correctly
        assert SiteConfiguration.get_value_for_org(self.test_config2['course_org_filter'], 'university') ==\
               self.test_config2['university']

        assert SiteConfiguration.get_value_for_org(self.test_config2['course_org_filter'], 'platform_name') ==\
               self.test_config2['platform_name']
        assert SiteConfiguration\
            .get_value_for_org(self.test_config2['course_org_filter'], 'SITE_NAME') == \
               self.test_config2['SITE_NAME']

        assert SiteConfiguration\
            .get_value_for_org(self.test_config2['course_org_filter'],
                               'css_overrides_file') == self.test_config2['css_overrides_file']

        assert SiteConfiguration\
            .get_value_for_org(self.test_config2['course_org_filter'],
                               'ENABLE_MKTG_SITE') == self.test_config2['ENABLE_MKTG_SITE']

        # Test that the default value is returned if the value for the given key is not found in the configuration
        assert SiteConfiguration\
            .get_value_for_org(self.test_config1['course_org_filter'],
                               'non-existent', 'dummy-default-value') == 'dummy-default-value'

        # Test that the default value is returned if the value for the given key is not found in the configuration
        assert SiteConfiguration\
            .get_value_for_org(self.test_config2['course_org_filter'],
                               'non-existent', 'dummy-default-value') == 'dummy-default-value'

        # Test that the default value is returned if org is not found in the configuration
        assert SiteConfiguration.get_value_for_org('non-existent-org', 'platform_name', 'dummy-default-value') ==\
               'dummy-default-value'

    def test_get_site_for_org(self):
        """
        Test that get_value_for_org returns correct value for any given key.
        """
        # add SiteConfiguration to database
        config1 = SiteConfigurationFactory.create(
            site=self.site,
            site_values=self.test_config1
        )
        config2 = SiteConfigurationFactory.create(
            site=self.site2,
            site_values=self.test_config2
        )

        # Make sure entry is saved and retrieved correctly
        assert SiteConfiguration.get_configuration_for_org(self.test_config1['course_org_filter']) == config1
        assert SiteConfiguration.get_configuration_for_org(self.test_config2['course_org_filter']) == config2
        assert SiteConfiguration.get_configuration_for_org('something else') is None

    def test_get_all_orgs(self):
        """
        Test that get_all_orgs returns all orgs from site configuration.
        """
        expected_orgs = [self.test_config1['course_org_filter'], self.test_config2['course_org_filter']]
        # add SiteConfiguration to database
        SiteConfigurationFactory.create(
            site=self.site,
            site_values=self.test_config1
        )
        SiteConfigurationFactory.create(
            site=self.site2,
            site_values=self.test_config2
        )

        # Test that the default value is returned if the value for the given key is not found in the configuration
        self.assertCountEqual(SiteConfiguration.get_all_orgs(), expected_orgs)

    def test_get_all_orgs_returns_only_enabled(self):
        """
        Test that get_all_orgs returns only those orgs whose configurations are enabled.
        """
        expected_orgs = [self.test_config2['course_org_filter']]
        # add SiteConfiguration to database
        SiteConfigurationFactory.create(
            site=self.site,
            site_values=self.test_config1,
            enabled=False,
        )
        SiteConfigurationFactory.create(
            site=self.site2,
            site_values=self.test_config2
        )

        # Test that the default value is returned if the value for the given key is not found in the configuration
        self.assertCountEqual(SiteConfiguration.get_all_orgs(), expected_orgs)
