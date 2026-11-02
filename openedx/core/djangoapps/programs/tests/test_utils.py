"""Tests covering Programs utilities."""

import datetime
import json
import uuid
from collections import namedtuple
from copy import deepcopy
from unittest import mock

import ddt
import httpretty
from django.conf import settings
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_switch
from opaque_keys.edx.keys import CourseKey  # lint-amnesty, pylint: disable=wrong-import-order
from zoneinfo import ZoneInfo
from testfixtures import LogCapture

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.entitlements.tests.factories import CourseEntitlementFactory
from common.djangoapps.student.tests.factories import AnonymousUserFactory, CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.date_utils import strftime_localized
from lms.djangoapps.certificates.api import MODES
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.commerce.tests.test_utils import update_commerce_config
from lms.djangoapps.commerce.utils import EcommerceService
from openedx.core.djangoapps.catalog.tests.factories import (
    CourseFactory,
    CourseRunFactory,
    EntitlementFactory,
    ProgramFactory,
    SeatFactory,
    generate_course_run_key
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.programs import ALWAYS_CALCULATE_PROGRAM_PRICE_AS_ANONYMOUS_USER
from openedx.core.djangoapps.programs.tests.factories import ProgressFactory
from openedx.core.djangoapps.programs.utils import (
    DEFAULT_ENROLLMENT_START_DATE,
    ProgramDataExtender,
    ProgramMarketingDataExtender,
    ProgramProgressMeter,
    get_certificates,
    get_logged_in_program_certificate_url,
    is_user_enrolled_in_program_type
)
from openedx.core.djangoapps.site_configuration.tests.factories import SiteFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory as ModuleStoreCourseFactory

ECOMMERCE_URL_ROOT = 'https://ecommerce.example.com'
UTILS_MODULE = 'openedx.core.djangoapps.programs.utils'
LOGGER_NAME = 'openedx.core.djangoapps.programs.utils'


@ddt.ddt
@skip_unless_lms
@mock.patch("lms.djangoapps.certificates.api.auto_certificate_generation_enabled", mock.Mock(return_value=True))
@mock.patch(UTILS_MODULE + '.get_programs')
class TestProgramProgressMeter(ModuleStoreTestCase):
    """Tests of the program progress utility class."""

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.site = SiteFactory()

    def _create_enrollments(self, *course_run_ids):
        """Variadic helper used to create course run enrollments."""
        for course_run_id in course_run_ids:
            CourseEnrollmentFactory(user=self.user, course_id=course_run_id, mode=CourseMode.VERIFIED)

    def _create_entitlements(self, *course_uuids):
        """ Variadic helper used to create course entitlements. """
        for course_uuid in course_uuids:
            CourseEntitlementFactory(user=self.user, course_uuid=course_uuid)

    def _create_certificates(self, *course_keys, **kwargs):
        """ Variadic helper used to create course certificates. """
        kwargs.setdefault('status', 'downloadable')
        return [GeneratedCertificateFactory(user=self.user, course_id=key, **kwargs) for key in course_keys]

    def _assert_progress(self, meter, *progresses):
        """Variadic helper used to verify progress calculations."""
        assert meter.progress() == list(progresses)

    def _attach_detail_url(self, programs):
        """Add expected detail URLs to a list of program dicts."""
        for program in programs:
            program['detail_url'] = reverse('program_details_view', kwargs={'program_uuid': program['uuid']})

    def test_no_enrollments_or_entitlements(self, mock_get_programs):
        """Verify behavior when programs exist, but no relevant enrollments or entitlements do."""
        data = [ProgramFactory()]
        mock_get_programs.return_value = data

        meter = ProgramProgressMeter(self.site, self.user)

        assert not meter.engaged_programs
        self._assert_progress(meter)
        assert not list(meter.completed_programs_with_available_dates.keys())

    def test_enrollments_but_no_programs(self, mock_get_programs):
        """Verify behavior when enrollments exist, but no matching programs do."""
        mock_get_programs.return_value = []

        course_run_id = generate_course_run_key()
        self._create_enrollments(course_run_id)
        meter = ProgramProgressMeter(self.site, self.user)

        assert not meter.engaged_programs
        self._assert_progress(meter)
        assert not list(meter.completed_programs_with_available_dates.keys())

    def test_entitlements_but_no_programs(self, mock_get_programs):
        """ Verify engaged_programs is empty when entitlements exist, but no matching programs do. """
        mock_get_programs.return_value = []

        self._create_entitlements(uuid.uuid4())
        meter = ProgramProgressMeter(self.site, self.user)

        assert not meter.engaged_programs

    def test_single_program_enrollment(self, mock_get_programs):
        """
        Verify that correct program is returned when the user is enrolled in a
        course run appearing in one program.
        """
        course_run_key = generate_course_run_key()
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        self._create_enrollments(course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        program = data[0]
        assert meter.engaged_programs == [program]
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program['uuid'],
                in_progress=1,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

    def test_single_program_entitlement(self, mock_get_programs):
        """
        Verify that the correct program is returned when the user holds an entitlement
        to a course appearing in one program.
        """
        course_uuid = uuid.uuid4()
        data = [
            ProgramFactory(courses=[CourseFactory(uuid=str(course_uuid))]),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        self._create_entitlements(course_uuid)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        program = data[0]
        assert meter.engaged_programs == [program]

    def test_single_program_multiple_entitlements(self, mock_get_programs):
        """
        Verify that the most recent entitlement is returned when a user has multiple for the same course
        """
        course_uuid = uuid.uuid4()
        data = [
            ProgramFactory(courses=[CourseFactory(uuid=str(course_uuid))]),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data
        course_run_key = generate_course_run_key()
        course_run_key2 = generate_course_run_key()

        enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=course_run_key,
            mode=CourseMode.VERIFIED,
            is_active=False
        )
        enrollment2 = CourseEnrollmentFactory(
            user=self.user,
            course_id=course_run_key2,
            mode=CourseMode.VERIFIED
        )

        CourseEntitlementFactory.create(
            user=self.user,
            course_uuid=course_uuid,
            expired_at=datetime.datetime.now(ZoneInfo("UTC")),
            mode=CourseMode.VERIFIED,
            enrollment_course_run=enrollment

        )
        CourseEntitlementFactory.create(
            user=self.user,
            course_uuid=course_uuid,
            mode=CourseMode.VERIFIED,
            enrollment_course_run=enrollment2
        )

        meter = ProgramProgressMeter(self.site, self.user)
        self._attach_detail_url(data)
        assert len(meter.entitlements) == 1

        entitlement = meter.entitlements[0]
        assert entitlement.expired_at is None
        assert entitlement.enrollment_course_run.course_id == enrollment2.course_id

    def test_course_progress(self, mock_get_programs):
        """
        Verify that the progress meter can represent progress in terms of
        serialized courses.
        """
        course_run_key = generate_course_run_key()
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                    ]),
                ]
            )
        ]
        mock_get_programs.return_value = data

        self._create_enrollments(course_run_key)

        meter = ProgramProgressMeter(self.site, self.user)

        program = data[0]
        expected = [
            ProgressFactory(
                uuid=program['uuid'],
                completed=[],
                in_progress=[program['courses'][0]],
                not_started=[],
                all_unenrolled=False,
            )
        ]

        assert meter.progress(count_only=False) == expected

    def test_no_id_professional_in_progress(self, mock_get_programs):
        """
        Verify that the progress meter treats no-id-professional enrollments
        as professional.
        """
        course_run_key = generate_course_run_key()
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key, type=CourseMode.PROFESSIONAL),
                    ]),
                ]
            )
        ]
        mock_get_programs.return_value = data

        CourseEnrollmentFactory(
            user=self.user, course_id=course_run_key,
            mode=CourseMode.NO_ID_PROFESSIONAL_MODE
        )

        meter = ProgramProgressMeter(self.site, self.user)

        program = data[0]
        expected = [
            ProgressFactory(
                uuid=program['uuid'],
                completed=[],
                in_progress=[program['courses'][0]],
                not_started=[],
                all_unenrolled=False,
            )
        ]

        assert meter.progress(count_only=False) == expected

    @ddt.data(None, 1, -1)
    def test_in_progress_course_upgrade_deadline_check(self, offset, mock_get_programs):
        """
        Verify that if the user's enrollment is not of the same type as the course run,
        the course will only count as in progress if there is another available seat with
        the right type for which the upgrade deadline has not passed.
        """
        course_run_key = generate_course_run_key()
        now = datetime.datetime.now(ZoneInfo("UTC"))
        upgrade_deadline = None if not offset else str(now + datetime.timedelta(days=offset))
        required_seat = SeatFactory(type=CourseMode.VERIFIED, upgrade_deadline=upgrade_deadline)
        enrolled_seat = SeatFactory(type=CourseMode.AUDIT)
        seats = [required_seat, enrolled_seat]

        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key, type=CourseMode.VERIFIED, seats=seats),
                    ]),
                ]
            )
        ]
        mock_get_programs.return_value = data

        CourseEnrollmentFactory(user=self.user, course_id=course_run_key, mode=CourseMode.AUDIT)

        meter = ProgramProgressMeter(self.site, self.user)

        program = data[0]
        expected = [
            ProgressFactory(
                uuid=program['uuid'],
                completed=0,
                in_progress=1 if offset in [None, 1] else 0,
                not_started=1 if offset in [-1] else 0,
                all_unenrolled=False,
            )
        ]

        assert meter.progress(count_only=True) == expected

    def test_multiple_program_enrollment(self, mock_get_programs):
        """
        Verify that correct programs are returned in the correct order when the
        user is enrolled in course runs appearing in programs.
        """
        newer_course_run_key, older_course_run_key = (generate_course_run_key() for __ in range(2))
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=newer_course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=older_course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        # The creation time of the enrollments matters to the test. We want
        # the first_course_run_key to represent the newest enrollment.
        self._create_enrollments(older_course_run_key, newer_course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        programs = data[:2]
        assert meter.engaged_programs == programs

        self._assert_progress(
            meter,
            *(ProgressFactory(
                uuid=program['uuid'],
                in_progress=1,
                all_unenrolled=False,
            ) for program in programs)
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

    def test_multiple_program_entitlement(self, mock_get_programs):
        """
        Verify that the correct programs are returned in the correct order
        when the user holds entitlements to courses appearing in those programs.
        """
        newer_course_uuid, older_course_uuid = (uuid.uuid4() for __ in range(2))
        data = [
            ProgramFactory(courses=[CourseFactory(uuid=str(older_course_uuid)), ]),
            ProgramFactory(courses=[CourseFactory(uuid=str(newer_course_uuid)), ]),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        # The creation time of the entitlements matters to the test. We want
        # the newer_course_uuid to represent the newest entitlement.
        self._create_entitlements(older_course_uuid, newer_course_uuid)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        programs = data[:2]
        assert meter.engaged_programs == programs

    def test_shared_enrollment_engagement(self, mock_get_programs):
        """
        Verify that correct programs are returned when the user is enrolled in a
        single course run appearing in multiple programs.
        """
        shared_course_run_key, solo_course_run_key = (generate_course_run_key() for __ in range(2))

        batch = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=shared_course_run_key),
                    ]),
                ]
            )
            for __ in range(2)
        ]

        joint_programs = sorted(batch, key=lambda program: program['title'])
        data = joint_programs + [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=solo_course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(),
        ]

        mock_get_programs.return_value = data

        # Enrollment for the shared course run created last (most recently).
        self._create_enrollments(solo_course_run_key, shared_course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        programs = data[:3]
        assert meter.engaged_programs == programs

        self._assert_progress(
            meter,
            *(ProgressFactory(
                uuid=program['uuid'],
                in_progress=1,
                all_unenrolled=False,
            ) for program in programs)
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

    def test_shared_entitlement_engagement(self, mock_get_programs):
        """
        Verify that correct programs are returned when the user holds an entitlement
        to a single course appearing in multiple programs.
        """
        shared_course_uuid, solo_course_uuid = (uuid.uuid4() for __ in range(2))

        batch = [
            ProgramFactory(courses=[CourseFactory(uuid=str(shared_course_uuid)), ])
            for __ in range(2)
        ]

        joint_programs = sorted(batch, key=lambda program: program['title'])
        data = joint_programs + [
            ProgramFactory(courses=[CourseFactory(uuid=str(solo_course_uuid)), ]),
            ProgramFactory(),
        ]

        mock_get_programs.return_value = data

        # Entitlement for the shared course created last (most recently).
        self._create_entitlements(shared_course_uuid, solo_course_uuid)
        meter = ProgramProgressMeter(self.site, self.user)

        self._attach_detail_url(data)
        programs = data[:3]
        assert meter.engaged_programs == programs

    def test_simulate_progress(self, mock_get_programs):  # lint-amnesty, pylint: disable=too-many-statements
        """Simulate the entirety of a user's progress through a program."""
        today = datetime.datetime.now(ZoneInfo("UTC"))
        two_days_ago = today - datetime.timedelta(days=2)
        three_days_ago = today - datetime.timedelta(days=3)
        yesterday = today - datetime.timedelta(days=1)
        tomorrow = today + datetime.timedelta(days=1)
        course1 = ModuleStoreCourseFactory.create(
            start=yesterday,
            end=tomorrow,
            self_paced=True,
        )
        first_course_run_key = str(course1.id)
        course2 = ModuleStoreCourseFactory.create(
            start=yesterday,
            end=tomorrow,
            self_paced=True,
        )
        second_course_run_key = str(course2.id)
        course3 = ModuleStoreCourseFactory.create(
            start=three_days_ago,
            end=two_days_ago,
            self_paced=False,
            certificate_available_date=tomorrow,
            certificates_display_behavior=CertificatesDisplayBehaviors.END_WITH_DATE
        )
        third_course_run_key = str(course3.id)

        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=first_course_run_key),
                    ]),
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=second_course_run_key),
                    ]),
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=third_course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        # No enrollments, no programs in progress.
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(meter)
        assert not list(meter.completed_programs_with_available_dates.keys())

        # One enrollment, one program in progress.
        self._create_enrollments(first_course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)
        _, program_uuid = data[0], data[0]['uuid']
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                in_progress=1,
                not_started=2,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

        # 3 enrollments, 3 courses in progress.
        self._create_enrollments(second_course_run_key)
        self._create_enrollments(third_course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                in_progress=3,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

        # One valid certificate earned, one course complete.
        self._create_certificates(first_course_run_key, mode=MODES.verified)
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                completed=1,
                in_progress=2,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

        # Invalid certificate earned, still one course to complete. (invalid because mode doesn't match the course)
        second_cert = self._create_certificates(second_course_run_key, mode=MODES.honor)[0]

        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                completed=1,
                in_progress=2,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

        # Second valid certificate obtained, 2 courses complete.
        second_cert.mode = MODES.verified
        second_cert.save()
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                completed=2,
                in_progress=1,
                all_unenrolled=False,
            )
        )
        assert not list(meter.completed_programs_with_available_dates.keys())

        # 3 certs, 1 unavailable, Program available in the future
        self._create_certificates(third_course_run_key, mode=MODES.verified)
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                completed=2,
                not_started=1,
                all_unenrolled=False,
            )
        )
        assert list(meter.completed_programs_with_available_dates.keys()) == [program_uuid]
        assert meter.completed_programs_with_available_dates[program_uuid] > today

        # 3 certs, all available, program cert in the past/now
        course3_overview = CourseOverview.get_from_id(course3.id)
        course3_overview.certificate_available_date = yesterday
        course3_overview.certificates_display_behavior = CertificatesDisplayBehaviors.END_WITH_DATE
        course3_overview.save()
        meter = ProgramProgressMeter(self.site, self.user)
        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_uuid,
                completed=3,
                all_unenrolled=False,
            )
        )
        assert list(meter.completed_programs_with_available_dates.keys()) == [program_uuid]
        assert meter.completed_programs_with_available_dates[program_uuid].date() == today.date()

    def test_old_course_runs(self, mock_get_programs):
        """
        Test that old course runs may exist for a program which do not exist in LMS.
        In that case, continue considering the course run to have been failed by the learner
        """
        course_run = CourseRunFactory.create()
        course = CourseFactory.create(course_runs=[course_run])
        program_data = ProgramFactory(courses=[course])

        data = [program_data]
        mock_get_programs.return_value = data

        course_run_key = str(course_run['key'])
        self._create_enrollments(course_run_key)
        self._create_certificates(course_run_key, mode=MODES.verified)

        meter = ProgramProgressMeter(self.site, self.user)

        self._assert_progress(
            meter,
            ProgressFactory(
                uuid=program_data['uuid'],
                not_started=1,
                all_unenrolled=False,
            )
        )

    def test_nonverified_course_run_completion(self, mock_get_programs):
        """
        Course runs aren't necessarily of type verified. Verify that a program can
        still be completed when this is the case.
        """
        course1 = ModuleStoreCourseFactory.create(self_paced=True, )
        course_run_key = str(course1.id)
        course2 = ModuleStoreCourseFactory.create(self_paced=True, )
        program = ProgramFactory(
            courses=[
                CourseFactory(course_runs=[
                    CourseRunFactory(key=course_run_key, type='honor'),
                    CourseRunFactory(key=str(course2.id)),
                ]),
            ]
        )
        data = [
            program,
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        self._create_enrollments(course_run_key)
        self._create_certificates(course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)

        program_uuid = program['uuid']
        self._assert_progress(
            meter,
            ProgressFactory(uuid=program_uuid, completed=1, all_unenrolled=False)
        )
        assert list(meter.completed_programs_with_available_dates.keys()) == [program_uuid]

    @mock.patch(UTILS_MODULE + '.certificate_api.available_date_for_certificate')
    def test_completed_programs_with_available_dates(self, mock_available_date_for_certificate, mock_get_programs):
        # First we want to set up the scenario:
        # - A program that is incomplete due to no cert (won't show up in result)
        # - A program that is incomplete due to cert with wrong mode (won't show up in result)
        # - A completed program with the multiple certs, each with specific available dates

        run_no_cert = CourseRunFactory()
        run_wrong_mode = CourseRunFactory()
        run_course1_1 = CourseRunFactory()
        run_course1_2 = CourseRunFactory()
        run_course2 = CourseRunFactory()

        course_no_cert = CourseFactory(course_runs=[run_no_cert])
        course_wrong_mode = CourseFactory(course_runs=[run_wrong_mode])
        course1 = CourseFactory(course_runs=[run_course1_1, run_course1_2])
        course2 = CourseFactory(course_runs=[run_course2])

        program_incomplete_no_cert = ProgramFactory(courses=[course_no_cert, course1, course2])
        program_incomplete_wrong_mode = ProgramFactory(courses=[course_wrong_mode])
        program_complete = ProgramFactory(courses=[course1, course2])
        mock_get_programs.return_value = [program_incomplete_no_cert, program_incomplete_wrong_mode, program_complete]

        self._create_enrollments(run_no_cert['key'], run_wrong_mode['key'], run_course1_1['key'], run_course1_2['key'],
                                 run_course2['key'])
        self._create_certificates(run_wrong_mode['key'], mode=MODES.audit)
        self._create_certificates(run_course1_1['key'], run_course1_2['key'], run_course2['key'], mode=MODES.verified)

        def available_date_fake(_course, cert):
            """ Fake available_date_for_certificate """
            if str(cert.course_id) == run_course1_1['key']:
                return datetime.datetime(2018, 1, 1)
            if str(cert.course_id) == run_course1_2['key']:
                return datetime.datetime(2017, 1, 1)
            if str(cert.course_id) == run_course2['key']:
                return datetime.datetime(2016, 1, 1)
            return datetime.datetime(2015, 1, 1)

        mock_available_date_for_certificate.side_effect = available_date_fake

        meter = ProgramProgressMeter(self.site, self.user)
        available_dates = meter.completed_programs_with_available_dates
        self.assertDictEqual(available_dates, {
            program_complete['uuid']: datetime.datetime(2017, 1, 1)
        })

    def test_completed_course_runs(self, mock_get_programs):
        """
        Verify that the method can find course run certificates when not mocked out.
        """
        downloadable_module_store_course = ModuleStoreCourseFactory.create(self_paced=True, )
        downloadable = CourseRunFactory(key=downloadable_module_store_course.id)
        course_availability_in_future = CourseRunFactory()
        generating_module_store_course = ModuleStoreCourseFactory.create(self_paced=True, )
        generating = CourseRunFactory(key=generating_module_store_course.id)
        unknown = CourseRunFactory()
        course = CourseFactory(course_runs=[downloadable, course_availability_in_future, generating, unknown])
        program = ProgramFactory(courses=[course])
        mock_get_programs.return_value = [program]

        self._create_enrollments(
            downloadable['key'],
            generating['key'],
            unknown['key']
        )

        self._create_certificates(downloadable['key'], mode=CourseMode.VERIFIED)
        self._create_certificates(generating['key'], status='generating', mode=CourseMode.HONOR)
        self._create_certificates(unknown['key'], status='unknown')

        meter = ProgramProgressMeter(self.site, self.user)
        self.assertCountEqual(
            meter.completed_course_runs,
            [
                {'course_run_id': str(downloadable['key']), 'type': CourseMode.VERIFIED},
                {'course_run_id': str(generating['key']), 'type': CourseMode.HONOR},
            ]
        )

    def test_program_completion_with_no_id_professional(self, mock_get_programs):
        """
        Verify that 'no-id-professional' certificates are treated as if they were
        'professional' certificates when determining program completion.
        """
        # Create serialized course runs like the ones we expect to receive from the discovery service's API.
        # These runs are of type 'professional' because there is no seat type for no-id-professional;
        # it uses professional as the seat type instead.
        course_runs = CourseRunFactory.create_batch(2, type=CourseMode.PROFESSIONAL)
        program = ProgramFactory(courses=[CourseFactory(course_runs=course_runs)])
        mock_get_programs.return_value = [program]

        # Verify that the test program is not complete.
        meter = ProgramProgressMeter(self.site, self.user)
        assert not list(meter.completed_programs_with_available_dates.keys())

        # Grant a 'no-id-professional' certificate for one of the course runs,
        # thereby completing the program.
        self._create_enrollments(course_runs[0]['key'])
        self._create_certificates(course_runs[0]['key'], mode=CourseMode.NO_ID_PROFESSIONAL_MODE)

        # Verify that the program is complete.
        meter = ProgramProgressMeter(self.site, self.user)
        assert list(meter.completed_programs_with_available_dates.keys()) == [program['uuid']]

    @mock.patch(UTILS_MODULE + '.ProgramProgressMeter.completed_course_runs', new_callable=mock.PropertyMock)
    def test_credit_course_counted_complete_for_verified(self, mock_completed_course_runs, mock_get_programs):
        """
        Verify that 'credit' course certificate type are treated as if they were
        "verified" when checking for course completion status.
        """
        course_run_key = generate_course_run_key()
        course = CourseFactory(course_runs=[
            CourseRunFactory(key=course_run_key, type='credit'),
        ])
        program = ProgramFactory(courses=[course])
        mock_get_programs.return_value = [program]
        self._create_enrollments(course_run_key)
        meter = ProgramProgressMeter(self.site, self.user)
        mock_completed_course_runs.return_value = [{'course_run_id': course_run_key, 'type': CourseMode.VERIFIED}]
        assert meter._is_course_complete(course) is True  # lint-amnesty, pylint: disable=protected-access

    def test_detail_url_for_mobile_only(self, mock_get_programs):
        """
        Verify that correct program detail url is returned for mobile.
        """
        course_run_key = generate_course_run_key()
        data = [
            ProgramFactory(
                courses=[
                    CourseFactory(course_runs=[
                        CourseRunFactory(key=course_run_key),
                    ]),
                ]
            ),
            ProgramFactory(),
        ]
        mock_get_programs.return_value = data

        self._create_enrollments(course_run_key)
        meter = ProgramProgressMeter(self.site, self.user, mobile_only=True)

        program_data = meter.engaged_programs[0]
        detail_fragment_url = reverse('program_details_fragment_view', kwargs={'program_uuid': program_data['uuid']})
        path_id = detail_fragment_url.replace('/dashboard/', '')
        expected_url = f'edxapp://enrolled_program_info?path_id={path_id}'

        assert program_data['detail_url'] == expected_url


def _create_course(self, course_price, course_run_count=1, make_entitlement=False):
    """
    Creates the course in mongo and update it with the instructor data.
    Also creates catalog course with respect to course run.

    Returns:
        Catalog course dict.
    """
    course_runs = []
    for x in range(course_run_count):
        course = ModuleStoreCourseFactory.create(run='Run_' + str(x))
        course.start = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=1)
        course.end = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=1)
        course.instructor_info = self.instructors
        course = self.update_course(course, self.user.id)

        run = CourseRunFactory(key=str(course.id), seats=[SeatFactory(price=course_price)])
        course_runs.append(run)
    entitlements = [EntitlementFactory()] if make_entitlement else []

    return CourseFactory(course_runs=course_runs, entitlements=entitlements)


