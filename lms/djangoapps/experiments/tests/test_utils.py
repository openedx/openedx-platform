"""
Tests of experiment functionality
"""

from datetime import timedelta
from decimal import Decimal
from unittest import TestCase

from django.utils.timezone import now
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.course_blocks.transformers.tests.helpers import ModuleStoreTestCase
from lms.djangoapps.experiments.utils import (
    get_course_entitlement_price_and_sku,
    get_experiment_user_metadata_context,
    get_program_price_and_skus,
    get_unenrolled_courses,
    is_enrolled_in_course_run,
)
from xmodule.modulestore.tests.factories import CourseFactory
from xmodule.partitions.partitions import Group


class ExperimentUtilsTests(ModuleStoreTestCase, TestCase):
    """
    Tests of experiment functionality
    """

    def setUp(self):
        super().setUp()

        # Create a course run
        self.run_a_price = '86.00'
        self.run_a_sku = 'B9B6D0B'
        seat_a = {'type': 'verified', 'price': self.run_a_price, 'sku': self.run_a_sku}
        seats = [seat_a]
        self.course_run_a = {'status': 'published', 'seats': seats}

        # Create an entitlement
        self.entitlement_a_price = '199.23'
        self.entitlement_a_sku = 'B37EBA0'
        self.entitlement_a = {'mode': 'verified', 'price': self.entitlement_a_price, 'sku': self.entitlement_a_sku}

    def test_valid_course_run_key_enrollment(self):
        course_run = {
            'key': 'course-v1:DelftX+NGIx+RA0',
        }
        enrollment_ids = {CourseKey.from_string('course-v1:DelftX+NGIx+RA0')}
        assert is_enrolled_in_course_run(course_run, enrollment_ids)

    def test_invalid_course_run_key_enrollment(self):
        course_run = {
            'key': 'cr_key',
        }
        enrollment_ids = {CourseKey.from_string('course-v1:DelftX+NGIx+RA0')}
        assert not is_enrolled_in_course_run(course_run, enrollment_ids)

    def test_program_price_and_skus_for_empty_courses(self):
        price, skus = get_program_price_and_skus([])
        assert price is None
        assert skus is None

    def test_unenrolled_courses_for_empty_courses(self):
        unenrolled_courses = get_unenrolled_courses([], [])
        assert not unenrolled_courses

    def test_unenrolled_courses_for_single_course(self):
        course = {'key': 'UQx+ENGY1x'}
        courses_in_program = [course]
        user_enrollments = []

        unenrolled_courses = get_unenrolled_courses(courses_in_program, user_enrollments)
        expected_unenrolled_courses = [course]
        assert expected_unenrolled_courses == unenrolled_courses

    def test_price_and_sku_from_empty_course(self):
        course = {}

        price, sku = get_course_entitlement_price_and_sku(course)
        assert price is None
        assert sku is None

    def test_price_and_sku_from_entitlement(self):
        entitlements = [self.entitlement_a]
        course = {'key': 'UQx+ENGY1x', 'entitlements': entitlements}

        price, sku = get_course_entitlement_price_and_sku(course)
        assert self.entitlement_a_price == price
        assert self.entitlement_a_sku == sku

    def test_price_and_sku_from_course_run(self):
        course_runs = [self.course_run_a]
        course = {'key': 'UQx+ENGY1x', 'course_runs': course_runs}

        price, sku = get_course_entitlement_price_and_sku(course)
        expected_price = Decimal(self.run_a_price)
        assert expected_price == price
        assert self.run_a_sku == sku

    def test_price_and_sku_from_course(self):
        entitlements = [self.entitlement_a]
        course_a = {'key': 'UQx+ENGYCAPx', 'entitlements': entitlements}
        courses = [course_a]  # lint-amnesty, pylint: disable=redefined-outer-name

        price, skus = get_program_price_and_skus(courses)
        expected_price = '$199.23'
        assert expected_price == price
        assert 1 == len(skus)
        assert self.entitlement_a_sku in skus

    def test_price_and_sku_from_multiple_courses(self):
        entitlements = [self.entitlement_a]
        course_runs = [self.course_run_a]
        course_a = {'key': 'UQx+ENGY1x', 'course_runs': course_runs}
        course_b = {'key': 'UQx+ENGYCAPx', 'entitlements': entitlements}
        courses = [course_a, course_b]  # lint-amnesty, pylint: disable=redefined-outer-name

        price, skus = get_program_price_and_skus(courses)
        expected_price = '$285.23'
        assert expected_price == price
        assert 2 == len(skus)
        assert self.run_a_sku in skus
        assert self.entitlement_a_sku in skus

    def test_get_experiment_user_metadata_context(self):
        course = CourseFactory.create(start=now() - timedelta(days=30))
        user = UserFactory()
        enrollment = CourseEnrollmentFactory(course_id=course.id, user=user)
        context = get_experiment_user_metadata_context(course, user)

        user_metadata_expected_result = {'username': user.username,
                                         'user_id': user.id,
                                         'course_id': str(course.id),
                                         'course_display_name': course.display_name_with_default,
                                         'enrollment_mode': enrollment.mode,
                                         'upgrade_link': None,
                                         'upgrade_price': 'Free',
                                         'audit_access_deadline': None,
                                         'course_duration': None,
                                         'pacing_type': 'instructor_paced',
                                         'has_staff_access': False,
                                         'forum_roles': [('Student',)],
                                         'partition_groups': {'Enrollment Track Groups': Group(id=1, name='Audit')},
                                         'has_non_audit_enrollments': False,
                                         'program_key_fields': None,
                                         'email': user.email,
                                         'schedule_start': enrollment.schedule.start_date.isoformat(),
                                         'enrollment_time': enrollment.created.isoformat(),
                                         'course_start': course.start.isoformat(),
                                         'course_end': course.end,
                                         'dynamic_upgrade_deadline': None,
                                         'course_upgrade_deadline': None,
                                         'course_key_fields': {
                                             'org': course.id.org,
                                             'course': course.id.course,
                                             'run': course.id.run,
                                         }}

        user_metadata = context.get('user_metadata')

        assert user_metadata == user_metadata_expected_result
