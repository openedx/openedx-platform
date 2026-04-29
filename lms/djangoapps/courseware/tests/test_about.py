"""
Test the about xblock
"""


import datetime
from unittest import mock
from unittest.mock import patch

import ddt
import pytz
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from milestones.tests.utils import MilestonesTestCaseMixin

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import CourseEnrollmentAllowedFactory, UserFactory
from common.djangoapps.track.tests import EventTrackingTestCase
from common.djangoapps.util.milestones_helpers import get_prerequisite_courses_display, set_prerequisite_courses
from openedx.core.djangoapps.models.course_details import CourseDetails
from openedx.features.course_experience import COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, course_home_url
from openedx.features.course_experience.waffle import ENABLE_COURSE_ABOUT_SIDEBAR_HTML
from xmodule.course_block import (
    CATALOG_VISIBILITY_ABOUT,
    CATALOG_VISIBILITY_NONE,
    COURSE_VISIBILITY_PRIVATE,
    COURSE_VISIBILITY_PUBLIC,
    COURSE_VISIBILITY_PUBLIC_OUTLINE,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory
from xmodule.modulestore.tests.utils import TEST_DATA_DIR
from xmodule.modulestore.xml_importer import import_course_from_xml

from .helpers import LoginEnrollmentTestCase

# HTML for registration button
REG_STR = "<form id=\"class_enroll_form\" method=\"post\" data-remote=\"true\" action=\"/change_enrollment\">"
SHIB_ERROR_STR = "The currently logged-in user account does not have permission to enroll in this course."


@ddt.ddt
class AboutTestCase(LoginEnrollmentTestCase, SharedModuleStoreTestCase, EventTrackingTestCase, MilestonesTestCaseMixin):
    """
    Tests about xblock.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        cls.course_without_about = CourseFactory.create(catalog_visibility=CATALOG_VISIBILITY_NONE)
        cls.course_with_about = CourseFactory.create(catalog_visibility=CATALOG_VISIBILITY_ABOUT)
        cls.purchase_course = CourseFactory.create(org='MITx', number='buyme', display_name='Course To Buy')
        CourseDetails.update_about_item(cls.course, 'overview', 'OOGIE BLOOGIE', None)
        CourseDetails.update_about_item(cls.course_without_about, 'overview', 'WITHOUT ABOUT', None)
        CourseDetails.update_about_item(cls.course_with_about, 'overview', 'WITH ABOUT', None)

    def setUp(self):
        super().setUp()

        self.course_mode = CourseMode(
            course_id=self.purchase_course.id,
            mode_slug=CourseMode.DEFAULT_MODE_SLUG,
            mode_display_name=CourseMode.DEFAULT_MODE_SLUG,
            min_price=10
        )
        self.course_mode.save()

    def test_anonymous_user(self):
        """
        This test asserts that a non-logged in user can visit the course about page
        """
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "OOGIE BLOOGIE")

        # Check that registration button is present
        self.assertContains(resp, REG_STR)

    def test_logged_in(self):
        """
        This test asserts that a logged-in user can visit the course about page
        """
        self.setup_user()
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "OOGIE BLOOGIE")

    def test_already_enrolled(self):
        """
        Asserts that the end user sees the appropriate messaging
        when he/she visits the course about page, but is already enrolled
        """
        self.setup_user()
        self.enroll(self.course, True)
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "You are enrolled in this course")
        self.assertContains(resp, "View Course")

    @override_settings(COURSE_ABOUT_VISIBILITY_PERMISSION="see_about_page")
    def test_visible_about_page_settings(self):
        """
        Verify that the About Page honors the permission settings in the course block
        """
        url = reverse('about_course', args=[str(self.course_with_about.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "WITH ABOUT")

        url = reverse('about_course', args=[str(self.course_without_about.id)])
        resp = self.client.get(url)
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_logged_in_marketing(self):
        self.setup_user()
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertRedirects(resp, course_home_url(self.course.id), fetch_redirect_response=False)

    @patch.dict(settings.FEATURES, {'ENABLE_COURSE_HOME_REDIRECT': False})
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': True})
    def test_logged_in_marketing_without_course_home_redirect(self):
        """
        Verify user is not redirected to course home page when
        ENABLE_COURSE_HOME_REDIRECT is set to False
        """
        self.setup_user()
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        # should not be redirected
        self.assertContains(resp, "OOGIE BLOOGIE")

    @patch.dict(settings.FEATURES, {'ENABLE_COURSE_HOME_REDIRECT': True})
    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_SITE': False})
    def test_logged_in_marketing_without_mktg_site(self):
        """
        Verify user is not redirected to course home page when
        ENABLE_MKTG_SITE is set to False
        """
        self.setup_user()
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        # should not be redirected
        self.assertContains(resp, "OOGIE BLOOGIE")

    @patch.dict(settings.FEATURES, {'ENABLE_PREREQUISITE_COURSES': True})
    def test_pre_requisite_course(self):
        pre_requisite_course = CourseFactory.create(org='edX', course='900', display_name='pre requisite course')
        course = CourseFactory.create(pre_requisite_courses=[str(pre_requisite_course.id)])
        self.setup_user()
        url = reverse('about_course', args=[str(course.id)])
        resp = self.client.get(url)
        assert resp.status_code == 200
        pre_requisite_courses = get_prerequisite_courses_display(course)
        pre_requisite_course_about_url = reverse('about_course', args=[str(pre_requisite_courses[0]['key'])])
        assert (
            f'You must successfully complete <a href="{pre_requisite_course_about_url}">'
            f'{pre_requisite_courses[0]["display"]}</a> before you begin this course.'
        ) in resp.content.decode(resp.charset).strip('\n')

    @patch.dict(settings.FEATURES, {'ENABLE_PREREQUISITE_COURSES': True})
    def test_about_page_unfulfilled_prereqs(self):
        pre_requisite_course = CourseFactory.create(
            org='edX',
            course='901',
            display_name='pre requisite course',
        )

        pre_requisite_courses = [str(pre_requisite_course.id)]

        # for this failure to occur, the enrollment window needs to be in the past
        course = CourseFactory.create(
            org='edX',
            course='1000',
            # closed enrollment
            enrollment_start=datetime.datetime(2013, 1, 1),
            enrollment_end=datetime.datetime(2014, 1, 1),
            start=datetime.datetime(2013, 1, 1),
            end=datetime.datetime(2030, 1, 1),
            pre_requisite_courses=pre_requisite_courses,
        )
        set_prerequisite_courses(course.id, pre_requisite_courses)

        self.setup_user()
        self.enroll(self.course, True)
        self.enroll(pre_requisite_course, True)

        url = reverse('about_course', args=[str(course.id)])
        resp = self.client.get(url)
        assert resp.status_code == 200
        pre_requisite_courses = get_prerequisite_courses_display(course)
        pre_requisite_course_about_url = reverse('about_course', args=[str(pre_requisite_courses[0]['key'])])
        assert (
            f'You must successfully complete <a href="{pre_requisite_course_about_url}">'
            f'{pre_requisite_courses[0]["display"]}</a> before you begin this course.'
        ) in resp.content.decode(resp.charset).strip('\n')

        url = reverse('about_course', args=[str(pre_requisite_course.id)])
        resp = self.client.get(url)
        assert resp.status_code == 200

    @ddt.data(
        [COURSE_VISIBILITY_PRIVATE],
        [COURSE_VISIBILITY_PUBLIC_OUTLINE],
        [COURSE_VISIBILITY_PUBLIC],
    )
    @ddt.unpack
    def test_about_page_public_view(self, course_visibility):
        """
        Assert that anonymous or unenrolled users see View Course option
        when unenrolled access flag is set
        """
        with mock.patch('xmodule.course_block.CourseBlock.course_visibility', course_visibility):
            with override_waffle_flag(COURSE_ENABLE_UNENROLLED_ACCESS_FLAG, active=True):
                url = reverse('about_course', args=[str(self.course.id)])
                resp = self.client.get(url)
        if course_visibility == COURSE_VISIBILITY_PUBLIC or course_visibility == COURSE_VISIBILITY_PUBLIC_OUTLINE:  # pylint: disable=consider-using-in
            self.assertContains(resp, "View Course")
        else:
            self.assertContains(resp, "Enroll Now")


class AboutTestCaseXML(LoginEnrollmentTestCase, ModuleStoreTestCase):
    """
    Tests for the course about page
    """
    def setUp(self):
        """
        Set up the tests
        """
        super().setUp()

        # The following test course (which lives at common/test/data/2014)
        # is closed; we're testing that an about page still appears when
        # the course is already closed
        self.xml_course_id = self.store.make_course_key('edX', 'detached_pages', '2014')
        import_course_from_xml(
            self.store,
            self.user.id,
            TEST_DATA_DIR,
            source_dirs=['2014'],
            static_content_store=None,
            target_id=self.xml_course_id,
            raise_on_failure=True,
            create_if_not_present=True,
        )

        # this text appears in that course's about page
        # common/test/data/2014/about/overview.html
        self.xml_data = "about page 463139"

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_logged_in_xml(self):
        self.setup_user()
        url = reverse('about_course', args=[str(self.xml_course_id)])
        resp = self.client.get(url)
        self.assertContains(resp, self.xml_data)

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_anonymous_user_xml(self):
        url = reverse('about_course', args=[str(self.xml_course_id)])
        resp = self.client.get(url)
        self.assertContains(resp, self.xml_data)


class AboutWithCappedEnrollmentsTestCase(LoginEnrollmentTestCase, SharedModuleStoreTestCase):
    """
    This test case will check the About page when a course has a capped enrollment
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(metadata={"max_student_enrollments_allowed": 1})
        CourseDetails.update_about_item(cls.course, 'overview', 'OOGIE BLOOGIE', None)

    def test_enrollment_cap(self):
        """
        This test will make sure that enrollment caps are enforced
        """
        self.setup_user()
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, '<a href="#" class="register">')

        self.enroll(self.course, verify=True)

        # pylint: disable=attribute-defined-outside-init
        # create a new account since the first account is already enrolled in the course
        self.email = 'foo_second@test.com'
        self.password = 'Password1234'
        self.username = 'test_second'
        self.create_account(self.username, self.email, self.password)
        self.activate_user(self.email)
        self.login(self.email, self.password)

        # Get the about page again and make sure that the page says that the course is full
        resp = self.client.get(url)
        self.assertContains(resp, "Course is full")

        # Try to enroll as well
        result = self.enroll(self.course)
        assert not result

        # Check that registration button is not present
        self.assertNotContains(resp, REG_STR)


