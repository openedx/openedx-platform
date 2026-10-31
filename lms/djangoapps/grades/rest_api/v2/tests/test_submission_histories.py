"""Tests for the submission history endpoint of the Grades API v2."""

import json
from contextlib import ExitStack
from unittest.mock import patch

import ddt
from django.core.cache import cache
from django.db import connections
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from edx_django_utils.cache import RequestCache
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.models import RateLimitConfiguration
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    GRADE_READ_SCOPE,
    PAGE_FIELDS,
    CohortedCourseMixin,
    ReadOnlyRequestMixin,
    capture_cohort_assignment,
    submission_history_list_url,
    token_header,
    usernames_in,
    warm_read_caches,
    with_query,
)
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from openedx.core.djangoapps.enrollments.views import EnrollmentUserThrottle

MISSING_COURSE_KEY = 'course-v1:NoSuch+Course+Run'
FIXTURE_LEARNERS = ['student', 'other_student', 'program_student', 'program_masters_student']
DATABASES = {'default', 'student_module_history'}


class SubmissionHistoryTestBase(GradeViewTestMixin, APITestCase):
    """The shared course, with two recorded submissions by one learner to its first problem."""

    databases = DATABASES

    def setUp(self):
        super().setUp()
        cache.clear()
        warm_read_caches(self.course_key)
        vertical = self.store.get_course(self.course_key).get_children()[0].get_children()[0].get_children()[0]
        self.problem = vertical.get_children()[0]
        self.submit(self.student, self.problem, grade=0, max_grade=1)
        self.submit(self.student, self.problem, grade=1, max_grade=1)

    def submit(self, user, problem, grade, max_grade):
        """Record one submission by ``user`` to ``problem``, keeping the history of earlier ones."""
        module, _ = StudentModuleFactory._meta.model.objects.get_or_create(  # pylint: disable=protected-access
            student=user, course_id=self.course_key, module_state_key=problem.location,
            defaults={'module_type': 'problem'},
        )
        # History records made within one request are merged, and each call stands for one request.
        RequestCache.clear_all_namespaces()
        module.state = json.dumps({'attempts': grade + 1})
        module.grade = grade
        module.max_grade = max_grade
        module.save()
        return module

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)


