"""
This file demonstrates writing tests using the unittest module. These will pass
when you run "manage.py test".

Replace this with more appropriate tests for your application.
"""


import itertools
from datetime import timedelta
from unittest.mock import patch

import ddt
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils.timezone import now
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.course_modes.helpers import enrollment_mode_display
from common.djangoapps.course_modes.models import (
    CourseMode,
    Mode,
    get_cosmetic_display_price,
    invalidate_course_mode_cache,
)
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class CourseModeModelTest(TestCase):
    """
    Tests for the CourseMode model
    """
    NOW = 'now'
    DATES = {
        NOW: now(),
        None: None,
    }

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator('Test', 'TestCourse', 'TestCourseRun')
        CourseMode.objects.all().delete()

    def tearDown(self):
        super().tearDown()
        invalidate_course_mode_cache(sender=None)

    def create_mode(
            self,
            mode_slug,
            mode_name,
            min_price=0,
            suggested_prices='',
            currency='usd',
            expiration_datetime=None,
    ):
        """
        Create a new course mode
        """
        return CourseMode.objects.get_or_create(
            course_id=self.course_key,
            mode_display_name=mode_name,
            mode_slug=mode_slug,
            min_price=min_price,
            suggested_prices=suggested_prices,
            currency=currency,
            _expiration_datetime=expiration_datetime,
        )

    def test_save(self):
        """ Verify currency is always lowercase. """
        cm, __ = self.create_mode('honor', 'honor', 0, '', 'USD')
        assert cm.currency == 'usd'

        cm.currency = 'GHS'
        cm.save()
        assert cm.currency == 'ghs'

    def test_modes_for_course_empty(self):
        """
        If we can't find any modes, we should get back the default mode
        """
        # shouldn't be able to find a corresponding course
        modes = CourseMode.modes_for_course(self.course_key)
        assert [CourseMode.DEFAULT_MODE] == modes

    def test_nodes_for_course_single(self):
        """
        Find the modes for a course with only one mode
        """

        self.create_mode('verified', 'Verified Certificate', 10)
        modes = CourseMode.modes_for_course(self.course_key)
        mode = Mode('verified', 'Verified Certificate', 10, '', 'usd', None, None, None, None, None, None)
        assert [mode] == modes

        modes_dict = CourseMode.modes_for_course_dict(self.course_key)
        assert modes_dict['verified'] == mode
        assert CourseMode.mode_for_course(self.course_key, 'verified') == mode

    def test_modes_for_course_multiple(self):
        """
        Finding the modes when there's multiple modes
        """
        mode1 = Mode('honor', 'Honor Code Certificate', 0, '', 'usd', None, None, None, None, None, None)
        mode2 = Mode('verified', 'Verified Certificate', 10, '', 'usd', None, None, None, None, None, None)
        set_modes = [mode1, mode2]
        for mode in set_modes:
            self.create_mode(mode.slug, mode.name, mode.min_price, mode.suggested_prices)

        modes = CourseMode.modes_for_course(self.course_key)
        assert modes == set_modes
        assert mode1 == CourseMode.mode_for_course(self.course_key, 'honor')
        assert mode2 == CourseMode.mode_for_course(self.course_key, 'verified')
        assert CourseMode.mode_for_course(self.course_key, 'DNE') is None

    def test_min_course_price_for_currency(self):
        """
        Get the min course price for a course according to currency
        """
        # no modes, should get 0
        assert 0 == CourseMode.min_course_price_for_currency(self.course_key, 'usd')

        # with mode with other currency, should get 0
        mode = Mode('audit', 'Audit', 30, '', 'eur', None, None, None, None, None, None)
        self.create_mode(mode.slug, mode.name, mode.min_price, mode.suggested_prices, mode.currency)
        assert 0 == CourseMode.min_course_price_for_currency(self.course_key, 'usd')

        # create some modes
        mode1 = Mode('honor', 'Honor Code Certificate', 10, '', 'usd', None, None, None, None, None, None)
        mode2 = Mode('verified', 'Verified Certificate', 20, '', 'usd', None, None, None, None, None, None)
        mode3 = Mode('honor', 'Honor Code Certificate', 80, '', 'cny', None, None, None, None, None, None)
        set_modes = [mode1, mode2, mode3]
        for mode in set_modes:
            self.create_mode(mode.slug, mode.name, mode.min_price, mode.suggested_prices, mode.currency)

        assert 10 == CourseMode.min_course_price_for_currency(self.course_key, 'usd')
        assert 80 == CourseMode.min_course_price_for_currency(self.course_key, 'cny')

    def test_modes_for_course_expired(self):
        expired_mode, _status = self.create_mode('verified', 'Verified Certificate', 10)
        expired_mode.expiration_datetime = now() + timedelta(days=-1)
        expired_mode.save()
        modes = CourseMode.modes_for_course(self.course_key)
        assert [CourseMode.DEFAULT_MODE] == modes

        mode1 = Mode('honor', 'Honor Code Certificate', 0, '', 'usd', None, None, None, None, None, None)
        self.create_mode(mode1.slug, mode1.name, mode1.min_price, mode1.suggested_prices)
        modes = CourseMode.modes_for_course(self.course_key)
        assert [mode1] == modes

        expiration_datetime = now() + timedelta(days=1)
        expired_mode.expiration_datetime = expiration_datetime
        expired_mode.save()
        expired_mode_value = Mode(
            'verified',
            'Verified Certificate',
            10,
            '',
            'usd',
            expiration_datetime,
            None,
            None,
            None,
            None,
            None
        )
        modes = CourseMode.modes_for_course(self.course_key)
        assert [expired_mode_value, mode1] == modes

        modes = CourseMode.modes_for_course(CourseLocator('TestOrg', 'TestCourse', 'TestRun'))
        assert [CourseMode.DEFAULT_MODE] == modes

    def test_verified_mode_for_course(self):
        self.create_mode('verified', 'Verified Certificate', 10)

        mode = CourseMode.verified_mode_for_course(self.course_key)

        assert mode.slug == 'verified'

        # verify that the professional mode is preferred
        self.create_mode('professional', 'Professional Education Verified Certificate', 10)

        mode = CourseMode.verified_mode_for_course(self.course_key)

        assert mode.slug == 'professional'

    def test_course_has_payment_options(self):
        # Has no payment options.
        honor, _ = self.create_mode('honor', 'Honor')
        assert not CourseMode.has_payment_options(self.course_key)

        # Now we do have a payment option.
        verified, _ = self.create_mode('verified', 'Verified', min_price=5)
        assert CourseMode.has_payment_options(self.course_key)

        # Remove the verified option.
        verified.delete()
        assert not CourseMode.has_payment_options(self.course_key)

        # Finally, give the honor mode payment options
        honor.suggested_prices = '5, 10, 15'
        honor.save()
        assert CourseMode.has_payment_options(self.course_key)

    def test_course_has_payment_options_with_no_id_professional(self):
        # Has payment options.
        self.create_mode('no-id-professional', 'no-id-professional', min_price=5)
        assert CourseMode.has_payment_options(self.course_key)

    @ddt.data(
        ([], True),
        ([("honor", 0), ("audit", 0), ("verified", 100)], True),
        ([("honor", 100)], False),
        ([("professional", 100)], False),
        ([("no-id-professional", 100)], False),
    )
    @ddt.unpack
    def test_can_auto_enroll(self, modes_and_prices, can_auto_enroll):
        # Create the modes and min prices
        for mode_slug, min_price in modes_and_prices:
            self.create_mode(mode_slug, mode_slug.capitalize(), min_price=min_price)

        # Verify that we can or cannot auto enroll
        assert CourseMode.can_auto_enroll(self.course_key) == can_auto_enroll

    @ddt.data(
        ([], None),
        (["honor", "audit", "verified"], "honor"),
        (["honor", "audit"], "honor"),
        (["audit", "verified"], "audit"),
        (["professional"], None),
        (["no-id-professional"], None),
        (["credit", "audit", "verified"], "audit"),
        (["credit"], None),
    )
    @ddt.unpack
    def test_auto_enroll_mode(self, modes, result):
        # Verify that the proper auto enroll mode is returned
        assert CourseMode.auto_enroll_mode(self.course_key, modes) == result

    def test_all_modes_for_courses(self):
        now_dt = now()
        future = now_dt + timedelta(days=1)
        past = now_dt - timedelta(days=1)

        # Unexpired, no expiration date
        CourseModeFactory.create(
            course_id=self.course_key,
            mode_display_name="Honor No Expiration",
            mode_slug="honor_no_expiration",
            expiration_datetime=None
        )

        # Unexpired, expiration date in future
        CourseModeFactory.create(
            course_id=self.course_key,
            mode_display_name="Honor Not Expired",
            mode_slug="honor_not_expired",
            expiration_datetime=future
        )

        # Expired
        CourseModeFactory.create(
            course_id=self.course_key,
            mode_display_name="Verified Expired",
            mode_slug="verified_expired",
            expiration_datetime=past
        )

        # We should get all of these back when querying for *all* course modes,
        # including ones that have expired.
        other_course_key = CourseLocator(org="not", course="a", run="course")
        all_modes = CourseMode.all_modes_for_courses([self.course_key, other_course_key])
        assert len(all_modes[self.course_key]) == 3
        assert all_modes[self.course_key][0].name == 'Honor No Expiration'
        assert all_modes[self.course_key][1].name == 'Honor Not Expired'
        assert all_modes[self.course_key][2].name == 'Verified Expired'

        # Check that we get a default mode for when no course mode is available
        assert len(all_modes[other_course_key]) == 1
        assert all_modes[other_course_key][0] == CourseMode.DEFAULT_MODE

    @ddt.data('', 'no-id-professional', 'professional', 'verified')
    def test_course_has_professional_mode(self, mode):
        # check the professional mode.

        self.create_mode(mode, 'course mode', 10)
        modes_dict = CourseMode.modes_for_course_dict(self.course_key)

        if mode in ['professional', 'no-id-professional']:
            assert CourseMode.has_professional_mode(modes_dict)
        else:
            assert not CourseMode.has_professional_mode(modes_dict)

    @ddt.data('no-id-professional', 'professional', 'verified')
    def test_course_is_professional_mode(self, mode):
        # check that tuple has professional mode

        course_mode, __ = self.create_mode(mode, 'course mode', 10)
        if mode in ['professional', 'no-id-professional']:
            assert CourseMode.is_professional_mode(course_mode.to_tuple())
        else:
            assert not CourseMode.is_professional_mode(course_mode.to_tuple())

    def test_course_is_professional_mode_with_invalid_tuple(self):
        # check that tuple has professional mode with None
        assert not CourseMode.is_professional_mode(None)

    @ddt.data(
        ('no-id-professional', False),
        ('professional', True),
        ('verified', True),
        ('honor', False),
        ('audit', False)
    )
    @ddt.unpack
    def test_is_verified_slug(self, mode_slug, is_verified):
        # check that mode slug is verified or not
        if is_verified:
            assert CourseMode.is_verified_slug(mode_slug)
        else:
            assert not CourseMode.is_verified_slug(mode_slug)

    @ddt.data(*itertools.product(
        (
            CourseMode.HONOR,
            CourseMode.AUDIT,
            CourseMode.VERIFIED,
            CourseMode.PROFESSIONAL,
            CourseMode.NO_ID_PROFESSIONAL_MODE
        ),
        (NOW, None),
    ))
    @ddt.unpack
    def test_invalid_mode_expiration(self, mode_slug, exp_dt_name):
        exp_dt = self.DATES[exp_dt_name]
        is_error_expected = CourseMode.is_professional_slug(mode_slug) and exp_dt is not None
        try:
            self.create_mode(mode_slug=mode_slug, mode_name=mode_slug.title(), expiration_datetime=exp_dt, min_price=10)
            assert not is_error_expected, 'Expected a ValidationError to be thrown.'
        except ValidationError as exc:
            assert is_error_expected, 'Did not expect a ValidationError to be thrown.'
            assert exc.messages == ['Professional education modes are not allowed to have expiration_datetime set.']

    @ddt.data(
        "verified",
        "honor",
        "audit",
        "professional",
        "no-id-professional",
    )
    def test_enrollment_mode_display(self, mode):
        assert enrollment_mode_display(mode, self.course_key) == \
               self._enrollment_display_modes_dicts(mode)

    @ddt.data(
        (['honor', 'verified', 'credit'], ['honor', 'verified']),
        (['professional', 'credit'], ['professional']),
    )
    @ddt.unpack
    def test_hide_credit_modes(self, available_modes, expected_selectable_modes):
        # Create the course modes
        for mode in available_modes:
            CourseModeFactory.create(
                course_id=self.course_key,
                mode_display_name=mode,
                mode_slug=mode,
            )

        # Check the selectable modes, which should exclude credit
        selectable_modes = CourseMode.modes_for_course_dict(self.course_key)
        self.assertCountEqual(list(selectable_modes.keys()), expected_selectable_modes)

        # When we get all unexpired modes, we should see credit as well
        all_modes = CourseMode.modes_for_course_dict(self.course_key, only_selectable=False)
        self.assertCountEqual(list(all_modes.keys()), available_modes)

    def _enrollment_display_modes_dicts(self, mode):
        """
        Helper function to generate the enrollment display mode dict.
        """
        dict_keys = ['enrollment_title', 'enrollment_value', 'show_image', 'image_alt', 'display_mode']
        display_values = {
            "verified": ["You're enrolled as a verified student", "Verified", True, 'ID Verified Ribbon/Badge',
                         'verified'],
            "honor": ["You're enrolled as an honor code student", "Honor Code", False, '', 'honor'],
            "audit": ["", "", False, '', 'audit'],
            "professional": ["You're enrolled as a professional education student", "Professional Ed", False, '',
                             'professional'],
            "no-id-professional": ["You're enrolled as a professional education student", "Professional Ed", False, '',
                                   'professional'],
        }

        return dict(list(zip(dict_keys, display_values.get(mode))))

    def test_expiration_datetime_explicitly_set(self):
        """ Verify that setting the expiration_date property sets the explicit flag. """
        verified_mode, __ = self.create_mode('verified', 'Verified Certificate', 10)
        now_dt = now()
        verified_mode.expiration_datetime = now_dt

        assert verified_mode.expiration_datetime_is_explicit
        assert verified_mode.expiration_datetime == now_dt

    def test_expiration_datetime_not_explicitly_set(self):
        """ Verify that setting the _expiration_date property does not set the explicit flag. """
        verified_mode, __ = self.create_mode('verified', 'Verified Certificate', 10)
        now_dt = now()
        verified_mode._expiration_datetime = now_dt  # pylint: disable=protected-access

        assert not verified_mode.expiration_datetime_is_explicit
        assert verified_mode.expiration_datetime == now_dt

    def test_expiration_datetime_explicitly_set_to_none(self):
        """ Verify that setting the _expiration_date property does not set the explicit flag. """
        verified_mode, __ = self.create_mode('verified', 'Verified Certificate', 10)
        assert not verified_mode.expiration_datetime_is_explicit

        verified_mode.expiration_datetime = None
        assert not verified_mode.expiration_datetime_is_explicit
        assert verified_mode.expiration_datetime is None

    @ddt.data(
        (False, CourseMode.AUDIT, False),
        (False, CourseMode.HONOR, True),
        (False, CourseMode.VERIFIED, True),
        (False, CourseMode.CREDIT_MODE, True),
        (False, CourseMode.PROFESSIONAL, True),
        (False, CourseMode.NO_ID_PROFESSIONAL_MODE, True),
        (True, CourseMode.AUDIT, False),
        (True, CourseMode.HONOR, False),
        (True, CourseMode.VERIFIED, True),
        (True, CourseMode.CREDIT_MODE, True),
        (True, CourseMode.PROFESSIONAL, True),
        (True, CourseMode.NO_ID_PROFESSIONAL_MODE, True),
    )
    @ddt.unpack
    def test_eligible_for_cert(self, disable_honor_cert, mode_slug, expected_eligibility):
        """Verify that non-audit modes are eligible for a cert."""
        with override_settings(FEATURES={'DISABLE_HONOR_CERTIFICATES': disable_honor_cert}):
            assert CourseMode.is_eligible_for_certificate(mode_slug) == expected_eligibility

    @ddt.data(
        (CourseMode.AUDIT, False),
        (CourseMode.HONOR, False),
        (CourseMode.VERIFIED, True),
        (CourseMode.CREDIT_MODE, False),
        (CourseMode.PROFESSIONAL, True),
        (CourseMode.NO_ID_PROFESSIONAL_MODE, False),
        (CourseMode.MASTERS, False),
    )
    @ddt.unpack
    def test_verified_min_price(self, mode_slug, is_error_expected):
        """Verify that verified modes have a price."""
        try:
            self.create_mode(mode_slug=mode_slug, mode_name=mode_slug.title(), min_price=0)
        except ValidationError:
            assert is_error_expected, 'Did not expect a ValidationError to be thrown.'
        else:
            assert not is_error_expected, 'Expected a ValidationError to be thrown.'

    @ddt.data(
        ([], False),
        ([CourseMode.VERIFIED, CourseMode.AUDIT], False),
        ([CourseMode.MASTERS], True),
        ([CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.MASTERS], True)
    )
    @ddt.unpack
    def test_contains_masters_mode(self, available_modes, expected_contains_masters_mode):
        for mode in available_modes:
            self.create_mode(mode, mode, 10)

        modes = CourseMode.modes_for_course_dict(self.course_key)
        assert CourseMode.contains_masters_mode(modes) == expected_contains_masters_mode

    @ddt.data(
        ([], False),
        ([CourseMode.VERIFIED, CourseMode.AUDIT], False),
        ([CourseMode.MASTERS], True),
        ([CourseMode.VERIFIED, CourseMode.AUDIT, CourseMode.MASTERS], False)
    )
    @ddt.unpack
    def test_is_masters_only(self, available_modes, expected_is_masters_only):
        for mode in available_modes:
            self.create_mode(mode, mode, 10)

        assert CourseMode.is_masters_only(self.course_key) == expected_is_masters_only


