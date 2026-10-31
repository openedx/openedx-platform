"""Tests for the course-grade endpoints of the Grades API v2."""

from unittest.mock import patch

import ddt
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, StaffFactory, UserFactory
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from lms.djangoapps.grades.models import PersistentCourseGrade
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.filters import CourseGradeFilterSet
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.scoping import CourseGradeScopingPolicy
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    AUTO_COHORT,
    COHORT_ASSIGNMENT_TABLES,
    COHORT_ASSIGNMENT_TRACKING,
    PAGE_FIELDS,
    CohortedCourseMixin,
    ReadOnlyRequestMixin,
    capture_cohort_assignment,
    count_queries,
    course_grade_detail_url,
    course_grade_list_url,
    token_header,
    usernames_in,
    warm_read_caches,
    with_query,
)
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

#: Every learner the shared fixture enrolls in the test course, in enrollment order.
FIXTURE_LEARNERS = ['student', 'other_student', 'program_student', 'program_masters_student']


def persist_passing_grade(user, course_key):
    """Store a passing course grade of 62% for ``user``."""
    PersistentCourseGrade.objects.create(
        user_id=user.id,
        course_id=course_key,
        percent_grade=0.62,
        letter_grade='Pass',
        grading_policy_hash='policy-hash',
    )


class CourseGradeTestBase(GradeViewTestMixin, APITestCase):
    """Fixtures shared by the course-grade endpoint tests."""

    def setUp(self):
        super().setUp()
        for course_key in (self.course_key, self.empty_course.id):
            # A published course's overview carries the organization of its key.
            CourseOverview.objects.filter(id=course_key).update(org=course_key.org)
            warm_read_caches(course_key)

    def expected_grade(self, user, email='', course_key=None):
        """Return the row the endpoints return for a learner who has attempted nothing."""
        return {
            'username': user.username,
            'course_key': str(course_key or self.course_key),
            'email': email,
            'passed': False,
            'percent': 0.0,
            'letter_grade': None,
        }

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)

    def enroll(self, user, course_key, **kwargs):
        """Enroll ``user`` in ``course_key``."""
        return CourseEnrollmentFactory(user=user, course_id=course_key, **kwargs)


class CourseGradeListAccessTest(CourseGradeTestBase):
    """Who may list course grades, and whose grades each caller is shown."""

    def test_anonymous_caller_is_refused(self):
        response = self.client.get(course_grade_list_url())
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug='authn')

    def test_learner_sees_only_their_own_grade(self):
        self.login(self.student)
        response = self.client.get(course_grade_list_url())
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'] == [self.expected_grade(self.student)]

    def test_learner_cannot_list_another_learners_grade(self):
        self.login(self.student)
        response = self.client.get(course_grade_list_url(username=self.other_student.username))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0
        assert response.json()['results'] == []

    def test_learner_naming_the_course_still_sees_only_their_own_grade(self):
        self.login(self.student)
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert usernames_in(response) == ['student']

    def test_learner_sees_their_grades_across_courses(self):
        self.enroll(self.student, self.empty_course.id)
        self.login(self.student)
        response = self.client.get(course_grade_list_url())
        assert response.json()['results'] == [
            self.expected_grade(self.student),
            self.expected_grade(self.student, course_key=self.empty_course.id),
        ]

    def test_course_staff_see_only_their_own_grade(self):
        course_staff = StaffFactory.create(course_key=self.course_key, password=self.password)
        self.enroll(course_staff, self.course_key)
        self.login(course_staff)
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert usernames_in(response) == [course_staff.username]

    def test_global_staff_see_every_grade_in_the_course(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['results'] == [
            self.expected_grade(self.student),
            self.expected_grade(self.other_student),
            self.expected_grade(self.program_student),
            self.expected_grade(self.program_masters_student, email=self.program_masters_student.email),
        ]

    def test_global_staff_list_spans_courses_without_a_course_key(self):
        self.enroll(self.other_student, self.empty_course.id)
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_list_url())
        assert [(row['username'], row['course_key']) for row in response.json()['results']] == [
            *[(name, str(self.course_key)) for name in FIXTURE_LEARNERS],
            ('other_student', str(self.empty_course.id)),
        ]

    def test_inactive_enrollments_are_not_listed(self):
        CourseEnrollment.objects.filter(user=self.other_student, course_id=self.course_key).update(is_active=False)
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert usernames_in(response) == ['student', 'program_student', 'program_masters_student']

    def test_deactivated_account_session_is_refused(self):
        self.student.is_active = False
        self.student.save()
        self.client.login(username='student', password=self.password)
        response = self.client.get(course_grade_list_url())
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug='authn')

    def test_deactivated_account_token_is_admitted(self):
        self.student.is_active = False
        self.student.save()
        response = self.client.get(course_grade_list_url(), **token_header(self.student))
        assert response.status_code == status.HTTP_200_OK
        assert usernames_in(response) == ['student']

    def test_unrestricted_token_scopes_like_a_session(self):
        response = self.client.get(course_grade_list_url(), **token_header(self.student, scopes=()))
        assert usernames_in(response) == ['student']


