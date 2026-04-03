"""
Tests for student enrollment.
"""

from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.urls import reverse
from openedx_events.tests.utils import OpenEdxEventsTestMixin

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import (
    SCORE_RECALCULATION_DELAY_ON_ENROLLMENT_UPDATE,
    CourseEnrollment,
    CourseFullError,
    EnrollmentClosedError,
)
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentAllowedFactory, UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangoapps.embargo.test_utils import restrict_course
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
@patch.dict('django.conf.settings.FEATURES', {'ENABLE_SPECIAL_EXAMS': True})
@skip_unless_lms
class EnrollmentTest(UrlResetMixin, ModuleStoreTestCase, OpenEdxEventsTestMixin):
    """
    Test student enrollment, especially with different course modes.
    """

    ENABLED_OPENEDX_EVENTS = []

    USERNAME = "Bob"
    EMAIL = "bob@example.com"
    PASSWORD = "edx"
    URLCONF_MODULES = ['openedx.core.djangoapps.embargo']

    @classmethod
    def setUpClass(cls):
        """
        Set up class method for the Test class.

        This method starts manually events isolation. Explanation here:
        openedx/core/djangoapps/user_authn/views/tests/test_events.py#L44
        """
        super().setUpClass()
        cls.start_events_isolation()

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def setUp(self):
        """ Create a course and user, then log in. """
        super().setUp()
        self.course = CourseFactory.create()
        self.course_limited = CourseFactory.create(max_student_enrollments_allowed=1)
        self.proctored_course = CourseFactory(
            enable_proctored_exams=True, enable_timed_exams=True
        )
        self.proctored_course_no_exam = CourseFactory(
            enable_proctored_exams=True, enable_timed_exams=True
        )
        self.user = UserFactory.create(username=self.USERNAME, email=self.EMAIL, password=self.PASSWORD)
        self.client.login(username=self.USERNAME, password=self.PASSWORD)
        self.urls = [
            reverse('course_modes_choose', kwargs={'course_id': str(self.course.id)})
        ]
        # Set up proctored exam
        self._create_proctored_exam(self.proctored_course)

    def _create_proctored_exam(self, course):
        """
        Helper function to create a proctored exam for a given course
        """
        chapter = BlockFactory.create(
            parent=course, category='chapter', display_name='Test Section', publish_item=True
        )
        BlockFactory.create(
            parent=chapter, category='sequential', display_name='Test Proctored Exam',
            graded=True, is_time_limited=True, default_time_limit_minutes=10,
            is_proctored_enabled=True, publish_item=True
        )

    @ddt.data(
        # Default (no course modes in the database)
        # Expect that we're redirected to the dashboard
        # and automatically enrolled
        ([], '', CourseMode.DEFAULT_MODE_SLUG),

        # Audit / Verified
        # We should always go to the "choose your course" page.
        # We should also be enrolled as the default mode.
        (['verified', 'audit'], 'course_modes_choose', CourseMode.DEFAULT_MODE_SLUG),

        # Audit / Verified / Honor
        # We should always go to the "choose your course" page.
        # We should also be enrolled as the honor mode.
        # Since honor and audit are currently offered together this precedence must
        # be maintained.
        (['honor', 'verified', 'audit'], 'course_modes_choose', CourseMode.HONOR),

        # Professional ed
        # Expect that we're sent to the "choose your track" page
        # (which will, in turn, redirect us to a page where we can verify/pay)
        # We should NOT be auto-enrolled, because that would be giving
        # away an expensive course for free :)
        (['professional'], 'course_modes_choose', None),
        (['no-id-professional'], 'course_modes_choose', None),
    )
    @ddt.unpack
    def test_enroll(self, course_modes, next_url, enrollment_mode):
        # Create the course modes (if any) required for this test case
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

        # Reverse the expected next URL, if one is provided
        # (otherwise, use an empty string, which the JavaScript client
        # interprets as a redirect to the dashboard)
        full_url = (
            reverse(next_url, kwargs={'course_id': str(self.course.id)})
            if next_url else next_url
        )

        # Enroll in the course and verify the URL we get sent to
        resp = self._change_enrollment('enroll')
        assert resp.status_code == 200
        assert resp.content.decode('utf-8') == full_url

        # If we're not expecting to be enrolled, verify that this is the case
        if enrollment_mode is None:
            assert not CourseEnrollment.is_enrolled(self.user, self.course.id)

        # Otherwise, verify that we're enrolled with the expected course mode
        else:
            assert CourseEnrollment.is_enrolled(self.user, self.course.id)
            course_mode, is_active = CourseEnrollment.enrollment_mode_for_user(self.user, self.course.id)
            assert is_active
            assert course_mode == enrollment_mode

    def test_unenroll(self):
        # Enroll the student in the course
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")

        # Attempt to unenroll the student
        resp = self._change_enrollment('unenroll')
        assert resp.status_code == 200

        # Expect that we're no longer enrolled
        assert not CourseEnrollment.is_enrolled(self.user, self.course.id)

    @ddt.data(-1, 0, 1)
    def test_external_course_updates_signal(self, value):
        """Confirm that we send the external updates experiment bucket with the activation signal"""
        with patch('openedx.core.djangoapps.schedules.config.set_up_external_updates_for_enrollment',
                   return_value=value):
            with patch('common.djangoapps.student.models.course_enrollment.segment') as mock_segment:
                CourseEnrollment.enroll(self.user, self.course.id)

        assert mock_segment.track.call_count == 1
        assert mock_segment.track.call_args[0][1] == 'edx.course.enrollment.activated'
        assert mock_segment.track.call_args[0][2]['external_course_updates'] == value

    def test_enrollment_properties_in_segment_traits(self):
        with patch('common.djangoapps.student.models.course_enrollment.segment') as mock_segment:
            enrollment = CourseEnrollment.enroll(self.user, self.course.id)
        assert mock_segment.track.call_count == 1
        assert mock_segment.track.call_args[0][1] == 'edx.course.enrollment.activated'
        traits = mock_segment.track.call_args[1]['traits']
        assert traits['course_title'] == self.course.display_name
        assert traits['mode'] == 'audit'
        assert traits['email'] == self.EMAIL

        with patch('common.djangoapps.student.models.course_enrollment.segment') as mock_segment:
            enrollment.update_enrollment(mode='verified')
        assert mock_segment.track.call_count == 1
        assert mock_segment.track.call_args[0][1] == 'edx.course.enrollment.mode_changed'
        traits = mock_segment.track.call_args[1]['traits']
        assert traits['course_title'] == self.course.display_name
        assert traits['mode'] == 'verified'
        assert traits['email'] == self.EMAIL

    @patch.dict(settings.FEATURES, {'ENABLE_MKTG_EMAIL_OPT_IN': True})
    @patch('openedx.core.djangoapps.user_api.preferences.api.update_email_opt_in')
    @ddt.data(
        ([], 'true'),
        ([], 'false'),
        ([], None),
        (['honor', 'verified'], 'true'),
        (['honor', 'verified'], 'false'),
        (['honor', 'verified'], None),
        (['professional'], 'true'),
        (['professional'], 'false'),
        (['professional'], None),
        (['no-id-professional'], 'true'),
        (['no-id-professional'], 'false'),
        (['no-id-professional'], None),
    )
    @ddt.unpack
    def test_enroll_with_email_opt_in(self, course_modes, email_opt_in, mock_update_email_opt_in):
        # Create the course modes (if any) required for this test case
        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )

        # Enroll in the course
        self._change_enrollment('enroll', email_opt_in=email_opt_in)

        # Verify that the profile API has been called as expected
        if email_opt_in is not None:
            opt_in = email_opt_in == 'true'
            mock_update_email_opt_in.assert_called_once_with(self.user, self.course.org, opt_in)
        else:
            assert not mock_update_email_opt_in.called

    @ddt.data(
        ('honor', False),
        ('audit', False),
        ('verified', True),
        ('masters', True),
        ('professional', True),
        ('no-id-professional', False),
        ('credit', False),
        ('executive-education', True)
    )
    @ddt.unpack
    def test_enroll_in_proctored_course(self, mode, email_sent):
        """
        When enrolling in a proctoring-enabled course in a verified mode, an email with proctoring
        requirements should be sent. The email should not be sent for non-verified modes.
        """
        with patch(
            'common.djangoapps.student.emails.send_proctoring_requirements_email',
            return_value=None
        ) as mock_send_email:
            # First enroll in a non-proctored course. This should not trigger the email.
            CourseEnrollment.enroll(self.user, self.course.id, mode)
            assert not mock_send_email.called
            # Then, enroll in a proctored course, and assert that the email is sent only when
            # enrolling in a verified mode.
            CourseEnrollment.enroll(self.user, self.proctored_course.id, mode)  # pylint: disable=no-member
            assert email_sent == mock_send_email.called

    def test_enroll_in_proctored_course_no_exam(self):
        """
        If a verified learner enrolls in a course that has proctoring enabled, but does not have
        any proctored exams, they should not receive a proctoring requirements email.
        """
        with patch(
            'common.djangoapps.student.emails.send_proctoring_requirements_email',
            return_value=None
        ) as mock_send_email:
            CourseEnrollment.enroll(
                self.user, self.proctored_course_no_exam.id, 'verified'  # pylint: disable=no-member
            )
            assert not mock_send_email.called

    @ddt.data('verified', 'masters', 'professional', 'executive-education')
    def test_upgrade_proctoring_enrollment(self, mode):
        """
        When upgrading from audit in a course with proctored exams, an email with proctoring requirements
        should be sent.
        """
        with patch(
            'common.djangoapps.student.emails.send_proctoring_requirements_email',
            return_value=None
        ) as mock_send_email:
            enrollment = CourseEnrollment.enroll(
                self.user, self.proctored_course.id, 'audit'  # pylint: disable=no-member
            )
            enrollment.update_enrollment(mode=mode)
            assert mock_send_email.called

    @patch.dict(
        'django.conf.settings.PROCTORING_BACKENDS', {'test_provider_honor_mode': {'allow_honor_mode': True}}
    )
    @patch.dict(settings.FEATURES, {'ENABLE_PROCTORED_EXAMS': True})
    def test_enroll_in_proctored_course_honor_mode_allowed(self):
        """
        If the proctoring provider allows honor mode, send proctoring requirements email when learners
        enroll in honor mode for a course with proctored exams.
        """
        with patch(
            'common.djangoapps.student.emails.send_proctoring_requirements_email',
            return_value=None
        ) as mock_send_email:
            course_honor_mode = CourseFactory(
                enable_proctored_exams=True,
                enable_timed_exams=True,
                proctoring_provider='test_provider_honor_mode',
            )
            self._create_proctored_exam(course_honor_mode)
            CourseEnrollment.enroll(self.user, course_honor_mode.id, 'honor')  # pylint: disable=no-member
            assert mock_send_email.called

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_restrict(self):
        # When accessing the course from an embargoed country,
        # we should be blocked.
        with restrict_course(self.course.id) as redirect_url:
            response = self._change_enrollment('enroll')
            assert response.status_code == 200
            assert response.content.decode('utf-8') == redirect_url

        # Verify that we weren't enrolled
        is_enrolled = CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert not is_enrolled

    @patch.dict(settings.FEATURES, {'EMBARGO': True})
    def test_embargo_allow(self):
        response = self._change_enrollment('enroll')
        assert response.status_code == 200
        assert response.content.decode('utf-8') == ''

        # Verify that we were enrolled
        is_enrolled = CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert is_enrolled

    def test_user_not_authenticated(self):
        # Log out, so we're no longer authenticated
        self.client.logout()

        # Try to enroll, expecting a forbidden response
        resp = self._change_enrollment('enroll')
        assert resp.status_code == 403

    def test_missing_course_id_param(self):
        resp = self.client.post(
            reverse('change_enrollment'),
            {'enrollment_action': 'enroll'}
        )
        assert resp.status_code == 400

    def test_unenroll_not_enrolled_in_course(self):
        # Try unenroll without first enrolling in the course
        resp = self._change_enrollment('unenroll')
        assert resp.status_code == 400

    def test_invalid_enrollment_action(self):
        resp = self._change_enrollment('not_an_action')
        assert resp.status_code == 400

    def test_with_invalid_course_id(self):
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")
        resp = self._change_enrollment('unenroll', course_id="edx/")
        assert resp.status_code == 400

    @patch.dict(settings.FEATURES, {'DISABLE_UNENROLLMENT': True})
    def test_unenroll_when_unenrollment_disabled(self):
        """
        Tests that a user cannot unenroll when unenrollment has been disabled.
        """
        # Enroll the student in the course
        CourseEnrollment.enroll(self.user, self.course.id, mode="honor")

        # Attempt to unenroll
        resp = self._change_enrollment('unenroll')
        assert resp.status_code == 400

        # Verify that user is still enrolled
        is_enrolled = CourseEnrollment.is_enrolled(self.user, self.course.id)
        assert is_enrolled

    def test_enrollment_limit(self):
        """
        Assert that in a course with max student limit set to 1, we can enroll staff and instructor along with
        student. To make sure course full check excludes staff and instructors.
        """
        assert self.course_limited.max_student_enrollments_allowed == 1
        user1 = UserFactory.create(username="tester1", email="tester1@e.com", password="test")
        user2 = UserFactory.create(username="tester2", email="tester2@e.com", password="test")

        # create staff on course.
        staff = UserFactory.create(username="staff", email="staff@e.com", password="test")
        role = CourseStaffRole(self.course_limited.id)
        role.add_users(staff)

        # create instructor on course.
        instructor = UserFactory.create(username="instructor", email="instructor@e.com", password="test")
        role = CourseInstructorRole(self.course_limited.id)
        role.add_users(instructor)

        CourseEnrollment.enroll(staff, self.course_limited.id, check_access=True)
        CourseEnrollment.enroll(instructor, self.course_limited.id, check_access=True)

        assert CourseEnrollment.objects.filter(course_id=self.course_limited.id, user=staff).exists()

        assert CourseEnrollment.objects.filter(course_id=self.course_limited.id, user=instructor).exists()

        CourseEnrollment.enroll(user1, self.course_limited.id, check_access=True)
        assert CourseEnrollment.objects.filter(course_id=self.course_limited.id, user=user1).exists()

        with pytest.raises(CourseFullError):
            CourseEnrollment.enroll(user2, self.course_limited.id, check_access=True)

        assert not CourseEnrollment.objects.filter(course_id=self.course_limited.id, user=user2).exists()

    def _change_enrollment(self, action, course_id=None, email_opt_in=None):
        """Change the student's enrollment status in a course.

        Args:
            action (str): The action to perform (either "enroll" or "unenroll")

        Keyword Args:
            course_id (unicode): If provided, use this course ID.  Otherwise, use the
                course ID created in the setup for this test.
            email_opt_in (unicode): If provided, pass this value along as
                an additional GET parameter.

        Returns:
            Response

        """
        if course_id is None:
            course_id = str(self.course.id)

        params = {
            'enrollment_action': action,
            'course_id': course_id
        }

        if email_opt_in:
            params['email_opt_in'] = email_opt_in

        return self.client.post(reverse('change_enrollment'), params)

    def test_cea_enrolls_only_one_user(self):
        """
        Tests that a CourseEnrollmentAllowed can be used by just one user.
        If the user changes e-mail and then a second user tries to enroll with the same accepted e-mail,
        the second enrollment should fail.
        However, the original user can reuse the CEA many times.
        """

        cea = CourseEnrollmentAllowedFactory(
            email='allowed@edx.org',
            course_id=self.course.id,
            auto_enroll=False,
        )
        # Still unlinked
        assert cea.user is None

        user1 = UserFactory.create(username="tester1", email="tester1@e.com", password="test")
        user2 = UserFactory.create(username="tester2", email="tester2@e.com", password="test")

        assert not CourseEnrollment.objects.filter(course_id=self.course.id, user=user1).exists()

        user1.email = 'allowed@edx.org'
        user1.save()

        CourseEnrollment.enroll(user1, self.course.id, check_access=True)

        assert CourseEnrollment.objects.filter(course_id=self.course.id, user=user1).exists()

        # The CEA is now linked
        cea.refresh_from_db()
        assert cea.user == user1

        # user2 wants to enroll too, (ab)using the same allowed e-mail, but cannot
        user1.email = 'my_other_email@edx.org'
        user1.save()
        user2.email = 'allowed@edx.org'
        user2.save()
        with pytest.raises(EnrollmentClosedError):
            CourseEnrollment.enroll(user2, self.course.id, check_access=True)

        # CEA still linked to user1. Also after unenrolling
        cea.refresh_from_db()
        assert cea.user == user1

        CourseEnrollment.unenroll(user1, self.course.id)

        cea.refresh_from_db()
        assert cea.user == user1

        # Enroll user1 again. Because it's the original owner of the CEA, the enrollment is allowed
        CourseEnrollment.enroll(user1, self.course.id, check_access=True)

        # Still same
        cea.refresh_from_db()
        assert cea.user == user1

    def test_score_recalculation_on_enrollment_update(self):
        """
        Test that an update in enrollment cause score recalculation.
        Note:
        Score recalculation task must be called with a delay of SCORE_RECALCULATION_DELAY_ON_ENROLLMENT_UPDATE
        """
        course_modes = ['verified', 'audit']

        for mode_slug in course_modes:
            CourseModeFactory.create(
                course_id=self.course.id,
                mode_slug=mode_slug,
                mode_display_name=mode_slug,
            )
        CourseEnrollment.enroll(self.user, self.course.id, mode="audit")

        local_task_args = dict(
            user_id=self.user.id,
            course_key=str(self.course.id)
        )

        with patch(
            'lms.djangoapps.grades.tasks.recalculate_course_and_subsection_grades_for_user.apply_async',
            return_value=None
        ) as mock_task_apply:
            CourseEnrollment.enroll(self.user, self.course.id, mode="verified")
            mock_task_apply.assert_called_once_with(
                countdown=SCORE_RECALCULATION_DELAY_ON_ENROLLMENT_UPDATE,
                kwargs=local_task_args
            )