@ddt.ddt
class SubmissionHistoryAccessTest(SubmissionHistoryTestBase):
    """Only global staff may read submission histories."""

    def test_anonymous_caller_is_refused(self):
        assert_error_envelope(
            self.client.get(submission_history_list_url(self.course_key)),
            expected_status=401, expected_type_slug='authn',
        )

    @ddt.data('learner', 'course_staff', 'course_instructor')
    def test_everyone_but_global_staff_is_refused(self, kind):
        user = {'learner': lambda: self.student}.get(kind, lambda: UserFactory(password=self.password))()
        if kind != 'learner':
            role = {'course_staff': CourseStaffRole, 'course_instructor': CourseInstructorRole}[kind]
            role(self.course_key).add_users(user)
        self.login(user)
        assert_error_envelope(
            self.client.get(submission_history_list_url(self.course_key)),
            expected_status=403, expected_type_slug='authz',
        )

    def test_learner_cannot_read_even_their_own_history(self):
        self.login(self.student)
        response = self.client.get(submission_history_list_url(self.course_key, username='student'))
        assert_error_envelope(response, expected_status=403, expected_type_slug='authz')

    def test_global_staff_are_admitted(self):
        self.login(self.global_staff)
        response = self.client.get(submission_history_list_url(self.course_key))
        assert usernames_in(response) == FIXTURE_LEARNERS

    def test_caller_without_access_cannot_learn_whether_a_course_exists(self):
        self.login(self.student)
        assert_error_envelope(
            self.client.get(submission_history_list_url(MISSING_COURSE_KEY)),
            expected_status=403, expected_type_slug='authz',
        )

    def test_missing_course_is_not_found(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.client.get(submission_history_list_url(MISSING_COURSE_KEY)),
            expected_status=404, expected_type_slug='not-found',
        )

    @ddt.data((), (GRADE_READ_SCOPE,))
    def test_restricted_token_is_refused_even_for_global_staff(self, scopes):
        headers = token_header(
            self.global_staff, scopes=scopes, is_restricted=True, filters=[f'content_org:{self.course_key.org}'],
        )
        response = self.client.get(submission_history_list_url(self.course_key), **headers)
        assert_error_envelope(response, expected_status=403, expected_type_slug='authz')

    def test_unrestricted_token_for_global_staff_is_admitted(self):
        response = self.client.get(submission_history_list_url(self.course_key), **token_header(self.global_staff))
        assert usernames_in(response) == FIXTURE_LEARNERS

    def test_restricted_token_is_refused_by_v1_too(self):
        headers = token_header(
            self.global_staff, is_restricted=True, filters=[f'content_org:{self.course_key.org}'],
        )
        legacy_url = reverse('grades_api:v1:submission_history', kwargs={'course_id': str(self.course_key)})
        legacy = self.client.get(legacy_url, **headers)
        new = self.client.get(submission_history_list_url(self.course_key), **headers)
        assert (legacy.status_code, new.status_code) == (status.HTTP_403_FORBIDDEN, status.HTTP_403_FORBIDDEN)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class SubmissionHistoryThrottleTest(SubmissionHistoryTestBase):
    """Requests are rate limited unless rate limiting is switched off for the site."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.url = submission_history_list_url(self.course_key)

    def statuses(self, count):
        with patch.dict(EnrollmentUserThrottle.THROTTLE_RATES, {'staff': '2/minute'}):
            return [self.client.get(self.url).status_code for _ in range(count)]

    def test_limit_applies_when_rate_limiting_is_on(self):
        RateLimitConfiguration.objects.create(enabled=True)
        assert self.statuses(2) == [200, 200]
        with patch.dict(EnrollmentUserThrottle.THROTTLE_RATES, {'staff': '2/minute'}):
            response = self.client.get(self.url)
        assert_error_envelope(response, expected_status=429, expected_type_slug='rate-limited')

    def test_limit_is_lifted_when_rate_limiting_is_off(self):
        RateLimitConfiguration.objects.create(enabled=False)
        assert self.statuses(3) == [200, 200, 200]


class SubmissionHistoryContentTest(SubmissionHistoryTestBase):
    """The rows and what they hold."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def body(self, **query):
        response = self.client.get(submission_history_list_url(self.course_key, **query))
        assert response.status_code == status.HTTP_200_OK, response.content
        return response.json()

    def test_rows(self):
        body = self.body()
        assert set(body) == PAGE_FIELDS
        assert body['results'][0] == {
            'username': 'student',
            'problems': [{
                'usage_key': str(self.problem.location),
                'display_name': self.problem.display_name,
                'submissions': [
                    {'state': {'attempts': 2}, 'grade': 1.0, 'max_grade': 1.0},
                    {'state': {'attempts': 1}, 'grade': 0.0, 'max_grade': 1.0},
                    {'state': None, 'grade': None, 'max_grade': None},
                ],
            }],
        }
        assert [row['problems'] for row in body['results'][1:]] == [[], [], []]

    def test_each_learner_gets_only_their_own_submissions(self):
        second_problem = self.problem.get_parent().get_parent().get_parent().get_children()[1].get_children()[0]
        self.submit(self.other_student, second_problem.get_children()[0], grade=1, max_grade=2)
        rows = {row['username']: row['problems'] for row in self.body()['results']}
        assert [problem['usage_key'] for problem in rows['student']] == [str(self.problem.location)]
        other = [(problem['usage_key'], problem['submissions'][0]['max_grade']) for problem in rows['other_student']]
        assert other == [(str(second_problem.get_children()[0].location), 2.0)]

    def test_full_view_adds_each_problem_definition(self):
        problem = self.body(view='full')['results'][0]['problems'][0]
        assert problem['data'] == self.problem.data

    def test_unknown_view_is_refused(self):
        response = self.client.get(submission_history_list_url(self.course_key, view='minimal'))
        body = assert_error_envelope(response, expected_status=400, expected_type_slug='validation')
        assert list(body['errors']) == ['view']

    def test_username_filter(self):
        assert usernames_in(self.client.get(submission_history_list_url(
            self.course_key, username='student,program_student',
        ))) == ['student', 'program_student']

    def test_more_than_a_hundred_usernames_are_refused(self):
        response = self.client.get(submission_history_list_url(
            self.course_key, username=','.join(f'user{index}' for index in range(101)),
        ))
        body = assert_error_envelope(response, expected_status=400, expected_type_slug='validation')
        assert list(body['errors']) == ['username']

    def test_ordering(self):
        assert usernames_in(self.client.get(submission_history_list_url(self.course_key, ordering='username'))) == [
            'other_student', 'program_masters_student', 'program_student', 'student',
        ]
        response = self.client.get(submission_history_list_url(self.course_key, ordering='bogus'))
        assert_error_envelope(response, expected_status=400, expected_type_slug='validation')

    def test_inactive_enrollments_are_left_out(self):
        CourseEnrollment.objects.filter(user=self.other_student, course_id=self.course_key).update(is_active=False)
        assert 'other_student' not in usernames_in(self.client.get(submission_history_list_url(self.course_key)))

    def test_submissions_in_another_course_are_left_out(self):
        CourseEnrollmentFactory(user=self.student, course_id=self.empty_course.id)
        rows = self.body()['results']
        assert [problem['usage_key'] for problem in rows[0]['problems']] == [str(self.problem.location)]
        other = self.client.get(submission_history_list_url(self.empty_course.id)).json()['results']
        assert other == [{'username': 'student', 'problems': []}]

    def test_paging(self):
        body = self.body(page_size=3, page=2)
        assert (body['count'], body['num_pages'], body['current_page'], body['start']) == (4, 2, 2, 3)
        assert [row['username'] for row in body['results']] == ['program_masters_student']


