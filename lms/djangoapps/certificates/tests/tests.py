"""
Tests for the certificates models.
"""


from datetime import datetime, timedelta
from unittest.mock import patch

from ddt import data, ddt, unpack
from django.conf import settings
from milestones.tests.utils import MilestonesTestCaseMixin
from pytz import UTC

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.milestones_helpers import milestones_achieved_by_user, set_prerequisite_courses
from lms.djangoapps.certificates.api import certificate_info_for_user, certificate_status_for_student
from lms.djangoapps.certificates.models import CertificateStatuses, GeneratedCertificate
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt
class CertificatesModelTest(ModuleStoreTestCase, MilestonesTestCaseMixin):
    """
    Tests for the GeneratedCertificate model
    """

    def setUp(self):
        super().setUp()

        today = datetime.now(UTC)
        self.instructor_paced_course = CourseFactory.create(
            org='edx', number='instructor', display_name='Instructor Paced Course',
            start=today - timedelta(days=30),
            end=today - timedelta(days=2),
            certificate_available_date=today - timedelta(days=1),
            self_paced=False
        )
        self.self_paced_course = CourseFactory.create(
            org='edx', number='self',
            display_name='Self Paced Course', self_paced=True
        )

    def test_certificate_status_for_student(self):
        student = UserFactory()
        course = CourseFactory.create(org='edx', number='verified', display_name='Verified Course')

        certificate_status = certificate_status_for_student(student, course.id)
        assert certificate_status['status'] == CertificateStatuses.unavailable
        assert certificate_status['mode'] == GeneratedCertificate.MODES.honor

    @unpack
    @data(
        {'allowlisted': False, 'grade': None, 'output': ['N', 'N', 'N/A']},
        {'allowlisted': True, 'grade': None, 'output': ['Y', 'N', 'N/A']},
        {'allowlisted': False, 'grade': 0.9, 'output': ['N', 'N', 'N/A']},
        {'allowlisted': True, 'grade': 0.8, 'output': ['Y', 'N', 'N/A']},
        {'allowlisted': None, 'grade': 0.8, 'output': ['N', 'N', 'N/A']}
    )
    def test_certificate_info_for_user(self, allowlisted, grade, output):
        """
        Verify that certificate_info_for_user works.
        """
        student = UserFactory()

        # for instructor paced course
        certificate_info = certificate_info_for_user(
            student, self.instructor_paced_course.id, grade,
            allowlisted, user_certificate=None
        )
        assert certificate_info == output

        # for self paced course
        certificate_info = certificate_info_for_user(
            student, self.self_paced_course.id, grade,
            allowlisted, user_certificate=None
        )
        assert certificate_info == output

    @unpack
    @data(
        {'allowlisted': False, 'grade': None, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': True, 'grade': None, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': False, 'grade': 0.9, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': True, 'grade': 0.8, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': None, 'grade': 0.8, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': None, 'grade': None, 'output': ['Y', 'Y', 'honor']},
        {'allowlisted': True, 'grade': None, 'output': ['Y', 'Y', 'honor']}
    )
    def test_certificate_info_for_user_when_grade_changes(self, allowlisted, grade, output):
        """
        Verify that certificate_info_for_user works as expect in scenario when grading of problems
        changes after certificates already generated. In such scenario `Certificate delivered` should not depend
        on student's eligibility to get certificates since in above scenario eligibility can change over period
        of time.
        """
        student = UserFactory()

        certificate1 = GeneratedCertificateFactory.create(
            user=student,
            course_id=self.instructor_paced_course.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )

        certificate2 = GeneratedCertificateFactory.create(
            user=student,
            course_id=self.self_paced_course.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )

        # for instructor paced course
        certificate_info = certificate_info_for_user(
            student, self.instructor_paced_course.id, grade,
            allowlisted, certificate1
        )
        assert certificate_info == output

        # for self paced course
        certificate_info = certificate_info_for_user(
            student, self.self_paced_course.id, grade,
            allowlisted, certificate2
        )
        assert certificate_info == output

    @unpack
    @data(
        {'allowlisted': False, 'grade': 0.8, 'mode': 'audit', 'output': ['N', 'N', 'N/A']},
        {'allowlisted': True, 'grade': 0.8, 'mode': 'audit', 'output': ['Y', 'N', 'N/A']},
        {'allowlisted': False, 'grade': 0.8, 'mode': 'verified', 'output': ['Y', 'N', 'N/A']}
    )
    def test_certificate_info_for_user_with_course_modes(self, allowlisted, grade, mode, output):
        """
        Verify that certificate_info_for_user works with course modes.
        """
        user = UserFactory.create()

        _ = CourseEnrollment.enroll(user, self.instructor_paced_course.id, mode)
        certificate_info = certificate_info_for_user(
            user, self.instructor_paced_course.id, grade,
            allowlisted, user_certificate=None
        )
        assert certificate_info == output

    def test_course_ids_with_certs_for_user(self):
        # Create one user with certs and one without
        student_no_certs = UserFactory()
        student_with_certs = UserFactory()

        # Set up a couple of courses
        course_1 = CourseFactory.create()
        course_2 = CourseFactory.create()

        # Generate certificates
        GeneratedCertificateFactory.create(
            user=student_with_certs,
            course_id=course_1.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )
        GeneratedCertificateFactory.create(
            user=student_with_certs,
            course_id=course_2.id,
            status=CertificateStatuses.downloadable,
            mode='honor'
        )

        # User with no certs should return an empty set.
        self.assertSetEqual(
            GeneratedCertificate.course_ids_with_certs_for_user(student_no_certs),
            set()
        )
        # User with certs should return a set with the two course_ids
        self.assertSetEqual(
            GeneratedCertificate.course_ids_with_certs_for_user(student_with_certs),
            {course_1.id, course_2.id}
        )

    @patch.dict(settings.FEATURES, {'ENABLE_PREREQUISITE_COURSES': True})
    def test_course_milestone_collected(self):
        student = UserFactory()
        course = CourseFactory.create(org='edx', number='998', display_name='Test Course')
        pre_requisite_course = CourseFactory.create(org='edx', number='999', display_name='Pre requisite Course')
        # set pre-requisite course
        set_prerequisite_courses(course.id, [str(pre_requisite_course.id)])
        # get milestones collected by user before completing the pre-requisite course
        completed_milestones = milestones_achieved_by_user(student, str(pre_requisite_course.id))
        assert len(completed_milestones) == 0

        GeneratedCertificateFactory.create(
            user=student,
            course_id=pre_requisite_course.id,
            status=CertificateStatuses.generating,
            mode='verified'
        )
        # get milestones collected by user after user has completed the pre-requisite course
        completed_milestones = milestones_achieved_by_user(student, str(pre_requisite_course.id))
        assert len(completed_milestones) == 1
        assert completed_milestones[0]['namespace'] == str(pre_requisite_course.id)