class RestrictedTokenListTest(CourseGradeTestBase):
    """A token issued to a restricted application sees what its scope and filters allow."""

    def org_filter(self, org=None):
        return f'content_org:{org or self.course_key.org}'

    def test_token_without_the_read_scope_is_refused(self):
        headers = token_header(self.global_staff, scopes=(), is_restricted=True, filters=[self.org_filter()])
        response = self.client.get(course_grade_list_url(), **headers)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz')

    def test_token_without_an_organization_filter_sees_nothing(self):
        response = self.client.get(course_grade_list_url(), **token_header(self.global_staff, is_restricted=True))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    def test_token_for_another_organization_sees_nothing(self):
        headers = token_header(self.global_staff, is_restricted=True, filters=[self.org_filter('NotThisOrg')])
        response = self.client.get(course_grade_list_url(), **headers)
        assert response.json()['count'] == 0

    def test_organization_filter_admits_every_learner_in_the_organization(self):
        headers = token_header(self.student, is_restricted=True, filters=[self.org_filter()])
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == FIXTURE_LEARNERS

    def test_user_filter_narrows_to_that_learner(self):
        headers = token_header(
            self.global_staff, is_restricted=True, filters=[self.org_filter(), 'user:other_student'],
        )
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == ['other_student']

    def test_me_user_filter_means_the_token_holder(self):
        headers = token_header(self.student, is_restricted=True, filters=[self.org_filter(), 'user:me'])
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == ['student']

    def test_only_the_first_user_filter_counts(self):
        headers = token_header(
            self.global_staff, is_restricted=True,
            filters=[self.org_filter(), 'user:other_student', 'user:student'],
        )
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == ['other_student']

    def test_lower_case_user_filter_matches_a_username_with_capitals(self):
        self.enroll(UserFactory(username='MixedCase'), self.course_key)
        headers = token_header(self.global_staff, is_restricted=True, filters=[self.org_filter(), 'user:mixedcase'])
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == ['MixedCase']

    def test_user_filter_with_capitals_matches_nobody(self):
        self.enroll(UserFactory(username='MixedCase'), self.course_key)
        headers = token_header(self.global_staff, is_restricted=True, filters=[self.org_filter(), 'user:MixedCase'])
        response = self.client.get(course_grade_list_url(), **headers)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    def test_me_user_filter_matches_a_token_holder_with_capitals(self):
        holder = UserFactory(username='MixedCase')
        self.enroll(holder, self.course_key)
        headers = token_header(holder, is_restricted=True, filters=[self.org_filter(), 'user:me'])
        response = self.client.get(course_grade_list_url(), **headers)
        assert usernames_in(response) == ['MixedCase']


@ddt.ddt
class RestrictedTokenUserFilterParityTest(CourseGradeTestBase):
    """
    A restricted token reads one learner's grade in v2 exactly when v1's
    single-learner address lets the same token read it.

    v1 refuses with 403 where v2 answers 404, because v2 hides whether the
    grade exists; the comparison is of whether the grade is served.
    """

    def setUp(self):
        super().setUp()
        self.mixed_case = UserFactory(username='MixedCase')
        self.enroll(self.mixed_case, self.course_key)

    @ddt.data(
        ('student', ['user:student']),
        ('student', ['user:other_student']),
        ('student', ['user:student', 'user:other_student']),
        ('student', ['user:other_student', 'user:student']),
        ('MixedCase', ['user:mixedcase']),
        ('MixedCase', ['user:MixedCase']),
        ('MixedCase', ['user:me']),
        ('student', []),
    )
    @ddt.unpack
    def test_served_exactly_when_v1_serves_it(self, username, user_filters):
        holder = self.mixed_case if 'user:me' in user_filters else self.global_staff
        headers = token_header(
            holder, is_restricted=True, filters=[f'content_org:{self.course_key.org}', *user_filters],
        )
        legacy_url = reverse('grades_api:v1:course_grades', kwargs={'course_id': str(self.course_key)})
        legacy = self.client.get(legacy_url, {'username': username}, **headers)
        new = self.client.get(course_grade_detail_url(username, self.course_key), **headers)
        assert legacy.status_code in (status.HTTP_200_OK, status.HTTP_403_FORBIDDEN), legacy.content
        assert new.status_code in (status.HTTP_200_OK, status.HTTP_404_NOT_FOUND), new.content
        assert (new.status_code == status.HTTP_200_OK) == (legacy.status_code == status.HTTP_200_OK)
        if new.status_code == status.HTTP_200_OK:
            assert new.json()['username'] == legacy.json()[0]['username'] == username


