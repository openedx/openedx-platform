"""
Tests for courseware API
"""

from datetime import datetime, timedelta
from typing import Optional
from unittest import mock
from urllib.parse import urlencode

import ddt
from completion.test_utils import CompletionWaffleTestMixin, submit_completions_for_testing
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.test.client import RequestFactory
from edx_django_utils.cache import TieredCache
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentCelebration
from common.djangoapps.student.roles import CourseInstructorRole
from common.djangoapps.student.tests.factories import CourseEnrollmentCelebrationFactory, UserFactory
from lms.djangoapps.certificates.api import get_certificate_url
from lms.djangoapps.certificates.tests.factories import (
    GeneratedCertificateFactory,
    LinkedInAddToProfileConfigurationFactory
)
from lms.djangoapps.courseware.access_utils import ACCESS_DENIED, ACCESS_GRANTED
from lms.djangoapps.courseware.models import LastSeenCoursewareTimezone
from lms.djangoapps.courseware.tabs import ExternalLinkCourseTab
from lms.djangoapps.courseware.tests.helpers import MasqueradeMixin
from lms.djangoapps.courseware.toggles import (
    COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT,
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES,
    COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION
)
from lms.djangoapps.verify_student.services import IDVerificationService
from openedx.core.djangoapps.agreements.api import create_integrity_signature
from openedx.core.djangolib.testing.utils import skip_unless_lms
from openedx.features.course_experience.waffle import ENABLE_COURSE_ABOUT_SIDEBAR_HTML
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, ToyCourseFactory
from xmodule.partitions.partitions import ENROLLMENT_TRACK_PARTITION_ID

User = get_user_model()

_NEXT_WEEK = datetime.now() + timedelta(days=7)


