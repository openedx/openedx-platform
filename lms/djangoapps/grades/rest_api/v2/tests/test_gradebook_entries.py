"""Tests for the gradebook endpoints of the Grades API v2."""

from datetime import datetime
from unittest.mock import patch

import ddt
import pytest
from ccx_keys.locator import CCXLocator
from django.http import Http404
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.locator import BlockUsageLocator
from pytz import UTC
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.grades.config.waffle import WRITABLE_GRADEBOOK
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.models import (
    BlockRecord,
    BlockRecordList,
    PersistentCourseGrade,
    PersistentSubsectionGrade,
)
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    PAGE_FIELDS,
    CohortedCourseMixin,
    ReadOnlyRequestMixin,
    capture_cohort_assignment,
    count_queries,
    gradebook_entry_detail_url,
    gradebook_entry_list_url,
    usernames_in,
    warm_cohort_settings,
    warm_read_caches,
    with_query,
)
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups.models import CourseCohortsSettings
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory

#: Every learner the shared fixture enrolls in the test course, in enrollment order.
FIXTURE_LEARNERS = ['student', 'other_student', 'program_student', 'program_masters_student']

#: Number of graded subsections in the shared test course.
GRADED_SUBSECTIONS = 26

MISSING_COURSE_KEY = 'course-v1:NoSuch+Course+Run'


class GradebookTestBase(GradeViewTestMixin, APITestCase):
    """Fixtures shared by the gradebook endpoint tests."""

    def setUp(self):
        super().setUp()
        CourseOverview.objects.filter(id=self.course_key).update(org=self.course_key.org)
        warm_read_caches(self.course_key)
        warm_cohort_settings(self.course_key)
        self.homework = self.store.get_course(self.course_key).get_children()[0].get_children()[0]

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)

    def user_with_role(self, role):
        """Return a new user holding ``role`` on the test course."""
        user = UserFactory(password=self.password)
        role(self.course_key).add_users(user)
        return user

    def store_subsection_grade(self, user, earned_graded, possible_graded):
        """Store an attempted grade on the first homework subsection for ``user``."""
        location = BlockUsageLocator(self.course_key, 'problem', 'problem_for_grade')
        PersistentSubsectionGrade.update_or_create_grade(
            user_id=user.id,
            usage_key=self.homework.location,
            course_version='',
            subtree_edited_timestamp=datetime(2020, 1, 1, tzinfo=UTC),
            earned_all=earned_graded,
            possible_all=possible_graded,
            earned_graded=earned_graded,
            possible_graded=possible_graded,
            visible_blocks=BlockRecordList(
                [BlockRecord(locator=location, weight=1, raw_possible=possible_graded, graded=True)],
                self.course_key,
            ),
            first_attempted=datetime(2020, 1, 2, tzinfo=UTC),
        )

    def store_course_grade(self, user, percent):
        """Store a course grade of ``percent`` for ``user``."""
        PersistentCourseGrade.objects.create(
            user_id=user.id, course_id=self.course_key, percent_grade=percent,
            letter_grade='Pass' if percent >= 0.5 else '', grading_policy_hash='policy-hash',
        )


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class GradebookAccessTest(GradebookTestBase):
    """Who may read a gradebook while its course has the writable gradebook switched on."""

    def test_anonymous_caller_is_refused(self):
        for url in (gradebook_entry_list_url(self.course_key), gradebook_entry_detail_url(self.course_key, 'student')):
            assert_error_envelope(
                self.client.get(url), expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug='authn',
            )

    @ddt.data('student', 'other_student')
    def test_learners_are_refused(self, username):
        self.login(getattr(self, username))
        for url in (gradebook_entry_list_url(self.course_key), gradebook_entry_detail_url(self.course_key, 'student')):
            assert_error_envelope(
                self.client.get(url), expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz',
            )

    @ddt.data(CourseStaffRole, CourseInstructorRole, CourseLimitedStaffRole)
    def test_course_team_is_admitted(self, role):
        self.login(self.user_with_role(role))
        response = self.client.get(gradebook_entry_list_url(self.course_key))
        assert response.status_code == status.HTTP_200_OK
        assert usernames_in(response) == FIXTURE_LEARNERS

    def test_global_staff_are_admitted(self):
        self.login(self.global_staff)
        response = self.client.get(gradebook_entry_detail_url(self.course_key, 'student'))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['username'] == 'student'

    def test_staff_of_another_course_are_refused(self):
        other_staff = UserFactory(password=self.password)
        CourseStaffRole(self.empty_course.id).add_users(other_staff)
        self.login(other_staff)
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(self.course_key)),
            expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz',
        )

    def test_caller_without_access_cannot_learn_whether_a_course_exists(self):
        self.login(self.student)
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(MISSING_COURSE_KEY)),
            expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz',
        )

    def test_missing_course_is_not_found_for_a_caller_with_access(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(MISSING_COURSE_KEY)),
            expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found',
        )

    def test_learner_not_enrolled_is_not_found(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.client.get(gradebook_entry_detail_url(self.course_key, self.global_staff.username)),
            expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found',
        )

    def test_deactivated_course_staff_session_is_refused(self):
        """
        A deactivated account's browser session does not authenticate. v1's
        gradebook authenticated it, then refused it with 403 because the course
        roles of a deactivated account do not count.
        """
        course_staff = self.user_with_role(CourseStaffRole)
        course_staff.is_active = False
        course_staff.save()
        self.login(course_staff)
        legacy = self.client.get(reverse('grades_api:v1:course_gradebook', kwargs={'course_id': str(self.course_key)}))
        assert legacy.status_code == status.HTTP_403_FORBIDDEN
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(self.course_key)),
            expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug='authn',
        )

    def test_active_course_staff_session_is_admitted(self):
        self.login(self.user_with_role(CourseStaffRole))
        assert usernames_in(self.client.get(gradebook_entry_list_url(self.course_key))) == FIXTURE_LEARNERS

    def test_inactive_enrollment_is_readable_but_not_listed(self):
        CourseEnrollment.objects.filter(user=self.student, course_id=self.course_key).update(is_active=False)
        self.login(self.global_staff)
        assert 'student' not in usernames_in(self.client.get(gradebook_entry_list_url(self.course_key)))
        response = self.client.get(gradebook_entry_detail_url(self.course_key, 'student'))
        assert response.status_code == status.HTTP_200_OK