class TestCourseOverviewIntegration(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def test_course_overview_version_update(self):
        course = CourseFactory.create()
        course_overview = CourseOverview.get_from_id(course.id)
        course_overview.version -= 1
        course_overview.save()
        course_mode = CourseModeFactory.create(course_id=course_overview.id)

        assert CourseMode.objects.filter(pk=course_mode.pk).exists()
        CourseOverview.get_from_id(course.id)
        assert CourseMode.objects.filter(pk=course_mode.pk).exists()


class TestDisplayPrices(ModuleStoreTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    @override_settings(PAID_COURSE_REGISTRATION_CURRENCY=["USD", "$"])
    def test_get_cosmetic_display_price(self):
        """
        Check that get_cosmetic_display_price() returns the correct price given its inputs.
        """
        course = CourseFactory.create()
        registration_price = 99
        course.cosmetic_display_price = 10
        with patch(
                'common.djangoapps.course_modes.models.CourseMode.min_course_price_for_currency',
                return_value=registration_price,
        ):
            # Since registration_price is set, it overrides the cosmetic_display_price and should be returned
            assert get_cosmetic_display_price(course) == '$99'

        registration_price = 0
        with patch(
                'common.djangoapps.course_modes.models.CourseMode.min_course_price_for_currency',
                return_value=registration_price,
        ):
            # Since registration_price is not set, cosmetic_display_price should be returned
            assert get_cosmetic_display_price(course) == '$10'

        course.cosmetic_display_price = 0
        # Since both prices are not set, there is no price, thus "Free"
        assert get_cosmetic_display_price(course) == 'Free'
