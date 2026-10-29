"""
Tests for EmbargoMiddleware
"""

from contextlib import contextmanager
from unittest import mock
from unittest.mock import MagicMock, patch

import ddt
import geoip2.database
import maxminddb
import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test.client import RequestFactory
from django.test.utils import override_settings

from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseRole,
    CourseStaffRole,
    GlobalStaff,
    OrgInstructorRole,
    OrgRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.waffle_utils.testutils import WAFFLE_TABLES
from openedx.core.djangolib.testing.utils import AUTHZ_TABLES, skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, mixed_store_config
from xmodule.modulestore.tests.factories import CourseFactory

from .. import api as embargo_api
from ..exceptions import InvalidAccessPoint
from ..models import Country, CountryAccessRule, GlobalRestrictedCountry, RestrictedCourse

QUERY_COUNT_TABLE_IGNORELIST = WAFFLE_TABLES + AUTHZ_TABLES

MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {})


@ddt.ddt
@override_settings(MODULESTORE=MODULESTORE_CONFIG, EMBARGO=True)
@skip_unless_lms
class EmbargoCheckAccessApiTests(ModuleStoreTestCase):
    """Test the embargo API calls to determine whether a user has access. """
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.user = UserFactory.create()
        self.restricted_course = RestrictedCourse.objects.create(course_key=self.course.id)
        Country.objects.create(country='US')
        Country.objects.create(country='IR')
        Country.objects.create(country='CU')

        # Clear the cache to prevent interference between tests
        cache.clear()

    @ddt.data(
        # IP country, profile_country, blacklist, whitelist, allow_access
        ('US', None, [], [], True),
        ('IR', None, ['IR', 'CU'], [], False),
        ('US', 'IR', ['IR', 'CU'], [], False),
        ('IR', 'IR', ['IR', 'CU'], [], False),
        ('US', None, [], ['US'], True),
        ('IR', None, [], ['US'], False),
        ('US', 'IR', [], ['US'], False),
    )
    @ddt.unpack
    def test_country_access_rules(self, ip_country, profile_country, blacklist, whitelist, allow_access):
        # Configure the access rules
        for whitelist_country in whitelist:
            CountryAccessRule.objects.create(
                rule_type=CountryAccessRule.WHITELIST_RULE,
                restricted_course=self.restricted_course,
                country=Country.objects.get(country=whitelist_country)
            )

        for blacklist_country in blacklist:
            CountryAccessRule.objects.create(
                rule_type=CountryAccessRule.BLACKLIST_RULE,
                restricted_course=self.restricted_course,
                country=Country.objects.get(country=blacklist_country)
            )

        # Configure the user's profile country
        if profile_country is not None:
            self.user.profile.country = profile_country
            self.user.profile.save()

        # Appear to make a request from an IP in a particular country
        with self._mock_geoip(ip_country):
            # Call the API.  Note that the IP address we pass in doesn't
            # matter, since we're injecting a mock for geo-location
            result = embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

        # Verify that the access rules were applied correctly
        assert result == allow_access

    def test_no_user_has_access(self):
        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=self.restricted_course,
            country=Country.objects.get(country='US')
        )

        # The user is set to None, because the user has not been authenticated.
        with self._mock_geoip(""):
            result = embargo_api.check_course_access(self.course.id, ip_addresses=['0.0.0.0'])
        assert result

    def test_no_user_blocked(self):
        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=self.restricted_course,
            country=Country.objects.get(country='US')
        )

        with self._mock_geoip('US'):
            # The user is set to None, because the user has not been authenticated.
            result = embargo_api.check_course_access(self.course.id, ip_addresses=['0.0.0.0'])
            assert not result

    def test_course_not_restricted(self):
        # No `RestrictedCourse` row for this course, and no `GlobalRestrictedCountry`
        # rows either, so `check_course_access` takes its fast path: it only needs
        # to warm the two "is anything restricted at all" caches, then returns
        # without ever looking at the IP or the user's profile.
        unrestricted_course = CourseFactory.create()
        with self.assertNumQueries(2):
            embargo_api.check_course_access(unrestricted_course.id, user=self.user, ip_addresses=['0.0.0.0'])

        # The second check should require no database queries - both caches
        # (restricted-course list, global-country list) are warm by now.
        with self.assertNumQueries(0):
            embargo_api.check_course_access(unrestricted_course.id, user=self.user, ip_addresses=['0.0.0.0'])

    def test_ip_v6(self):
        # Test the scenario that will go through every check
        # (restricted course, but pass all the checks)
        with self._mock_geoip('US'):
            result = embargo_api.check_course_access(self.course.id, user=self.user,
                                                     ip_addresses=['FE80::0202:B3FF:FE1E:8329'])
        assert result

    def test_country_access_fallback_to_continent_code(self):
        # Simulate Geolite2 falling back to a continent code
        # instead of a country code.  In this case, we should
        # allow the user access.
        with self._mock_geoip('EU'):
            result = embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])
            assert result

    def test_profile_country_db_null(self):
        # Django country fields treat NULL values inconsistently.
        # When saving a profile with country set to None, Django saves an empty string to the database.
        # However, when the country field loads a NULL value from the database, it sets
        # `country.code` to `None`.  This caused a bug in which country values created by
        # the original South schema migration -- which defaulted to NULL -- caused a runtime
        # exception when the embargo middleware treated the value as a string.
        # In order to simulate this behavior, we can't simply set `profile.country = None`.
        # (because when we save it, it will set the database field to an empty string instead of NULL)
        query = "UPDATE auth_userprofile SET country = NULL WHERE id = %s"
        connection.cursor().execute(query, [str(self.user.profile.id)])

        # Verify that we can check the user's access without error
        with self._mock_geoip('US'):
            result = embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])
        assert result

    def test_caching(self):
        with self._mock_geoip('US'):
            # Test the scenario that will go through every check
            # (restricted course, but pass all the checks)
            # This is the worst case, so it will hit all of the
            # caching code: restricted-course cache (1) + global-country
            # cache (1) + per-course country-access-rule cache (1) = 3.
            # `CountryAccessRule.check_country_access` caches its allowed-countries
            # list per course_key, so the IP check and the profile check below share
            # that single query rather than each paying for their own. This scenario
            # also doesn't pay for the `has_course_author_access` role lookup, since
            # that's deferred until a block is about to happen, and nothing blocks here.
            with self.assertNumQueries(3, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
                embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

            with self.assertNumQueries(0, table_ignorelist=QUERY_COUNT_TABLE_IGNORELIST):
                embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

    def test_caching_no_restricted_courses(self):
        RestrictedCourse.objects.all().delete()
        cache.clear()

        # Same fast path as `test_course_not_restricted` - see there for the count.
        with self.assertNumQueries(2):
            embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

        with self.assertNumQueries(0):
            embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

    @ddt.data(
        GlobalStaff,
        CourseStaffRole,
        CourseInstructorRole,
        OrgStaffRole,
        OrgInstructorRole,
    )
    def test_staff_access_country_block(self, staff_role_cls):
        # Add a country to the blacklist
        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=self.restricted_course,
            country=Country.objects.get(country='US')
        )

        # Appear to make a request from an IP in the blocked country
        with self._mock_geoip('US'):
            result = embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

        # Expect that the user is blocked, because the user isn't staff
        assert not result, "User should not have access because the user isn't staff."

        self._add_staff_role(staff_role_cls, self.course.id)

        # Now the user should have access
        with self._mock_geoip('US'):
            result = embargo_api.check_course_access(self.course.id, user=self.user, ip_addresses=['0.0.0.0'])

        assert result, 'User should have access because the user is staff.'

    @ddt.data(
        GlobalStaff,
        CourseStaffRole,
        CourseInstructorRole,
        OrgStaffRole,
        OrgInstructorRole,
    )
    def test_staff_access_global_country_block(self, staff_role_cls):
        # Staff should bypass a `GlobalRestrictedCountry` block too, on a
        # course that has no `RestrictedCourse` row at all.
        course_key = CourseFactory.create().id
        GlobalRestrictedCountry.objects.create(country=Country.objects.get(country='IR'))

        with self._mock_geoip('IR'):
            result = embargo_api.check_course_access(course_key, user=self.user, ip_addresses=['0.0.0.0'])
        assert not result, "User should not have access because the user isn't staff."

        self._add_staff_role(staff_role_cls, course_key)

        with self._mock_geoip('IR'):
            result = embargo_api.check_course_access(course_key, user=self.user, ip_addresses=['0.0.0.0'])
        assert result, 'User should have access because the user is staff.'

    def _add_staff_role(self, staff_role_cls, course_key):
        """Instantiate `staff_role_cls` for `course_key` (or its org) and add `self.user` to it."""
        if issubclass(staff_role_cls, CourseRole):
            staff_role = staff_role_cls(course_key)
        elif issubclass(staff_role_cls, OrgRole):
            staff_role = staff_role_cls(course_key.org)
        else:
            staff_role = staff_role_cls()
        staff_role.add_users(self.user)

    @ddt.data(
        # course_restricted, rule_type, rule_country, global_country, country, source, allow_access
        (False, None, None, 'IR', 'IR', 'ip', False),  # no RestrictedCourse row at all, blocked globally
        (False, None, None, 'IR', 'US', 'ip', True),  # no RestrictedCourse row, unrelated country is fine
        (False, None, None, 'IR', 'IR', 'profile', False),  # global block also applies via profile country
        (True, CountryAccessRule.BLACKLIST_RULE, 'CU', None, 'CU', 'ip', False),  # existing per-course rule, unaffected
        (True, CountryAccessRule.BLACKLIST_RULE, 'CU', None, 'US', 'ip', True),
        (True, CountryAccessRule.WHITELIST_RULE, 'IR', 'IR', 'IR', 'ip', False),  # global restriction beats whitelist
    )
    @ddt.unpack
    def test_global_restricted_country_access(
        self, course_restricted, rule_type, rule_country, global_country, country, source, allow_access,
    ):
        course_key = self.course.id if course_restricted else CourseFactory.create().id

        if rule_type is not None:
            CountryAccessRule.objects.create(
                rule_type=rule_type,
                restricted_course=self.restricted_course,
                country=Country.objects.get(country=rule_country),
            )

        if global_country is not None:
            GlobalRestrictedCountry.objects.create(country=Country.objects.get(country=global_country))

        if source == 'profile':
            self.user.profile.country = country
            self.user.profile.save()
            ip_country = ''
        else:
            ip_country = country

        with self._mock_geoip(ip_country):
            result = embargo_api.check_course_access(course_key, user=self.user, ip_addresses=['0.0.0.0'])
        assert result == allow_access

    def test_redirect_if_blocked_global_restricted_country(self):
        # A course with no `RestrictedCourse` row still redirects to the
        # default blocked-message page when blocked by `GlobalRestrictedCountry`.
        unrestricted_course = CourseFactory.create()
        GlobalRestrictedCountry.objects.create(country=Country.objects.get(country='IR'))

        request = RequestFactory().get('', HTTP_X_FORWARDED_FOR='0.0.0.0')
        request.user = self.user

        with self._mock_geoip('IR'):
            redirect_url = embargo_api.redirect_if_blocked(request, unrestricted_course.id, access_point='courseware')
        assert redirect_url == '/embargo/blocked-message/courseware/default/'

    def test_disable_access_check_does_not_bypass_global_restriction(self):
        # `disable_access_check` is a per-course escape hatch for `CountryAccessRule`
        # blocks. It must NOT let a `GlobalRestrictedCountry` block through too.
        self.restricted_course.disable_access_check = True
        self.restricted_course.save()
        GlobalRestrictedCountry.objects.create(country=Country.objects.get(country='IR'))

        request = RequestFactory().get('', HTTP_X_FORWARDED_FOR='0.0.0.0')
        request.user = self.user

        with self._mock_geoip('IR'):
            redirect_url = embargo_api.redirect_if_blocked(request, self.course.id, access_point='courseware')
        assert redirect_url is not None, "A global restriction should still redirect even with disable_access_check."

    def test_global_restriction_via_profile_not_masked_by_earlier_ip_rule_match(self):
        # A per-course `CountryAccessRule` match on the IP must not short-circuit
        # before the profile country is also checked against `GlobalRestrictedCountry` -
        # otherwise a globally-restricted user could slip through via disable_access_check
        # just because their IP happened to also fail a (unrelated) per-course rule first.
        self.restricted_course.disable_access_check = True
        self.restricted_course.save()
        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=self.restricted_course,
            country=Country.objects.get(country='CU'),
        )
        GlobalRestrictedCountry.objects.create(country=Country.objects.get(country='IR'))
        self.user.profile.country = 'IR'
        self.user.profile.save()

        request = RequestFactory().get('', HTTP_X_FORWARDED_FOR='0.0.0.0')
        request.user = self.user

        # IP matches the per-course blacklist (CU), not the global list - but the
        # profile country (IR) is globally restricted, and that must still win.
        with self._mock_geoip('CU'):
            redirect_url = embargo_api.redirect_if_blocked(request, self.course.id, access_point='courseware')
        assert redirect_url is not None, "Global restriction must not be masked by an earlier IP rule match."

    @ddt.data(
        # (Note that any '0.x.x.x' IP _should_ be blocked in this test.)
        # ips, allow access
        (['1.1.1.1', '2.2.2.2'], True),  # normal chain of access
        (['1.1.1.1', '0.0.0.0', '2.2.2.2'], False),  # tried to sneak a blocked IP in, but we caught it
    )
    @ddt.unpack
    @mock.patch('openedx.core.djangoapps.embargo.api.country_code_from_ip')
    def test_redirect_if_blocked_ips(self, ips, allow_access, mock_country):
        # Block the US
        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=self.restricted_course,
            country=Country.objects.get(country='US')
        )

        # Treat any ip that starts with zero as from the US
        mock_country.side_effect = lambda x: 'US' if x.startswith('0.') else 'XX'

        request = RequestFactory().get('', HTTP_X_FORWARDED_FOR=','.join(ips))
        request.user = self.user

        assert (embargo_api.redirect_if_blocked(request, self.course.id) is None) == allow_access

    @ddt.data(
        # access point, check disabled, allow access
        ('enrollment', False, False),  # 'enrollment' never changes blocked status
        ('enrollment', True, False),  # 'enrollment' never changes blocked status
        ('courseware', False, False),  # 'courseware' normally also leaves blocked status in place
        ('courseware', True, True),  # Unless the access check has been disabled, then we allow them
    )
    @ddt.unpack
    @mock.patch(
        'openedx.core.djangoapps.embargo.api._check_course_access',
        return_value=embargo_api._AccessCheckResult(False, False),  # pylint: disable=protected-access
    )
    def test_redirect_if_blocked_courseware(self, access_point, check_disabled, allow_access, _mock_access):  # noqa: PT019  # pylint: disable=line-too-long
        # blocked_globally=False here - this test is specifically about the
        # per-course disable_access_check override, which only ever applies
        # to a non-global (CountryAccessRule) block.
        self.restricted_course.disable_access_check = check_disabled
        self.restricted_course.save()

        request = RequestFactory().get('')
        maybe_url = embargo_api.redirect_if_blocked(request, self.course.id, access_point=access_point, user=self.user)
        assert (maybe_url is None) == allow_access

    @contextmanager
    def _mock_geoip(self, country_code):
        """
        Mock for the GeoIP module.
        """

        # pylint: disable=unused-argument
        def mock_country(reader, country):
            """
            :param reader:
            :param country:
            :return:
            """
            magic_mock = MagicMock()
            magic_mock.country = MagicMock()
            type(magic_mock.country).iso_code = country_code

            return magic_mock

        patcher = patch.object(maxminddb, 'open_database')
        patcher.start()
        country_patcher = patch.object(geoip2.database.Reader, 'country', new=mock_country)
        country_patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(country_patcher.stop)
        yield


