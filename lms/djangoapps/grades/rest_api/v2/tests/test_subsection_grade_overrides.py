"""Tests for the subsection grade override endpoint of the Grades API v2."""

import json
from datetime import timedelta
from unittest.mock import patch

import ddt
from ccx_keys.locator import CCXLocator
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
)
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.grades.config.waffle import ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, WRITABLE_GRADEBOOK
from lms.djangoapps.grades.constants import GradeOverrideFeatureEnum
from lms.djangoapps.grades.models import (
    PersistentCourseGrade,
    PersistentSubsectionGrade,
    PersistentSubsectionGradeOverride,
)
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.serializers import MAX_OVERRIDES
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    GRADE_READ_SCOPE,
    count_post_queries,
    subsection_grade_override_list_url,
    token_header,
    warm_cohort_settings,
    warm_read_caches,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

MISSING_COURSE_KEY = 'course-v1:NoSuch+Course+Run'
OVERRIDE_VALUES = {
    'earned_all_override': 3.0,
    'possible_all_override': 4.0,
    'earned_graded_override': 2.0,
    'possible_graded_override': 4.0,
}


class OverrideTestBase(GradeViewTestMixin, APITestCase):
    """Fixtures shared by the override endpoint tests."""

    def setUp(self):
        super().setUp()
        warm_read_caches(self.course_key)
        warm_cohort_settings(self.course_key)
        subsections = self.store.get_course(self.course_key).get_children()[0].get_children()
        self.homework, self.second_homework = subsections[0], subsections[1]

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)

    def user_with_role(self, role):
        """Return a new user holding ``role`` on the test course."""
        user = UserFactory(password=self.password)
        role(self.course_key).add_users(user)
        return user

    def item(self, username='student', usage_key=None, **values):
        """Return one request item overriding ``username``'s grade on a subsection."""
        return {
            'username': username,
            'usage_key': str(usage_key or self.homework.location),
            **OVERRIDE_VALUES,
            **values,
        }

    def post(self, *items, course_key=None, client=None):
        """Send ``items`` as one batch and return the response."""
        return (client or self.client).post(
            subsection_grade_override_list_url(course_key or self.course_key),
            data=json.dumps({'overrides': list(items)}),
            content_type='application/json',
        )

    def stored_overrides(self):
        """Return ``{(username, usage key): override values}`` for every override in the database."""
        return {
            (override.grade.user_id, str(override.grade.usage_key)): {
                name: getattr(override, name) for name in (*OVERRIDE_VALUES, 'override_reason', 'system')
            }
            for override in PersistentSubsectionGradeOverride.objects.select_related('grade')
        }

    def set_course_end(self, end):
        """Set the end date the course overview records."""
        CourseOverview.objects.filter(id=self.course_key).update(end=end)


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class OverrideAccessTest(OverrideTestBase):
    """Who may record overrides while the writable gradebook is on and grades are not frozen."""

    def test_anonymous_caller_is_refused(self):
        assert_error_envelope(self.post(self.item()), expected_status=401, expected_type_slug='authn')
        assert self.stored_overrides() == {}

    @ddt.data('student', 'other_student')
    def test_learners_are_refused(self, username):
        self.login(getattr(self, username))
        assert_error_envelope(self.post(self.item()), expected_status=403, expected_type_slug='authz')
        assert self.stored_overrides() == {}

    def test_staff_of_another_course_are_refused(self):
        other_staff = UserFactory(password=self.password)
        CourseStaffRole(self.empty_course.id).add_users(other_staff)
        self.login(other_staff)
        assert_error_envelope(self.post(self.item()), expected_status=403, expected_type_slug='authz')

    @ddt.data(CourseStaffRole, CourseInstructorRole, CourseLimitedStaffRole)
    def test_course_team_is_admitted(self, role):
        self.login(self.user_with_role(role))
        response = self.post(self.item())
        assert response.status_code == status.HTTP_200_OK, response.content
        assert list(self.stored_overrides()) == [(self.student.id, str(self.homework.location))]

    def test_global_staff_are_admitted(self):
        self.login(self.global_staff)
        assert self.post(self.item()).status_code == status.HTTP_200_OK

    def test_caller_without_access_cannot_learn_whether_a_course_exists(self):
        self.login(self.student)
        assert_error_envelope(
            self.post(self.item(), course_key=MISSING_COURSE_KEY), expected_status=403, expected_type_slug='authz',
        )

    def test_missing_course_is_not_found_for_a_caller_with_access(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.post(self.item(), course_key=MISSING_COURSE_KEY), expected_status=404, expected_type_slug='not-found',
        )

    def test_session_caller_without_a_csrf_token_is_refused(self):
        client = APIClient(enforce_csrf_checks=True)
        client.login(username=self.global_staff.username, password=self.password)
        assert_error_envelope(self.post(self.item(), client=client), expected_status=403, expected_type_slug='authz')
        assert self.stored_overrides() == {}

    def test_reading_the_collection_is_not_allowed(self):
        self.login(self.global_staff)
        response = self.client.get(subsection_grade_override_list_url(self.course_key))
        # edx-drf-extensions does not catalog MethodNotAllowed yet, so its type is
        # internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(
            response, expected_status=status.HTTP_405_METHOD_NOT_ALLOWED, expected_type_slug='internal',
        )

    @ddt.data((), (GRADE_READ_SCOPE,))
    def test_restricted_token_is_refused_even_for_global_staff(self, scopes):
        headers = token_header(
            self.global_staff, scopes=scopes, is_restricted=True, filters=[f'content_org:{self.course_key.org}'],
        )
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key),
            data=json.dumps({'overrides': [self.item()]}), content_type='application/json', **headers,
        )
        assert_error_envelope(response, expected_status=403, expected_type_slug='authz')
        assert self.stored_overrides() == {}

    def test_unrestricted_token_for_global_staff_is_admitted(self):
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key),
            data=json.dumps({'overrides': [self.item()]}), content_type='application/json',
            **token_header(self.global_staff),
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert list(self.stored_overrides()) == [(self.student.id, str(self.homework.location))]


