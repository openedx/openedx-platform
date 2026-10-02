"""
Tests for the CCX Coach MFE rollout flag.
"""

from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.ccx.toggles import LEGACY_CCX_COACH_DASHBOARD
from lms.djangoapps.courseware.tabs import get_course_tab_list
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase

CCX_COACH_MFE_URL = 'http://localhost:2003/ccx-coach'


@override_settings(CUSTOM_COURSES_EDX=True, CCX_COACH_MICROFRONTEND_URL=CCX_COACH_MFE_URL)
class CCXCoachMFEFlagTest(CcxTestCase, LoginEnrollmentTestCase):
    """
    The MFE serves CCX Coach by default; the ``ccx.legacy_ccx_coach_dashboard``
    flag opts a course back out to the legacy Django dashboard.
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

    # -- MFE is the default (flag off) -------------------------------------

    def test_dashboard_redirects_to_mfe_by_default_without_ccx(self):
        """With no CCX yet, the coach lands on the master course so the MFE shows its empty state."""
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 302
        assert response['Location'] == f'{CCX_COACH_MFE_URL}/{self.course.id}'

    def test_dashboard_redirects_to_mfe_by_default_with_ccx(self):
        """When the coach already has a CCX, the redirect targets that CCX."""
        ccx = self.make_ccx()
        ccx_key = CCXLocator.from_course_locator(self.course.id, str(ccx.id))

        response = self.client.get(self._dashboard_url(self.course.id))

        assert response.status_code == 302
        assert response['Location'] == f'{CCX_COACH_MFE_URL}/{ccx_key}'

    def test_tab_links_to_mfe_by_default(self):
        tab = self._ccx_tab(self.course, self.coach)
        assert tab is not None
        assert tab.link_func(self.course, reverse) == f'{CCX_COACH_MFE_URL}/{self.course.id}'

    def test_non_coach_still_forbidden_on_mfe_path(self):
        """The flag changes routing only; it does not relax access control."""
        self.client.logout()
        other_user = UserFactory.create(password=self.TEST_PASSWORD)
        self.client.login(username=other_user.username, password=self.TEST_PASSWORD)

        response = self.client.get(self._dashboard_url(self.course.id))

        assert response.status_code == 403

    # -- opting out to legacy (flag on) ------------------------------------

    @override_waffle_flag(LEGACY_CCX_COACH_DASHBOARD, active=True)
    def test_dashboard_renders_legacy_when_opted_out(self):
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 200

    @override_waffle_flag(LEGACY_CCX_COACH_DASHBOARD, active=True)
    def test_tab_links_to_legacy_when_opted_out(self):
        tab = self._ccx_tab(self.course, self.coach)
        assert tab is not None
        assert tab.link_func(self.course, reverse) == self._dashboard_url(self.course.id)

    # -- unset MFE URL keeps the legacy experience -------------------------

    @override_settings(CCX_COACH_MICROFRONTEND_URL=None)
    def test_legacy_served_when_mfe_url_unset(self):
        """
        Without a configured MFE URL the legacy dashboard is served, even though
        the MFE is otherwise the default, so nothing routes to a broken address.
        """
        response = self.client.get(self._dashboard_url(self.course.id))
        assert response.status_code == 200

        tab = self._ccx_tab(self.course, self.coach)
        assert tab.link_func(self.course, reverse) == self._dashboard_url(self.course.id)
