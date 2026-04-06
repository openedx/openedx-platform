"""Utilities for writing unit tests that involve course embargos. """

import contextlib
from unittest.mock import MagicMock, patch

import geoip2.database
import maxminddb
from django.core.cache import cache
from django.urls import reverse

from .models import Country, CountryAccessRule, RestrictedCourse


@contextlib.contextmanager
def restrict_course(course_key, access_point="enrollment", disable_access_check=False):
    """Simulate that a course is restricted.

    This does two things:
    1) Configures country access rules so that the course is restricted.
    2) Mocks the GeoIP call so the user appears to be coming
        from a country that's blocked from the course.

    This is useful for tests that need to verify
    that restricted users won't be able to access
    particular views.

    Arguments:
        course_key (CourseKey): The location of the course to block.

    Keyword Arguments:
        access_point (str): Either "courseware" or "enrollment"

    Yields:
        str: A URL to the page in the embargo app that explains
            why the user was blocked.

    Example Usage:
    >>> with restrict_course(course_key) as redirect_url:
    >>>     # The client will appear to be coming from
    >>>     # an IP address that is blocked.
    >>>     resp = self.client.get(url)
    >>>     self.assertRedirects(resp, redirect_url)

    """
    # Clear the cache to ensure that previous tests don't interfere
    # with this test.
    cache.clear()

    # pylint: disable=unused-argument
    def mock_country(reader, country):
        """
        :param reader:
        :param country:
        :return:
        """
        magic_mock = MagicMock()
        magic_mock.country = MagicMock()
        # Simulate that the user is coming from the blacklisted country
        type(magic_mock.country).iso_code = 'IR'

        return magic_mock

    patcher = patch.object(maxminddb, 'open_database')
    patcher.start()
    country_patcher = patch.object(geoip2.database.Reader, 'country', mock_country)
    country_patcher.start()

    # Remove all existing rules for the course
    CountryAccessRule.objects.all().delete()

    # Create the country object
    # Ordinarily, we'd create models for every country,
    # but that would slow down the test suite.
    country, __ = Country.objects.get_or_create(country='IR')

    # Create a model for the restricted course
    restricted_course, __ = RestrictedCourse.objects.get_or_create(course_key=course_key)
    restricted_course.enroll_msg_key = 'default'
    restricted_course.access_msg_key = 'default'
    restricted_course.disable_access_check = disable_access_check
    restricted_course.save()

    # Ensure that there is a blacklist rule for the country
    CountryAccessRule.objects.get_or_create(
        restricted_course=restricted_course,
        country=country,
        rule_type='blacklist'
    )

    # Yield the redirect url so the tests don't need to know
    # the embargo messaging URL structure.
    redirect_url = reverse(
        'embargo:blocked_message',
        kwargs={
            'access_point': access_point,
            'message_key': 'default'
        }
    )
    yield redirect_url
    patcher.stop()
    country_patcher.stop()
