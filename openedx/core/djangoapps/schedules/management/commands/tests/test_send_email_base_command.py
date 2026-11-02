"""
Tests for send_email_base_command
"""


import datetime
from unittest import skipUnless
from unittest.mock import DEFAULT, Mock, patch

import ddt
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib.sites.models import Site

from openedx.core.djangoapps.schedules.management.commands import SendEmailBaseCommand
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms


@ddt.ddt
@skip_unless_lms
@skipUnless('openedx.core.djangoapps.schedules.apps.SchedulesConfig' in settings.INSTALLED_APPS,
            "Can't test schedules if the app isn't installed")
class TestSendEmailBaseCommand(CacheIsolationTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    def setUp(self):  # lint-amnesty, pylint: disable=super-method-not-called
        self.command = SendEmailBaseCommand()
        self.site = SiteFactory()
        self.site_config = SiteConfigurationFactory.create(site=self.site)

    def test_handle(self):
        with patch.object(self.command, 'send_emails') as send_emails:
            self.command.handle(site_domain_name=self.site.domain, date='2017-09-29')
            send_emails.assert_called_once_with(
                self.site,
                datetime.datetime(2017, 9, 29, tzinfo=ZoneInfo("UTC")),
                None,
                None
            )

    def test_handle_all_sites(self):
        with patch.object(self.command, 'send_emails') as send_emails:
            self.command.handle(site_domain_name=None, date='2017-09-29')
            expected_sites = Site.objects.all()
            for expected_site in expected_sites:
                send_emails.assert_any_call(
                    expected_site,
                    datetime.datetime(2017, 9, 29, tzinfo=ZoneInfo("UTC")),
                    None,
                    None
                )
            assert send_emails.call_count == len(expected_sites)

    def test_weeks_option(self):
        with patch.object(self.command, 'enqueue') as enqueue:
            self.command.handle(site_domain_name=self.site.domain, date='2017-09-29', weeks=12)
            assert enqueue.call_count == 12

    def test_send_emails(self):
        with patch.multiple(
            self.command,
            offsets=(1, 3, 5),
            enqueue=DEFAULT,
        ):
            arg = Mock(name='arg')
            kwarg = Mock(name='kwarg')
            self.command.send_emails(arg, kwarg=kwarg)
            assert not arg.called
            assert not kwarg.called

            for offset in self.command.offsets:
                self.command.enqueue.assert_any_call(offset, arg, kwarg=kwarg)  # lint-amnesty, pylint: disable=no-member
