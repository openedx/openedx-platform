# pylint: disable=django-not-configured
"""Tests for course home page date summary blocks."""


from datetime import datetime, timedelta
from unittest.mock import patch

import crum
import ddt
from django.conf import settings
from django.test import RequestFactory
from edx_toggles.toggles.testutils import override_waffle_flag, override_waffle_switch
from freezegun import freeze_time
from pytz import utc

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.certificates.config import AUTO_CERTIFICATE_GENERATION
from lms.djangoapps.commerce.models import CommerceConfiguration
from lms.djangoapps.courseware.courses import get_course_date_blocks
from lms.djangoapps.courseware.date_summary import (
    CertificateAvailableDate,
    CourseAssignmentDate,
    CourseEndDate,
    CourseExpiredDate,
    CourseStartDate,
    TodaysDate,
    VerificationDeadlineDate,
    VerifiedUpgradeDeadlineDate,
)
from lms.djangoapps.courseware.models import (
    CourseDynamicUpgradeDeadlineConfiguration,
    DynamicUpgradeDeadlineConfiguration,
    OrgDynamicUpgradeDeadlineConfiguration,
)
from lms.djangoapps.verify_student.models import VerificationDeadline
from lms.djangoapps.verify_student.services import IDVerificationService
from lms.djangoapps.verify_student.tests.factories import SoftwareSecurePhotoVerificationFactory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.features.course_duration_limits.models import CourseDurationLimitConfig
from openedx.features.course_experience import RELATIVE_DATES_FLAG
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class CourseDateSummaryTest(SharedModuleStoreTestCase):
    """Tests for course date summary blocks."""

    def make_request(self, user):
        """ Creates a request """
        request = RequestFactory().request()
        request.user = user
        self.addCleanup(crum.set_current_request, None)
        crum.set_current_request(request)
        return request

    # Tests for which blocks are enabled
    def assert_block_types(self, course, user, expected_blocks):
        """Assert that the enabled block types for this course are as expected."""
        blocks = get_course_date_blocks(course, user)
        assert len(blocks) == len(expected_blocks)
        assert {type(b) for b in blocks} == set(expected_blocks)

    @ddt.data(
        # Verified enrollment with no photo-verification before course start
        ({}, {}, (CourseEndDate, CourseStartDate, TodaysDate, VerificationDeadlineDate)),
        # Verified enrollment with `approved` photo-verification after course end
        ({'days_till_start': -10,
          'days_till_end': -5,
          'days_till_upgrade_deadline': -6,
          'days_till_verification_deadline': -5,
          },
         {'verification_status': 'approved'},
         (TodaysDate, CourseEndDate)),
        # Verified enrollment with `expired` photo-verification during course run
        ({'days_till_start': -10},
         {'verification_status': 'expired'},
         (TodaysDate, CourseEndDate, VerificationDeadlineDate)),
        # Verified enrollment with `approved` photo-verification during course run
        ({'days_till_start': -10},
         {'verification_status': 'approved'},
         (TodaysDate, CourseEndDate)),
        # Verified enrollment with *NO* course end date
        ({'days_till_end': None},
         {},
         (CourseStartDate, TodaysDate, VerificationDeadlineDate)),
        # Verified enrollment with no photo-verification during course run
        ({'days_till_start': -1},
         {},
         (TodaysDate, CourseEndDate, VerificationDeadlineDate)),
        # Verification approved
        ({'days_till_start': -10,
          'days_till_upgrade_deadline': -1,
          'days_till_verification_deadline': 1,
          },
         {'verification_status': 'approved'},
         (TodaysDate, CourseEndDate)),
        # After upgrade deadline
        ({'days_till_start': -10,
          'days_till_upgrade_deadline': -1},
         {},
         (TodaysDate, CourseEndDate, VerificationDeadlineDate)),
        # After verification deadline
        ({'days_till_start': -10,
          'days_till_upgrade_deadline': -2,
          'days_till_verification_deadline': -1},
         {},
         (TodaysDate, CourseEndDate, VerificationDeadlineDate)),
    )
    @ddt.unpack
    def test_enabled_block_types(self, course_kwargs, user_kwargs, expected_blocks):
        course = create_course_run(**course_kwargs)
        user = create_user(**user_kwargs)
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        self.assert_block_types(course, user, expected_blocks)

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_enabled_block_types_with_assignments(self):  # pylint: disable=too-many-statements
        """
        Creates a course with multiple subsections to test all of the different
        cases for assignment dates showing up. Mocks out calling the edx-when
        service and then validates the correct data is set and returned.
        """
        course = create_course_run(days_till_start=-100)
        user = create_user()
        request = self.make_request(user)
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        now = datetime.now(utc)
        assignment_title_html = ['<a href=', '</a>']
        with self.store.bulk_operations(course.id):
            section = BlockFactory.create(category='chapter', parent_location=course.location)
            BlockFactory.create(
                category='sequential',
                display_name='Released',
                parent_location=section.location,
                start=now - timedelta(days=1),
                due=now + timedelta(days=6),
                graded=True,
                format='Homework',
            )
            BlockFactory.create(
                category='sequential',
                display_name='Not released',
                parent_location=section.location,
                start=now + timedelta(days=1),
                due=now + timedelta(days=7),
                graded=True,
                format='Homework',
            )
            BlockFactory.create(
                category='sequential',
                display_name='Third nearest assignment',
                parent_location=section.location,
                start=now + timedelta(days=1),
                due=now + timedelta(days=8),
                graded=True,
                format='Exam',
            )
            BlockFactory.create(
                category='sequential',
                display_name='Past due date',
                parent_location=section.location,
                start=now - timedelta(days=14),
                due=now - timedelta(days=7),
                graded=True,
                format='Exam',
            )
            BlockFactory.create(
                category='sequential',
                display_name='Not returned since we do not get non-graded subsections',
                parent_location=section.location,
                start=now + timedelta(days=1),
                due=now - timedelta(days=7),
                graded=False,
            )
            BlockFactory.create(
                category='sequential',
                display_name='No start date',
                parent_location=section.location,
                start=None,
                due=now + timedelta(days=9),
                graded=True,
                format='Speech',
            )
            BlockFactory.create(
                category='sequential',
                # Setting display name to None should set the assignment title to 'Assignment'
                display_name=None,
                parent_location=section.location,
                start=now - timedelta(days=14),
                due=now + timedelta(days=10),
                graded=True,
                format=None,
            )
            dummy_subsection = BlockFactory.create(category='sequential', graded=True, due=now + timedelta(days=11))

        # We are deleting this subsection right after creating it because we need to pass in a real
        # location object (dummy_subsection.location), but do not want this to exist inside of the modulestore
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, course.id):
            self.store.delete_item(dummy_subsection.location, user.id)

        # Standard widget case where we restrict the number of assignments.
        expected_blocks = (
            TodaysDate, CourseAssignmentDate, CourseAssignmentDate, CourseEndDate, VerificationDeadlineDate
        )
        blocks = get_course_date_blocks(course, user, request, num_assignments=2)
        assert len(blocks) == len(expected_blocks)
        assert {type(b) for b in blocks} == set(expected_blocks)
        assignment_blocks = filter(
            lambda b: isinstance(b, CourseAssignmentDate), blocks
        )
        for assignment in assignment_blocks:
            assignment_title = str(assignment.title_html) or str(assignment.title)
            assert assignment_title != 'Third nearest assignment'
            assert assignment_title != 'Past due date'
            assert assignment_title != 'Not returned since we do not get non-graded subsections'
            # checking if it is _in_ the title instead of being the title since released assignments
            # are actually links. Unreleased assignments are just the string of the title.
            if 'Released' in assignment_title:
                for html_tag in assignment_title_html:
                    assert html_tag in assignment_title
            elif assignment_title == 'Not released':
                for html_tag in assignment_title_html:
                    assert html_tag not in assignment_title

        # No restrictions on number of assignments to return
        expected_blocks = (
            CourseStartDate, TodaysDate, CourseAssignmentDate, CourseAssignmentDate, CourseAssignmentDate,
            CourseAssignmentDate, CourseAssignmentDate, CourseAssignmentDate, CourseEndDate,
            VerificationDeadlineDate
        )
        blocks = get_course_date_blocks(course, user, request, include_past_dates=True)
        assert len(blocks) == len(expected_blocks)
        assert {type(b) for b in blocks} == set(expected_blocks)
        assignment_blocks = filter(
            lambda b: isinstance(b, CourseAssignmentDate), blocks
        )
        for assignment in assignment_blocks:
            assignment_title = str(assignment.title_html) or str(assignment.title)
            assert assignment_title != 'Not returned since we do not get non-graded subsections'

            assignment_type = str(assignment.assignment_type)
            # checking if it is _in_ the title instead of being the title since released assignments
            # are actually links. Unreleased assignments are just the string of the title.
            # also checking that the assignment type is returned for graded subsections
            if 'Released' in assignment_title:
                assert assignment_type == 'Homework'
                for html_tag in assignment_title_html:
                    assert html_tag in assignment_title
            elif assignment_title == 'Not released':
                assert assignment_type == 'Homework'
                for html_tag in assignment_title_html:
                    assert html_tag not in assignment_title
            elif assignment_title == 'Third nearest assignment':
                assert assignment_type == 'Exam'
                # It's still not released
                for html_tag in assignment_title_html:
                    assert html_tag not in assignment_title
            elif 'Past due date' in assignment_title:
                assert now > assignment.date
                assert assignment_type == 'Exam'
                for html_tag in assignment_title_html:
                    assert html_tag in assignment_title
            elif 'No start date' == assignment_title:
                assert assignment_type == 'Speech'
                # Can't determine if it is released so it does not get a link
                for html_tag in assignment_title_html:
                    assert html_tag not in assignment_title
            # This is the item with no display name where we set one ourselves.
            elif 'Assignment' in assignment_title:
                assert assignment_type is None
                # Can't determine if it is released so it does not get a link
                for html_tag in assignment_title_html:
                    assert html_tag in assignment_title

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    @ddt.data(
        ([], 3),
        ([{
            'due': None,
            'start': None,
            'name': 'student-training',
            'examples': [
                {
                    'answer': ['Replace this text with your own sample response...'],
                    'options_selected': [
                        {'option': 'Fair', 'criterion': 'Ideas'},
                        {'option': 'Good', 'criterion': 'Content'}
                    ]
                }, {
                    'answer': ['Replace this text with another sample response...'],
                    'options_selected': [
                        {'option': 'Poor', 'criterion': 'Ideas'},
                        {'option': 'Good', 'criterion': 'Content'}
                    ]
                }
            ]
        }, {
            'due': '2029-01-01T00:00:00+00:00',
            'start': '2001-01-01T00:00:00+00:00',
            'must_be_graded_by': 3,
            'name': 'peer-assessment',
            'must_grade': 5
        }, {
            'due': '2029-01-01T00:00:00+00:00',
            'start': '2001-01-01T00:00:00+00:00',
            'name': 'self-assessment'
        }], 5)
    )
    @ddt.unpack
    def test_dates_with_openassessments(self, rubric_assessments, date_block_count):
        course = create_self_paced_course_run(days_till_start=-1, org_id='TestOrg')

        user = create_user()
        request = self.make_request(user)
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        now = datetime.now(utc)

        chapter = BlockFactory.create(
            parent=course,
            category="chapter",
            graded=True,
        )
        section = BlockFactory.create(
            parent=chapter,
            category="sequential",
        )
        vertical = BlockFactory.create(
            parent=section,
            category="vertical",
        )
        BlockFactory.create(
            parent=vertical,
            category="openassessment",
            rubric_assessments=rubric_assessments,
            submission_start=(now + timedelta(days=1)).isoformat(),
            submission_end=(now + timedelta(days=7)).isoformat(),
        )
        blocks = get_course_date_blocks(course, user, request, include_past_dates=True)
        assert len(blocks) == date_block_count

    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_enabled_block_types_with_expired_course(self):
        course = create_course_run(days_till_start=-100)
        user = create_user()
        self.make_request(user)
        # These two lines are to trigger the course expired block to be rendered
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.AUDIT)
        CourseDurationLimitConfig.objects.create(enabled=True, enabled_as_of=datetime(2018, 1, 1, tzinfo=utc))

        expected_blocks = (
            TodaysDate, CourseEndDate, CourseExpiredDate, VerifiedUpgradeDeadlineDate
        )
        self.assert_block_types(course, user, expected_blocks)

    @ddt.data(
        # Course not started
        ({}, (CourseStartDate, TodaysDate, CourseEndDate)),
        # Course active
        ({'days_till_start': -1}, (TodaysDate, CourseEndDate)),
        # Course ended
        ({'days_till_start': -10, 'days_till_end': -5},
         (TodaysDate, CourseEndDate)),
    )
    @ddt.unpack
    def test_enabled_block_types_without_enrollment(self, course_kwargs, expected_blocks):
        course = create_course_run(**course_kwargs)
        user = create_user()
        self.assert_block_types(course, user, expected_blocks)

    def test_enabled_block_types_with_non_upgradeable_course_run(self):
        course = create_course_run(days_till_start=-10, days_till_verification_deadline=None)
        user = create_user()
        CourseMode.objects.get(course_id=course.id, mode_slug=CourseMode.VERIFIED).delete()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.AUDIT)
        self.assert_block_types(course, user, (TodaysDate, CourseEndDate))

    def test_todays_date_block(self):
        """
        Helper function to test that today's date block renders correctly
        and displays the correct time, accounting for daylight savings
        """
        with freeze_time('2015-01-02'):
            course = create_course_run()
            user = create_user()
            block = TodaysDate(course, user)
            assert block.is_enabled
            assert block.is_allowed
            assert block.date == datetime.now(utc)
            assert block.title == 'current_datetime'

    ## Tests Course Start Date
    def test_course_start_date(self):
        course = create_course_run()
        user = create_user()
        block = CourseStartDate(course, user)
        assert block.date == course.start

    @ddt.data(
        # Instructor-paced course: Use course start date
        (False, datetime(2025, 1, 10, tzinfo=utc), datetime(2025, 1, 12, tzinfo=utc),
            datetime(2025, 1, 10, tzinfo=utc), 'Course starts'),

        # Self-paced course: Enrollment created later than course start
        (True, datetime(2025, 1, 10, tzinfo=utc), datetime(2025, 1, 12), datetime(2025, 1, 12, tzinfo=utc),
            'Enrollment Date'),

        # Self-paced course: Enrollment created earlier than course start
        (True, datetime(2025, 1, 10, tzinfo=utc), datetime(2025, 1, 8), datetime(2025, 1, 10, tzinfo=utc),
            'Course starts'),

        # Self-paced course: No enrollment
        (True, datetime(2025, 1, 10, tzinfo=utc), None, datetime(2025, 1, 10, tzinfo=utc), 'Course starts'),
    )
    @ddt.unpack
    def test_course_start_date_label(self, self_paced, course_start, enrollment_created, expected_date, expected_title):
        """
        Test the CourseStartDate class has correct label for course start date
        """
        course = CourseFactory(self_paced=self_paced, start=course_start)
        user = create_user()
        if enrollment_created:
            enrollment = CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
            enrollment.created = enrollment_created
            enrollment.save()
        date_summary = CourseStartDate(user=user, course=course)
        self.assertEqual(date_summary.date, expected_date)  # noqa: PT009
        self.assertEqual(str(date_summary.title), expected_title)  # noqa: PT009

    ## Tests Course End Date Block
    def test_course_end_date_for_certificate_eligible_mode(self):
        course = create_course_run(days_till_start=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        block = CourseEndDate(course, user)
        assert block.description == ('After this date, the course will be archived, which means you can review the '
                                     'course content but can no longer participate in graded assignments or work '
                                     'towards earning a certificate.')

    def test_course_end_date_for_non_certificate_eligible_mode(self):
        course = create_course_run(days_till_start=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.AUDIT)
        block = CourseEndDate(course, user)
        assert block.description == 'After the course ends, the course content will be archived and no longer active.'
        assert block.title == 'Course ends'

    def test_course_end_date_after_course(self):
        course = create_course_run(days_till_start=-2, days_till_end=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        block = CourseEndDate(course, user)
        assert block.description ==\
               'This course is archived, which means you can review course content but it is no longer active.'
        assert block.title == 'Course ends'

    @ddt.data(300, 400)
    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_course_end_date_self_paced(self, days_till_end):
        """
        In self-paced courses, the end date will only show up if the learner
        views the course within 365 days of the course end date.
        """
        now = datetime.now(utc)
        course = CourseFactory.create(
            start=now + timedelta(days=-7), end=now + timedelta(days=days_till_end), self_paced=True)
        user = create_user()
        block = CourseEndDate(course, user)
        assert block.title == 'Course ends'
        if 365 > days_till_end:
            assert block.date == course.end
        else:
            assert block.date is None
            assert block.description == ''

    def test_ecommerce_checkout_redirect(self):
        """Verify the block link redirects to ecommerce checkout if it's enabled."""
        sku = 'TESTSKU'
        configuration = CommerceConfiguration.objects.create(checkout_on_ecommerce_service=True)
        course = create_course_run()
        user = create_user()
        course_mode = CourseMode.objects.get(course_id=course.id, mode_slug=CourseMode.VERIFIED)
        course_mode.sku = sku
        course_mode.save()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)

        block = VerifiedUpgradeDeadlineDate(course, user)
        assert block.link == f'{configuration.basket_checkout_page}?sku={sku}'

    ## CertificateAvailableDate
    @override_waffle_switch(AUTO_CERTIFICATE_GENERATION, True)
    def test_no_certificate_available_date(self):
        course = create_course_run(days_till_start=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.AUDIT)
        block = CertificateAvailableDate(course, user)
        assert block.date is None
        assert not block.is_allowed

    ## CertificateAvailableDate
    @override_waffle_switch(AUTO_CERTIFICATE_GENERATION, True)
    def test_no_certificate_available_date_for_self_paced(self):
        course = create_self_paced_course_run()
        verified_user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=verified_user, mode=CourseMode.VERIFIED)
        course.certificate_available_date = datetime.now(utc) + timedelta(days=7)
        course.save()
        block = CertificateAvailableDate(course, verified_user)
        assert block.date is not None
        assert not block.is_allowed

    def test_no_certificate_available_date_for_audit_course(self):
        """
        Tests that Certificate Available Date is not visible in the course "Important Course Dates" section
        if the course only has audit mode.
        """
        course = create_course_run()
        audit_user = create_user()

        # Enroll learner in the audit mode and verify the course only has 1 mode (audit)
        CourseEnrollmentFactory(course_id=course.id, user=audit_user, mode=CourseMode.AUDIT)
        CourseMode.objects.get(course_id=course.id, mode_slug=CourseMode.VERIFIED).delete()
        all_course_modes = CourseMode.modes_for_course(course.id)
        assert len(all_course_modes) == 1
        assert all_course_modes[0].slug == CourseMode.AUDIT

        course.certificate_available_date = datetime.now(utc) + timedelta(days=7)
        course.save()

        # Verify Certificate Available Date is not enabled for learner.
        block = CertificateAvailableDate(course, audit_user)
        assert not block.is_allowed
        assert block.date is not None

    @override_waffle_switch(AUTO_CERTIFICATE_GENERATION, True)
    def test_certificate_available_date_defined(self):
        course = create_course_run()
        audit_user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=audit_user, mode=CourseMode.AUDIT)
        verified_user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=verified_user, mode=CourseMode.VERIFIED)
        course.certificate_available_date = datetime.now(utc) + timedelta(days=7)
        enable_course_certificates(course)
        expected_blocks = [
            CourseEndDate, CourseStartDate, TodaysDate, VerificationDeadlineDate, CertificateAvailableDate
        ]
        self.assert_block_types(course, verified_user, expected_blocks)
        for block in (CertificateAvailableDate(course, audit_user), CertificateAvailableDate(course, verified_user)):
            assert course.certificate_available_date is not None
            assert block.date == course.certificate_available_date
            assert block.is_allowed

    ## VerificationDeadlineDate
    def test_no_verification_deadline(self):
        course = create_course_run(days_till_start=-1, days_till_verification_deadline=None)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        block = VerificationDeadlineDate(course, user)
        assert block.date is None
        assert block.is_allowed

    def test_no_verified_enrollment(self):
        course = create_course_run(days_till_start=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.AUDIT)
        block = VerificationDeadlineDate(course, user)
        assert not block.is_allowed

    @patch.dict(settings.FEATURES, {'ENABLE_INTEGRITY_SIGNATURE': True})
    def test_verification_deadline_with_integrity_signature(self):
        course = create_course_run(days_till_start=-1)
        user = create_user()
        CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)
        block = VerificationDeadlineDate(course, user)
        assert not block.is_allowed

    def test_verification_deadline_date_upcoming(self):
        with freeze_time('2015-01-02'):
            course = create_course_run(days_till_start=-1)
            user = create_user()
            CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)

            block = VerificationDeadlineDate(course, user)
            assert block.css_class == 'verification-deadline-upcoming'
            assert block.title == 'Verification Deadline'
            assert block.date == (datetime.now(utc) + timedelta(days=14))
            assert block.description ==\
                   'You must successfully complete verification before this date to qualify for a Verified Certificate.'
            assert block.link_text == 'Verify My Identity'
            assert block.link == IDVerificationService.get_verify_location(course.id)

    def test_verification_deadline_date_retry(self):
        with freeze_time('2015-01-02'):
            course = create_course_run(days_till_start=-1)
            user = create_user(verification_status='denied')
            CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)

            block = VerificationDeadlineDate(course, user)
            assert block.css_class == 'verification-deadline-retry'
            assert block.title == 'Verification Deadline'
            assert block.date == (datetime.now(utc) + timedelta(days=14))
            assert block.description ==\
                   'You must successfully complete verification before this date to qualify for a Verified Certificate.'
            assert block.link_text == 'Retry Verification'
            assert block.link == IDVerificationService.get_verify_location()

    def test_verification_deadline_date_denied(self):
        with freeze_time('2015-01-02'):
            course = create_course_run(days_till_start=-10, days_till_verification_deadline=-1)
            user = create_user(verification_status='denied')
            CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)

            block = VerificationDeadlineDate(course, user)
            assert block.css_class == 'verification-deadline-passed'
            assert block.title == 'Missed Verification Deadline'
            assert block.date == (datetime.now(utc) + timedelta(days=- 1))
            assert block.description == "Unfortunately you missed this course's deadline for a successful verification."
            assert block.link_text == 'Learn More'
            assert block.link == ''

    @ddt.data(
        (-1, '1 day ago - {date}'),
        (1, 'in 1 day - {date}')
    )
    @ddt.unpack
    def test_render_date_string_past(self, delta, expected_date_string):
        with freeze_time('2015-01-02'):
            course = create_course_run(days_till_start=-10, days_till_verification_deadline=delta)
            user = create_user(verification_status='denied')
            CourseEnrollmentFactory(course_id=course.id, user=user, mode=CourseMode.VERIFIED)

            block = VerificationDeadlineDate(course, user)
            assert block.relative_datestring == expected_date_string