@override_waffle_flag(WRITABLE_GRADEBOOK, active=False)
class GradebookDisabledTest(GradebookTestBase):
    """A course with the writable gradebook switched off."""

    def test_caller_with_access_is_refused_with_the_feature_type(self):
        self.login(self.global_staff)
        for url in (gradebook_entry_list_url(self.course_key), gradebook_entry_detail_url(self.course_key, 'student')):
            assert_error_envelope(
                self.client.get(url),
                expected_status=status.HTTP_403_FORBIDDEN,
                expected_type_slug='grades/writable-gradebook-disabled',
            )

    def test_caller_without_access_cannot_learn_the_setting(self):
        self.login(self.student)
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(self.course_key)),
            expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz',
        )


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class GradebookCcxAccessTest(CcxTestCase, APITestCase):
    """Who may read the gradebook of a CCX course."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.learner = UserFactory()
        CourseEnrollmentFactory(user=self.learner, course_id=self.ccx_key)

    def actor(self, kind):
        """Return a user holding the named kind of access to ``self.ccx_key``."""
        if kind == 'coach':
            return self.coach
        if kind == 'site_staff':
            return UserFactory(is_staff=True)
        user = UserFactory()
        if kind == 'ccx_staff':
            CourseStaffRole(self.ccx_key).add_users(user)
        elif kind == 'ccx_instructor':
            CourseInstructorRole(self.ccx_key).add_users(user)
        return user

    @ddt.data('coach', 'ccx_staff', 'ccx_instructor', 'site_staff')
    def test_ccx_team_is_admitted(self, kind):
        self.client.force_authenticate(self.actor(kind))
        response = self.client.get(gradebook_entry_list_url(self.ccx_key))
        assert response.status_code == status.HTTP_200_OK
        assert usernames_in(response) == [self.learner.username]

    def test_outsider_is_refused(self):
        self.client.force_authenticate(self.actor('outsider'))
        assert_error_envelope(
            self.client.get(gradebook_entry_list_url(self.ccx_key)),
            expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz',
        )

    def test_coach_is_refused_when_custom_courses_are_off(self):
        self.client.force_authenticate(self.coach)
        with override_settings(CUSTOM_COURSES_EDX=False):
            response = self.client.get(gradebook_entry_list_url(self.ccx_key))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz')

    def test_coach_is_refused_when_the_master_course_disables_ccx(self):
        master_course = self.store.get_course(self.course.id)
        master_course.enable_ccx = False
        self.client.force_authenticate(self.coach)
        with patch(
            'lms.djangoapps.grades.rest_api.v1.gradebook_views.get_course_by_id', return_value=master_course,
        ):
            response = self.client.get(gradebook_entry_list_url(self.ccx_key))
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz')

    def test_coach_reads_one_learners_row(self):
        self.client.force_authenticate(self.coach)
        response = self.client.get(gradebook_entry_detail_url(self.ccx_key, self.learner.username))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['username'] == self.learner.username


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookRowTest(GradebookTestBase):
    """The content of the rows."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_rows_carry_the_learner_and_every_graded_subsection(self):
        self.store_course_grade(self.student, 0.85)
        self.store_subsection_grade(self.student, 6.0, 8.0)
        rows = self.client.get(gradebook_entry_list_url(self.course_key)).json()['results']
        assert [row['username'] for row in rows] == FIXTURE_LEARNERS
        student = rows[0]
        assert set(student) == {'username', 'email', 'percent', 'section_breakdown'}
        assert (student['email'], student['percent']) == ('', 0.85)
        assert len(student['section_breakdown']) == GRADED_SUBSECTIONS
        assert student['section_breakdown'][0] == {
            'attempted': True,
            'category': 'Homework',
            'label': 'HW 01',
            'usage_key': str(self.homework.location),
            'percent': 0.75,
            'score_earned': 6.0,
            'score_possible': 8.0,
            'subsection_name': self.homework.display_name,
        }
        assert student['section_breakdown'][1]['attempted'] is False
        assert (student['section_breakdown'][1]['score_earned'], student['section_breakdown'][1]['label']) == (
            0, 'HW 02',
        )

    def test_every_row_numbers_its_subsections_from_one(self):
        rows = self.client.get(gradebook_entry_list_url(self.course_key)).json()['results']
        assert {row['section_breakdown'][0]['label'] for row in rows} == {'HW 01'}
        assert {row['section_breakdown'][-1]['label'] for row in rows} == {'Final 01'}

    def rows_by_username(self):
        """Return the rows of the first gradebook page, keyed by username."""
        results = self.client.get(gradebook_entry_list_url(self.course_key)).json()['results']
        return {row['username']: row for row in results}

    def test_program_learners_carry_their_program_key(self):
        rows = self.rows_by_username()
        assert rows['program_student']['external_user_key'] == 'program_user_key_0'
        assert 'external_user_key' not in rows['student']

    def test_masters_learners_carry_their_name_and_email(self):
        rows = self.rows_by_username()
        masters = rows['program_masters_student']
        assert (masters['email'], masters['full_name']) == (
            self.program_masters_student.email, self.program_masters_student.profile.name,
        )
        assert 'full_name' not in rows['program_student']

    def test_minimal_view(self):
        self.store_course_grade(self.student, 0.85)
        response = self.client.get(gradebook_entry_list_url(self.course_key, view='minimal'))
        assert response.json()['results'] == [
            {'username': 'student', 'percent': 0.85},
            {'username': 'other_student', 'percent': 0.0},
            {'username': 'program_student', 'percent': 0.0},
            {'username': 'program_masters_student', 'percent': 0.0},
        ]
        detail = self.client.get(gradebook_entry_detail_url(self.course_key, 'student', view='minimal'))
        assert detail.json() == {'username': 'student', 'percent': 0.85}

    def test_unknown_view_is_refused(self):
        for url in (
            gradebook_entry_list_url(self.course_key, view='full'),
            gradebook_entry_detail_url(self.course_key, 'student', view='full'),
        ):
            body = assert_error_envelope(
                self.client.get(url), expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
            )
            assert list(body['errors']) == ['view']

    def test_learner_whose_grade_cannot_be_computed_is_left_out_of_the_page(self):
        real_read = CourseGradeFactory.read

        def read(factory, user, *args, **kwargs):
            if user.username == 'other_student':
                raise RuntimeError('cannot grade')
            return real_read(factory, user, *args, **kwargs)

        with patch.object(CourseGradeFactory, 'read', autospec=True, side_effect=read):
            response = self.client.get(gradebook_entry_list_url(self.course_key))
        assert usernames_in(response) == ['student', 'program_student', 'program_masters_student']

    def test_row_that_cannot_be_computed_is_a_server_error(self):
        with patch.object(CourseGradeFactory, 'read', side_effect=RuntimeError('boom')):
            response = self.client.get(gradebook_entry_detail_url(self.course_key, 'student'))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR, expected_type_slug='internal',
        )
        assert 'boom' not in body['detail']

    def test_course_without_content_is_not_found(self):
        with patch('lms.djangoapps.grades.rest_api.v2.services.get_course_by_id', side_effect=Http404):
            response = self.client.get(gradebook_entry_list_url(self.course_key))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_page_caches_the_cohorts_of_its_learners(self):
        """
        Caching every learner's cohort up front keeps grading from assigning
        learners to cohorts one by one while the page is read.
        """
        with patch('lms.djangoapps.grades.rest_api.v2.services.cohorts.bulk_cache_cohorts') as bulk_cache:
            self.client.get(gradebook_entry_list_url(self.course_key))
        (course_key, users), _ = bulk_cache.call_args
        assert (course_key, [user.username for user in users]) == (self.course_key, FIXTURE_LEARNERS)

    def test_paging(self):
        for index in range(8):
            CourseEnrollmentFactory(user=UserFactory(username=f'extra_{index}'), course_id=self.course_key)
        body = self.client.get(gradebook_entry_list_url(self.course_key, page_size=5, page=3)).json()
        assert set(body) == PAGE_FIELDS
        assert (body['count'], body['num_pages'], body['current_page'], body['start']) == (12, 3, 3, 10)
        assert [row['username'] for row in body['results']] == ['extra_6', 'extra_7']


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class GradebookFilterTest(GradebookTestBase):
    """Each filter narrows the gradebook, and bad values are refused."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.store_course_grade(self.student, 0.85)
        self.store_course_grade(self.other_student, 0.45)
        self.store_subsection_grade(self.student, 6.0, 8.0)
        self.store_subsection_grade(self.other_student, 2.0, 8.0)

    def usernames(self, **query):
        """Return the usernames on the first gradebook page for ``query``."""
        response = self.client.get(gradebook_entry_list_url(self.course_key, **query))
        assert response.status_code == status.HTTP_200_OK, response.content
        return usernames_in(response)

    def refused_parameters(self, **query):
        """Return the names of the parameters a 400 response blames."""
        response = self.client.get(gradebook_entry_list_url(self.course_key, **query))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
        )
        return sorted(body['errors'])

    @ddt.data(
        ('program_user_key_0', ['program_student', 'program_masters_student']),
        ('i_like', ['other_student']),
        ('MASTERS', ['program_masters_student']),
    )
    @ddt.unpack
    def test_user_contains(self, text, expected):
        assert self.usernames(user_contains=text) == expected

    def test_username_contains(self):
        assert self.usernames(username_contains='other') == ['other_student']

    def test_cohort_id(self):
        cohort = CohortFactory(course_id=self.course_key, users=[self.student, self.program_student])
        assert self.usernames(cohort_id=cohort.id) == ['student', 'program_student']

    def test_cohort_of_another_course_matches_nobody(self):
        CourseEnrollmentFactory(user=self.student, course_id=self.empty_course.id)
        cohort = CohortFactory(course_id=self.empty_course.id, users=[self.student])
        assert self.usernames(cohort_id=cohort.id) == []

    def test_enrollment_mode(self):
        assert self.usernames(enrollment_mode='masters') == ['program_masters_student']

    def test_excluded_course_roles(self):
        CourseBetaTesterRole(self.course_key).add_users(self.student)
        CourseStaffRole(self.course_key).add_users(self.other_student)
        CourseStaffRole(self.empty_course.id).add_users(self.program_student)
        assert self.usernames(excluded_course_roles='beta_testers') == [
            'other_student', 'program_student', 'program_masters_student',
        ]
        assert self.usernames(excluded_course_roles=['beta_testers', 'staff']) == [
            'program_student', 'program_masters_student',
        ]
        assert self.usernames(excluded_course_roles='all') == ['program_student', 'program_masters_student']
        assert self.usernames(excluded_course_roles=['staff', 'all']) == ['program_student', 'program_masters_student']
        assert self.usernames(excluded_course_roles='') == FIXTURE_LEARNERS

    @ddt.data(
        ({'assignment_grade_min': 70}, ['student']),
        ({'assignment_grade_max': 50}, ['other_student']),
        ({'assignment_grade_min': 20, 'assignment_grade_max': 80}, ['student', 'other_student']),
        ({'assignment_grade_min': 80}, []),
        ({}, FIXTURE_LEARNERS),
    )
    @ddt.unpack
    def test_assignment_grade(self, bounds, expected):
        assert self.usernames(assignment_usage_key=str(self.homework.location), **bounds) == expected

    def test_assignment_bounds_without_an_assignment_have_no_effect(self):
        assert self.usernames(assignment_grade_min=70) == FIXTURE_LEARNERS

    @ddt.data(
        ({'course_grade_min': 50}, ['student']),
        ({'course_grade_min': 0}, FIXTURE_LEARNERS),
        ({'course_grade_max': 50}, ['other_student', 'program_student', 'program_masters_student']),
        ({'course_grade_min': 40, 'course_grade_max': 50}, ['other_student']),
    )
    @ddt.unpack
    def test_course_grade(self, bounds, expected):
        assert self.usernames(**bounds) == expected

    @ddt.data(
        ({'assignment_usage_key': 'not-a-key', 'assignment_grade_min': 1}, ['assignment_usage_key']),
        ({'assignment_grade_min': 'abc'}, ['assignment_grade_min']),
        ({'course_grade_min': 'abc'}, ['course_grade_min']),
        ({'course_grade_max': 'abc'}, ['course_grade_max']),
        ({'cohort_id': 'abc'}, ['cohort_id']),
        ({'cohort_id': '1.5'}, ['cohort_id']),
        ({'ordering': 'bogus'}, ['ordering']),
    )
    @ddt.unpack
    def test_bad_values_are_refused(self, query, expected):
        assert self.refused_parameters(**query) == expected

    @ddt.data(
        ('username', ['other_student', 'program_masters_student', 'program_student', 'student']),
        ('-id', list(reversed(FIXTURE_LEARNERS))),
    )
    @ddt.unpack
    def test_ordering(self, ordering, expected):
        assert self.usernames(ordering=ordering) == expected

    def test_filters_combine(self):
        assert self.usernames(course_grade_max=50, user_contains='program') == [
            'program_student', 'program_masters_student',
        ]


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookParityTest(GradebookTestBase):
    """
    The v2 bodies equal the v1 bodies they replace, apart from the declared differences,
    and each filter selects the same learners in both.
    """

    ROW_DIFFERENCES = [
        ('results[*].user_id', 'database ids are not published'),
    ]
    ENVELOPE_DIFFERENCES = [
        ('count', 'page-number envelope replaces the cursor envelope'),
        ('num_pages', 'page-number envelope replaces the cursor envelope'),
        ('current_page', 'page-number envelope replaces the cursor envelope'),
        ('start', 'page-number envelope replaces the cursor envelope'),
        ('total_users_count', 'only the seven page members are returned; count is the filtered total'),
        ('filtered_users_count', 'only the seven page members are returned; count is the filtered total'),
    ]

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.store_course_grade(self.student, 0.85)
        self.store_course_grade(self.other_student, 0.45)
        self.store_subsection_grade(self.student, 6.0, 8.0)

    def v1_url(self, **query):
        url = reverse('grades_api:v1:course_gradebook', kwargs={'course_id': str(self.course_key)})
        return with_query(url, **query)

    def test_gradebook(self):
        legacy = self.client.get(self.v1_url())
        new = self.client.get(gradebook_entry_list_url(self.course_key))
        assert legacy.status_code == new.status_code == status.HTTP_200_OK
        assert_parity(
            legacy.json(), new.json(),
            renames={'module_id': 'usage_key'},
            differences=[*self.ROW_DIFFERENCES, *self.ENVELOPE_DIFFERENCES],
        )

    def test_one_learner(self):
        legacy = self.client.get(self.v1_url(username='student'))
        new = self.client.get(gradebook_entry_detail_url(self.course_key, 'student'))
        assert legacy.status_code == new.status_code == status.HTTP_200_OK
        assert_parity(
            legacy.json(), new.json(),
            renames={'module_id': 'usage_key'},
            differences=[('user_id', 'database ids are not published')],
        )

    def test_one_masters_learner(self):
        legacy = self.client.get(self.v1_url(username='program_masters_student'))
        new = self.client.get(gradebook_entry_detail_url(self.course_key, 'program_masters_student'))
        assert_parity(
            legacy.json(), new.json(),
            renames={'module_id': 'usage_key'},
            differences=[
                ('user_id', 'database ids are not published'),
                ('email', "v1 hid the master's-track email on the single-learner address only"),
            ],
        )

    def test_each_filter_selects_the_same_learners(self):
        cohort = CohortFactory(course_id=self.course_key, users=[self.student, self.program_student])
        CourseBetaTesterRole(self.course_key).add_users(self.student)
        homework = str(self.homework.location)
        pairs = [
            ({'user_contains': 'program'}, {'user_contains': 'program'}),
            ({'username_contains': 'other'}, {'username_contains': 'other'}),
            ({'cohort_id': cohort.id}, {'cohort_id': cohort.id}),
            ({'enrollment_mode': 'masters'}, {'enrollment_mode': 'masters'}),
            ({'excluded_course_roles': 'beta_testers'}, {'excluded_course_roles': 'beta_testers'}),
            ({'excluded_course_roles': 'all'}, {'excluded_course_roles': 'all'}),
            (
                {'assignment': homework, 'assignment_grade_min': 70},
                {'assignment_usage_key': homework, 'assignment_grade_min': 70},
            ),
            ({'course_grade_min': 50}, {'course_grade_min': 50}),
            ({'course_grade_min': 0}, {'course_grade_min': 0}),
            ({'course_grade_max': 50}, {'course_grade_max': 50}),
        ]
        for legacy_query, new_query in pairs:
            legacy = [row['username'] for row in self.client.get(self.v1_url(**legacy_query)).json()['results']]
            new = usernames_in(self.client.get(gradebook_entry_list_url(self.course_key, **new_query)))
            assert new == legacy, (legacy_query, legacy, new)
            assert legacy != FIXTURE_LEARNERS or new_query == {'course_grade_min': 0}, (
                f'{legacy_query} does not narrow the fixture, so it proves nothing'
            )

    def test_status_codes_that_changed(self):
        """Pins the v1 and v2 statuses where the plan declares they differ."""
        self.client.force_authenticate(self.student)
        missing_v1 = reverse('grades_api:v1:course_gradebook', kwargs={'course_id': MISSING_COURSE_KEY})
        assert self.client.get(missing_v1).status_code == status.HTTP_404_NOT_FOUND
        assert self.client.get(gradebook_entry_list_url(MISSING_COURSE_KEY)).status_code == 403
        self.client.force_authenticate(self.global_staff)
        with pytest.raises(ValueError, match='could not convert string to float'):
            self.client.get(self.v1_url(course_grade_min='abc'))
        assert self.client.get(gradebook_entry_list_url(self.course_key, course_grade_min='abc')).status_code == 400
        CourseEnrollmentFactory(user=self.student, course_id=self.empty_course.id)
        other_course_cohort = CohortFactory(course_id=self.empty_course.id, users=[self.student])
        for cohort_id in (other_course_cohort.id, other_course_cohort.id + 1000):
            assert self.client.get(self.v1_url(cohort_id=cohort_id)).status_code == status.HTTP_404_NOT_FOUND
            response = self.client.get(gradebook_entry_list_url(self.course_key, cohort_id=cohort_id))
            assert response.status_code == status.HTTP_200_OK
            assert (response.json()['count'], response.json()['results']) == (0, [])


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookReadOnlyTest(ReadOnlyRequestMixin, GradebookTestBase):
    """Reading a gradebook changes nothing and announces nothing."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.store_subsection_grade(self.student, 6.0, 8.0)

    def test_list(self):
        with self.assert_nothing_is_written():
            assert self.client.get(gradebook_entry_list_url(self.course_key)).status_code == 200

    def test_list_minimal_view(self):
        with self.assert_nothing_is_written():
            assert self.client.get(gradebook_entry_list_url(self.course_key, view='minimal')).status_code == 200

    def test_detail(self):
        with self.assert_nothing_is_written():
            assert self.client.get(gradebook_entry_detail_url(self.course_key, 'student')).status_code == 200

    def test_head(self):
        with self.assert_nothing_is_written():
            assert self.client.head(gradebook_entry_list_url(self.course_key)).status_code == 200
            assert self.client.head(gradebook_entry_detail_url(self.course_key, 'student')).status_code == 200


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookCohortAssignmentTest(CohortedCourseMixin, GradebookTestBase):
    """
    On a course whose content groups follow cohorts, reading the gradebook
    assigns no learner to a cohort, including a learner who is in none.

    The page looks up every learner's cohort in one pass before grading, so the
    per-learner content-group lookup finds an answer and never assigns one.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def assert_not_assigned(self, url):
        with capture_cohort_assignment() as record:
            response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert (record.tables, record.memberships, record.tracking) == ([], [], [])
        assert self.cohort_of(self.uncohorted) is None
        return response

    def test_list(self):
        response = self.assert_not_assigned(gradebook_entry_list_url(self.course_key))
        assert 'uncohorted' in usernames_in(response)

    def test_list_minimal_view(self):
        response = self.assert_not_assigned(gradebook_entry_list_url(self.course_key, view='minimal'))
        assert 'uncohorted' in usernames_in(response)

    def test_detail(self):
        response = self.assert_not_assigned(gradebook_entry_detail_url(self.course_key, 'uncohorted'))
        assert response.json()['username'] == 'uncohorted'


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookColdCohortSettingsTest(GradeViewTestMixin, APITestCase):
    """
    The first gradebook read of a course copies its cohort settings into the database.

    Reading cohorts is what keeps the page from assigning learners to cohorts
    one by one, and the shared cohort code copies a course's settings the first
    time anything reads them. v1 behaves the same. This pins the behaviour so a
    change to either side is deliberate.
    """

    def setUp(self):
        super().setUp()
        warm_read_caches(self.course_key)
        self.client.force_authenticate(self.global_staff)

    def test_first_read_copies_the_settings_and_later_reads_write_nothing(self):
        assert not CourseCohortsSettings.objects.filter(course_id=self.course_key).exists()
        assert self.client.get(gradebook_entry_list_url(self.course_key)).status_code == status.HTTP_200_OK
        assert CourseCohortsSettings.objects.filter(course_id=self.course_key).count() == 1

    def test_v1_does_the_same(self):
        url = reverse('grades_api:v1:course_gradebook', kwargs={'course_id': str(self.course_key)})
        assert self.client.get(url).status_code == status.HTTP_200_OK
        assert CourseCohortsSettings.objects.filter(course_id=self.course_key).count() == 1


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class GradebookQueryCountTest(GradebookTestBase):
    """
    The v2 reads issue no more queries than v1 on the same fixture, and the list
    grows by one query per row, less than v1 grows.

    Each address is requested once before it is measured, so one-off lookups
    made by the first request of a test count for neither side.
    """

    #: Measured on this fixture; a change in any of them is a change in the data path.
    LIST_QUERIES = 26
    DETAIL_QUERIES = 15

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def measure(self, url):
        """Return the number of queries a second GET of ``url`` issues."""
        assert self.client.get(url).status_code == status.HTTP_200_OK
        response, count = count_queries(self.client, url)
        assert response.status_code == status.HTTP_200_OK
        return count

    def v1_url(self, **query):
        url = reverse('grades_api:v1:course_gradebook', kwargs={'course_id': str(self.course_key)})
        return with_query(url, **query)

    def test_list(self):
        legacy = self.measure(self.v1_url())
        new = self.measure(gradebook_entry_list_url(self.course_key))
        assert new == self.LIST_QUERIES
        assert new <= legacy

    def test_detail(self):
        legacy = self.measure(self.v1_url(username='student'))
        new = self.measure(gradebook_entry_detail_url(self.course_key, 'student'))
        assert new == self.DETAIL_QUERIES
        assert new <= legacy

    def test_list_grows_by_one_query_per_row(self):
        """
        Each row costs exactly one query: grading a learner checks access to the
        course content, and each check reads the authorization engine's policy
        version (``PolicyCacheControl``) to decide whether its cached policies
        are stale. v1 pays that and reads the learner's program enrollment too.
        """
        for index in range(8):
            CourseEnrollmentFactory(user=UserFactory(username=f'extra_{index}'), course_id=self.course_key)
        five = self.measure(gradebook_entry_list_url(self.course_key, page_size=5))
        ten = self.measure(gradebook_entry_list_url(self.course_key, page_size=10))
        assert ten - five == 5
        legacy_five = self.measure(self.v1_url(page_size=5))
        legacy_ten = self.measure(self.v1_url(page_size=10))
        assert legacy_ten - legacy_five == 10


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@override_settings(DEBUG=False)
class GradebookUrlTest(GradebookTestBase):
    """The addresses, their names, and the not-found body for addresses no route matches."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def test_reverse_literals(self):
        assert reverse('grade_v2:gradebook_entry_list', kwargs={'course_key': self.COURSE_KEY}) == (
            f'/api/grade/v2/courses/{self.COURSE_KEY}/gradebook_entries/'
        )
        assert reverse(
            'grade_v2:gradebook_entry_detail', kwargs={'course_key': self.COURSE_KEY, 'username': 'u'},
        ) == f'/api/grade/v2/courses/{self.COURSE_KEY}/gradebook_entries/u/'

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/courses/not-a-course-key/gradebook_entries/',
            '/api/grade/v2/courses/edX/DemoX/Demo/gradebook_entries/',
            '/api/grade/v2/courses/not-a-course-key/gradebook_entries/student/',
            f'/api/grade/v2/courses/{self.course_key}/gradebook_entries/student/extra/',
        ):
            response = self.client.get(path)
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_missing_trailing_slash_redirects(self):
        response = self.client.get(f'/api/grade/v2/courses/{self.course_key}/gradebook_entries')
        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response['Location'].endswith(f'/api/grade/v2/courses/{self.course_key}/gradebook_entries/')