@ddt.ddt
@override_settings(MODULESTORE=MODULESTORE_CONFIG, EMBARGO=True)
@skip_unless_lms
class EmbargoMessageUrlApiTests(UrlResetMixin, ModuleStoreTestCase):
    """Test the embargo API calls for retrieving the blocking message URLs. """

    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']
    ENABLED_CACHES = ['default', 'mongo_metadata_inheritance', 'loc_cache']

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @ddt.data(
        ('enrollment', '/embargo/blocked-message/enrollment/embargo/'),
        ('courseware', '/embargo/blocked-message/courseware/embargo/')
    )
    @ddt.unpack
    def test_message_url_path(self, access_point, expected_url_path):
        self._restrict_course(self.course.id)

        # Retrieve the URL to the blocked message page
        url_path = embargo_api.message_url_path(self.course.id, access_point)
        assert url_path == expected_url_path

    def test_message_url_path_caching(self):
        self._restrict_course(self.course.id)

        # The first time we retrieve the message, we'll need
        # to hit the database.
        with self.assertNumQueries(2):
            embargo_api.message_url_path(self.course.id, "enrollment")

        # The second time, we should be using cached values
        with self.assertNumQueries(0):
            embargo_api.message_url_path(self.course.id, "enrollment")

    @ddt.data('enrollment', 'courseware')
    def test_message_url_path_no_restrictions_for_course(self, access_point):
        # No restrictions for the course
        url_path = embargo_api.message_url_path(self.course.id, access_point)

        # Use a default path - the default for the access point the user was blocked at.
        # A course blocked only by `GlobalRestrictedCountry` has no `RestrictedCourse` row,
        # so this fallback is what an embargoed learner actually sees.
        assert url_path == f'/embargo/blocked-message/{access_point}/default/'

    def test_invalid_access_point(self):
        with pytest.raises(InvalidAccessPoint):
            embargo_api.message_url_path(self.course.id, "invalid")

    def test_message_url_stale_cache(self):
        # Retrieve the URL once, populating the cache with the list
        # of restricted courses.
        self._restrict_course(self.course.id)
        embargo_api.message_url_path(self.course.id, 'courseware')

        # Delete the restricted course entry
        RestrictedCourse.objects.get(course_key=self.course.id).delete()

        # Clear the message URL cache
        message_cache_key = (  # noqa: UP032
            'embargo.message_url_path.courseware.{course_key}'
        ).format(course_key=self.course.id)
        cache.delete(message_cache_key)

        # Try again.  Even though the cache results are stale,
        # we should still get a valid URL.
        url_path = embargo_api.message_url_path(self.course.id, 'courseware')
        assert url_path == '/embargo/blocked-message/courseware/default/'

    def _restrict_course(self, course_key):
        """Restrict the user from accessing the course. """
        country = Country.objects.create(country='us')
        restricted_course = RestrictedCourse.objects.create(
            course_key=course_key,
            enroll_msg_key='embargo',
            access_msg_key='embargo'
        )
        CountryAccessRule.objects.create(
            restricted_course=restricted_course,
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            country=country
        )