@ddt.ddt
class TestScheduleOverrides(SharedModuleStoreTestCase):
    """ Tests for Schedule Overrides """

    def test_date_with_self_paced_with_enrollment_before_course_start(self):
        """ Enrolling before a course begins should result in the upgrade deadline being set relative to the
        course start date. """
        global_config = DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        course = create_self_paced_course_run(days_till_start=3)
        overview = CourseOverview.get_from_id(course.id)
        expected = overview.start + timedelta(days=global_config.deadline_days)
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        assert block.date == expected
        self._check_text(block)

    def _check_text(self, upgrade_date_summary):
        """ Validates the text on an upgrade_date_summary """
        assert upgrade_date_summary.title == 'Upgrade to Verified Certificate'
        assert upgrade_date_summary.description ==\
               "Don't miss the opportunity to highlight your new knowledge and skills by earning a verified" \
               " certificate."
        assert upgrade_date_summary.relative_datestring == 'by {date}'

    def test_date_with_self_paced_with_enrollment_after_course_start(self):
        """ Enrolling after a course begins should result in the upgrade deadline being set relative to the
        enrollment date.

        Additionally, OrgDynamicUpgradeDeadlineConfiguration should override the number of days until the deadline,
        and CourseDynamicUpgradeDeadlineConfiguration should override the org-level override.
        """
        global_config = DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        course = create_self_paced_course_run(days_till_start=-1, org_id='TestOrg')
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        expected = enrollment.created + timedelta(days=global_config.deadline_days)
        assert block.date == expected

        # Orgs should be able to override the deadline
        org_config = OrgDynamicUpgradeDeadlineConfiguration.objects.create(
            enabled=True, org_id=course.org, deadline_days=4
        )
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        expected = enrollment.created + timedelta(days=org_config.deadline_days)
        assert block.date == expected

        # Courses should be able to override the deadline (and the org-level override)
        course_config = CourseDynamicUpgradeDeadlineConfiguration.objects.create(
            enabled=True, course_id=course.id, deadline_days=3
        )
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        expected = enrollment.created + timedelta(days=course_config.deadline_days)
        assert block.date == expected

    def test_date_with_self_paced_without_dynamic_upgrade_deadline(self):
        """ Disabling the dynamic upgrade deadline functionality should result in the verified mode's
        expiration date being returned. """
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=False)
        course = create_self_paced_course_run()
        expected = CourseMode.objects.get(course_id=course.id, mode_slug=CourseMode.VERIFIED).expiration_datetime
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)
        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        assert block.date == expected

    def test_date_with_existing_schedule(self):
        """ If a schedule is created while deadlines are disabled, they shouldn't magically appear once the feature is
        turned on. """
        course = create_self_paced_course_run(days_till_start=-1)
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=False)
        course_config = CourseDynamicUpgradeDeadlineConfiguration.objects.create(enabled=False, course_id=course.id)
        enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT)

        # The enrollment has a schedule, but the upgrade deadline should be None
        assert enrollment.schedule.upgrade_deadline is None

        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        expected = CourseMode.objects.get(course_id=course.id, mode_slug=CourseMode.VERIFIED).expiration_datetime
        assert block.date == expected

        # Now if we turn on the feature for this course, this existing enrollment should be unaffected
        course_config.enabled = True
        course_config.save()

        block = VerifiedUpgradeDeadlineDate(course, enrollment.user)
        assert block.date == expected

    @ddt.data(
        # (enroll before configs, org enabled, org opt-out, course enabled, course opt-out, expected dynamic deadline)
        (False, False, False, False, False, True),
        (False, False, False, False, True, True),
        (False, False, False, True, False, True),
        (False, False, False, True, True, False),
        (False, False, True, False, False, True),
        (False, False, True, False, True, True),
        (False, False, True, True, False, True),
        (False, False, True, True, True, False),
        (False, True, False, False, False, True),
        (False, True, False, False, True, True),
        (False, True, False, True, False, True),
        (False, True, False, True, True, False),  # course-level overrides org-level
        (False, True, True, False, False, False),
        (False, True, True, False, True, False),
        (False, True, True, True, False, True),  # course-level overrides org-level
        (False, True, True, True, True, False),

        (True, False, False, False, False, True),
        (True, False, False, False, True, True),
        (True, False, False, True, False, True),
        (True, False, False, True, True, False),
        (True, False, True, False, False, True),
        (True, False, True, False, True, True),
        (True, False, True, True, False, True),
        (True, False, True, True, True, False),
        (True, True, False, False, False, True),
        (True, True, False, False, True, True),
        (True, True, False, True, False, True),
        (True, True, False, True, True, False),  # course-level overrides org-level
        (True, True, True, False, False, False),
        (True, True, True, False, True, False),
        (True, True, True, True, False, True),  # course-level overrides org-level
        (True, True, True, True, True, False),
    )
    @ddt.unpack
    def test_date_with_org_and_course_config_overrides(self, enroll_first, org_config_enabled, org_config_opt_out,
                                                       course_config_enabled, course_config_opt_out,
                                                       expected_dynamic_deadline):
        """ Runs through every combination of org-level plus course-level DynamicUpgradeDeadlineConfiguration enabled
        and opt-out states to verify that course-level overrides the org-level config. """
        course = create_self_paced_course_run(days_till_start=-1, org_id='TestOrg')
        DynamicUpgradeDeadlineConfiguration.objects.create(enabled=True)
        if enroll_first:
            course_overview = CourseOverviewFactory.create(self_paced=True)
            enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT, course=course_overview)
        OrgDynamicUpgradeDeadlineConfiguration.objects.create(
            enabled=org_config_enabled, opt_out=org_config_opt_out, org_id=course.id.org
        )
        CourseDynamicUpgradeDeadlineConfiguration.objects.create(
            enabled=course_config_enabled, opt_out=course_config_opt_out, course_id=course.id
        )
        if not enroll_first:
            course_overview = CourseOverviewFactory.create(self_paced=True)
            enrollment = CourseEnrollmentFactory(course_id=course.id, mode=CourseMode.AUDIT, course=course_overview)

        # The enrollment has a schedule, and the upgrade_deadline is set when expected_dynamic_deadline is True
        if not enroll_first:
            assert (enrollment.schedule.upgrade_deadline is not None) == expected_dynamic_deadline
        # The CourseEnrollment.upgrade_deadline property method is checking the configs
        assert (enrollment.dynamic_upgrade_deadline is not None) == expected_dynamic_deadline