@override_waffle_flag(WRITABLE_GRADEBOOK, active=False)
class OverrideGradebookDisabledTest(OverrideTestBase):
    """A course with the writable gradebook switched off."""

    def test_caller_with_access_is_refused_with_the_feature_type(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.post(self.item()), expected_status=403, expected_type_slug='grades/writable-gradebook-disabled',
        )
        assert self.stored_overrides() == {}

    def test_caller_without_access_cannot_learn_the_setting(self):
        self.login(self.student)
        assert_error_envelope(self.post(self.item()), expected_status=403, expected_type_slug='authz')


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class OverrideFrozenGradesTest(OverrideTestBase):
    """Grades freeze a fixed number of days after the course ends, when freezing is switched on."""

    def setUp(self):
        super().setUp()
        self.login(self.global_staff)

    @ddt.data(
        (True, -60, True),
        (True, 60, False),
        (True, None, False),
        (False, -60, False),
    )
    @ddt.unpack
    def test_freeze(self, enforced, end_in_days, frozen):
        self.set_course_end(None if end_in_days is None else timezone.now() + timedelta(days=end_in_days))
        with override_waffle_flag(ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, active=enforced):
            response = self.post(self.item())
        if frozen:
            assert_error_envelope(response, expected_status=403, expected_type_slug='grades/grades-frozen')
            assert self.stored_overrides() == {}
        else:
            assert response.status_code == status.HTTP_200_OK, response.content
            assert len(self.stored_overrides()) == 1

    def test_caller_without_access_cannot_learn_that_grades_are_frozen(self):
        self.set_course_end(timezone.now() - timedelta(days=60))
        self.login(self.student)
        with override_waffle_flag(ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, active=True):
            assert_error_envelope(self.post(self.item()), expected_status=403, expected_type_slug='authz')


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class OverrideCcxTest(CcxTestCase, APITestCase):
    """Recording overrides in a CCX course."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.learner = UserFactory()
        CourseEnrollmentFactory(user=self.learner, course_id=self.ccx_key)
        block = self.sequentials[0].location
        self.usage_key = self.ccx_key.make_usage_key(block.block_type, block.block_id)

    def post(self):
        return self.client.post(
            subsection_grade_override_list_url(self.ccx_key),
            data=json.dumps({'overrides': [
                {'username': self.learner.username, 'usage_key': str(self.usage_key), **OVERRIDE_VALUES},
            ]}),
            content_type='application/json',
        )

    def test_coach_records_an_override(self):
        self.client.force_authenticate(self.coach)
        response = self.post()
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json()['overrides'][0]['usage_key'] == str(self.usage_key)
        grade = PersistentSubsectionGrade.read_grade(self.learner.id, self.usage_key)
        assert grade.override.earned_graded_override == OVERRIDE_VALUES['earned_graded_override']

    def test_outsider_is_refused(self):
        self.client.force_authenticate(UserFactory())
        assert_error_envelope(self.post(), expected_status=403, expected_type_slug='authz')
        assert not PersistentSubsectionGradeOverride.objects.exists()


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class OverrideWriteTest(OverrideTestBase):
    """What a valid batch records and returns."""

    def setUp(self):
        super().setUp()
        self.login(self.global_staff)

    def test_batch_is_recorded_and_returned_in_order(self):
        response = self.post(
            self.item(comment='regraded'),
            self.item(username='other_student', usage_key=self.second_homework.location, earned_graded_override=1.0),
        )
        assert response.status_code == status.HTTP_200_OK, response.content
        assert response.json() == {'overrides': [
            {'username': 'student', 'usage_key': str(self.homework.location), **OVERRIDE_VALUES, 'comment': 'regraded'},
            {
                'username': 'other_student',
                'usage_key': str(self.second_homework.location),
                **OVERRIDE_VALUES,
                'earned_graded_override': 1.0,
                'comment': None,
            },
        ]}
        assert self.stored_overrides() == {
            (self.student.id, str(self.homework.location)): {
                **OVERRIDE_VALUES, 'override_reason': 'regraded', 'system': GradeOverrideFeatureEnum.gradebook,
            },
            (self.other_student.id, str(self.second_homework.location)): {
                **OVERRIDE_VALUES,
                'earned_graded_override': 1.0,
                'override_reason': None,
                'system': GradeOverrideFeatureEnum.gradebook,
            },
        }

    def test_values_left_out_keep_the_computed_grade(self):
        response = self.post(
            {'username': 'student', 'usage_key': str(self.homework.location), 'earned_graded_override': 1.0},
        )
        assert response.json()['overrides'][0] == {
            'username': 'student',
            'usage_key': str(self.homework.location),
            'earned_all_override': 0.0,
            'possible_all_override': 0.0,
            'earned_graded_override': 1.0,
            'possible_graded_override': 0.0,
            'comment': None,
        }

    def test_history_names_the_caller(self):
        self.post(self.item())
        history = PersistentSubsectionGradeOverride.history.get()
        assert (history.history_user, history.history_type) == (self.global_staff, '+')

    def test_null_clears_an_earlier_override_of_one_value(self):
        self.post(self.item())
        response = self.post(self.item(**{**OVERRIDE_VALUES, 'earned_graded_override': None}))
        assert response.json()['overrides'][0]['earned_graded_override'] is None
        stored, = self.stored_overrides().values()
        assert stored['earned_graded_override'] is None
        assert stored['possible_graded_override'] == OVERRIDE_VALUES['possible_graded_override']

    def test_the_course_grade_is_recomputed(self):
        """Half marks on one of the twelve homeworks, two of which are dropped, is 0.75% of the course."""
        assert not PersistentCourseGrade.objects.filter(user_id=self.student.id).exists()
        self.post(self.item())
        assert PersistentCourseGrade.read(self.student.id, self.course_key).percent_grade == 0.01

    def test_grades_are_recomputed_after_every_override_is_stored(self):
        stored_when_recomputed = []

        def recompute(**kwargs):
            stored_when_recomputed.append(PersistentSubsectionGradeOverride.objects.count())

        with patch('lms.djangoapps.grades.rest_api.v2.services.recalculate_subsection_grade_v3.apply',
                   side_effect=recompute):
            self.post(self.item(), self.item(usage_key=self.second_homework.location))
        assert stored_when_recomputed == [2, 2]

    def test_failure_while_recomputing_keeps_every_override_and_answers_500(self):
        with patch(
            'lms.djangoapps.grades.rest_api.v2.services.recalculate_subsection_grade_v3.apply',
            side_effect=RuntimeError('recompute exploded'),
        ) as recompute:
            response = self.post(self.item(), self.item(username='other_student'))
        body = assert_error_envelope(response, expected_status=500, expected_type_slug='internal')
        assert 'exploded' not in json.dumps(body)
        assert recompute.call_count == 1
        assert set(self.stored_overrides()) == {
            (self.student.id, str(self.homework.location)),
            (self.other_student.id, str(self.homework.location)),
        }
        assert not PersistentCourseGrade.objects.filter(user_id=self.student.id).exists()

        retried = self.post(self.item(), self.item(username='other_student'))
        assert retried.status_code == status.HTTP_200_OK, retried.content
        assert PersistentCourseGrade.read(self.student.id, self.course_key).percent_grade == 0.01
        assert PersistentCourseGrade.read(self.other_student.id, self.course_key).percent_grade == 0.01

    def test_a_later_item_for_the_same_grade_wins(self):
        self.post(self.item(), self.item(earned_graded_override=4.0))
        stored, = self.stored_overrides().values()
        assert stored['earned_graded_override'] == 4.0

    def test_inactive_enrollment_may_still_be_overridden(self):
        self.student.courseenrollment_set.filter(course_id=self.course_key).update(is_active=False)
        assert self.post(self.item()).status_code == status.HTTP_200_OK


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@ddt.ddt
class OverrideValidationTest(OverrideTestBase):
    """A batch with any invalid item records nothing."""

    def setUp(self):
        super().setUp()
        self.login(self.global_staff)

    def refused(self, response):
        """Return the ``errors`` member of a validation error body."""
        return assert_error_envelope(response, expected_status=400, expected_type_slug='validation')['errors']

    def test_invalid_second_item_leaves_the_first_unrecorded(self):
        stranger = UserFactory()
        grades_before = PersistentSubsectionGrade.objects.count()
        with patch('lms.djangoapps.grades.rest_api.v2.services.recalculate_subsection_grade_v3.apply') as recompute:
            response = self.post(
                self.item(), self.item(username=stranger.username), self.item(username='other_student'),
            )
        assert self.refused(response) == {
            'overrides[1].username': ['No learner with this username is enrolled in this course.'],
        }
        assert self.stored_overrides() == {}
        assert PersistentSubsectionGrade.objects.count() == grades_before
        assert recompute.call_args_list == []

    def test_errors_name_every_failed_item(self):
        response = self.post(
            self.item(username='nobody'),
            self.item(),
            self.item(usage_key='not-a-key'),
        )
        assert self.refused(response) == {
            'overrides[0].username': ['No learner with this username is enrolled in this course.'],
            'overrides[2].usage_key': ['Not a valid usage key.'],
        }

    @ddt.data(
        'i4x://edX/DemoX/sequential/basic',
        'block-v1:Other+Course+Run+type@sequential+block@homework',
        'block-v1:{org}+{course}+{run}+type@sequential+block@no_such_block',
    )
    def test_usage_key_outside_the_course_is_refused(self, usage_key):
        usage_key = usage_key.format(org=self.course_key.org, course=self.course_key.course, run=self.course_key.run)
        assert list(self.refused(self.post(self.item(usage_key=usage_key)))) == ['overrides[0].usage_key']
        assert self.stored_overrides() == {}

    @ddt.data(
        ({'earned_graded_override': 'lots'}, 'earned_graded_override'),
        ({'comment': 'x' * 301}, 'comment'),
        ({'username': None}, 'username'),
    )
    @ddt.unpack
    def test_bad_values_are_refused(self, change, field):
        item = {**self.item(), **change}
        assert list(self.refused(self.post(item))) == [f'overrides[0].{field}']

    def test_empty_batch_is_refused(self):
        assert self.refused(self.post()) == {'overrides': ['This list may not be empty.']}

    def test_oversized_batch_is_refused(self):
        items = [self.item()] * (MAX_OVERRIDES + 1)
        assert self.refused(self.post(*items)) == {
            'overrides': [f'Ensure this field has no more than {MAX_OVERRIDES} elements.'],
        }
        assert self.stored_overrides() == {}

    @ddt.data(
        ([{'user_id': 1, 'usage_id': 'x', 'grade': {}}], ['non_field_errors']),
        ({}, ['overrides']),
        ({'overrides': 'nope'}, ['overrides']),
        ({'overrides': ['nope']}, ['overrides[0]']),
    )
    @ddt.unpack
    def test_malformed_body_is_refused(self, body, paths):
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key), data=json.dumps(body), content_type='application/json',
        )
        assert list(self.refused(response)) == paths
        assert self.stored_overrides() == {}

    def test_body_that_is_not_json_is_refused(self):
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key), data='{not json', content_type='application/json',
        )
        # edx-drf-extensions does not catalog ParseError yet, so its type is
        # internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(response, expected_status=status.HTTP_400_BAD_REQUEST, expected_type_slug='internal')
        assert self.stored_overrides() == {}

    def test_form_body_is_unsupported(self):
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key),
            data='overrides=student', content_type='application/x-www-form-urlencoded',
        )
        # edx-drf-extensions does not catalog UnsupportedMediaType yet, so its type
        # is internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(
            response, expected_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, expected_type_slug='internal',
        )
        assert self.stored_overrides() == {}

    def test_multipart_body_is_refused_as_empty(self):
        """
        A multipart body is read by the site's middleware before the view runs,
        so the view finds no body at all and refuses it as missing its overrides.
        """
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key), data={'overrides': 'student'},
        )
        assert response.wsgi_request.content_type == 'multipart/form-data'
        assert self.refused(response) == {'overrides': ['This field is required.']}
        assert self.stored_overrides() == {}

    def test_response_that_cannot_be_json_is_not_acceptable(self):
        response = self.client.post(
            subsection_grade_override_list_url(self.course_key),
            data=json.dumps({'overrides': [self.item()]}), content_type='application/json', HTTP_ACCEPT='text/html',
        )
        # edx-drf-extensions does not catalog NotAcceptable yet, so its type is
        # internal. Expect its own slug once the pinned release catalogs it.
        assert_error_envelope(response, expected_status=status.HTTP_406_NOT_ACCEPTABLE, expected_type_slug='internal')
        assert self.stored_overrides() == {}

    def test_failure_while_storing_records_nothing(self):
        real_store = PersistentSubsectionGradeOverride.update_or_create_override
        calls = []

        def store(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('storage exploded')
            return real_store(*args, **kwargs)

        with patch.object(PersistentSubsectionGradeOverride, 'update_or_create_override', side_effect=store):
            response = self.post(self.item(), self.item(usage_key=self.second_homework.location))
        body = assert_error_envelope(response, expected_status=500, expected_type_slug='internal')
        assert 'exploded' not in json.dumps(body)
        assert calls == [1, 1]
        assert self.stored_overrides() == {}


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class OverrideParityTest(OverrideTestBase):
    """v2 records the same overrides v1 records, and differs from it only as declared."""

    def setUp(self):
        super().setUp()
        self.login(self.global_staff)

    def v1_post(self, *items):
        return self.client.post(
            reverse('grades_api:v1:course_gradebook_bulk_update', kwargs={'course_id': str(self.course_key)}),
            data=json.dumps(list(items)),
            content_type='application/json',
        )

    def v1_item(self, user, usage_key=None, **grade):
        return {'user_id': user.id, 'usage_id': str(usage_key or self.homework.location), 'grade': grade}

    def stored_for(self, user):
        """Return the override and recomputed grade values stored for ``user`` on the homework."""
        grade = PersistentSubsectionGrade.read_grade(user.id, self.homework.location)
        override_fields = (*OVERRIDE_VALUES, 'override_reason', 'system')
        return (
            {name: getattr(grade.override, name) for name in override_fields},
            (grade.earned_all, grade.possible_all, grade.earned_graded, grade.possible_graded),
            PersistentCourseGrade.read(user.id, self.course_key).percent_grade,
        )

    def test_same_batch_records_the_same_override(self):
        legacy = self.v1_post(self.v1_item(self.student, **OVERRIDE_VALUES, comment='regraded'))
        new = self.post(self.item(username='other_student', comment='regraded'))
        assert (legacy.status_code, new.status_code) == (status.HTTP_202_ACCEPTED, status.HTTP_200_OK)
        assert self.stored_for(self.student) == self.stored_for(self.other_student)
        assert self.stored_for(self.other_student)[2] == 0.01

    def test_mixed_batch_is_applied_in_part_by_v1_and_not_at_all_by_v2(self):
        stranger = UserFactory()
        legacy = self.v1_post(self.v1_item(self.student, **OVERRIDE_VALUES), self.v1_item(stranger, **OVERRIDE_VALUES))
        assert legacy.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert [item['success'] for item in legacy.json()] == [True, False]
        new = self.post(self.item(username='other_student'), self.item(username=stranger.username))
        assert new.status_code == status.HTTP_400_BAD_REQUEST
        assert list(self.stored_overrides()) == [(self.student.id, str(self.homework.location))]


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
class OverrideQueryCountTest(OverrideTestBase):
    """
    The number of queries a one-item batch issues, against v1 on the same fixture.

    Each side records the override once before it is measured, so both are
    measured updating an existing override rather than creating one.
    """

    #: Measured on this fixture; a change is a change in the write path. v2
    #: looks the learner and enrollment up in one query where v1 uses two, and
    #: adds the two statements of the transaction the batch is stored in.
    ONE_ITEM_QUERIES = 49
    V1_ONE_ITEM_QUERIES = 48

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def measure(self, url, body):
        """Return the number of queries the second POST of ``body`` issues."""
        assert self.client.post(url, data=json.dumps(body), content_type='application/json').status_code < 300
        response, count = count_post_queries(self.client, url, body)
        assert response.status_code < 300, response.content
        return count

    def test_one_item(self):
        legacy = self.measure(
            reverse('grades_api:v1:course_gradebook_bulk_update', kwargs={'course_id': str(self.course_key)}),
            [{'user_id': self.student.id, 'usage_id': str(self.homework.location), 'grade': OVERRIDE_VALUES}],
        )
        new = self.measure(
            subsection_grade_override_list_url(self.course_key), {'overrides': [self.item(username='other_student')]},
        )
        assert (new, legacy) == (self.ONE_ITEM_QUERIES, self.V1_ONE_ITEM_QUERIES)


@override_waffle_flag(WRITABLE_GRADEBOOK, active=True)
@override_settings(DEBUG=False)
class OverrideUrlTest(OverrideTestBase):
    """The address, its name, and the not-found body for addresses no route matches."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def test_reverse_literal(self):
        assert reverse('grade_v2:subsection_grade_override_list', kwargs={'course_key': self.COURSE_KEY}) == (
            f'/api/grade/v2/courses/{self.COURSE_KEY}/subsection_grade_overrides/'
        )

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/courses/not-a-course-key/subsection_grade_overrides/',
            '/api/grade/v2/courses/edX/DemoX/Demo/subsection_grade_overrides/',
            f'/api/grade/v2/courses/{self.course_key}/subsection_grade_overrides/extra/',
        ):
            response = self.client.post(path, data='{}', content_type='application/json')
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')