@ddt.ddt
@override_settings(ECOMMERCE_PUBLIC_URL_ROOT=ECOMMERCE_URL_ROOT)
@skip_unless_lms
class TestProgramDataExtender(ModuleStoreTestCase):
    """Tests of the program data extender utility class."""
    maxDiff = None
    sku = 'abc123'
    checkout_path = '/basket/add/'
    instructors = {
        'instructors': [
            {
                'name': 'test-instructor1',
                'organization': 'TextX',
            },
            {
                'name': 'test-instructor2',
                'organization': 'TextX',
            }
        ]
    }

    def setUp(self):
        super().setUp()

        self.course = ModuleStoreCourseFactory()
        self.course.start = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=1)
        self.course.end = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=1)
        self.course = self.update_course(self.course, self.user.id)

        self.course_run = CourseRunFactory(key=str(self.course.id))
        self.catalog_course = CourseFactory(course_runs=[self.course_run])
        self.program = ProgramFactory(courses=[self.catalog_course])
        self.course_price = 100

    def _assert_supplemented(self, actual, **kwargs):
        """DRY helper used to verify that program data is extended correctly."""
        program = deepcopy(self.program)
        course_run = deepcopy(self.course_run)
        course = deepcopy(self.catalog_course)

        course_run.update(
            dict(
                {
                    'certificate_url': None,
                    'course_url': reverse('course_root', args=[self.course.id]),
                    'enrollment_open_date': strftime_localized(DEFAULT_ENROLLMENT_START_DATE, 'SHORT_DATE'),
                    'is_course_ended': self.course.has_ended(),
                    'is_enrolled': False,
                    'is_enrollment_open': True,
                    'upgrade_url': None,
                    'advertised_start': None,
                },
                **kwargs
            )
        )

        course['course_runs'] = [course_run]
        program['courses'] = [course]

        assert actual == program

    @ddt.data(-1, 0, 1)
    def test_is_enrollment_open(self, days_offset):
        """
        Verify that changes to the course run end date do not affect our
        assessment of the course run being open for enrollment.
        """
        self.course.end = datetime.datetime.now(ZoneInfo("UTC")) + datetime.timedelta(days=days_offset)
        self.course = self.update_course(self.course, self.user.id)

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(data)

    @ddt.data(
        (True, MODES.audit, True),
    )
    @ddt.unpack
    @mock.patch(UTILS_MODULE + '.CourseMode.mode_for_course')
    def test_student_enrollment_status(self, is_enrolled, enrolled_mode, is_upgrade_required, mock_get_mode):
        """Verify that program data is supplemented with the student's enrollment status."""
        expected_upgrade_url = '{root}/{path}/?sku={sku}'.format(
            root=ECOMMERCE_URL_ROOT,
            path=self.checkout_path.strip('/'),
            sku=self.sku,
        )

        update_commerce_config(enabled=True, checkout_page=self.checkout_path)

        mock_mode = mock.Mock()
        mock_mode.sku = self.sku
        mock_get_mode.return_value = mock_mode

        if is_enrolled:
            CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=enrolled_mode)

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(
            data,
            is_enrolled=is_enrolled,
            upgrade_url=expected_upgrade_url if is_upgrade_required else None
        )

    @ddt.data(MODES.audit, MODES.verified)
    def test_inactive_enrollment_no_upgrade(self, enrolled_mode):
        """
        Verify that a student with an inactive enrollment isn't encouraged to upgrade.
        """
        update_commerce_config(enabled=True, checkout_page=self.checkout_path)

        CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            mode=enrolled_mode,
            is_active=False,
        )

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(data)

    @mock.patch(UTILS_MODULE + '.CourseMode.mode_for_course')
    def test_ecommerce_disabled(self, mock_get_mode):
        """
        Verify that the utility can operate when the ecommerce service is disabled.
        """
        update_commerce_config(enabled=False, checkout_page=self.checkout_path)

        mock_mode = mock.Mock()
        mock_mode.sku = self.sku
        mock_get_mode.return_value = mock_mode

        CourseEnrollmentFactory(user=self.user, course_id=self.course.id, mode=MODES.audit)

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(data, is_enrolled=True, upgrade_url=None)

    @ddt.data(
        (1, 1, False),
        (1, -1, True),
    )
    @ddt.unpack
    def test_course_run_enrollment_status(self, start_offset, end_offset, is_enrollment_open):
        """
        Verify that course run enrollment status is reflected correctly.
        """
        self.course.enrollment_start = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=start_offset)
        self.course.enrollment_end = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=end_offset)

        self.course = self.update_course(self.course, self.user.id)

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(
            data,
            is_enrollment_open=is_enrollment_open,
            enrollment_open_date=strftime_localized(self.course.enrollment_start, 'SHORT_DATE'),
        )

    def test_no_enrollment_start_date(self):
        """
        Verify that a closed course run with no explicit enrollment start date
        doesn't cause an error. Regression test for ECOM-4973.
        """
        self.course.enrollment_end = datetime.datetime.now(ZoneInfo("UTC")) - datetime.timedelta(days=1)
        self.course = self.update_course(self.course, self.user.id)

        data = ProgramDataExtender(self.program, self.user).extend()

        self._assert_supplemented(
            data,
            is_enrollment_open=False,
        )

    @ddt.data(True, False)
    @mock.patch(UTILS_MODULE + '.certificate_api.certificate_downloadable_status')
    @mock.patch('lms.djangoapps.certificates.utils.has_html_certificates_enabled')
    def test_certificate_url_retrieval(self, is_uuid_available, mock_html_certs_enabled, mock_get_cert_data):
        """
        Verify that the student's run mode certificate is included,
        when available.
        """
        test_uuid = uuid.uuid4().hex
        mock_get_cert_data.return_value = {'uuid': test_uuid} if is_uuid_available else {}
        mock_html_certs_enabled.return_value = True

        data = ProgramDataExtender(self.program, self.user).extend()

        expected_url = reverse(
            'certificates:render_cert_by_uuid',
            kwargs={'certificate_uuid': test_uuid}
        ) if is_uuid_available else None

        self._assert_supplemented(data, certificate_url=expected_url)

    @ddt.data(True, False)
    def test_may_certify_attached(self, may_certify):
        """
        Verify that the `may_certify` is included during data extension.
        """
        self.course.certificates_show_before_end = may_certify
        self.course = self.update_course(self.course, self.user.id)

        data = ProgramDataExtender(self.program, self.user).extend()

        assert may_certify == data['courses'][0]['course_runs'][0]['may_certify']

        self._assert_supplemented(data)

    def test_learner_eligibility_for_one_click_purchase(self):
        """
        Learner should be eligible for one click purchase if:
            - program is eligible for one click purchase
            - There are courses remaining that have not been purchased and enrolled in.
        """
        data = ProgramDataExtender(self.program, self.user).extend()
        assert not data['is_learner_eligible_for_one_click_purchase']

        courses = [_create_course(self, self.course_price)]

        program = ProgramFactory(
            courses=courses,
            is_program_eligible_for_one_click_purchase=False
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert not data['is_learner_eligible_for_one_click_purchase']

        course1 = _create_course(self, self.course_price)
        course2 = _create_course(self, self.course_price)
        CourseEnrollmentFactory(user=self.user, course_id=course1['course_runs'][0]['key'], mode=CourseMode.VERIFIED)
        CourseEnrollmentFactory(user=self.user, course_id=course2['course_runs'][0]['key'], mode=CourseMode.AUDIT)
        program2 = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program2, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']

    def test_learner_eligibility_for_one_click_purchase_with_unpublished(self):
        """
        Learner should be eligible for one click purchase if:
            - program is eligible for one click purchase
            - There are courses remaining that have not been purchased and enrolled in.
        """
        course1 = _create_course(self, self.course_price, course_run_count=2)
        course2 = _create_course(self, self.course_price)
        CourseEnrollmentFactory(user=self.user, course_id=course1['course_runs'][0]['key'], mode=CourseMode.VERIFIED)
        course1['course_runs'][0]['status'] = 'unpublished'
        program2 = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program2, self.user).extend()
        assert len(data['skus']) == 1
        assert data['is_learner_eligible_for_one_click_purchase']

    def test_learner_eligibility_for_one_click_purchase_professional_no_id(self):
        """
        Learner should not be eligible for one click purchase if:
            - There are no courses remaining that have not been purchased and enrolled in.
        This test is primarily for the case of no-id-professional enrollment modes
        """
        course1 = _create_course(self, self.course_price)
        CourseEnrollmentFactory(
            user=self.user, course_id=course1['course_runs'][0]['key'], mode=CourseMode.NO_ID_PROFESSIONAL_MODE
        )
        program2 = ProgramFactory(
            courses=[course1],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.PROFESSIONAL]
        )
        data = ProgramDataExtender(program2, self.user).extend()
        assert not data['is_learner_eligible_for_one_click_purchase']

    def test_multiple_published_course_runs(self):
        """
        Learner should not be eligible for one click purchase if:
            - program has a course with more than one published course run
        """
        course_run_1 = CourseRunFactory(
            key=str(ModuleStoreCourseFactory().id),  # lint-amnesty, pylint: disable=no-member
            status='published'
        )
        course_run_2 = CourseRunFactory(
            key=str(ModuleStoreCourseFactory().id),  # lint-amnesty, pylint: disable=no-member
            status='published'
        )
        course = CourseFactory(course_runs=[course_run_1, course_run_2], entitlements=[])
        program = ProgramFactory(
            courses=[
                CourseFactory(course_runs=[
                    CourseRunFactory(
                        key=str(ModuleStoreCourseFactory().id),  # lint-amnesty, pylint: disable=no-member
                        status='published'
                    )
                ]),
                course,
                CourseFactory(course_runs=[
                    CourseRunFactory(
                        key=str(ModuleStoreCourseFactory().id),  # lint-amnesty, pylint: disable=no-member
                        status='published'
                    )
                ])
            ],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED]
        )
        data = ProgramDataExtender(program, self.user).extend()

        assert not data['is_learner_eligible_for_one_click_purchase']

        course_run_2['status'] = 'unpublished'
        data = ProgramDataExtender(program, self.user).extend()

        assert data['is_learner_eligible_for_one_click_purchase']

    def test_learner_eligibility_for_one_click_purchase_entitlement_products(self):
        """
        Learner should be eligible for one click purchase if:
            - program is eligible for one click purchase
            - There are remaining unpurchased courses with entitlement products
        """
        course1 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        course2 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        expected_skus = {course1['entitlements'][0]['sku'], course2['entitlements'][0]['sku']}
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']
        assert set(data['skus']) == expected_skus

    def test_learner_eligibility_for_one_click_purchase_ineligible_program(self):
        """
        Learner should not be eligible for one click purchase if the program is not eligible for one click purchase
        """
        course1 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        course2 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=False,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert not data['is_learner_eligible_for_one_click_purchase']
        assert data['skus'] == []

    def test_learner_eligibility_for_one_click_purchase_user_entitlements(self):
        """
        Learner should be eligible for one click purchase if they hold an entitlement in one or more courses
        in the program and there are remaining unpurchased courses in the program with entitlement products.
        """
        course1 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        course2 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        CourseEntitlementFactory(user=self.user, course_uuid=course1['uuid'], mode=CourseMode.VERIFIED)
        expected_skus = {course2['entitlements'][0]['sku']}
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']
        assert set(data['skus']) == expected_skus

    def test_all_courses_owned(self):
        """
        Learner should not be eligible for one click purchase if they hold entitlements in all courses in the program.
        """
        course1 = _create_course(self, self.course_price, make_entitlement=True)
        course2 = _create_course(self, self.course_price)
        CourseEntitlementFactory(user=self.user, course_uuid=course1['uuid'], mode=CourseMode.VERIFIED)
        CourseEntitlementFactory(user=self.user, course_uuid=course2['uuid'], mode=CourseMode.VERIFIED)
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()

        assert not data['is_learner_eligible_for_one_click_purchase']
        assert data['skus'] == []

    def test_old_course_runs(self):
        """
        Test that old course runs may exist for a program which do not exist in LMS.
        In that case, continue without the course run.
        """
        course_run = CourseRunFactory.create()
        course = CourseFactory.create(course_runs=[course_run])
        program_data = ProgramFactory(courses=[course])

        with LogCapture(LOGGER_NAME) as logger:
            ProgramDataExtender(program_data, self.user).extend()
            logger.check(
                (LOGGER_NAME,
                 'WARNING', 'Failed to get course overview for course run key: {}'.format(course_run.get('key')))
            )

    def test_entitlement_product_wrong_mode(self):
        """
        Learner should not be eligible for one click purchase if the only entitlement product
        for a course in the program is not in an applicable mode, and that course has multiple course runs.
        """
        course1 = _create_course(self, self.course_price)
        course2 = _create_course(self, self.course_price, course_run_count=2)
        course2['entitlements'].append(EntitlementFactory(mode=CourseMode.PROFESSIONAL))
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert not data['is_learner_eligible_for_one_click_purchase']
        assert data['skus'] == []

    def test_second_entitlement_product_wrong_mode(self):
        """
        Learner should be eligible for one click purchase if a course has multiple entitlement products
        and at least one of them is in an applicable mode, even if one is not in an applicable mode.
        """
        course1 = _create_course(self, self.course_price)
        course2 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        # The above statement makes a verified entitlement for the course, which is an applicable seat type
        # and the statement below makes a professional entitlement for the same course, which is not applicable
        course2['entitlements'].append(EntitlementFactory(mode=CourseMode.PROFESSIONAL))
        expected_skus = {course1['course_runs'][0]['seats'][0]['sku'], course2['entitlements'][0]['sku']}
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']
        assert set(data['skus']) == expected_skus

    def test_entitlement_product_and_user_enrollment(self):
        """
        Learner should be eligible for one click purchase if they hold an enrollment
        but not an entitlement in a course for which there exists an entitlement product.
        """
        course1 = _create_course(self, self.course_price, make_entitlement=True)
        course2 = _create_course(self, self.course_price)
        expected_skus = {course2['course_runs'][0]['seats'][0]['sku']}
        CourseEnrollmentFactory(user=self.user, course_id=course1['course_runs'][0]['key'], mode=CourseMode.VERIFIED)
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']
        assert set(data['skus']) == expected_skus

    def test_user_enrollment_with_other_course_entitlement_product(self):
        """
        Learner should be eligible for one click purchase if they hold an enrollment in one course of the program
        and there is an entitlement product for another course in the program.
        """
        course1 = _create_course(self, self.course_price, course_run_count=2)
        course2 = _create_course(self, self.course_price, course_run_count=2, make_entitlement=True)
        CourseEnrollmentFactory(user=self.user, course_id=course1['course_runs'][0]['key'], mode=CourseMode.VERIFIED)
        expected_skus = {course2['entitlements'][0]['sku']}
        program = ProgramFactory(
            courses=[course1, course2],
            is_program_eligible_for_one_click_purchase=True,
            applicable_seat_types=[CourseMode.VERIFIED, CourseMode.PROFESSIONAL],
        )
        data = ProgramDataExtender(program, self.user).extend()
        assert data['is_learner_eligible_for_one_click_purchase']
        assert set(data['skus']) == expected_skus

    def test_course_url_with_mobile_only(self):
        """
        Verify that correct course url is returned for mobile.
        """
        data = ProgramDataExtender(self.program, self.user, mobile_only=True).extend()
        expected_course_url = f'edxapp://enrolled_course_info?course_id={self.course.id}'
        self._assert_supplemented(data, course_url=expected_course_url)