def create_user(verification_status=None):
    """ Create a new User instance.

    Arguments:
        verification_status (str): User's verification status. If this value is set an instance of
            SoftwareSecurePhotoVerification will be created for the user with the specified status.
    """
    user = UserFactory()

    if verification_status is not None:
        SoftwareSecurePhotoVerificationFactory.create(user=user, status=verification_status)

    return user


def create_course_run(
    days_till_start=1, days_till_end=14, days_till_upgrade_deadline=4, days_till_verification_deadline=14,
):
    """ Create a new course run and course modes.

    All date-related arguments are relative to the current date-time (now) unless otherwise specified.

    Both audit and verified `CourseMode` objects will be created for the course run.

    Arguments:
        days_till_end (int): Number of days until the course ends.
        days_till_start (int): Number of days until the course starts.
        days_till_upgrade_deadline (int): Number of days until the course run's upgrade deadline.
        days_till_verification_deadline (int): Number of days until the course run's verification deadline. If this
            value is set to `None` no deadline will be verification deadline will be created.
    """
    now = datetime.now(utc)
    course = CourseFactory.create(start=now + timedelta(days=days_till_start))

    course.end = None
    if days_till_end is not None:
        course.end = now + timedelta(days=days_till_end)

    CourseModeFactory(course_id=course.id, mode_slug=CourseMode.AUDIT)
    CourseModeFactory(
        course_id=course.id,
        mode_slug=CourseMode.VERIFIED,
        expiration_datetime=now + timedelta(days=days_till_upgrade_deadline)
    )

    if days_till_verification_deadline is not None:
        VerificationDeadline.objects.create(
            course_key=course.id,
            deadline=now + timedelta(days=days_till_verification_deadline)
        )

    return course


def create_self_paced_course_run(days_till_start=1, org_id=None):
    """ Create a new course run and course modes.

    All date-related arguments are relative to the current date-time (now) unless otherwise specified.

    Both audit and verified `CourseMode` objects will be created for the course run.

    Arguments:
        days_till_start (int): Number of days until the course starts.
        org_id (string): String org id to assign the course to (default: None; use CourseFactory default)
    """
    now = datetime.now(utc)
    course = CourseFactory.create(start=now + timedelta(days=days_till_start), self_paced=True,
                                  org=org_id if org_id else 'TestedX')

    CourseModeFactory(
        course_id=course.id,
        mode_slug=CourseMode.AUDIT
    )
    CourseModeFactory(
        course_id=course.id,
        mode_slug=CourseMode.VERIFIED,
        expiration_datetime=now + timedelta(days=100)
    )

    return course


def enable_course_certificates(course):
    """
    Enable course certificate configuration.
    """
    course.certificates = {
        'certificates': [{
            'course_title': 'Test',
            'name': '',
            'is_active': True,
        }]
    }
    course.save()
