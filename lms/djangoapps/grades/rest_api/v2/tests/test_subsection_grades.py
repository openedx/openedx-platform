"""Tests for the subsection grade endpoints of the Grades API v2."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import ddt
from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from edx_rest_framework_extensions.testing import assert_error_envelope
from opaque_keys.edx.locator import BlockUsageLocator
from pytz import UTC
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.models import AnonymousUserId, CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseLimitedStaffRole, CourseStaffRole
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.models import (
    BlockRecord,
    BlockRecordList,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
)
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    AUTO_COHORT,
    COHORT_ASSIGNMENT_TABLES,
    COHORT_ASSIGNMENT_TRACKING,
    PAGE_FIELDS,
    CohortedCourseMixin,
    ReadOnlyRequestMixin,
    capture_cohort_assignment,
    count_queries,
    subsection_grade_detail_url,
    subsection_grade_override_history_url,
    warm_anonymous_id,
    warm_read_caches,
    with_query,
)
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from xmodule.modulestore.tests.factories import BlockFactory

STORED_SCORES = {'earned_all': 6.0, 'possible_all': 12.0, 'earned_graded': 6.0, 'possible_graded': 8.0}
OVERRIDE = {
    'earned_all_override': 10.0,
    'possible_all_override': 12.0,
    'earned_graded_override': 7.0,
    'possible_graded_override': 8.0,
}


def store_subsection_grade(user, usage_key, scores=None):
    """Store a grade of ``scores`` for ``user`` on the subsection ``usage_key``."""
    scores = scores or STORED_SCORES
    problem = BlockUsageLocator(usage_key.course_key, 'problem', 'problem_for_grade')
    return PersistentSubsectionGrade.update_or_create_grade(
        user_id=user.id,
        usage_key=usage_key,
        course_version='deadbeef',
        subtree_edited_timestamp=datetime(2016, 8, 1, tzinfo=UTC),
        visible_blocks=BlockRecordList(
            [BlockRecord(locator=problem, weight=1, raw_possible=scores['possible_all'], graded=True)],
            usage_key.course_key,
        ),
        first_attempted=datetime(2000, 1, 1, tzinfo=UTC),
        **scores,
    )


def override_grade(grade, by, **values):
    """Record an override of ``grade`` made by ``by``, as the gradebook does."""
    return PersistentSubsectionGradeOverride.update_or_create_override(
        requesting_user=by,
        subsection_grade_model=grade,
        feature=GradeOverrideFeatureEnum.gradebook,
        system=GradeOverrideFeatureEnum.gradebook,
        **(values or OVERRIDE),
    )


class SubsectionGradeTestBase(GradeViewTestMixin, APITestCase):
    """Fixtures shared by the subsection grade endpoint tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chapter = cls.store.get_course(cls.course_key).get_children()[0]
        cls.hidden_subsection = BlockFactory.create(
            parent_location=chapter.location,
            category='sequential',
            graded=True,
            format='Homework',
            visible_to_staff_only=True,
            display_name='Hidden homework',
        )

    def setUp(self):
        super().setUp()
        warm_read_caches(self.course_key)
        self.homework = self.store.get_course(self.course_key).get_children()[0].get_children()[0].location

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)

    def user_with_role(self, role, course_key=None):
        """Return a new user holding ``role`` on ``course_key``, by default the test course."""
        user = UserFactory(password=self.password)
        role(course_key or self.course_key).add_users(user)
        return user

    def urls(self, username='student', usage_key=None):
        """Return the grade and history addresses of ``username`` on a subsection."""
        usage_key = usage_key or self.homework
        return (
            subsection_grade_detail_url(username, usage_key),
            subsection_grade_override_history_url(username, usage_key),
        )


