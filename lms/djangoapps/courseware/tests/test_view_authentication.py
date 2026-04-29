"""
Check that view authentication works properly.
"""


import datetime
from unittest.mock import patch

import pytz
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag

from common.djangoapps.student.tests.factories import (
    BetaTesterFactory,
    CourseEnrollmentFactory,
    GlobalStaffFactory,
    InstructorFactory,
    OrgInstructorFactory,
    OrgStaffFactory,
    StaffFactory,
    UserFactory,
)
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.tests.helpers import CourseAccessTestMixin, LoginEnrollmentTestCase
from lms.djangoapps.instructor.toggles import LEGACY_INSTRUCTOR_DASHBOARD
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseTestConsentRequired
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestViewAuth(EnterpriseTestConsentRequired, ModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Check that view authentication works properly.
    """

    ACCOUNT_INFO = [('view@test.com', 'Password1234'), ('view2@test.com', 'Password1234')]
    ENABLED_SIGNALS = ['course_published']

    @staticmethod
    def _reverse_urls(names, course):
        """
        Reverse a list of course urls.

        `names` is a list of URL names that correspond to sections in a course.

        `course` is the instance of CourseBlock whose section URLs are to be returned.

        Returns a list URLs corresponding to section in the passed in course.

        """
        return [reverse(name, kwargs={'course_id': str(course.id)})
                for name in names]

    def _check_non_staff_light(self, course):
        """
        Check that non-staff have access to light urls.

        `course` is an instance of CourseBlock.
        """
        urls = [reverse('about_course', kwargs={'course_id': str(course.id)}),
                reverse('courses')]
        for url in urls:
            self.assert_request_status_code(200, url)

    def _check_non_staff_dark(self, course):
        """
        Check that non-staff don't have access to dark urls.
        """

        names = ['progress']
        urls = self._reverse_urls(names, course)
        urls.extend([
            reverse('book', kwargs={'course_id': str(course.id),
                                    'book_index': index})
            for index, __ in enumerate(course.textbooks)
        ])
        for url in urls:
            self.assert_request_status_code(302, url)

        self.assert_request_status_code(
            404, reverse('instructor_dashboard', kwargs={'course_id': str(course.id)})
        )

    def _check_staff(self, course):
        """
        Check that access is right for staff in course.
        """
        names = ['about_course', 'instructor_dashboard', 'progress']
        urls = self._reverse_urls(names, course)
        urls.extend([
            reverse('book', kwargs={'course_id': str(course.id),
                                    'book_index': index})
            for index in range(len(course.textbooks))
        ])
        for url in urls:
            # Instructor dashboard returns 302 (MFE redirect) by default
            if 'instructor' in url:
                self.assert_request_status_code(302, url)
            else:
                self.assert_request_status_code(200, url)

        # The student progress tab is not accessible to a student
        # before launch, so the instructor view-as-student feature
        # should return a 404.
        # TODO (vshnayder): If this is not the behavior we want, will need
        # to make access checking smarter and understand both the effective
        # user (the student), and the requesting user (the prof)
        url = reverse(
            'student_progress',
            kwargs={
                'course_id': str(course.id),
                'student_id': self.enrolled_user.id,
            }
        )
        self.assert_request_status_code(302, url)

    def _check_staff_legacy(self, course):
        """
        Check that access is right for staff in course with legacy instructor dashboard enabled.
        """
        names = ['about_course', 'instructor_dashboard', 'progress']
        urls = self._reverse_urls(names, course)
        urls.extend([
            reverse('book', kwargs={'course_id': str(course.id),
                                    'book_index': index})
            for index in range(len(course.textbooks))
        ])
        for url in urls:
            # With legacy flag enabled, all URLs return 200 (instructor dashboard skips MFE redirect)
            self.assert_request_status_code(200, url)

        # The student progress tab behavior is affected by legacy flag in normal scenarios
        url = reverse(
            'student_progress',
            kwargs={
                'course_id': str(course.id),
                'student_id': self.enrolled_user.id,
            }
        )
        self.assert_request_status_code(200, url)

    def _check_staff_legacy_dark_launch(self, course):
        """
        Check staff access during dark launch with legacy instructor dashboard enabled.
        In dark launch scenarios, student progress URL still returns 302 even with legacy flag.
        """
        names = ['about_course', 'instructor_dashboard', 'progress']
        urls = self._reverse_urls(names, course)
        urls.extend([
            reverse('book', kwargs={'course_id': str(course.id),
                                    'book_index': index})
            for index in range(len(course.textbooks))
        ])
        for url in urls:
            # With legacy flag enabled, all URLs return 200 (instructor dashboard skips MFE redirect)
            self.assert_request_status_code(200, url)

        # In dark launch scenarios, student progress URL still returns 302 even with legacy flag
        # because course access restrictions take precedence
        url = reverse(
            'student_progress',
            kwargs={
                'course_id': str(course.id),
                'student_id': self.enrolled_user.id,
            }
        )
        self.assert_request_status_code(302, url)

    def login(self, user):  # pylint: disable=arguments-differ
        return super().login(user.email, self.TEST_PASSWORD)

    def setUp(self):
        super().setUp()

        self.course = CourseFactory.create(number='999', display_name='Robot_Super_Course')
        self.courseware_chapter = BlockFactory.create(display_name='courseware')
        self.overview_chapter = BlockFactory.create(
            parent_location=self.course.location,
            display_name='Super Overview'
        )
        self.welcome_section = BlockFactory.create(
            parent_location=self.overview_chapter.location,
            display_name='Super Welcome'
        )
        self.welcome_unit = BlockFactory.create(
            parent_location=self.welcome_section.location,
            display_name='Super Unit'
        )
        self.course = modulestore().get_course(self.course.id)

        self.test_course = CourseFactory.create(org=self.course.id.org)
        self.other_org_course = CourseFactory.create(org='Other_Org_Course')
        self.sub_courseware_chapter = BlockFactory.create(
            parent_location=self.test_course.location,
            display_name='courseware'
        )
        self.sub_overview_chapter = BlockFactory.create(
            parent_location=self.sub_courseware_chapter.location,
            display_name='Overview'
        )
        self.sub_welcome_section = BlockFactory.create(
            parent_location=self.sub_overview_chapter.location,
            display_name='Welcome'
        )
        self.sub_welcome_unit = BlockFactory.create(
            parent_location=self.sub_welcome_section.location,
            display_name='New Unit'
        )
        self.test_course = modulestore().get_course(self.test_course.id)

        self.global_staff_user = GlobalStaffFactory()
        self.unenrolled_user = UserFactory(last_name="Unenrolled")

        self.enrolled_user = UserFactory(last_name="Enrolled")
        CourseEnrollmentFactory(user=self.enrolled_user, course_id=self.course.id)
        CourseEnrollmentFactory(user=self.enrolled_user, course_id=self.test_course.id)

        self.staff_user = StaffFactory(course_key=self.course.id)
        self.instructor_user = InstructorFactory(course_key=self.course.id)
        self.org_staff_user = OrgStaffFactory(course_key=self.course.id)
        self.org_instructor_user = OrgInstructorFactory(course_key=self.course.id)

    def test_instructor_page_access_nonstaff(self):
        """
        Verify non-staff cannot load the instructor
        dashboard, the grade views, and student profile pages.
        """
        self.login(self.enrolled_user)

        urls = [reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)}),
                reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})]

        # Shouldn't be able to get to the instructor pages
        for url in urls:
            self.assert_request_status_code(404, url)

    def test_staff_course_access(self):
        """
        Verify staff can load the staff dashboard, the grade views,
        and student profile pages for their course.
        """
        self.login(self.staff_user)

        # Now should be able to get to self.course, but not  self.test_course
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(404, url)

    def test_instructor_course_access(self):
        """
        Verify instructor can load the instructor dashboard, the grade views,
        and student profile pages for their course.
        """
        self.login(self.instructor_user)

        # Now should be able to get to self.course, but not  self.test_course
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(404, url)

    def test_org_staff_access(self):
        """
        Verify org staff can load the instructor dashboard, the grade views,
        and student profile pages for course in their org.
        """
        self.login(self.org_staff_user)
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.other_org_course.id)})
        self.assert_request_status_code(404, url)

    def test_org_instructor_access(self):
        """
        Verify org instructor can load the instructor dashboard, the grade views,
        and student profile pages for course in their org.
        """
        self.login(self.org_instructor_user)
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(302, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.other_org_course.id)})
        self.assert_request_status_code(404, url)

    def test_global_staff_access(self):
        """
        Verify the global staff user can access any course.
        """
        self.login(self.global_staff_user)

        # and now should be able to load both
        urls = [reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)}),
                reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})]

        for url in urls:
            self.assert_request_status_code(302, url)

    # Legacy instructor dashboard tests (with waffle flag enabled, expect 200 responses)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_staff_course_access_legacy(self):
        """
        Verify staff can load the legacy instructor dashboard (expects 200 response).
        """
        self.login(self.staff_user)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(404, url)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_instructor_course_access_legacy(self):
        """
        Verify instructor can load the legacy instructor dashboard (expects 200 response).
        """
        self.login(self.instructor_user)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(404, url)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_org_staff_access_legacy(self):
        """
        Verify org staff can load the legacy instructor dashboard (expects 200 response).
        """
        self.login(self.org_staff_user)
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.other_org_course.id)})
        self.assert_request_status_code(404, url)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_org_instructor_access_legacy(self):
        """
        Verify org instructor can load the legacy instructor dashboard (expects 200 response).
        """
        self.login(self.org_instructor_user)
        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})
        self.assert_request_status_code(200, url)

        url = reverse('instructor_dashboard', kwargs={'course_id': str(self.other_org_course.id)})
        self.assert_request_status_code(404, url)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_global_staff_access_legacy(self):
        """
        Verify the global staff user can access the legacy instructor dashboard (expects 200 response).
        """
        self.login(self.global_staff_user)

        urls = [reverse('instructor_dashboard', kwargs={'course_id': str(self.course.id)}),
                reverse('instructor_dashboard', kwargs={'course_id': str(self.test_course.id)})]

        for url in urls:
            self.assert_request_status_code(200, url)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    def test_staff_method_legacy(self):
        """
        Test the _check_staff_legacy helper method with legacy flag enabled (expects 200 response).
        """
        self.login(self.staff_user)
        self.enroll(self.course, True)

        # Test the _check_staff_legacy method which includes instructor dashboard checks
        self._check_staff_legacy(self.course)

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_dark_launch_enrolled_student(self):
        """
        Make sure that before course start, students can't access course
        pages.
        """

        # Make courses start in the future
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)
        self.course.start = tomorrow
        self.test_course.start = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        assert not self.course.has_started()
        assert not self.test_course.has_started()

        # First, try with an enrolled student
        self.login(self.enrolled_user)

        # shouldn't be able to get to anything except the light pages
        self._check_non_staff_light(self.course)
        self._check_non_staff_dark(self.course)
        self._check_non_staff_light(self.test_course)
        self._check_non_staff_dark(self.test_course)

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_dark_launch_instructor(self):
        """
        Make sure that before course start instructors can access the
        page for their course.
        """
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)
        self.course.start = tomorrow
        self.test_course.start = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        self.login(self.instructor_user)
        # Enroll in the classes---can't see courseware otherwise.
        self.enroll(self.course, True)
        self.enroll(self.test_course, True)

        # should now be able to get to everything for self.course
        self._check_staff(self.course)
        self._check_non_staff_light(self.test_course)
        self._check_non_staff_dark(self.test_course)

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_dark_launch_global_staff(self):
        """
        Make sure that before course start staff can access
        course pages.
        """
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)

        self.course.start = tomorrow
        self.test_course.start = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        self.login(self.global_staff_user)
        self.enroll(self.course, True)
        self.enroll(self.test_course, True)

        # and now should be able to load both
        self._check_staff(self.course)
        self._check_staff(self.test_course)

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_enrollment_period(self):
        """
        Check that enrollment periods work.
        """
        # Make courses start in the future
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)
        nextday = tomorrow + datetime.timedelta(days=1)
        yesterday = now - datetime.timedelta(days=1)

        # self.course's enrollment period hasn't started
        self.course.enrollment_start = tomorrow
        self.course.enrollment_end = nextday
        # test_course course's has
        self.test_course.enrollment_start = yesterday
        self.test_course.enrollment_end = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        # First, try with an enrolled student
        self.login(self.unenrolled_user)
        assert not self.enroll(self.course)
        assert self.enroll(self.test_course)

        # Then, try as an instructor
        self.logout()
        self.login(self.instructor_user)
        assert self.enroll(self.course)

        # Then, try as global staff
        self.logout()
        self.login(self.global_staff_user)
        assert self.enroll(self.course)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_dark_launch_instructor_legacy(self):
        """
        Make sure that before course start instructors can access the
        page for their course with legacy instructor dashboard enabled.
        """
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)
        self.course.start = tomorrow
        self.test_course.start = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        self.login(self.instructor_user)
        # Enroll in the classes---can't see courseware otherwise.
        self.enroll(self.course, True)
        self.enroll(self.test_course, True)

        # should now be able to get to everything for self.course
        self._check_staff_legacy_dark_launch(self.course)
        self._check_non_staff_dark(self.test_course)

    @override_waffle_flag(LEGACY_INSTRUCTOR_DASHBOARD, active=True)
    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_dark_launch_global_staff_legacy(self):
        """
        Make sure that before course start staff can access
        course pages with legacy instructor dashboard enabled.
        """
        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)

        self.course.start = tomorrow
        self.test_course.start = tomorrow
        self.course = self.update_course(self.course, self.user.id)
        self.test_course = self.update_course(self.test_course, self.user.id)

        self.login(self.global_staff_user)
        self.enroll(self.course, True)
        self.enroll(self.test_course, True)

        # and now should be able to load both
        self._check_staff_legacy_dark_launch(self.course)
        self._check_staff_legacy_dark_launch(self.test_course)


class TestBetatesterAccess(ModuleStoreTestCase, CourseAccessTestMixin):
    """
    Tests for the beta tester feature
    """

    def setUp(self):
        super().setUp()

        now = datetime.datetime.now(pytz.UTC)
        tomorrow = now + datetime.timedelta(days=1)

        self.course = CourseFactory(days_early_for_beta=2, start=tomorrow)
        self.content = BlockFactory(parent=self.course)

        self.normal_student = UserFactory()
        self.beta_tester = BetaTesterFactory(course_key=self.course.id)  # pylint: disable=no-member

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_course_beta_period(self):
        """
        Check that beta-test access works for courses.
        """
        assert not self.course.has_started()  # pylint: disable=no-member
        self.assertCannotAccessCourse(self.normal_student, 'load', self.course)
        self.assertCanAccessCourse(self.beta_tester, 'load', self.course)

    @patch.dict('lms.djangoapps.courseware.access.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_content_beta_period(self):
        """
        Check that beta-test access works for content.
        """
        # student user shouldn't see it
        assert not has_access(self.normal_student, 'load', self.content, self.course.id)  # pylint: disable=no-member, line-too-long

        # now the student should see it
        assert has_access(self.beta_tester, 'load', self.content, self.course.id)  # pylint: disable=no-member, line-too-long
