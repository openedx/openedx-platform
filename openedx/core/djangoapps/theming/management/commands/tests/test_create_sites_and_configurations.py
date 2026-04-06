"""
Test cases for create_sites_and_configurations command.
"""

from unittest import mock

import pytest
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.core.management import CommandError, call_command
from django.test import TestCase
from oauth2_provider.models import Application

from common.djangoapps.student.models import UserProfile
from openedx.core.djangoapps.oauth_dispatch.models import ApplicationAccess
from openedx.core.djangoapps.theming.models import SiteTheme

SITES = ["site_a", "site_b"]
MANAGEMENT_COMMAND_PATH = "openedx.core.djangoapps.theming.management.commands.create_sites_and_configurations."


def _generate_site_config(dns_name, site_domain, devstack=False):
    """ Generate the site configuration for a given site """
    if devstack:
        lms_url_fmt = "{domain}-{dns_name}.e2e.devstack"
    else:
        lms_url_fmt = "{domain}-{dns_name}.sandbox.edx.org"

    return {
        "lms_url": lms_url_fmt.format(domain=site_domain, dns_name=dns_name),
        "platform_name": f"{site_domain}-{dns_name}"
    }


def _get_sites(dns_name, devstack=False):
    """ Creates the mocked data for management command """
    sites = {}

    if devstack:
        site_domain_fmt = "{site}-{dns_name}.e2e.devstack"
    else:
        site_domain_fmt = "{site}-{dns_name}.sandbox.edx.org"

    for site in SITES:
        sites.update({
            site: {
                "theme_dir_name": f"{site}_dir_name",
                "configuration": _generate_site_config(dns_name, site),
                "site_domain": site_domain_fmt.format(site=site, dns_name=dns_name)
            }
        })
    return sites