@ddt.ddt
class SubsectionGradeAccessTest(SubsectionGradeTestBase):
    """Who may read a subsection grade and its override history."""

    def test_anonymous_caller_is_refused(self):
        for url in self.urls():
            assert_error_envelope(self.client.get(url), expected_status=401, expected_type_slug='authn')

    @ddt.data('student', 'other_student')
    def test_learners_are_refused_even_for_their_own_grade(self, caller):
        self.login(getattr(self, caller))
        for url in self.urls('student'):
            assert_error_envelope(self.client.get(url), expected_status=403, expected_type_slug='authz')

    def test_staff_of_another_course_are_refused(self):
        self.login(self.user_with_role(CourseStaffRole, self.empty_course.id))
        for url in self.urls():
            assert_error_envelope(self.client.get(url), expected_status=403, expected_type_slug='authz')

    @ddt.data(CourseStaffRole, CourseInstructorRole, CourseLimitedStaffRole)
    def test_course_team_is_admitted(self, role):
        self.login(self.user_with_role(role))
        grade_url, history_url = self.urls()
        assert self.client.get(grade_url).json()['username'] == 'student'
        assert self.client.get(history_url).json()['results'] == []

    def test_global_staff_are_admitted(self):
        self.login(self.global_staff)
        for url in self.urls():
            assert self.client.get(url).status_code == status.HTTP_200_OK

    @ddt.data('global_staff_username', 'no_such_user')
    def test_learner_not_enrolled_is_not_found(self, username):
        self.login(self.global_staff)
        username = self.global_staff.username if username == 'global_staff_username' else username
        for url in self.urls(username):
            assert_error_envelope(self.client.get(url), expected_status=404, expected_type_slug='not-found')

    def test_caller_without_access_cannot_learn_who_is_enrolled(self):
        self.login(self.student)
        for url in self.urls('no_such_user'):
            assert_error_envelope(self.client.get(url), expected_status=403, expected_type_slug='authz')

    def test_inactive_enrollment_is_readable(self):
        CourseEnrollment.objects.filter(user=self.student, course_id=self.course_key).update(is_active=False)
        self.login(self.global_staff)
        for url in self.urls():
            assert self.client.get(url).status_code == status.HTTP_200_OK


class SubsectionGradeCcxTest(CcxTestCase, APITestCase):
    """
    CCX coaches may read the subsection grades of their learners.

    v1 refuses them, because its subsection endpoint checks course author
    access without the CCX branch its gradebook endpoints have.
    """

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.learner = UserFactory()
        CourseEnrollmentFactory(user=self.learner, course_id=self.ccx_key)
        block = self.sequentials[0].location
        self.usage_key = self.ccx_key.make_usage_key(block.block_type, block.block_id)
        store_subsection_grade(self.learner, self.usage_key)

    def get(self, caller):
        """Return the grade and history responses ``caller`` gets for the CCX learner."""
        self.client.force_authenticate(caller)
        return [
            self.client.get(url)
            for url in (
                subsection_grade_detail_url(self.learner.username, self.usage_key),
                subsection_grade_override_history_url(self.learner.username, self.usage_key),
            )
        ]

    def test_coach_is_admitted(self):
        grade, history = self.get(self.coach)
        assert (grade.status_code, history.status_code) == (status.HTTP_200_OK, status.HTTP_200_OK)
        assert grade.json()['original_grade'] == STORED_SCORES
        assert grade.json()['course_key'] == str(self.ccx_key)

    def test_ccx_staff_are_admitted(self):
        staff = UserFactory()
        CourseStaffRole(self.ccx_key).add_users(staff)
        assert [response.status_code for response in self.get(staff)] == [200, 200]

    def test_outsider_is_refused(self):
        for response in self.get(UserFactory()):
            assert_error_envelope(response, expected_status=403, expected_type_slug='authz')

    def test_coach_is_refused_when_custom_courses_are_off(self):
        with override_settings(CUSTOM_COURSES_EDX=False):
            responses = self.get(self.coach)
        for response in responses:
            assert_error_envelope(response, expected_status=403, expected_type_slug='authz')

    def test_v1_refuses_the_coach(self):
        self.client.force_authenticate(self.coach)
        url = reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': str(self.usage_key)})
        assert self.client.get(with_query(url, user_id=self.learner.id)).status_code == status.HTTP_403_FORBIDDEN