@ddt.ddt
class CourseGradeFullViewAccessTest(CourseGradeTestBase):
    """
    Only global staff, signed in as themselves, may read the breakdown.

    The breakdown carries scores the course hides from learners, so it is
    refused to learners asking about themselves, to course staff, and to any
    restricted token, whatever its filters and whoever it was issued to.
    """

    def assert_refused(self, **headers):
        """Fail unless the breakdown is refused on both addresses."""
        for url in (
            course_grade_list_url(course_key=str(self.course_key), view='full'),
            course_grade_detail_url('student', self.course_key, view='full'),
        ):
            response = self.client.get(url, **headers)
            assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz')

    def org_token(self, user):
        """Return the headers of a restricted token for ``user`` naming the test course's organization."""
        return token_header(user, is_restricted=True, filters=[f'content_org:{self.course_key.org}'])

    def test_learner_is_refused_their_own_breakdown(self):
        self.login(self.student)
        self.assert_refused()

    def test_learner_still_reads_their_own_grade_without_it(self):
        self.login(self.student)
        response = self.client.get(course_grade_detail_url('student', self.course_key))
        assert response.json() == self.expected_grade(self.student)

    def test_learner_token_is_refused(self):
        self.assert_refused(**token_header(self.student))

    def test_course_staff_are_refused(self):
        course_staff = StaffFactory.create(course_key=self.course_key, password=self.password)
        self.login(course_staff)
        self.assert_refused()

    def test_restricted_token_for_a_learner_is_refused(self):
        self.assert_refused(**self.org_token(self.student))

    def test_restricted_token_for_global_staff_is_refused(self):
        self.assert_refused(**self.org_token(self.global_staff))

    def test_restricted_token_keeps_the_default_view(self):
        response = self.client.get(course_grade_list_url(), **self.org_token(self.student))
        assert usernames_in(response) == FIXTURE_LEARNERS

    @ddt.data('session', 'token')
    def test_global_staff_are_admitted(self, how):
        headers = token_header(self.global_staff) if how == 'token' else {}
        if how == 'session':
            self.login(self.global_staff)
        response = self.client.get(course_grade_detail_url('student', self.course_key, view='full'), **headers)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()['section_breakdown']) == 28

    def test_unknown_view_is_still_a_validation_error_for_a_learner(self):
        self.login(self.student)
        response = self.client.get(course_grade_list_url(view='bogus'))
        assert_error_envelope(response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation')


@ddt.ddt
class CourseGradeFullViewParityTest(CourseGradeTestBase):
    """The breakdown admits exactly the callers v1's section breakdown admits."""

    def headers_for(self, caller):
        """Sign ``caller`` in, and return the request headers it sends."""
        org_filter = f'content_org:{self.course_key.org}'
        if caller == 'anonymous':
            return {}
        if caller.endswith('_session'):
            user = {
                'learner_session': self.student,
                'course_staff_session': StaffFactory.create(course_key=self.course_key, password=self.password),
                'global_staff_session': self.global_staff,
            }[caller]
            self.login(user)
            return {}
        user = self.global_staff if caller.startswith('global_staff') else self.student
        if caller.endswith('_restricted_token'):
            return token_header(user, is_restricted=True, filters=[org_filter])
        return token_header(user)

    @ddt.data(
        ('anonymous', status.HTTP_401_UNAUTHORIZED),
        ('learner_session', status.HTTP_403_FORBIDDEN),
        ('course_staff_session', status.HTTP_403_FORBIDDEN),
        ('global_staff_session', status.HTTP_200_OK),
        ('learner_token', status.HTTP_403_FORBIDDEN),
        ('global_staff_token', status.HTTP_200_OK),
        ('learner_restricted_token', status.HTTP_403_FORBIDDEN),
        ('global_staff_restricted_token', status.HTTP_403_FORBIDDEN),
    )
    @ddt.unpack
    def test_same_status_as_v1(self, caller, expected):
        headers = self.headers_for(caller)
        legacy = self.client.get(
            reverse('grades_api:v1:section_grades_breakdown'), {'course_id': str(self.course_key)}, **headers,
        )
        new_list = self.client.get(course_grade_list_url(course_key=str(self.course_key), view='full'), **headers)
        new_detail = self.client.get(course_grade_detail_url('student', self.course_key, view='full'), **headers)
        assert (legacy.status_code, new_list.status_code, new_detail.status_code) == (expected, expected, expected)


class CourseGradeVisibilityTest(CourseGradeTestBase):
    """Each branch of the scoping policy, applied directly to the enrollment table."""

    def scoped_usernames(self, user, auth=None, restricted=False, filters=()):
        """Return the usernames the policy leaves visible to ``user``."""
        request = APIRequestFactory().get('/')
        request.user = user
        request.auth = auth
        policy = CourseGradeScopingPolicy(request)
        with patch.object(policy, '_is_restricted_token', return_value=restricted), \
                patch('lms.djangoapps.grades.rest_api.v2.scoping.decode_jwt_filters', return_value=list(filters)):
            queryset = policy.scope(CourseEnrollment.objects.order_by('id'), user)
        return list(queryset.values_list('user__username', flat=True))

    def test_staff_see_every_row(self):
        assert self.scoped_usernames(self.global_staff) == FIXTURE_LEARNERS

    def test_other_callers_see_their_own_rows(self):
        assert self.scoped_usernames(self.other_student) == ['other_student']

    def test_staff_restricted_token_is_still_filtered(self):
        assert not self.scoped_usernames(self.global_staff, restricted=True)

    def test_restricted_token_organization_and_user_filters(self):
        filters = [('content_org', self.course_key.org), ('user', 'program_student')]
        assert self.scoped_usernames(self.student, restricted=True, filters=filters) == ['program_student']

    def test_restricted_token_me_filter(self):
        filters = [('content_org', self.course_key.org), ('user', 'me')]
        assert self.scoped_usernames(self.student, restricted=True, filters=filters) == ['student']

    def test_restricted_token_later_user_filters_are_ignored(self):
        filters = [('user', 'program_student'), ('content_org', self.course_key.org), ('user', 'student')]
        assert self.scoped_usernames(self.student, restricted=True, filters=filters) == ['program_student']


class CourseGradeDetailAccessTest(CourseGradeTestBase):
    """Who may read one learner's grade."""

    def test_anonymous_caller_is_refused(self):
        response = self.client.get(course_grade_detail_url('student', self.course_key))
        assert_error_envelope(response, expected_status=status.HTTP_401_UNAUTHORIZED, expected_type_slug='authn')

    def test_learner_reads_their_own_grade(self):
        self.login(self.student)
        response = self.client.get(course_grade_detail_url('student', self.course_key))
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == self.expected_grade(self.student)

    def test_learner_cannot_read_another_learners_grade(self):
        self.login(self.student)
        response = self.client.get(course_grade_detail_url('other_student', self.course_key))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_course_staff_cannot_read_a_learners_grade(self):
        course_staff = StaffFactory.create(course_key=self.course_key, password=self.password)
        self.login(course_staff)
        response = self.client.get(course_grade_detail_url('student', self.course_key))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_global_staff_read_any_learners_grade(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_detail_url('program_masters_student', self.course_key))
        assert response.json() == self.expected_grade(
            self.program_masters_student, email=self.program_masters_student.email,
        )

    def test_inactive_enrollment_is_still_readable(self):
        CourseEnrollment.objects.filter(user=self.student, course_id=self.course_key).update(is_active=False)
        self.login(self.student)
        response = self.client.get(course_grade_detail_url('student', self.course_key))
        assert response.json() == self.expected_grade(self.student)

    def test_learner_not_enrolled_is_not_found(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_detail_url('student', self.empty_course.id))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_unknown_learner_is_not_found(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_detail_url('nobody', self.course_key))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_restricted_token_for_another_learner_is_not_found(self):
        headers = token_header(
            self.global_staff, is_restricted=True,
            filters=[f'content_org:{self.course_key.org}', 'user:other_student'],
        )
        response = self.client.get(course_grade_detail_url('student', self.course_key), **headers)
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_restricted_token_for_the_named_learner_is_admitted(self):
        headers = token_header(
            self.global_staff, is_restricted=True,
            filters=[f'content_org:{self.course_key.org}', 'user:student'],
        )
        response = self.client.get(course_grade_detail_url('student', self.course_key), **headers)
        assert response.json() == self.expected_grade(self.student)

    def test_restricted_token_without_the_read_scope_is_refused(self):
        headers = token_header(
            self.student, scopes=(), is_restricted=True, filters=[f'content_org:{self.course_key.org}'],
        )
        response = self.client.get(course_grade_detail_url('student', self.course_key), **headers)
        assert_error_envelope(response, expected_status=status.HTTP_403_FORBIDDEN, expected_type_slug='authz')

    def test_grade_that_cannot_be_computed_is_a_server_error(self):
        self.client.force_authenticate(self.global_staff)
        with patch.object(CourseGradeFactory, 'read', side_effect=RuntimeError('boom')):
            response = self.client.get(course_grade_detail_url('student', self.course_key))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_500_INTERNAL_SERVER_ERROR, expected_type_slug='internal',
        )
        assert 'boom' not in body['detail']