@skip_unless_lms
class BaseCoursewareTests(SharedModuleStoreTestCase):
    """
    Base class for courseware API tests
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = modulestore()
        cls.course = ToyCourseFactory.create(
            end=datetime(2028, 1, 1, 1, 1, 1),
            enrollment_start=datetime(2020, 1, 1, 1, 1, 1),
            enrollment_end=datetime(2028, 1, 1, 1, 1, 1),
            emit_signals=True,
            modulestore=cls.store,
            certificate_available_date=_NEXT_WEEK,
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE
        )
        cls.chapter = BlockFactory(parent=cls.course, category='chapter')
        cls.sequence = BlockFactory(parent=cls.chapter, category='sequential', display_name='sequence')
        cls.unit = BlockFactory.create(parent=cls.sequence, category='vertical', display_name="Vertical")

        cls.user = UserFactory(
            username='student',
            email='user@example.com',
            password='foo',
            is_staff=False
        )
        cls.instructor = UserFactory(
            username='instructor',
            email='instructor@example.com',
            password='foo',
            is_staff=False
        )
        CourseInstructorRole(cls.course.id).add_users(cls.instructor)
        cls.url = f'/api/courseware/course/{cls.course.id}'

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.store.delete_course(cls.course.id, cls.user.id)

    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password='foo')


@ddt.ddt
@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES, active=True)
@override_waffle_flag(COURSEWARE_MICROFRONTEND_PROGRESS_MILESTONES_STREAK_CELEBRATION, active=True)
@skip_unless_lms
class CourseApiTestViews(BaseCoursewareTests, MasqueradeMixin):
    """
    Tests for the courseware REST API
    """
    @classmethod
    def setUpClass(cls):
        BaseCoursewareTests.setUpClass()
        cls.course.tabs.append(ExternalLinkCourseTab.load('external_link', name='Zombo', link='http://zombo.com'))
        cls.course.tabs.append(
            ExternalLinkCourseTab.load('external_link', name='Hidden', link='http://hidden.com', is_hidden=True)
        )
        cls.store.update_item(cls.course, cls.user.id)
        LinkedInAddToProfileConfigurationFactory.create()
        CourseModeFactory(course_id=cls.course.id, mode_slug=CourseMode.AUDIT)
        CourseModeFactory(
            course_id=cls.course.id,
            mode_slug=CourseMode.VERIFIED,
            expiration_datetime=datetime(3028, 1, 1),
            min_price=149,
            sku='ABCD1234',
        )

    @ddt.data(
        (True, 'audit'),
        (True, 'verified'),
    )
    @ddt.unpack
    @mock.patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_enrolled_course_metadata(self, logged_in, enrollment_mode):
        check_public_access = mock.Mock()
        check_public_access.return_value = ACCESS_DENIED
        with mock.patch('lms.djangoapps.courseware.access_utils.check_public_access', check_public_access):
            if not logged_in:
                self.client.logout()
            if enrollment_mode == 'verified':
                cert = GeneratedCertificateFactory.create(
                    user=self.user,
                    course_id=self.course.id,
                    status='downloadable',
                    mode='verified',
                )
            if enrollment_mode:
                CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)

            response = self.client.get(self.url)
            assert response.status_code == 200

            enrollment = response.data['enrollment']
            assert enrollment_mode == enrollment['mode']
            assert enrollment['is_active']

            assert not response.data['user_has_passing_grade']
            assert response.data['celebrations']['first_section']
            assert not response.data['celebrations']['weekly_goal']

            # This import errors in cms if it is imported at the top level
            from lms.djangoapps.course_goals.api import get_course_goal
            selected_goal = get_course_goal(self.user, self.course.id)
            if selected_goal:
                assert response.data['course_goals']['selected_goal'] == {
                    'days_per_week': selected_goal.days_per_week,
                    'subscribed_to_reminders': selected_goal.subscribed_to_reminders,
                }

            if enrollment_mode == 'audit':
                assert response.data['verify_identity_url'] is None
                assert response.data['verification_status'] == 'none'
                assert response.data['linkedin_add_to_profile_url'] is None
            else:
                assert response.data['certificate_data']['cert_status'] == 'earned_but_not_available'
                expected_verify_identity_url = IDVerificationService.get_verify_location(
                    course_id=self.course.id
                )
                # The response contains an absolute URL so this is only checking the path of the final
                assert expected_verify_identity_url in response.data['verify_identity_url']
                assert response.data['verification_status'] == 'none'

                request = RequestFactory().request()
                cert_url = get_certificate_url(course_id=self.course.id, uuid=cert.verify_uuid)
                linkedin_url_params = {
                    'name': '{platform_name} Verified Certificate for {course_name}'.format(
                        platform_name=settings.PLATFORM_NAME, course_name=self.course.display_name,
                    ),
                    'certUrl': request.build_absolute_uri(cert_url),
                    # default value from the LinkedInAddToProfileConfigurationFactory company_identifier
                    'organizationId': 1337,
                    'certId': cert.verify_uuid,
                    'issueYear': cert.created_date.year,
                    'issueMonth': cert.created_date.month,
                }
                expected_linkedin_url = (
                    'https://www.linkedin.com/profile/add?startTask=CERTIFICATION_NAME&{params}'.format(
                        params=urlencode(linkedin_url_params)
                    )
                )
                assert response.data['linkedin_add_to_profile_url'] == expected_linkedin_url

    @ddt.data(
        (True, ACCESS_DENIED),
        (False, ACCESS_DENIED),
        (False, ACCESS_GRANTED),
    )
    @ddt.unpack
    @mock.patch.dict('django.conf.settings.FEATURES', {'CERTIFICATES_HTML_VIEW': True})
    def test_unenrolled_course_metadata(self, logged_in, enable_anonymous):
        check_public_access = mock.Mock()
        check_public_access.return_value = enable_anonymous
        with mock.patch('lms.djangoapps.courseware.access_utils.check_public_access', check_public_access):
            if not logged_in:
                self.client.logout()

            response = self.client.get(self.url)
            assert response.status_code == 200

            if enable_anonymous and not logged_in:
                # multiple checks use this handler
                assert response.data['enrollment']['mode'] is None
                assert response.data['course_goals']['selected_goal'] is None
                assert response.data['course_goals']['weekly_learning_goal_enabled'] is False

    @ddt.data(
        # Who has access to MFE courseware?
        {
            # Enrolled learners should have access.
            "mfe_is_visible": True,
            "username": "student",
            "enroll_user": True,
            "masquerade_role": None,
        },
        {
            # Un-enrolled learners should NOT have access.
            "mfe_is_visible": True,
            "username": "student",
            "enroll_user": False,
            "masquerade_role": None,
        },
        {
            # Un-enrolled instructors should have access.
            "mfe_is_visible": True,
            "username": "instructor",
            "enroll_user": False,
            "masquerade_role": None,
        },
        {
            # Un-enrolled instructors masquerading as students should have access.
            "mfe_is_visible": True,
            "username": "instructor",
            "enroll_user": False,
            "masquerade_role": "student",
        },
        {
            # If MFE is not visible, enrolled learners shouldn't have access.
            "mfe_is_visible": False,
            "username": "student",
            "enroll_user": True,
            "masquerade_role": None,
        },
        {
            # If MFE is not visible, instructors shouldn't have access.
            "mfe_is_visible": False,
            "username": "instructor",
            "enroll_user": False,
            "masquerade_role": None,
        },
        {
            # If MFE is not visible, masquerading instructors shouldn't have access.
            "mfe_is_visible": False,
            "username": "instructor",
            "enroll_user": False,
            "masquerade_role": "student",
        },
    )
    @ddt.unpack
    def test_course_access(
            self,
            mfe_is_visible: bool,
            username: str,
            enroll_user: bool,
            masquerade_role: Optional[str],
    ):
        """
        Test that course_access is calculated correctly based on
        access to MFE and access to the course itself.
        """
        user = User.objects.get(username=username)
        if enroll_user:
            CourseEnrollment.enroll(user, self.course.id, 'audit')

        self.client.login(username=user, password='foo')
        if masquerade_role:
            self.update_masquerade(role=masquerade_role)

        response = self.client.get(self.url)

        assert response.status_code == 200

    def test_streak_data_in_response(self):
        """ Test that metadata endpoint returns data for the streak celebration """
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        with override_waffle_flag(COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT, active=True):
            UPDATE_MTHD_NAME = 'common.djangoapps.student.models.user.UserCelebration.perform_streak_updates'
            with mock.patch(UPDATE_MTHD_NAME, return_value=3):
                response = self.client.get(self.url, content_type='application/json')
                celebrations = response.json()['celebrations']
                assert celebrations['streak_length_to_celebrate'] == 3
                assert celebrations['streak_discount_enabled'] is True

    def test_streak_segment_suppressed_for_unverified(self):
        """ Test that metadata endpoint does not return a discount and signal is not sent if flag is not set """
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        with override_waffle_flag(COURSEWARE_MFE_MILESTONES_STREAK_DISCOUNT, active=False):
            UPDATE_MTHD_NAME = 'common.djangoapps.student.models.user.UserCelebration.perform_streak_updates'
            with mock.patch(UPDATE_MTHD_NAME, return_value=3):
                response = self.client.get(self.url, content_type='application/json')
                celebrations = response.json()['celebrations']
                assert celebrations['streak_length_to_celebrate'] == 3
                assert celebrations['streak_discount_enabled'] is False

    @ddt.data(
        (None, False, False, False),
        ('verified', False, False, True),
        ('masters', False, False, False),
        ('audit', False, False, False),
        ('verified', False, True, False),
        ('masters', False, True, False),
        ('verified', True, False, True),
        ('audit', True, False, False),
    )
    @ddt.unpack
    @mock.patch.dict(settings.FEATURES, {'ENABLE_INTEGRITY_SIGNATURE': True})
    def test_user_needs_integrity_signature(
        self, enrollment_mode, is_staff, has_integrity_signature, needs_integrity_signature,
    ):
        """
        Test that the correct value is returned if the user needs to sign the integrity agreement for the course.
        """
        if is_staff:
            self.user.is_staff = True
            self.user.save()
        if enrollment_mode:
            CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)
        if has_integrity_signature:
            create_integrity_signature(self.user.username, str(self.course.id))
        response = self.client.get(self.url)
        assert response.status_code == 200
        courseware_data = response.json()
        assert 'is_integrity_signature_enabled' in courseware_data
        assert courseware_data['is_integrity_signature_enabled'] is True
        assert 'user_needs_integrity_signature' in courseware_data
        assert courseware_data['user_needs_integrity_signature'] == needs_integrity_signature

    def test_set_last_seen_courseware_timezone_no_integrity_error(self):
        # Previously this function was trying to create duplicate records
        # that would bump into a uniqueness constraint causing an integrity error
        self.client.get(self.url, {'browser_timezone': 'America/New_York'})
        TieredCache.dangerous_clear_all_tiers()
        self.client.get(self.url, {'browser_timezone': 'Asia/Tokyo'})
        assert len(LastSeenCoursewareTimezone.objects.filter()) == 1

    @ddt.data(
        (1, False),
        (2, True),
        (3, True),
    )
    @ddt.unpack
    @mock.patch.dict(settings.FEATURES, {'ENABLE_INTEGRITY_SIGNATURE': True})
    def test_course_staff_masquerade(self, masquerade_group_id, needs_signature):
        self.user.is_staff = True
        self.user.save()
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        masquerade_config = {
            'role': 'student',
            'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
            'group_id': masquerade_group_id
        }
        self.update_masquerade(**masquerade_config)
        response = self.client.get(self.url)
        assert response.status_code == 200
        courseware_data = response.json()
        assert 'is_integrity_signature_enabled' in courseware_data
        assert courseware_data['is_integrity_signature_enabled'] is True
        assert 'user_needs_integrity_signature' in courseware_data
        assert courseware_data['user_needs_integrity_signature'] == needs_signature

    @ddt.data(
        ('audit', False),
        ('honor', False),
        ('verified', True),
        ('masters', True),
        ('professional', True),
        ('no-id-professional', True),
        ('executive-education', True),
        ('credit', True),
    )
    @ddt.unpack
    @mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_HONOR_CERTIFICATES': True})
    def test_can_access_proctored_exams(self, mode, result):
        CourseEnrollment.enroll(self.user, self.course.id, mode)
        response = self.client.get(self.url)
        assert response.status_code == 200
        courseware_data = response.json()
        assert 'can_access_proctored_exams' in courseware_data
        assert courseware_data['can_access_proctored_exams'] == result

    @ddt.data(
        (1, False),
        (2, True),
        (3, True),
    )
    @ddt.unpack
    @mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_HONOR_CERTIFICATES': True})
    def test_can_access_proctored_exams_masquerading(self, masquerade_group_id, result):
        self.user.is_staff = True
        self.user.save()
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        masquerade_config = {
            'role': 'student',
            'user_partition_id': ENROLLMENT_TRACK_PARTITION_ID,
            'group_id': masquerade_group_id
        }
        self.update_masquerade(**masquerade_config)
        response = self.client.get(self.url)
        assert response.status_code == 200
        courseware_data = response.json()
        assert 'can_access_proctored_exams' in courseware_data
        assert courseware_data['can_access_proctored_exams'] == result

    @ddt.data(
        True,
        False
    )
    def test_learning_assistant_enabled(self, setting_enabled):
        with override_settings(LEARNING_ASSISTANT_AVAILABLE=setting_enabled):
            response = self.client.get(self.url)

        learning_assistant_enabled = response.json()['learning_assistant_enabled']
        self.assertEqual(learning_assistant_enabled, setting_enabled)


@ddt.ddt
class SequenceApiTestViews(MasqueradeMixin, BaseCoursewareTests):
    """
    Tests for the sequence REST API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = f'/api/courseware/sequence/{cls.sequence.location}'

    @classmethod
    def tearDownClass(cls):
        cls.store.delete_item(cls.sequence.location, cls.user.id)
        super().tearDownClass()

    def test_sequence_metadata(self):
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['display_name'] == 'sequence'
        assert len(response.data['items']) == 1

    def test_unit_error(self):
        """Verify that we return a proper error when passed a non-sequence"""
        response = self.client.get(f'/api/courseware/sequence/{self.unit.location}')
        assert response.status_code == 422

    @ddt.data(
        (False, None, False, False),
        (True, None, True, False),
        (True, {'username': 'student'}, False, True),
        # Masquerading as a limited-access learner here, but specific partition/group doesn't matter.
        # We just want to test that masquerading as a non-specific learner has a different outcome.
        (True, {'user_partition_id': 51, 'group_id': 1}, True, False),
    )
    @ddt.unpack
    def test_hidden_after_due(self, is_past_due, masquerade_config, expected_hidden, expected_banner):
        """Validate the metadata when hide-after-due is set for a sequence"""
        due = datetime.now() + timedelta(days=-1 if is_past_due else 1)
        sequence = BlockFactory(
            parent_location=self.chapter.location,
            # ^ It is very important that we use parent_location=self.chapter.location (and not parent=self.chapter), as
            # chapter is a class attribute and passing it by value will update its .children=[] which will then leak
            # into other tests and cause errors if the children no longer exist.
            category='sequential',
            hide_after_due=True,
            due=due,
        )

        CourseEnrollment.enroll(self.user, self.course.id)

        user = self.instructor if masquerade_config else self.user
        self.client.login(username=user.username, password='foo')
        if masquerade_config:
            self.update_masquerade(**masquerade_config)

        response = self.client.get(f'/api/courseware/sequence/{sequence.location}')
        assert response.status_code == 200
        assert response.data['is_hidden_after_due'] == expected_hidden
        assert bool(response.data['banner_text']) == expected_banner