class SubsectionGradeContentTest(SubsectionGradeTestBase):
    """The grade returned for each kind of stored state."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def grade(self, usage_key=None):
        return self.client.get(subsection_grade_detail_url('student', usage_key or self.homework))

    def test_stored_grade_without_an_override(self):
        store_subsection_grade(self.student, self.homework)
        assert self.grade().json() == {
            'username': 'student',
            'usage_key': str(self.homework),
            'course_key': str(self.course_key),
            'original_grade': STORED_SCORES,
            'override': None,
        }

    def test_stored_grade_with_an_override(self):
        override_grade(store_subsection_grade(self.student, self.homework), by=self.global_staff)
        body = self.grade().json()
        assert (body['original_grade'], body['override']) == (STORED_SCORES, OVERRIDE)

    def test_grade_not_stored_is_computed_and_not_stored(self):
        body = self.grade().json()
        assert (body['original_grade'], body['override']) == (
            {'earned_all': 0.0, 'possible_all': 0.0, 'earned_graded': 0.0, 'possible_graded': 0.0}, None,
        )
        assert not PersistentSubsectionGrade.objects.filter(user_id=self.student.id).exists()

    def test_computed_grade_reports_each_total(self):
        computed = MagicMock(all_total=MagicMock(earned=1, possible=2), graded_total=MagicMock(earned=3, possible=4))
        with patch(
            'lms.djangoapps.grades.rest_api.v2.services.SubsectionGradeFactory.create', return_value=computed,
        ) as create:
            body = self.grade().json()
        assert body['original_grade'] == {
            'earned_all': 1.0, 'possible_all': 2.0, 'earned_graded': 3.0, 'possible_graded': 4.0,
        }
        assert create.call_args.kwargs == {'read_only': True, 'force_calculate': True}

    def test_subsection_the_learner_cannot_see_is_unavailable(self):
        assert_error_envelope(
            self.grade(self.hidden_subsection.location),
            expected_status=404, expected_type_slug='grades/subsection-unavailable',
        )

    def test_block_the_course_does_not_have_is_not_found(self):
        missing = BlockUsageLocator(self.course_key, 'sequential', 'no_such_block')
        assert_error_envelope(self.grade(missing), expected_status=404, expected_type_slug='not-found')


class OverrideHistoryTest(SubsectionGradeTestBase):
    """The override history of a subsection grade."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        self.grade = store_subsection_grade(self.student, self.homework)
        self.editor = UserFactory(username='editor')
        for earned in (1.0, 2.0, 3.0):
            override_grade(self.grade, by=self.editor, **{**OVERRIDE, 'earned_graded_override': earned})

    def history(self, **query):
        response = self.client.get(subsection_grade_override_history_url('student', self.homework, **query))
        assert response.status_code == status.HTTP_200_OK, response.content
        return response.json()

    def test_records_newest_first(self):
        body = self.history()
        assert set(body) == PAGE_FIELDS
        assert [record['earned_graded_override'] for record in body['results']] == [3.0, 2.0, 1.0]
        assert [record['history_type'] for record in body['results']] == ['~', '~', '+']
        newest = body['results'][0]
        assert set(newest) == {
            'history_id', 'history_date', 'history_type', 'history_user', 'earned_all_override',
            'possible_all_override', 'earned_graded_override', 'possible_graded_override',
            'override_reason', 'system',
        }
        assert (newest['history_user'], newest['system'], newest['override_reason']) == (
            'editor', GradeOverrideFeatureEnum.gradebook, None,
        )

    def test_ordering(self):
        oldest_first = self.history(ordering='history_date')['results']
        assert [record['earned_graded_override'] for record in oldest_first] == [1.0, 2.0, 3.0]

    def test_unknown_ordering_is_refused(self):
        response = self.client.get(subsection_grade_override_history_url('student', self.homework, ordering='bogus'))
        body = assert_error_envelope(response, expected_status=400, expected_type_slug='validation')
        assert list(body['errors']) == ['ordering']

    def test_paging(self):
        body = self.history(page_size=2, page=2)
        assert (body['count'], body['num_pages'], body['current_page']) == (3, 2, 2)
        assert [record['earned_graded_override'] for record in body['results']] == [1.0]

    def test_other_grades_are_left_out(self):
        override_grade(store_subsection_grade(self.other_student, self.homework), by=self.editor)
        second_homework = self.store.get_course(self.course_key).get_children()[0].get_children()[1].location
        override_grade(store_subsection_grade(self.student, second_homework), by=self.editor)
        assert self.history()['count'] == 3

    def test_learner_without_a_stored_grade_has_no_history(self):
        response = self.client.get(subsection_grade_override_history_url('other_student', self.homework))
        assert (response.status_code, response.json()['count']) == (status.HTTP_200_OK, 0)


