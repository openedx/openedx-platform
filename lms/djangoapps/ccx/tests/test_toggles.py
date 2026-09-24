"""
Tests for the CCX Coach MFE rollout flag.
"""

from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.ccx.toggles import ENABLE_CCX_COACH_MFE
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase

CCX_COACH_MFE_URL = 'http://localhost:2003/ccx-coach'


@override_settings(CUSTOM_COURSES_EDX=True, CCX_COACH_MICROFRONTEND_URL=CCX_COACH_MFE_URL)
class CCXCoachMFEFlagTest(CcxTestCase, LoginEnrollmentTestCase):
    """
    The ``ccx.enable_ccx_coach_mfe`` flag routes coaches to the MFE or the
    legacy Django dashboard.
    """

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.client.login(username=self.coach.username, password=self.TEST_PASSWORD)

    def _dashboard_url(self, course_id):
        return reverse('ccx_coach_dashboard', kwargs={'course_id': str(course_id)})

    def _ccx_tab(self, course, user):
        """Return the CCX coach tab for the course, or None."""
        return next(
            (tab for tab in get_course_tab_list(user, course) if tab.type == 'ccx_coach'),
            None,
        )

    # -- legacy (flag off, the default) ------------------------------------

    def test_dashboard_renders_legacy_when_flag_off(self):
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 200

    def test_tab_links_to_legacy_when_flag_off(self):
        tab = self._ccx_tab(self.course, self.coach)
        assert tab is not None
        assert tab.link_func(self.course, reverse) == self._dashboard_url(self.course.id)

    # -- MFE (flag on) -----------------------------------------------------

    @override_waffle_flag(ENABLE_CCX_COACH_MFE, active=True)
    def test_dashboard_redirects_to_mfe_without_ccx(self):
        """With no CCX yet, the coach lands on the master course so the MFE shows its empty state."""
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 302
        assert response['Location'] == f'{CCX_COACH_MFE_URL}/{self.course.id}'

    @override_waffle_flag(ENABLE_CCX_COACH_MFE, active=True)
    def test_dashboard_redirects_to_mfe_with_ccx(self):
        """When the coach already has a CCX, the redirect targets that CCX."""
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))

        response = self.client.get(self._dashboard_url(self.course.id))

        assert response.status_code == 302
        assert response['Location'] == f'{CCX_COACH_MFE_URL}/{ccx_key}'

    @override_waffle_flag(ENABLE_CCX_COACH_MFE, active=True)
    def test_tab_links_to_mfe_when_flag_on(self):
        tab = self._ccx_tab(self.course, self.coach)
        assert tab is not None
        assert tab.link_func(self.course, reverse) == f'{CCX_COACH_MFE_URL}/{self.course.id}'

    # -- unset MFE URL keeps the legacy experience -------------------------

    @override_waffle_flag(ENABLE_CCX_COACH_MFE, active=True)
    @override_settings(CCX_COACH_MICROFRONTEND_URL=None)
    def test_legacy_served_when_mfe_url_unset(self):
        """
        An enabled flag with no configured MFE URL must not redirect to a broken
        address; the legacy dashboard is served instead.
        """
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 200

        tab = self._ccx_tab(self.course, self.coach)
        assert tab.link_func(self.course, reverse) == self._dashboard_url(self.course.id)

    @override_waffle_flag(ENABLE_CCX_COACH_MFE, active=True)
    def test_non_coach_still_forbidden_with_flag_on(self):
        """The flag changes routing only; it does not relax access control."""
        self.client.logout()
        other_user = UserFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=other_user.username, password=self.TEST_PASSWORD)

        response = self.client.get(self._dashboard_url(self.course.id))

        assert response.status_code == 403