@ddt.ddt
class CourseGradeFilterTest(CourseGradeTestBase):
    """Each filter narrows the rows an authorized caller sees, and bad values are refused."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.enroll(self.student, self.empty_course.id)

    def test_course_key_narrows_to_one_course(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.empty_course.id)))
        assert response.json()['results'] == [self.expected_grade(self.student, course_key=self.empty_course.id)]

    def test_unknown_course_is_an_empty_page(self):
        response = self.client.get(course_grade_list_url(course_key='course-v1:NoSuch+Course+Run'))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['count'] == 0

    @ddt.data('not-a-course-key', 'edX/DemoX/Demo')
    def test_malformed_or_retired_course_key_is_refused(self, course_key):
        response = self.client.get(course_grade_list_url(course_key=course_key))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
        )
        assert list(body['errors']) == ['course_key']

    def test_username_narrows_to_one_learner(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), username='other_student'))
        assert usernames_in(response) == ['other_student']

    def test_username_accepts_a_comma_separated_list(self):
        response = self.client.get(course_grade_list_url(username='program_student,student'))
        assert [(row['username'], row['course_key']) for row in response.json()['results']] == [
            ('student', str(self.course_key)),
            ('program_student', str(self.course_key)),
            ('student', str(self.empty_course.id)),
        ]

    def test_more_than_a_hundred_usernames_are_refused(self):
        response = self.client.get(course_grade_list_url(username=','.join(f'u{i}' for i in range(101))))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
        )
        assert list(body['errors']) == ['username']

    def test_a_hundred_usernames_are_accepted(self):
        names = ['student'] + [f'u{i}' for i in range(99)]
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), username=','.join(names)))
        assert usernames_in(response) == ['student']

    @ddt.data(
        ('username', ['other_student', 'program_masters_student', 'program_student', 'student']),
        ('-username', ['student', 'program_student', 'program_masters_student', 'other_student']),
        ('-id', list(reversed(FIXTURE_LEARNERS))),
        ('created', FIXTURE_LEARNERS),
    )
    @ddt.unpack
    def test_ordering(self, ordering, expected):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), ordering=ordering))
        assert usernames_in(response) == expected

    @ddt.data(('created', ('created', 'id')), ('-username', ('-user__username', 'id')))
    @ddt.unpack
    def test_ordering_breaks_ties_on_id(self, ordering, expected):
        filterset = CourseGradeFilterSet(data={'ordering': ordering}, queryset=CourseEnrollment.objects.all())
        assert filterset.qs.query.order_by == expected

    def test_default_order_is_enrollment_order(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert usernames_in(response) == FIXTURE_LEARNERS

    def test_unknown_ordering_is_refused(self):
        response = self.client.get(course_grade_list_url(ordering='bogus'))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
        )
        assert list(body['errors']) == ['ordering']

    def test_filters_do_not_widen_a_learners_view(self):
        self.client.force_authenticate(self.other_student)
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), username='student'))
        assert response.json()['count'] == 0


class CourseGradeRepresentationTest(CourseGradeTestBase):
    """The default and full representations, and refusal of any other."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_default_rows_carry_no_breakdown(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert all('section_breakdown' not in row for row in response.json()['results'])

    def test_full_view_adds_the_breakdown_to_each_row(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), view='full'))
        rows = response.json()['results']
        assert [row['username'] for row in rows] == FIXTURE_LEARNERS
        homework = rows[0]['section_breakdown'][0]
        assert homework == {
            'category': 'Homework',
            'label': 'HW 01',
            'detail': 'Homework 1 Unreleased - 0% (?/?)',
            'percent': 0.0,
            'usage_key': None,
        }
        summaries = [entry for entry in rows[0]['section_breakdown'] if entry.get('prominent')]
        assert [entry['label'] for entry in summaries] == ['HW Avg', 'Lab Avg', 'Midterm', 'Final']
        dropped = [entry['label'] for entry in rows[0]['section_breakdown'] if 'mark' in entry]
        assert dropped == ['HW 11', 'HW 12', 'Lab 11', 'Lab 12']

    def test_full_view_on_the_member(self):
        response = self.client.get(course_grade_detail_url('student', self.course_key, view='full'))
        assert len(response.json()['section_breakdown']) == 28

    def test_unknown_view_is_refused_on_the_collection(self):
        response = self.client.get(course_grade_list_url(view='minimal'))
        body = assert_error_envelope(
            response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation',
        )
        assert list(body['errors']) == ['view']

    def test_unknown_view_is_refused_on_the_member(self):
        response = self.client.get(course_grade_detail_url('student', self.course_key, view='bogus'))
        assert_error_envelope(response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='validation')

    def test_learner_whose_grade_cannot_be_computed_is_left_out_of_the_page(self):
        real_read = CourseGradeFactory.read

        def read(factory, user, *args, **kwargs):
            if user.username == 'other_student':
                raise RuntimeError('cannot grade')
            return real_read(factory, user, *args, **kwargs)

        with patch.object(CourseGradeFactory, 'read', autospec=True, side_effect=read):
            response = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert usernames_in(response) == ['student', 'program_student', 'program_masters_student']