@skip_unless_lms
@mock.patch(UTILS_MODULE + '.get_credentials')
class TestGetCertificates(TestCase):
    """
    Tests of the function used to get certificates associated with a program.
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.program = ProgramFactory()
        self.course_certificate_url = 'fake-course-certificate-url'
        self.program_certificate_url = 'http://fake-credentials.edx.org/credentials/fake-uuid/'

        for course in self.program['courses']:
            # Give all course runs a certificate URL, but only expect one to come
            # back. This verifies the break in the function under test that ensures
            # only one certificate per course comes back.
            for index, course_run in enumerate(course['course_runs']):  # lint-amnesty, pylint: disable=unused-variable
                course_run['certificate_url'] = self.course_certificate_url
                course_run['may_certify'] = True

    def _first_course_runs(self):
        for course in self.program['courses']:
            for index, course_run in enumerate(course['course_runs']):
                if index == 0:
                    yield course_run

    def test_get_certificates(self, mock_get_credentials):
        """
        Verify course and program certificates are found when present. Only one
        course run certificate should be returned for each course when the user
        has earned certificates in multiple runs of the same course.
        """
        expected = [
            {
                'type': 'course',
                'title': course_run['title'],
                'url': course_run['certificate_url'],
            } for course_run in self._first_course_runs()
        ]

        expected.append({
            'type': 'program',
            'title': self.program['title'],
            'url': get_logged_in_program_certificate_url(self.program_certificate_url),
        })

        mock_get_credentials.return_value = [{
            'certificate_url': self.program_certificate_url
        }]

        certificates = get_certificates(self.user, self.program)
        assert certificates == expected

    def test_course_run_certificates_missing(self, mock_get_credentials):
        """
        Verify program certificates are not included when the learner has not earned all course certificates.
        """
        # make the first course have no certification, the second have no url...
        for course_index, course in enumerate(self.program['courses']):
            for index, course_run in enumerate(course['course_runs']):  # lint-amnesty, pylint: disable=unused-variable
                if course_index == 0:
                    course_run['may_certify'] = False
                elif course_index == 1:
                    course_run['certificate_url'] = False

        # ...but the third course should still be included
        expected = [{
            'type': 'course',
            'title': self.program['courses'][2]['course_runs'][0]['title'],
            'url': self.program['courses'][2]['course_runs'][0]['certificate_url'],
        }]

        mock_get_credentials.return_value = [{'certificate_url': self.program_certificate_url}]

        certificates = get_certificates(self.user, self.program)
        assert mock_get_credentials.called
        assert certificates == expected

    def test_program_certificate_missing(self, mock_get_credentials):
        """
        Verify that the function can handle a missing program certificate.
        """
        expected = [
            {
                'type': 'course',
                'title': course_run['title'],
                'url': course_run['certificate_url'],
            } for course_run in self._first_course_runs()
        ]

        mock_get_credentials.return_value = []

        certificates = get_certificates(self.user, self.program)
        assert certificates == expected

    def test_get_program_certificate_url(self, mock_get_credentials):  # pylint: disable=unused-argument
        """
        Verify that function returns correct url with login prepended
        """
        expected = 'http://fake-credentials.edx.org/login/?next=/credentials/fake-uuid/'
        actual = get_logged_in_program_certificate_url(self.program_certificate_url)
        assert expected == actual


@ddt.ddt
@override_settings(ECOMMERCE_PUBLIC_URL_ROOT=ECOMMERCE_URL_ROOT)
@skip_unless_lms
class TestProgramMarketingDataExtender(ModuleStoreTestCase):
    """Tests of the program data extender utility class."""
    ECOMMERCE_CALCULATE_DISCOUNT_ENDPOINT = f'{ECOMMERCE_URL_ROOT}/api/v2/baskets/calculate/'
    instructors = {
        'instructors': [
            {
                'name': 'test-instructor1',
                'organization': 'TextX',
            },
            {
                'name': 'test-instructor2',
                'organization': 'TextX',
            }
        ]
    }

    def setUp(self):
        super().setUp()

        # Ensure the E-Commerce service user exists
        UserFactory(username=settings.ECOMMERCE_SERVICE_WORKER_USERNAME, is_staff=True)

        self.course_price = 100
        self.number_of_courses = 2
        self.program = ProgramFactory(
            courses=[_create_course(self, self.course_price) for __ in range(self.number_of_courses)],
            applicable_seat_types=[CourseMode.VERIFIED]
        )

    def _prepare_program_for_discounted_price_calculation_endpoint(self):
        """
        Program's applicable seat types should match some or all seat types of the seats that are a part of the program.
        Otherwise, ecommerce API endpoint for calculating the discounted price won't be called.

        Returns:
            seat: seat for which the discount is applicable
        """
        self.ecommerce_service = EcommerceService()  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        seat = self.program['courses'][0]['course_runs'][0]['seats'][0]
        self.program['applicable_seat_types'] = [seat['type']]
        return seat

    def _update_discount_data(self, mock_discount_data):
        """
        Helper method that updates mocked discount data with
            - a flag indicating whether the program price is discounted
            - the amount of the discount (0 in case there's no discount)
        """
        program_discounted_price = mock_discount_data['total_incl_tax']
        program_full_price = mock_discount_data['total_incl_tax_excl_discounts']
        mock_discount_data.update({
            'is_discounted': program_discounted_price < program_full_price,
            'discount_value': program_full_price - program_discounted_price
        })

    def test_instructors(self):
        data = ProgramMarketingDataExtender(self.program, self.user).extend()

        self.program.update(self.instructors['instructors'])  # lint-amnesty, pylint: disable=no-member
        assert data == self.program

    def test_course_pricing(self):
        data = ProgramMarketingDataExtender(self.program, self.user).extend()

        program_full_price = self.course_price * self.number_of_courses
        assert data['number_of_courses'] == self.number_of_courses
        assert data['full_program_price'] == program_full_price
        assert data['avg_price_per_course'] == (program_full_price / self.number_of_courses)

    def test_course_pricing_when_all_course_runs_have_no_seats(self):
        # Create three seatless course runs and add them to the program
        course_runs = []
        for __ in range(3):
            course = ModuleStoreCourseFactory()
            course = self.update_course(course, self.user.id)
            course_runs.append(CourseRunFactory(key=str(course.id), seats=[]))
        program = ProgramFactory(courses=[CourseFactory(course_runs=course_runs)])

        data = ProgramMarketingDataExtender(program, self.user).extend()

        assert data['number_of_courses'] == len(program['courses'])
        assert data['full_program_price'] == 0.0
        assert data['avg_price_per_course'] == 0.0

    @ddt.data(True, False)
    @mock.patch('django.contrib.auth.models.PermissionsMixin.has_perm')
    def test_can_enroll(self, can_enroll, mock_has_perm):
        """
        Verify that the student's can_enroll status is included.
        """
        mock_has_perm.return_value = can_enroll

        data = ProgramMarketingDataExtender(self.program, self.user).extend()

        assert data['courses'][0]['course_runs'][0]['can_enroll'] == can_enroll

    @httpretty.activate
    def test_fetching_program_discounted_price(self):
        """
        Authenticated users eligible for one click purchase should see the purchase button
            - displaying program's discounted price if it exists.
            - leading to ecommerce basket page
        """
        self._prepare_program_for_discounted_price_calculation_endpoint()
        mock_discount_data = {
            'total_incl_tax_excl_discounts': 200.0,
            'currency': 'USD',
            'total_incl_tax': 50.0
        }
        httpretty.register_uri(
            httpretty.GET,
            self.ECOMMERCE_CALCULATE_DISCOUNT_ENDPOINT,
            body=json.dumps(mock_discount_data),
            content_type='application/json'
        )

        data = ProgramMarketingDataExtender(self.program, self.user).extend()
        self._update_discount_data(mock_discount_data)

        assert httpretty.last_request().querystring.get('username')[0] == self.user.username  # lint-amnesty, pylint: disable=no-member, line-too-long
        assert data['skus'] == [course['course_runs'][0]['seats'][0]['sku'] for course in self.program['courses']]
        assert data['discount_data'] == mock_discount_data

    @httpretty.activate
    @override_waffle_switch(ALWAYS_CALCULATE_PROGRAM_PRICE_AS_ANONYMOUS_USER, True)
    def test_fetching_program_price_when_forced_as_anonymous_user(self):
        """
        When all users are forced as anonymous, all requests to calculate the program
        price should have the query parameter is_anonymous=True
        """
        self._prepare_program_for_discounted_price_calculation_endpoint()
        mock_discount_data = {
            'total_incl_tax_excl_discounts': 200.0,
            'currency': 'USD',
            'total_incl_tax': 50.0
        }
        httpretty.register_uri(
            httpretty.GET,
            self.ECOMMERCE_CALCULATE_DISCOUNT_ENDPOINT,
            body=json.dumps(mock_discount_data),
            content_type='application/json'
        )
        ProgramMarketingDataExtender(self.program, self.user).extend()
        assert httpretty.last_request().querystring.get('is_anonymous')[0] == 'True'  # lint-amnesty, pylint: disable=no-member, line-too-long

    @httpretty.activate
    def test_fetching_program_discounted_price_as_anonymous_user(self):
        """
        Anonymous users should see the purchase button same way the authenticated users do
        when the program is eligible for one click purchase.
        """
        self._prepare_program_for_discounted_price_calculation_endpoint()
        mock_discount_data = {
            'total_incl_tax_excl_discounts': 200.0,
            'currency': 'USD',
            'total_incl_tax': 50.0
        }
        httpretty.register_uri(
            httpretty.GET,
            self.ECOMMERCE_CALCULATE_DISCOUNT_ENDPOINT,
            body=json.dumps(mock_discount_data),
            content_type='application/json'
        )
        user = AnonymousUserFactory()

        data = ProgramMarketingDataExtender(self.program, user).extend()
        self._update_discount_data(mock_discount_data)

        assert httpretty.last_request().querystring.get('is_anonymous', None) is not None  # lint-amnesty, pylint: disable=no-member, line-too-long
        assert data['skus'] == [course['course_runs'][0]['seats'][0]['sku'] for course in self.program['courses']]
        assert data['discount_data'] == mock_discount_data

    def test_fetching_program_discounted_price_no_applicable_seats(self):
        """
        User shouldn't be able to do a one click purchase of a program if a program has no applicable seat types.
        """
        self.program['applicable_seat_types'] = []
        data = ProgramMarketingDataExtender(self.program, self.user).extend()

        assert len(data['skus']) == 0

    @httpretty.activate
    def test_fetching_program_discounted_price_api_exception_caught(self):
        """
        User should be able to do a one click purchase of a program even if the ecommerce API throws an exception
        during the calculation of program discounted price.
        """
        self._prepare_program_for_discounted_price_calculation_endpoint()
        httpretty.register_uri(
            httpretty.GET,
            self.ECOMMERCE_CALCULATE_DISCOUNT_ENDPOINT,
            status=400,
            content_type='application/json'
        )

        data = ProgramMarketingDataExtender(self.program, self.user).extend()

        assert data['skus'] == [course['course_runs'][0]['seats'][0]['sku'] for course in self.program['courses']]


@skip_unless_lms
@mock.patch('openedx.core.djangoapps.programs.utils.get_programs_by_type')
class TestProgramEnrollment(SharedModuleStoreTestCase):
    """
    Tests to test program enrollment utility methods for program data from the program cache.

    Requests to the data in the Program cache are mocked out.
    """
    MICROBACHELORS = 'microbachelors'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = modulestore()
        cls.user = UserFactory()
        cls.program = ProgramFactory(type=cls.MICROBACHELORS)
        cls.catalog_course_run = cls.program['courses'][0]['course_runs'][0]
        cls.course_key = CourseKey.from_string(cls.catalog_course_run['key'])
        cls.course_run = ModuleStoreCourseFactory.create(
            org=cls.course_key.org,
            number=cls.course_key.course,
            run=cls.course_key.run,
            modulestore=cls.store,
        )
        CourseModeFactory.create(course_id=cls.course_run.id, mode_slug=CourseMode.VERIFIED)

    def test_user_not_in_program(self, mock_get_programs_by_type):
        mock_get_programs_by_type.return_value = [self.program]
        assert not is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS)

    def test_user_enrolled_in_mb_program(self, mock_get_programs_by_type):
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_run.id, mode=CourseMode.VERIFIED)
        mock_get_programs_by_type.return_value = [self.program]
        assert is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS)

    def test_user_enrolled_unpaid_in_program(self, mock_get_programs_by_type):
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_run.id, mode=CourseMode.AUDIT)
        mock_get_programs_by_type.return_value = [self.program]
        assert is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS)

    def test_user_enrolled_unpaid_in_program_paid_only_request(self, mock_get_programs_by_type):
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_run.id, mode=CourseMode.AUDIT)
        mock_get_programs_by_type.return_value = [self.program]
        assert not is_user_enrolled_in_program_type(user=self.user,
                                                    program_type_slug=self.MICROBACHELORS,
                                                    paid_modes_only=True)

    # NEW CODE HERE
    @mock.patch('openedx.core.djangoapps.programs.utils.get_paid_modes_for_course')
    def test_user_enrolled_in_paid_only_with_no_matching_paid_course_modes(self, mock_get_paid_modes_for_course,
                                                                           mock_get_programs_by_type):
        second_program = ProgramFactory(type=self.MICROBACHELORS)
        second_catalog_course_run = second_program['courses'][0]['course_runs'][0]
        second_course_key = CourseKey.from_string(second_catalog_course_run['key'])
        second_course_run = ModuleStoreCourseFactory.create(
            org=second_course_key.org,
            number=second_course_key.course,
            run=second_course_key.run,
            modulestore=self.store,
        )
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course_run.id, mode=CourseMode.AUDIT)
        CourseEnrollmentFactory.create(user=self.user, course_id=second_course_run.id, mode=CourseMode.AUDIT)

        mock_get_programs_by_type.return_value = [self.program, second_program]

        # While most of a programs courses would likely come with a paid mode, if the course in question is now expired,
        # then get_paid_modes_for_course would return an empty list. Even with no paid modes, if we request paid modes only  # lint-amnesty, pylint: disable=line-too-long
        # we should return False
        mock_get_paid_modes_for_course.return_value = []
        # raise Exception((mock_get_programs_by_type, mock_get_paid_modes_for_course))
        assert not is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS, paid_modes_only=True)

        # We should continue to return false even if they do contain paid modes
        Mode = namedtuple('Mode', ['slug'])  # lint-amnesty, pylint: disable=unused-variable
        # mock_get_paid_modes_for_course.return_value = [Mode(CourseMode.VERIFIED)]
        assert not is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS, paid_modes_only=True)

    def test_user_with_entitlement_no_enrollment(self, mock_get_programs_by_type):
        CourseEntitlementFactory.create(
            user=self.user,
            mode=CourseMode.VERIFIED,
            course_uuid=self.program['courses'][0]['uuid']
        )
        mock_get_programs_by_type.return_value = [self.program]
        assert is_user_enrolled_in_program_type(user=self.user, program_type_slug=self.MICROBACHELORS)