class TestCreateSiteAndConfiguration(TestCase):
    """ Test the create_site_and_configuration command """
    def setUp(self):
        super().setUp()

        self.dns_name = "dummy_dns"
        self.theme_path = "/dummyA/dummyB/"

    def _assert_sites_are_valid(self):
        """
        Checks that data of all sites is valid
        """
        sites = Site.objects.filter(domain__contains=self.dns_name)
        assert len(sites) == len(SITES)
        for site in sites:
            if site.name in SITES:
                site_theme = SiteTheme.objects.get(site=site)

                assert site_theme.theme_dir_name == f'{site.name}_dir_name'

                self.assertDictEqual(
                    dict(site.configuration.values),
                    _generate_site_config(self.dns_name, site.name)
                )

    def _assert_service_user_is_valid(self, username):  # lint-amnesty, pylint: disable=missing-function-docstring
        service_user = User.objects.filter(username=username)
        assert len(service_user) == 1
        assert service_user[0].is_active
        assert service_user[0].is_staff
        assert service_user[0].is_superuser

        user_profile = UserProfile.objects.filter(user=service_user[0])
        assert len(user_profile) == 1
        return service_user

    def _assert_ecommerce_clients_are_valid(self, devstack=False):
        """
        Checks that all ecommerce clients are valid
        """
        service_user = self._assert_service_user_is_valid("ecommerce_worker")

        clients = Application.objects.filter(user=service_user[0])

        assert len(clients) == len(SITES)

        if devstack:
            ecommerce_url_fmt = "http://ecommerce-{site_name}-{dns_name}.e2e.devstack:18130/"
        else:
            ecommerce_url_fmt = "https://ecommerce-{site_name}-{dns_name}.sandbox.edx.org/"

        for client in clients:
            assert client.user.username == service_user[0].username
            site_name = [name for name in SITES if name in client.name][0]
            ecommerce_url = ecommerce_url_fmt.format(
                site_name=site_name,
                dns_name=self.dns_name
            )
            assert client.redirect_uris == f'{ecommerce_url}complete/edx-oauth2/'
            assert client.client_id == f'ecommerce-key-{site_name}'
            access = ApplicationAccess.objects.filter(application_id=client.id).first()
            assert access.scopes == ['user_id']

    def _assert_discovery_clients_are_valid(self, devstack=False):
        """
        Checks that all discovery clients are valid
        """
        service_user = self._assert_service_user_is_valid("lms_catalog_service_user")

        clients = Application.objects.filter(user=service_user[0])

        assert len(clients) == len(SITES)

        if devstack:
            discovery_url_fmt = "http://discovery-{site_name}-{dns_name}.e2e.devstack:18381/"
        else:
            discovery_url_fmt = "https://discovery-{site_name}-{dns_name}.sandbox.edx.org/"

        for client in clients:
            assert client.user.username == service_user[0].username
            site_name = [name for name in SITES if name in client.name][0]
            discovery_url = discovery_url_fmt.format(
                site_name=site_name,
                dns_name=self.dns_name
            )

            assert client.redirect_uris == f'{discovery_url}complete/edx-oauth2/'
            assert client.client_id == f'discovery-key-{site_name}'
            access = ApplicationAccess.objects.filter(application_id=client.id).first()
            assert access.scopes == ['user_id']

    def test_without_dns(self):
        """ Test the command without dns_name """
        with pytest.raises(CommandError):
            call_command(
                "create_sites_and_configurations"
            )

    @mock.patch(MANAGEMENT_COMMAND_PATH + "Command._enable_commerce_configuration")
    @mock.patch(MANAGEMENT_COMMAND_PATH + "Command._get_sites_data")
    def test_with_dns(self, mock_get_sites, mock_commerce):
        """ Test the command with dns_name """
        mock_get_sites.return_value = _get_sites(self.dns_name)
        mock_commerce.return_value = None
        call_command(
            "create_sites_and_configurations",
            "--dns-name", self.dns_name,
            "--theme-path", self.theme_path
        )
        self._assert_sites_are_valid()
        self._assert_discovery_clients_are_valid()
        self._assert_ecommerce_clients_are_valid()

        call_command(
            "create_sites_and_configurations",
            "--dns-name", self.dns_name,
            "--theme-path", self.theme_path
        )
        # if we run command with same dns then it will not duplicates the sites and oauth2 clients.
        self._assert_sites_are_valid()
        self._assert_discovery_clients_are_valid()
        self._assert_ecommerce_clients_are_valid()

        self.dns_name = "new-dns"
        mock_get_sites.return_value = _get_sites(self.dns_name)
        call_command(
            "create_sites_and_configurations",
            "--dns-name", self.dns_name,
            "--theme-path", self.theme_path
        )
        # if we run command with different dns existing oauth2 clients are updated with new dns
        self._assert_sites_are_valid()
        self._assert_discovery_clients_are_valid()
        self._assert_ecommerce_clients_are_valid()

    @mock.patch(MANAGEMENT_COMMAND_PATH + "Command._enable_commerce_configuration")
    @mock.patch(MANAGEMENT_COMMAND_PATH + "Command._get_sites_data")
    def test_with_devstack_and_dns(self, mock_get_sites, mock_commerce):
        """ Test the command with dns_name """
        mock_get_sites.return_value = _get_sites(self.dns_name, devstack=True)
        mock_commerce.return_value = None
        call_command(
            "create_sites_and_configurations",
            "--dns-name", self.dns_name,
            "--theme-path", self.theme_path,
            "--devstack"
        )
        self._assert_sites_are_valid()
        self._assert_discovery_clients_are_valid(devstack=True)
        self._assert_ecommerce_clients_are_valid(devstack=True)

        call_command(
            "create_sites_and_configurations",
            "--dns-name", self.dns_name,
            "--theme-path", self.theme_path,
            "--devstack"
        )
        # if we run command with same dns then it will not duplicates the sites and oauth2 clients.
        self._assert_sites_are_valid()
        self._assert_discovery_clients_are_valid(devstack=True)
        self._assert_ecommerce_clients_are_valid(devstack=True)