class CourseGradePagingTest(CourseGradeTestBase):
    """The list pages with the standard envelope and limits."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        for index in range(8):
            self.enroll(UserFactory(username=f'extra_{index}'), self.course_key)

    def test_body_carries_the_seven_page_members(self):
        body = self.client.get(course_grade_list_url(course_key=str(self.course_key))).json()
        assert set(body) == PAGE_FIELDS
        assert (body['count'], body['num_pages'], body['current_page'], body['start']) == (12, 2, 1, 0)
        assert len(body['results']) == 10
        assert body['previous'] is None
        assert body['next'].endswith('page=2')

    def test_second_page(self):
        body = self.client.get(course_grade_list_url(course_key=str(self.course_key), page=2)).json()
        assert (body['current_page'], body['start']) == (2, 10)
        assert [row['username'] for row in body['results']] == ['extra_6', 'extra_7']

    def test_page_size(self):
        body = self.client.get(course_grade_list_url(course_key=str(self.course_key), page_size=5)).json()
        assert (len(body['results']), body['num_pages']) == (5, 3)

    def test_page_size_zero_falls_back_to_the_default(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), page_size=0))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()['results']) == 10

    def test_page_size_is_capped_at_a_hundred(self):
        request = Request(APIRequestFactory().get('/', {'page_size': 101}))
        assert GradePagination().get_page_size(request) == 100

    def test_page_past_the_end_is_not_found(self):
        response = self.client.get(course_grade_list_url(course_key=str(self.course_key), page=3))
        assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')


class CourseGradeParityTest(CourseGradeTestBase):
    """
    The v2 bodies equal the v1 bodies they replace, apart from the declared differences.

    v1's section breakdown is served to global staff only, and its course list
    to global staff or to the learner named, so those callers are used here.
    """

    PAGE_MEMBERS_ADDED = [
        ('count', 'page-number envelope replaces the cursor envelope'),
        ('num_pages', 'page-number envelope replaces the cursor envelope'),
        ('current_page', 'page-number envelope replaces the cursor envelope'),
        ('start', 'page-number envelope replaces the cursor envelope'),
    ]

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        persist_passing_grade(self.other_student, self.course_key)

    def v1_course_grades_url(self, **query):
        url = reverse('grades_api:v1:course_grades', kwargs={'course_id': str(self.course_key)})
        return f'{url}?{"&".join(f"{key}={value}" for key, value in query.items())}' if query else url

    def test_course_list(self):
        legacy = self.client.get(self.v1_course_grades_url())
        new = self.client.get(course_grade_list_url(course_key=str(self.course_key)))
        assert legacy.status_code == new.status_code == status.HTTP_200_OK
        assert_parity(
            legacy.json(), new.json(),
            renames={'course_id': 'course_key'},
            differences=self.PAGE_MEMBERS_ADDED,
        )

    def test_one_learner(self):
        legacy = self.client.get(self.v1_course_grades_url(username='student'))
        new = self.client.get(course_grade_detail_url('student', self.course_key))
        assert legacy.status_code == new.status_code == status.HTTP_200_OK
        assert len(legacy.json()) == 1
        assert_parity(legacy.json()[0], new.json(), renames={'course_id': 'course_key'})

    def test_one_learner_with_a_stored_grade(self):
        legacy = self.client.get(self.v1_course_grades_url(username='other_student'))
        new = self.client.get(course_grade_detail_url('other_student', self.course_key))
        assert (new.json()['percent'], new.json()['letter_grade'], new.json()['passed']) == (0.62, 'Pass', True)
        assert_parity(legacy.json()[0], new.json(), renames={'course_id': 'course_key'})

    def test_one_masters_learner(self):
        legacy = self.client.get(self.v1_course_grades_url(username='program_masters_student'))
        new = self.client.get(course_grade_detail_url('program_masters_student', self.course_key))
        assert_parity(
            legacy.json()[0], new.json(),
            renames={'course_id': 'course_key'},
            differences=[('email', "v1 hid the master's-track email on the single-learner address only")],
        )

    def test_section_breakdown(self):
        url = reverse('grades_api:v1:section_grades_breakdown')
        legacy = self.client.get(url, {'course_id': str(self.course_key)})
        new = self.client.get(course_grade_list_url(course_key=str(self.course_key), view='full'))
        assert legacy.status_code == new.status_code == status.HTTP_200_OK
        assert_parity(
            legacy.json(), new.json(),
            renames={'course_id': 'course_key', 'sequential_id': 'usage_key'},
            differences=[
                *self.PAGE_MEMBERS_ADDED,
                ('results[*].current_grade', 'the whole-number percentage becomes the percent fraction'),
                ('results[*].percent', 'the whole-number percentage becomes the percent fraction'),
                ('results[*].email', 'rows carry the same members with and without the breakdown'),
                ('results[*].letter_grade', 'rows carry the same members with and without the breakdown'),
                ('results[*].section_breakdown[*].usage_key', 'present as null on entries v1 gave no subsection'),
            ],
        )
        for legacy_row, new_row in zip(legacy.json()['results'], new.json()['results'], strict=True):
            assert legacy_row['current_grade'] == int(new_row['percent'] * 100)
            assert [entry.get('sequential_id') for entry in legacy_row['section_breakdown']] == [
                entry['usage_key'] for entry in new_row['section_breakdown']
            ]

    def test_same_row_set_for_the_same_caller(self):
        self.enroll(self.student, self.empty_course.id)
        legacy = self.client.get(reverse('grades_api:v1:section_grades_breakdown'))
        new = self.client.get(course_grade_list_url())
        assert [(row['username'], row['course_id']) for row in legacy.json()['results']] == [
            (row['username'], row['course_key']) for row in new.json()['results']
        ]

    def test_status_codes_that_changed(self):
        """Pins the v1 and v2 statuses where the plan declares they differ."""
        self.client.force_authenticate(self.student)
        assert self.client.get(self.v1_course_grades_url()).status_code == status.HTTP_403_FORBIDDEN
        assert self.client.get(course_grade_list_url(course_key=str(self.course_key))).status_code == 200
        assert self.client.get(self.v1_course_grades_url(username='other_student')).status_code == 403
        assert self.client.get(course_grade_detail_url('other_student', self.course_key)).status_code == 404
        self.client.force_authenticate(self.global_staff)
        unknown = 'course-v1:NoSuch+Course+Run'
        legacy = self.client.get(reverse('grades_api:v1:course_grades', kwargs={'course_id': unknown}))
        assert legacy.status_code == status.HTTP_404_NOT_FOUND
        assert self.client.get(course_grade_list_url(course_key=unknown)).status_code == status.HTTP_200_OK


class CourseGradeReadOnlyTest(ReadOnlyRequestMixin, CourseGradeTestBase):
    """Reading course grades changes nothing and announces nothing."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_list(self):
        with self.assert_nothing_is_written():
            assert self.client.get(course_grade_list_url(course_key=str(self.course_key))).status_code == 200

    def test_list_full_view(self):
        with self.assert_nothing_is_written():
            response = self.client.get(course_grade_list_url(view='full'))
            assert response.status_code == status.HTTP_200_OK

    def test_detail_full_view(self):
        with self.assert_nothing_is_written():
            response = self.client.get(course_grade_detail_url('student', self.course_key, view='full'))
            assert response.status_code == status.HTTP_200_OK

    def test_head(self):
        with self.assert_nothing_is_written():
            assert self.client.head(course_grade_list_url()).status_code == status.HTTP_200_OK
            assert self.client.head(course_grade_detail_url('student', self.course_key)).status_code == 200