class ResumeApiTestViews(BaseCoursewareTests, CompletionWaffleTestMixin):
    """
    Tests for the resume API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = f'/api/courseware/resume/{cls.course.id}'

    def test_resume_no_completion(self):
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['block_id'] is None
        assert response.data['unit_id'] is None
        assert response.data['section_id'] is None

    def test_resume_with_completion(self):
        self.override_waffle_switch(True)
        submit_completions_for_testing(self.user, [self.unit.location])
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['block_id'] == str(self.unit.location)
        assert response.data['unit_id'] == str(self.unit.location)
        assert response.data['section_id'] == str(self.sequence.location)

    def test_resume_invalid_key(self):
        """A resume key that does not exist should return null IDs (i.e. "redirect to first section")"""
        self.override_waffle_switch(True)
        submit_completions_for_testing(self.user, [self.course.id.make_usage_key('html', 'doesnotexist')])
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['block_id'] is None
        assert response.data['unit_id'] is None
        assert response.data['section_id'] is None


@ddt.ddt
class CelebrationApiTestViews(BaseCoursewareTests, MasqueradeMixin):
    """
    Tests for the celebration API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = f'/api/courseware/celebration/{cls.course.id}'

    def setUp(self):
        super().setUp()
        self.enrollment = CourseEnrollment.enroll(self.user, self.course.id, 'verified')

    @ddt.data(True, False)
    def test_happy_path(self, update):
        if update:
            CourseEnrollmentCelebrationFactory(enrollment=self.enrollment)

        data = {'first_section': True, 'weekly_goal': True}
        response = self.client.post(self.url, data, content_type='application/json')
        assert response.status_code == (200 if update else 201)

        celebration = CourseEnrollmentCelebration.objects.first()
        assert celebration.celebrate_first_section
        assert celebration.celebrate_weekly_goal
        assert celebration.enrollment.id == self.enrollment.id

    def test_extra_data(self):
        response = self.client.post(self.url, {'extra': True}, content_type='application/json')
        assert response.status_code == 400

    def test_no_data(self):
        response = self.client.post(self.url, {}, content_type='application/json')
        assert response.status_code == 200
        assert CourseEnrollmentCelebration.objects.count() == 0

    def test_no_enrollment(self):
        self.enrollment.delete()
        response = self.client.post(self.url, {'first_section': True}, content_type='application/json')
        assert response.status_code == 404

    def test_no_login(self):
        self.client.logout()
        response = self.client.post(self.url, {'first_section': True}, content_type='application/json')
        assert response.status_code == 401

    def test_invalid_course(self):
        response = self.client.post('/api/courseware/celebration/course-v1:does+not+exist',
                                    {'first_section': True}, content_type='application/json')
        assert response.status_code == 404

    def test_masquerade(self):
        self.user.is_staff = True
        self.user.save()

        user = UserFactory()
        CourseEnrollment.enroll(user, self.course.id, 'verified')

        data = {'first_section': True, 'weekly_goal': False}
        response = self.client.post(self.url, data, content_type='application/json')
        assert response.status_code == 201

        self.update_masquerade(username=user.username)
        data = {'first_section': False, 'weekly_goal': True}
        response = self.client.post(self.url, data, content_type='application/json')
        assert response.status_code == 202

        celebration = CourseEnrollmentCelebration.objects.first()
        # make sure they didn't change during masquerade attempt
        assert celebration.celebrate_first_section
        assert not celebration.celebrate_weekly_goal