class SubmissionHistoryParityTest(SubmissionHistoryTestBase):
    """The v2 body equals the v1 body it replaces, apart from the declared differences."""

    RENAMES = {
        'user': 'username',
        'location': 'usage_key',
        'name': 'display_name',
        'submission_history': 'submissions',
    }
    DIFFERENCES = [
        ('count', 'page-number envelope replaces the cursor envelope'),
        ('num_pages', 'page-number envelope replaces the cursor envelope'),
        ('current_page', 'page-number envelope replaces the cursor envelope'),
        ('start', 'page-number envelope replaces the cursor envelope'),
        ('results[*].course_id', 'the course is the address'),
        ('results[*].course_name', 'the course is the address'),
    ]

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def v1(self, **query):
        url = reverse('grades_api:v1:submission_history', kwargs={'course_id': str(self.course_key)})
        response = self.client.get(with_query(url, **query))
        assert response.status_code == status.HTTP_200_OK
        return response.json()

    def test_full_view(self):
        new = self.client.get(submission_history_list_url(self.course_key, view='full')).json()
        assert_parity(self.v1(), new, renames=self.RENAMES, differences=self.DIFFERENCES)

    def test_default_view_leaves_the_problem_definitions_out(self):
        new = self.client.get(submission_history_list_url(self.course_key)).json()
        assert_parity(
            self.v1(), new, renames=self.RENAMES,
            differences=[*self.DIFFERENCES, ('results[*].problems[*].data', 'returned only for view=full')],
        )

    def test_one_learner(self):
        new = self.client.get(submission_history_list_url(self.course_key, username='student', view='full')).json()
        assert_parity(self.v1(username='student'), new, renames=self.RENAMES, differences=self.DIFFERENCES)

    def test_missing_course_is_a_200_in_v1_and_a_404_in_v2(self):
        url = reverse('grades_api:v1:submission_history', kwargs={'course_id': MISSING_COURSE_KEY})
        assert self.client.get(url).json()['results'] == []
        assert self.client.get(submission_history_list_url(MISSING_COURSE_KEY)).status_code == 404