class AboutWithInvitationOnly(SharedModuleStoreTestCase):
    """
    This test case will check the About page when a course is invitation only.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(metadata={"invitation_only": True})

    def test_invitation_only(self):
        """
        Test for user not logged in, invitation only course.
        """

        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "Enrollment in this course is by invitation only")

        # Check that registration button is not present
        self.assertNotContains(resp, REG_STR)

    def test_invitation_only_but_allowed(self):
        """
        Test for user logged in and allowed to enroll in invitation only course.
        """

        # Course is invitation only, student is allowed to enroll and logged in
        user = UserFactory.create(username='allowed_student', password='test', email='allowed_student@test.com')
        CourseEnrollmentAllowedFactory(email=user.email, course_id=self.course.id)
        self.client.login(username=user.username, password='test')

        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "Enroll Now")

        # Check that registration button is present
        self.assertContains(resp, REG_STR)


class AboutWithClosedEnrollment(ModuleStoreTestCase):
    """
    This test case will check the About page for a course that has enrollment start/end
    set but it is currently outside of that period.
    """
    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(metadata={"invitation_only": False})

        # Setup enrollment period to be in future
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)
        nextday = tomorrow + datetime.timedelta(days=1)

        self.course.enrollment_start = tomorrow
        self.course.enrollment_end = nextday
        self.course = self.update_course(self.course, self.user.id)

    def test_closed_enrollmement(self):
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        self.assertContains(resp, "Enrollment is Closed")

        # Check that registration button is not present
        self.assertNotContains(resp, REG_STR)

    def test_course_price_is_not_visible_in_sidebar(self):
        url = reverse('about_course', args=[str(self.course.id)])
        resp = self.client.get(url)
        # course price is not visible ihe course_about page when the course
        # mode is not set to honor
        self.assertNotContains(resp, '<span class="important-dates-item-text">$10</span>')


@ddt.ddt
class AboutSidebarHTMLTestCase(SharedModuleStoreTestCase):
    """
    This test case will check the About page for the content in the HTML sidebar.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    @ddt.data(
        ("", "", False),
        ("about_sidebar_html", "About Sidebar HTML Heading", False),
        ("about_sidebar_html", "", False),
        ("", "", True),
        ("about_sidebar_html", "About Sidebar HTML Heading", True),
        ("about_sidebar_html", "", True),
    )
    @ddt.unpack
    def test_html_sidebar_enabled(self, itemfactory_display_name, itemfactory_data, waffle_switch_value):
        with override_waffle_switch(ENABLE_COURSE_ABOUT_SIDEBAR_HTML, active=waffle_switch_value):
            if itemfactory_display_name:
                BlockFactory.create(
                    category="about",
                    parent_location=self.course.location,
                    display_name=itemfactory_display_name,
                    data=itemfactory_data,
                )
            url = reverse('about_course', args=[str(self.course.id)])
            resp = self.client.get(url)
            if waffle_switch_value and itemfactory_display_name and itemfactory_data:
                self.assertContains(resp, '<section class="about-sidebar-html">')
                self.assertContains(resp, itemfactory_data)
            else:
                self.assertNotContains(resp, '<section class="about-sidebar-html">')