@ddt.ddt
@skip_unless_lms  # If run in CMS, the tests fail as the courseware_api.views module contains imports from the LMS.
class CoursewareMetaTestViews(BaseCoursewareTests):
    """
    Tests for the CoursewareMeta class
    """

    def setUp(self):
        super().setUp()
        self.course_enrollment = CourseEnrollment.enroll(self.user, self.course.id, 'audit')
        self.request = RequestFactory().get(self.url)

    def create_courseware_meta(self, user=None):
        """
        Helper method to create CoursewareMeta instance
        """
        from openedx.core.djangoapps.courseware_api.views import CoursewareMeta

        user = user or self.user
        self.request.user = user
        return CoursewareMeta(self.course.id, self.request, username=user.username)

    @ddt.data(True, False)
    def test_is_course_full_property(self, is_course_full):
        """
        Test is_course_full property
        """
        with mock.patch(
            'openedx.core.djangoapps.courseware_api.views.CourseEnrollment.objects.is_course_full'
        ) as mock_is_course_full:
            mock_is_course_full.return_value = is_course_full
            meta = self.create_courseware_meta()
            assert meta.is_course_full is is_course_full

    @ddt.data(True, False)
    def test_invitation_only_property(self, invitation_only):
        """
        Test invitation_only property
        """
        with override_settings(COURSES_INVITE_ONLY=invitation_only):
            meta = self.create_courseware_meta()
            assert meta.invitation_only is invitation_only

    @ddt.data(True, False)
    @mock.patch(
        'openedx.core.djangoapps.courseware_api.views.get_course_about_section', new_callable=mock.PropertyMock
    )
    def test_about_sidebar_html_property(self, waffle_enabled, mock_get_course_about_section):
        """
        Test about_sidebar_html property with different waffle settings.

        Ensure that when a value is returned, <script> tags are stripped.
        """
        # We're mocking the underlying method used to grab both the overview
        # html and the about_sidebar_html, so we can test sanitization on both.
        mock_get_course_about_section.return_value = '<div>About Course<script>alert("warning!");</script></div>'
        with override_waffle_switch(ENABLE_COURSE_ABOUT_SIDEBAR_HTML, active=waffle_enabled):
            meta = self.create_courseware_meta()
            if waffle_enabled:
                assert meta.about_sidebar_html == '<div>About Course</div>'
            else:
                assert meta.about_sidebar_html is None
            assert meta.overview == '<div>About Course</div>'