class SubsectionGradeParityTest(SubsectionGradeTestBase):
    """The v2 bodies equal the v1 body they replace, apart from the declared differences."""

    GRADE_DIFFERENCES = [
        ('success', 'errors are statuses, not a flag in a 200 body'),
        ('history', 'the history is its own paginated list'),
        ('user_id', 'database ids are not published'),
        ('username', 'the learner is named by username'),
    ]
    HISTORY_DIFFERENCES = [
        ('[*].created', 'the creation date of the override is not a change record field'),
        ('[*].grade_id', 'database ids are not published'),
        ('[*].history_user_id', 'database ids are not published'),
        ('[*].id', 'database ids are not published'),
    ]

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def v1(self, usage_key=None, **query):
        url = reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': str(usage_key or self.homework)})
        response = self.client.get(with_query(url, user_id=self.student.id, **query))
        assert response.status_code == status.HTTP_200_OK
        return response.json()

    def v2(self, usage_key=None):
        response = self.client.get(subsection_grade_detail_url('student', usage_key or self.homework))
        return response

    def test_stored_grade_with_an_override(self):
        grade = store_subsection_grade(self.student, self.homework)
        override_grade(grade, by=self.global_staff)
        assert_parity(
            self.v1(), self.v2().json(),
            renames={'subsection_id': 'usage_key', 'course_id': 'course_key'},
            differences=self.GRADE_DIFFERENCES,
        )

    def test_grade_not_stored(self):
        assert_parity(
            self.v1(), self.v2().json(),
            renames={'subsection_id': 'usage_key', 'course_id': 'course_key'},
            differences=self.GRADE_DIFFERENCES,
        )

    def test_unavailable_subsection_is_a_200_in_v1_and_a_404_in_v2(self):
        legacy = self.v1(self.hidden_subsection.location)
        assert (legacy['success'], legacy['original_grade']['possible_all']) == (False, 0.0)
        assert self.v2(self.hidden_subsection.location).status_code == status.HTTP_404_NOT_FOUND

    def test_history(self):
        grade = store_subsection_grade(self.student, self.homework)
        for earned in (1.0, 2.0):
            override_grade(grade, by=self.global_staff, **{**OVERRIDE, 'earned_graded_override': earned})
        legacy = self.v1()['history']
        new = self.client.get(
            subsection_grade_override_history_url('student', self.homework, ordering='history_date'),
        ).json()['results']
        assert_parity(legacy, new, differences=self.HISTORY_DIFFERENCES)

    def test_limited_history_is_the_same_records_newest_first(self):
        grade = store_subsection_grade(self.student, self.homework)
        for earned in range(7):
            override_grade(grade, by=self.global_staff, **{**OVERRIDE, 'earned_graded_override': float(earned)})
        legacy = [record['history_id'] for record in self.v1(history_record_limit=5)['history']]
        new = [
            record['history_id'] for record in self.client.get(
                subsection_grade_override_history_url('student', self.homework, page_size=5),
            ).json()['results']
        ]
        assert len(new) == 5
        assert new == list(reversed(legacy))


class SubsectionGradeReadOnlyTest(ReadOnlyRequestMixin, SubsectionGradeTestBase):
    """Reading a subsection grade or its history changes nothing and announces nothing."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_stored_grade(self):
        override_grade(store_subsection_grade(self.student, self.homework), by=self.global_staff)
        with self.assert_nothing_is_written():
            for url in self.urls():
                assert self.client.get(url).status_code == status.HTTP_200_OK

    def test_computed_grade(self):
        warm_anonymous_id(self.student, self.course_key)
        with self.assert_nothing_is_written():
            assert self.client.get(self.urls()[0]).status_code == status.HTTP_200_OK

    def test_head(self):
        warm_anonymous_id(self.student, self.course_key)
        with self.assert_nothing_is_written():
            for url in self.urls():
                assert self.client.head(url).status_code == status.HTTP_200_OK


class ComputedGradeAnonymousIdTest(SubsectionGradeTestBase):
    """
    The first computed grade of a learner in a course stores the learner's anonymous id there.

    Computing a grade the learner has no stored grade for reads their
    submissions, and the shared lookup of the anonymous id those are keyed by
    stores the id the first time it is asked for it. v1 behaves the same. This
    pins the behaviour so a change to either side is deliberate.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def anonymous_ids(self):
        return AnonymousUserId.objects.filter(user=self.student, course_id=self.course_key).count()

    def test_first_computed_read_stores_the_id_and_later_reads_do_not(self):
        assert self.anonymous_ids() == 0
        assert self.client.get(self.urls()[0]).status_code == status.HTTP_200_OK
        assert self.anonymous_ids() == 1
        assert self.client.get(self.urls()[0]).status_code == status.HTTP_200_OK
        assert self.anonymous_ids() == 1

    def test_stored_grade_read_stores_nothing(self):
        store_subsection_grade(self.student, self.homework)
        assert self.client.get(self.urls()[0]).status_code == status.HTTP_200_OK
        assert self.anonymous_ids() == 0

    def test_v1_does_the_same(self):
        url = reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': str(self.homework)})
        assert self.client.get(with_query(url, user_id=self.student.id)).status_code == status.HTTP_200_OK
        assert self.anonymous_ids() == 1


