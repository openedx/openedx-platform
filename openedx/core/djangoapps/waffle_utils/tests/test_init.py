"""
Tests for waffle utils features.
"""
# pylint: disable=toggle-missing-annotation

from unittest.mock import patch

import crum
import ddt
from django.test.client import RequestFactory
from edx_django_utils.cache import RequestCache
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from openedx.core.djangoapps.waffle_utils.models import WaffleFlagCourseOverrideModel, WaffleFlagOrgOverrideModel
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase


@ddt.ddt
class TestCourseWaffleFlag(CacheIsolationTestCase):
    """
    Tests the CourseWaffleFlag.
    """

    NAMESPACE_NAME = "test_namespace"
    FLAG_NAME = "test_flag"
    NAMESPACED_FLAG_NAME = NAMESPACE_NAME + "." + FLAG_NAME
    FLAG_2_NAME = "test_flag_2"
    NAMESPACED_FLAG_2_NAME = NAMESPACE_NAME + "." + FLAG_2_NAME

    TEST_ORG = "edX"
    TEST_COURSE_KEY = CourseKey.from_string(f"{TEST_ORG}/DemoX/Demo_Course")
    TEST_COURSE_2_KEY = CourseKey.from_string(f"{TEST_ORG}/DemoX/Demo_Course_2")
    TEST_COURSE_3_KEY = CourseKey.from_string("CollegeX/DemoX/Demo_Course")
    TEST_COURSE_FLAG = CourseWaffleFlag(NAMESPACED_FLAG_NAME, __name__)

    def setUp(self):
        super().setUp()
        request = RequestFactory().request()
        self.addCleanup(crum.set_current_request, None)
        crum.set_current_request(request)
        RequestCache.clear_all_namespaces()

    @ddt.data(
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, True),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, True),
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, False),
    )
    @ddt.unpack
    def test_course_waffle_flag(self, waffle_enabled, course_override, result):
        """
        Tests various combinations of a flag being set in waffle and overridden for a course.
        """
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=waffle_enabled):
            with patch.object(WaffleFlagCourseOverrideModel, 'override_value', return_value=course_override):
                # check twice to test that the result is properly cached
                assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_KEY) == result
                assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_KEY) == result
                # result is cached, so override check should happen only once
                # pylint: disable=no-member
                WaffleFlagCourseOverrideModel.override_value.assert_called_once_with(
                    self.NAMESPACED_FLAG_NAME,
                    self.TEST_COURSE_KEY
                )
            # Check flag for a second course.
            # This should be the same cached value as for overriden flag.
            assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_2_KEY) == waffle_enabled
        # Check the default value for the second course after the cache was restored
        assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_2_KEY) is False

    @ddt.data(
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
    )
    @ddt.unpack
    def test_matching_org_override_waffle_flag(self, waffle_enabled, org_override_choice, is_enabled):
        """
        Tests various combinations of a flag being set in waffle and overridden for an org
        which is the org which authored/owns the course.
        Since the org-level override has the same org as the course being checked, the org-level
        override's on/off/unset state determines whether is CourseWaffleFlag is active or not.

        on    = active (enabled)
        off   = inactive (disabled)
        unset = mirror the base waffle flag's activity
        """
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=self.NAMESPACED_FLAG_NAME,
            org=self.TEST_ORG,
            override_choice=org_override_choice,
            note='',
            enabled=True
        )
        # Both course keys should match the org-level override.
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=waffle_enabled):
            assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_KEY) == is_enabled
            assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_2_KEY) == is_enabled

    @ddt.data(
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, True),
    )
    @ddt.unpack
    def test_not_matching_org_override_waffle_flag(self, waffle_enabled, org_override_choice, is_enabled):
        """
        Tests various combinations of a flag being set in waffle and overridden for an org
        which is *not* the target course's org.
        Since the org-level override isn't relevant to the course being checked, whether the
        waffle flag is active/inactive determines whether the CourseWaffleFlag is active or not.

        So whether the non-matching org override is on/off/unset, simply mirror the base waffle flag's activity.
        """
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=self.NAMESPACED_FLAG_NAME,
            org=self.TEST_ORG,
            override_choice=org_override_choice,
            note='',
            enabled=True
        )
        # Org doesn't match the course key, so should never be enabled.
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=waffle_enabled):
            assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_3_KEY) == is_enabled

    @ddt.data(
        # "unset" isn't a typical override value - it nullifies the presence of the override.
        # Since both overrides are "unset", use the legacy waffle flag behavior and reflect the flag's active value.
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        # Since the course override matches the course ID and is on, the waffle flag is enabled.
        # The org override isn't relevant in this situation.
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, True),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.on, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, True),
        # Since the course override is nullified and the org override matches
        # the course ID and is on, the waffle flag is enabled.
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        # Since the course override matches the course ID but is off, the waffle flag is *not* enabled.
        # The org override isn't relevant in this situation - it's overridden by the course override.
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, False),
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        # Since the either the course override or the org override matches the course ID but is off
        # AND the other course/org override is unset/nullified, the waffle flag is *not* enabled.
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.off, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (False, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagCourseOverrideModel.ALL_CHOICES.unset, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
    )
    @ddt.unpack
    def test_matching_course_and_org_override_waffle_flag(
        self, waffle_enabled, course_override_choice, org_override_choice, is_enabled
    ):
        """
        Tests various combinations of a flag being set in waffle and overridden for both a matching
        course ID and a matching org - the org which authored/owns the course.
        Demonstrates the priorities of the two overrides - course and org.
        """
        WaffleFlagCourseOverrideModel.objects.create(
            waffle_flag=self.NAMESPACED_FLAG_NAME,
            course_id=self.TEST_COURSE_KEY,
            override_choice=course_override_choice,
            note='',
            enabled=True
        )
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=self.NAMESPACED_FLAG_NAME,
            org=self.TEST_ORG,
            override_choice=org_override_choice,
            note='',
            enabled=True
        )
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=waffle_enabled):
            assert self.TEST_COURSE_FLAG.is_enabled(self.TEST_COURSE_KEY) == is_enabled

    def test_undefined_waffle_flag(self):
        """
        Test flag with undefined waffle flag.
        """
        test_course_flag = CourseWaffleFlag(self.NAMESPACED_FLAG_NAME, __name__)

        with patch.object(
            WaffleFlagCourseOverrideModel,
            'override_value',
            return_value=WaffleFlagCourseOverrideModel.ALL_CHOICES.unset
        ):
            # check twice to test that the result is properly cached
            assert test_course_flag.is_enabled(self.TEST_COURSE_KEY) is False
            assert test_course_flag.is_enabled(self.TEST_COURSE_KEY) is False
            # result is cached, so override check should happen once
            # pylint: disable=no-member
            WaffleFlagCourseOverrideModel.override_value.assert_called_once_with(
                self.NAMESPACED_FLAG_NAME,
                self.TEST_COURSE_KEY
            )

    def test_without_request_and_undefined_waffle(self):
        """
        Test the flag behavior when outside a request context and waffle data undefined.
        """
        crum.set_current_request(None)
        test_course_flag = CourseWaffleFlag(self.NAMESPACED_FLAG_NAME, __name__)
        assert test_course_flag.is_enabled(self.TEST_COURSE_KEY) is False

    def test_without_request_and_everyone_active_waffle(self):
        """
        Test the flag behavior when outside a request context and waffle active for everyone.
        """
        crum.set_current_request(None)

        test_course_flag = CourseWaffleFlag(self.NAMESPACED_FLAG_NAME, __name__)
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=True):
            assert test_course_flag.is_enabled(self.TEST_COURSE_KEY) is True

    @ddt.data(
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.unset, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.on, True),
        (False, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
        (True, WaffleFlagOrgOverrideModel.ALL_CHOICES.off, False),
    )
    @ddt.unpack
    def test_is_enabled_for_org(self, waffle_enabled, org_override_choice, is_enabled):
        """
        Tests is_enabled_for_org: an org override (on/off) takes precedence, otherwise
        the base waffle switch decides. Takes an org short_name, not a course key.

        on    = active (enabled)
        off   = inactive (disabled)
        unset = mirror the base waffle flag's activity
        """
        WaffleFlagOrgOverrideModel.objects.create(
            waffle_flag=self.NAMESPACED_FLAG_NAME,
            org=self.TEST_ORG,
            override_choice=org_override_choice,
            note='',
            enabled=True
        )
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=waffle_enabled):
            assert self.TEST_COURSE_FLAG.is_enabled_for_org(self.TEST_ORG) == is_enabled

    def test_is_enabled_for_org_no_override_uses_global_switch(self):
        """
        With no org override at all, is_enabled_for_org falls back to the global switch.
        """
        with override_waffle_flag(self.TEST_COURSE_FLAG, active=True):
            assert self.TEST_COURSE_FLAG.is_enabled_for_org("SomeUnoverriddenOrg") is True
        assert self.TEST_COURSE_FLAG.is_enabled_for_org("SomeUnoverriddenOrg") is False