@ddt.ddt
@skip_unless_lms
class CoursewareMetaAPIResponseTestViews(BaseCoursewareTests):
    """
    Tests for API response fields returned by CoursewareMeta through the API endpoint
    """

    def setUp(self):
        super().setUp()
        CourseEnrollment.enroll(self.user, self.course.id, 'audit')

    def test_api_returns_show_courseware_link_field(self):
        """
        Test that API response contains show_courseware_link field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'show_courseware_link' in response.data
        assert isinstance(response.data['show_courseware_link'], bool)

    def test_api_returns_is_course_full_field(self):
        """
        Test that API response contains is_course_full field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'is_course_full' in response.data
        assert isinstance(response.data['is_course_full'], bool)

    def test_api_returns_can_enroll_field(self):
        """
        Test that API response contains can_enroll field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'can_enroll' in response.data
        assert isinstance(response.data['can_enroll'], bool)

    def test_api_returns_invitation_only_field(self):
        """
        Test that API response contains invitation_only field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'invitation_only' in response.data
        assert isinstance(response.data['invitation_only'], bool)

    def test_api_returns_is_shib_course_field(self):
        """
        Test that API response contains is_shib_course field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'is_shib_course' in response.data
        assert isinstance(response.data['is_shib_course'], bool)

    def test_api_returns_allow_anonymous_field(self):
        """
        Test that API response contains allow_anonymous field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'allow_anonymous' in response.data
        assert isinstance(response.data['allow_anonymous'], bool)

    def test_api_returns_ecommerce_checkout_field(self):
        """
        Test that API response contains ecommerce_checkout field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'ecommerce_checkout' in response.data
        assert isinstance(response.data['ecommerce_checkout'], bool)

    def test_api_returns_single_paid_mode_field(self):
        """
        Test that API response contains single_paid_mode field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'single_paid_mode' in response.data
        assert isinstance(response.data['single_paid_mode'], dict)

    def test_api_returns_ecommerce_checkout_link_field(self):
        """
        Test that API response contains ecommerce_checkout_link field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'ecommerce_checkout_link' in response.data
        checkout_link = response.data['ecommerce_checkout_link']
        assert isinstance(checkout_link, str) or checkout_link is None

    def test_api_returns_course_image_urls_field(self):
        """
        Test that API response contains course_image_urls field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'course_image_urls' in response.data
        assert isinstance(response.data['course_image_urls'], list)

    def test_api_returns_start_date_is_still_default_field(self):
        """
        Test that API response contains start_date_is_still_default field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'start_date_is_still_default' in response.data
        assert isinstance(response.data['start_date_is_still_default'], bool)

    def test_api_returns_advertised_start_field(self):
        """
        Test that API response contains advertised_start field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'advertised_start' in response.data
        advertised_start = response.data['advertised_start']
        assert isinstance(advertised_start, str) or advertised_start is None

    def test_api_returns_course_price_field(self):
        """
        Test that API response contains course_price field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'course_price' in response.data
        assert isinstance(response.data['course_price'], str)

    def test_api_returns_pre_requisite_courses_field(self):
        """
        Test that API response contains pre_requisite_courses field
        """
        response = self.client.get(self.url)
        assert response.status_code == 200
        assert 'pre_requisite_courses' in response.data
        assert isinstance(response.data['pre_requisite_courses'], list)

    @ddt.data(True, False)
    @mock.patch(
        'openedx.core.djangoapps.courseware_api.views.get_course_about_section', new_callable=mock.PropertyMock
    )
    def test_api_about_sidebar_html_with_waffle(self, waffle_enabled, mock_get_course_about_section):
        """
        Test API returns correct about_sidebar_html value based on waffle flag
        """
        with override_waffle_switch(ENABLE_COURSE_ABOUT_SIDEBAR_HTML, active=waffle_enabled):
            mock_get_course_about_section.return_value = '<div>About Course</div>'
            response = self.client.get(self.url)
            assert response.status_code == 200
            assert 'about_sidebar_html' in response.data
            if waffle_enabled:
                assert response.data['about_sidebar_html'] == '<div>About Course</div>'
            else:
                assert response.data['about_sidebar_html'] is None


@ddt.ddt
@skip_unless_lms
class CoursewareMetaIntegrationTestViews(BaseCoursewareTests):
    """
    Integration tests for CoursewareMeta with different user states and course configurations
    """

    @ddt.data(
        ('audit', False),
        ('verified', True),
        ('honor', True),
        ('professional', True),
    )
    @ddt.unpack
    def test_enrollment_mode_affects_can_access_proctored_exams(self, enrollment_mode, expected_access):
        """
        Test that enrollment mode affects proctored exam access in API response
        """
        CourseEnrollment.enroll(self.user, self.course.id, enrollment_mode)

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['can_access_proctored_exams'] == expected_access

    @mock.patch('openedx.core.djangoapps.courseware_api.views.check_public_access')
    def test_public_course_affects_allow_anonymous(self, mock_check_public_access):
        """
        Test that course visibility settings affect allow_anonymous field
        """
        mock_check_public_access.return_value = ACCESS_GRANTED

        response = self.client.get(self.url)
        assert response.status_code == 200
        assert response.data['allow_anonymous'] is True