class CourseGradeCohortAssignmentTest(CohortedCourseMixin, CourseGradeTestBase):
    """
    On a course whose content groups follow cohorts, the per-subsection
    breakdown assigns a learner who is in no cohort to one, as v1 does.

    Working out which subsections a learner can see asks for the learner's
    content group, and the cohort lookup behind it assigns a cohort to a learner
    who has none, storing the membership and announcing it. Only the breakdown
    walks the content per learner; the default view does not. This pins both
    sides so a change to either is deliberate.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def assert_assigned_once(self, url):
        """Assert the first read of ``url`` assigns the uncohorted learner, and a second read writes nothing."""
        with capture_cohort_assignment() as first:
            assert self.client.get(url).status_code == status.HTTP_200_OK
        assert first.tables == COHORT_ASSIGNMENT_TABLES
        assert first.memberships == [('uncohorted', AUTO_COHORT)]
        assert first.tracking == COHORT_ASSIGNMENT_TRACKING
        assert self.cohort_of(self.uncohorted) == AUTO_COHORT
        with capture_cohort_assignment() as second:
            assert self.client.get(url).status_code == status.HTTP_200_OK
        assert (second.tables, second.memberships, second.tracking) == ([], [], [])

    def assert_not_assigned(self, url):
        with capture_cohort_assignment() as record:
            assert self.client.get(url).status_code == status.HTTP_200_OK
        assert (record.tables, record.memberships, record.tracking) == ([], [], [])
        assert self.cohort_of(self.uncohorted) is None

    def test_full_list_assigns_the_learner(self):
        self.assert_assigned_once(
            course_grade_list_url(course_key=str(self.course_key), username='uncohorted', view='full')
        )

    def test_full_member_assigns_the_learner(self):
        self.assert_assigned_once(course_grade_detail_url('uncohorted', self.course_key, view='full'))

    def test_full_list_assigns_every_learner_on_the_page(self):
        with capture_cohort_assignment() as record:
            response = self.client.get(course_grade_list_url(course_key=str(self.course_key), view='full'))
        assert response.status_code == status.HTTP_200_OK
        expected = [(username, AUTO_COHORT) for username in [*FIXTURE_LEARNERS, 'uncohorted']]
        assert sorted(record.memberships) == sorted(expected)
        assert record.tables == COHORT_ASSIGNMENT_TABLES * len(expected)
        assert record.tracking == COHORT_ASSIGNMENT_TRACKING * len(expected)

    def test_default_list_assigns_nobody(self):
        self.assert_not_assigned(course_grade_list_url(course_key=str(self.course_key)))

    def test_default_member_assigns_nobody(self):
        self.assert_not_assigned(course_grade_detail_url('uncohorted', self.course_key))

    def test_v1_breakdown_assigns_the_learner_too(self):
        url = with_query(
            reverse('grades_api:v1:section_grades_breakdown'),
            course_id=str(self.course_key), username='uncohorted',
        )
        self.assert_assigned_once(url)


class CourseGradeQueryCountTest(CourseGradeTestBase):
    """
    The v2 reads issue no more queries than v1 on the same fixture. The default
    list does not grow with the page; the list with the breakdown grows by a
    fixed number of queries per row, fewer than v1 grows.

    Each address is requested once before it is measured, so one-off lookups
    made by the first request of a test (site, theme, session) count for neither
    side.
    """

    #: Measured on this fixture; a change in any of them is a change in the data path.
    LIST_QUERIES = 13
    FULL_LIST_QUERIES = 40
    DETAIL_QUERIES = 9
    #: Collecting the content each learner can see checks that learner's access:
    #: two enrollment reads, one course-role read, one discussion-role read, one
    #: schedule read and one read of the authorization engine's policy version.
    FULL_LIST_QUERIES_PER_ROW = 6
    V1_FULL_LIST_QUERIES_PER_ROW = 7

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
        url = reverse('grades_api:v1:course_grades', kwargs={'course_id': str(self.course_key)})
        return with_query(url, **query)

    def test_list(self):
        legacy = self.measure(self.v1_url())
        new = self.measure(course_grade_list_url(course_key=str(self.course_key)))
        assert new == self.LIST_QUERIES
        assert new <= legacy

    def test_full_list(self):
        legacy_url = with_query(reverse('grades_api:v1:section_grades_breakdown'), course_id=str(self.course_key))
        legacy = self.measure(legacy_url)
        new = self.measure(course_grade_list_url(course_key=str(self.course_key), view='full'))
        assert new == self.FULL_LIST_QUERIES
        assert new <= legacy

    def test_detail(self):
        legacy = self.measure(self.v1_url(username='student'))
        new = self.measure(course_grade_detail_url('student', self.course_key))
        assert new == self.DETAIL_QUERIES
        assert new <= legacy

    def test_full_list_grows_by_a_fixed_count_per_row(self):
        for index in range(8):
            self.enroll(UserFactory(username=f'extra_{index}'), self.course_key)
        course_key = str(self.course_key)
        five = self.measure(course_grade_list_url(course_key=course_key, view='full', page_size=5))
        ten = self.measure(course_grade_list_url(course_key=course_key, view='full', page_size=10))
        legacy_url = reverse('grades_api:v1:section_grades_breakdown')
        legacy_five = self.measure(with_query(legacy_url, course_id=course_key, page_size=5))
        legacy_ten = self.measure(with_query(legacy_url, course_id=course_key, page_size=10))
        assert (ten - five, legacy_ten - legacy_five) == (
            5 * self.FULL_LIST_QUERIES_PER_ROW, 5 * self.V1_FULL_LIST_QUERIES_PER_ROW,
        )
        assert ten <= legacy_ten

    def test_list_count_does_not_grow_with_the_page_size(self):
        for index in range(8):
            self.enroll(UserFactory(username=f'extra_{index}'), self.course_key)
        five = self.measure(course_grade_list_url(course_key=str(self.course_key), page_size=5))
        ten = self.measure(course_grade_list_url(course_key=str(self.course_key), page_size=10))
        assert five == ten


@override_settings(DEBUG=False)
class CourseGradeUrlTest(CourseGradeTestBase):
    """The addresses, their names, and the not-found body for addresses no route matches."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def test_reverse_literals(self):
        assert reverse('grade_v2:course_grade_list') == '/api/grade/v2/course_grades/'
        assert reverse(
            'grade_v2:course_grade_detail', kwargs={'username': 'u', 'course_key': self.COURSE_KEY},
        ) == f'/api/grade/v2/course_grades/u,{self.COURSE_KEY}/'

    def test_member_address_round_trips_keys_with_plus_signs(self):
        url = course_grade_detail_url('some.user-1', self.course_key)
        self.client.force_authenticate(self.global_staff)
        assert self.client.get(url).status_code == status.HTTP_404_NOT_FOUND
        CourseEnrollmentFactory(user=UserFactory(username='some.user-1'), course_id=self.course_key)
        assert self.client.get(url).status_code == status.HTTP_200_OK

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/course_grades/student,not-a-course-key/',
            '/api/grade/v2/course_grades/student,edX/DemoX/Demo/',
            '/api/grade/v2/course_grades/student/',
            f'/api/grade/v2/course_grades/student,{self.course_key}/extra/',
        ):
            response = self.client.get(path)
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')

    def test_unmatched_addresses_answer_every_method_alike(self):
        path = '/api/grade/v2/course_grades/student,not-a-course-key/'
        for method in ('post', 'put', 'patch', 'delete', 'options'):
            response = getattr(self.client, method)(path)
            assert_error_envelope(response, expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found')
        assert_error_envelope(
            self.client.get(path), expected_status=status.HTTP_404_NOT_FOUND, expected_type_slug='not-found',
        )

    def test_missing_trailing_slash_redirects(self):
        response = self.client.get(f'/api/grade/v2/course_grades/student,{self.course_key}')
        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response['Location'].endswith(f'/api/grade/v2/course_grades/student,{self.course_key}/')

    def test_post_on_the_collection_is_not_allowed(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.post(course_grade_list_url())
        # edx-drf-extensions does not catalog MethodNotAllowed yet, so its type is
        # internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(
            response, expected_status=status.HTTP_405_METHOD_NOT_ALLOWED, expected_type_slug='internal',
        )

    def test_response_that_cannot_be_json_is_not_acceptable(self):
        self.client.force_authenticate(self.global_staff)
        response = self.client.get(course_grade_list_url(), HTTP_ACCEPT='text/html')
        # edx-drf-extensions does not catalog NotAcceptable yet, so its type is
        # internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(response, expected_status=status.HTTP_406_NOT_ACCEPTABLE, expected_type_slug='internal')