class SubsectionGradeCohortAssignmentTest(CohortedCourseMixin, SubsectionGradeTestBase):
    """
    On a course whose content groups follow cohorts, the first computed
    subsection grade of a learner who is in no cohort assigns them to one, as
    v1 does; a stored grade and the override history do not.

    Computing a grade collects the course content the learner can see, which
    asks for the learner's content group, and the cohort lookup behind it
    assigns a cohort to a learner who has none, storing the membership and
    announcing it. This pins both sides so a change to either is deliberate.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def capture(self, url):
        with capture_cohort_assignment() as record:
            assert self.client.get(url).status_code == status.HTTP_200_OK
        return record

    def assert_assigned_once(self, url):
        first = self.capture(url)
        assert first.tables == COHORT_ASSIGNMENT_TABLES
        assert first.memberships == [('uncohorted', AUTO_COHORT)]
        assert first.tracking == COHORT_ASSIGNMENT_TRACKING
        assert self.cohort_of(self.uncohorted) == AUTO_COHORT
        second = self.capture(url)
        assert (second.tables, second.memberships, second.tracking) == ([], [], [])

    def test_computed_grade_assigns_the_learner(self):
        self.assert_assigned_once(self.urls('uncohorted')[0])

    def assert_not_assigned(self, url):
        record = self.capture(url)
        assert (record.tables, record.memberships, record.tracking) == ([], [], [])
        assert self.cohort_of(self.uncohorted) is None

    def test_stored_grade_assigns_nobody(self):
        store_subsection_grade(self.uncohorted, self.homework)
        self.assert_not_assigned(self.urls('uncohorted')[0])

    def test_history_assigns_nobody(self):
        override_grade(store_subsection_grade(self.uncohorted, self.homework), by=self.global_staff)
        self.assert_not_assigned(self.urls('uncohorted')[1])

    def test_v1_assigns_the_learner_too(self):
        url = reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': str(self.homework)})
        self.assert_assigned_once(with_query(url, user_id=self.uncohorted.id))


class SubsectionGradeQueryCountTest(SubsectionGradeTestBase):
    """
    Each v2 read issues no more queries than the one v1 read it replaces, on the
    same stored grade with three override records.

    Each address is requested once before it is measured.
    """

    #: Measured on this fixture; a change is a change in the data path.
    GRADE_QUERIES = 9
    HISTORY_QUERIES = 10
    V1_QUERIES = 12

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)
        grade = store_subsection_grade(self.student, self.homework)
        for earned in (1.0, 2.0, 3.0):
            override_grade(grade, by=self.global_staff, **{**OVERRIDE, 'earned_graded_override': earned})

    def measure(self, url):
        assert self.client.get(url).status_code == status.HTTP_200_OK
        response, count = count_queries(self.client, url)
        assert response.status_code == status.HTTP_200_OK
        return count

    def test_counts(self):
        v1_url = reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': str(self.homework)})
        legacy = self.measure(with_query(v1_url, user_id=self.student.id))
        grade_url, history_url = self.urls()
        grade, history = self.measure(grade_url), self.measure(history_url)
        assert (grade, history, legacy) == (self.GRADE_QUERIES, self.HISTORY_QUERIES, self.V1_QUERIES)
        assert max(grade, history) <= legacy

    def test_history_count_does_not_grow_with_the_page_size(self):
        _, history_url = self.urls()
        assert self.measure(with_query(history_url, page_size=1)) == self.measure(with_query(history_url, page_size=3))


@override_settings(DEBUG=False)
class SubsectionGradeUrlTest(SubsectionGradeTestBase):
    """The addresses, their names, and the not-found body for addresses no route matches."""

    USAGE_KEY = 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@basic_questions'

    def test_reverse_literals(self):
        kwargs = {'username': 'u', 'usage_key': self.USAGE_KEY}
        assert reverse('grade_v2:subsection_grade_detail', kwargs=kwargs) == (
            f'/api/grade/v2/subsection_grades/u,{self.USAGE_KEY}/'
        )
        assert reverse('grade_v2:subsection_grade_override_history_list', kwargs=kwargs) == (
            f'/api/grade/v2/subsection_grades/u,{self.USAGE_KEY}/override_history_records/'
        )

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/subsection_grades/student,not-a-usage-key/',
            '/api/grade/v2/subsection_grades/student,i4x://edX/DemoX/sequential/basic/',
            '/api/grade/v2/subsection_grades/student/',
            '/api/grade/v2/subsection_grades/student,not-a-usage-key/override_history_records/',
            f'/api/grade/v2/subsection_grades/student,{self.homework}/override_history_records/extra/',
        ):
            response = self.client.get(path)
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')

    def test_missing_trailing_slash_redirects(self):
        response = self.client.get(f'/api/grade/v2/subsection_grades/student,{self.homework}')
        assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
        assert response['Location'].endswith(f'/api/grade/v2/subsection_grades/student,{self.homework}/')