def _capture_every_database():
    """Return an ExitStack capturing the queries of both databases, and the two captures."""
    stack = ExitStack()
    captures = [stack.enter_context(CaptureQueriesContext(connections[alias])) for alias in sorted(DATABASES)]
    return stack, captures


class SubmissionHistoryReadOnlyTest(ReadOnlyRequestMixin, SubmissionHistoryTestBase):
    """Reading submission histories changes nothing, in either database."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_list_and_head(self):
        stack, (default, history) = _capture_every_database()
        with stack, self.assert_nothing_is_written():
            for query in ({}, {'view': 'full'}):
                assert self.client.get(submission_history_list_url(self.course_key, **query)).status_code == 200
            assert self.client.head(submission_history_list_url(self.course_key)).status_code == 200
        writes = [
            query['sql'] for query in history.captured_queries
            if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE'))
        ]
        assert (writes, len(default.captured_queries) > 0) == ([], True)


class SubmissionHistoryCohortAssignmentTest(CohortedCourseMixin, SubmissionHistoryTestBase):
    """
    On a course whose content groups follow cohorts, reading submission
    histories assigns no learner to a cohort, including a learner who is in none.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.submit(self.uncohorted, self.problem, grade=1, max_grade=1)

    def test_each_view(self):
        for query in ({}, {'view': 'full'}):
            with capture_cohort_assignment() as record:
                response = self.client.get(submission_history_list_url(self.course_key, **query))
            assert response.status_code == status.HTTP_200_OK
            assert 'uncohorted' in usernames_in(response)
            assert (record.tables, record.memberships, record.tracking) == ([], [], [])
        assert self.cohort_of(self.uncohorted) is None


class SubmissionHistoryQueryCountTest(SubmissionHistoryTestBase):
    """
    v2 issues no more queries than v1 on the same page, and the count does not grow with the page.

    Queries are counted in both databases, since submission histories live in
    their own. Each address is requested once first.
    """

    #: Measured on this fixture; a change is a change in the data path. v1
    #: reads each learner's submissions to each problem one at a time.
    LIST_QUERIES = 14
    V1_LIST_QUERIES = 223

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def measure(self, url):
        """Return the number of queries, in both databases, the second GET of ``url`` issues."""
        assert self.client.get(url).status_code == status.HTTP_200_OK
        stack, captures = _capture_every_database()
        with stack:
            response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        return sum(len(capture.captured_queries) for capture in captures)

    def test_list(self):
        legacy = self.measure(reverse('grades_api:v1:submission_history', kwargs={'course_id': str(self.course_key)}))
        new = self.measure(submission_history_list_url(self.course_key))
        assert (new, legacy) == (self.LIST_QUERIES, self.V1_LIST_QUERIES)

    def test_count_does_not_grow_with_the_page(self):
        self.submit(self.other_student, self.problem, grade=1, max_grade=1)
        two = self.measure(submission_history_list_url(self.course_key, page_size=2))
        four = self.measure(submission_history_list_url(self.course_key, page_size=4))
        assert two == four


@override_settings(DEBUG=False)
class SubmissionHistoryUrlTest(SubmissionHistoryTestBase):
    """The address, its name, and the not-found body for addresses no route matches."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def test_reverse_literal(self):
        assert reverse('grade_v2:submission_history_list', kwargs={'course_key': self.COURSE_KEY}) == (
            f'/api/grade/v2/courses/{self.COURSE_KEY}/submission_histories/'
        )

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/courses/not-a-course-key/submission_histories/',
            '/api/grade/v2/courses/edX/DemoX/Demo/submission_histories/',
            f'/api/grade/v2/courses/{self.course_key}/submission_histories/student/',
        ):
            response = self.client.get(path)
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')
