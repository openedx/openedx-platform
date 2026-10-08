"""Tests for the course grading policy endpoint of the Grades API v2."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import ddt
from ccx_keys.locator import CCXLocator
from django.http import Http404
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from edx_rest_framework_extensions.testing import assert_error_envelope
from edx_toggles.toggles.testutils import override_waffle_flag
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    OrgStaffRole,
)
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.grades.config.waffle import BULK_MANAGEMENT, ENFORCE_FREEZE_GRADE_AFTER_COURSE_END
from lms.djangoapps.grades.grade_utils import are_grades_frozen
from lms.djangoapps.grades.rest_api.v1.tests.mixins import GradeViewTestMixin
from lms.djangoapps.grades.rest_api.v2.services import grades_frozen
from lms.djangoapps.grades.rest_api.v2.tests.mixins import (
    CohortedCourseMixin,
    ReadOnlyRequestMixin,
    capture_cohort_assignment,
    count_queries,
    course_grading_policy_url,
    warm_read_caches,
    with_query,
)
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.courses import get_course_by_id
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.factories import BlockFactory

MISSING_COURSE_KEY = 'course-v1:NoSuch+Course+Run'
DEFAULT_ASSIGNMENT_TYPES = [
    {'type': 'Homework', 'short_label': 'HW', 'min_count': 12, 'drop_count': 2, 'weight': 0.15},
    {'type': 'Lab', 'short_label': 'Lab', 'min_count': 12, 'drop_count': 2, 'weight': 0.15},
    {'type': 'Midterm Exam', 'short_label': 'Midterm', 'min_count': 1, 'drop_count': 0, 'weight': 0.3},
    {'type': 'Final Exam', 'short_label': 'Final', 'min_count': 1, 'drop_count': 0, 'weight': 0.4},
]

#: Graded subsections the shared course fixture holds.
GRADED_SUBSECTIONS = 26
MINIMAL_MEMBERS = {'grade_cutoffs', 'assignment_types'}
DEFAULT_MEMBERS = {*MINIMAL_MEMBERS, 'subsections', 'grades_frozen', 'bulk_management_enabled'}


def reads_of_each_policy_address(client, course_key):
    """
    Return the statuses of the v1 policy, v1 grading information, v2 default
    and v2 minimal reads of ``course_key``'s grading policy, in that order.

    Fails if a v2 read that succeeds returns other members than its view carries.
    """
    course_id = str(course_key)
    legacy_policy = client.get(reverse('grades_api:v1:course_grading_policy', kwargs={'course_id': course_id}))
    legacy_info = client.get(reverse('grades_api:v1:course_gradebook_grading_info', kwargs={'course_id': course_id}))
    new_default = client.get(course_grading_policy_url(course_key))
    new_minimal = client.get(course_grading_policy_url(course_key, view='minimal'))
    for response, members in ((new_default, DEFAULT_MEMBERS), (new_minimal, MINIMAL_MEMBERS)):
        if response.status_code == status.HTTP_200_OK:
            assert set(response.json()) == members
    return legacy_policy.status_code, legacy_info.status_code, new_default.status_code, new_minimal.status_code


def assert_views_follow_v1(legacy_policy, legacy_info, new_default, new_minimal):
    """
    Fail unless each v2 view admits exactly the callers of the v1 data it serves.

    The default view serves v1's grading information, so it admits whom that
    endpoint admits. The minimal view serves v1's policy, which the grading
    information also contains, so it admits whom either endpoint admits.
    """
    assert new_default == legacy_info
    assert (new_minimal == status.HTTP_200_OK) == (status.HTTP_200_OK in (legacy_policy, legacy_info))


class GradingPolicyTestBase(GradeViewTestMixin, APITestCase):
    """
    The shared course, plus one subsection of each kind the policy treats differently:
    an ungraded one, a staff-only one, and one in a section hidden from the outline.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chapter = cls.store.get_course(cls.course_key).get_children()[0]
        cls.ungraded = BlockFactory.create(
            parent_location=chapter.location, category='sequential', display_name='Reading',
        )
        BlockFactory.create(
            parent_location=chapter.location, category='sequential', graded=True, format='Homework',
            visible_to_staff_only=True, display_name='Staff-only homework',
        )
        hidden_chapter = BlockFactory.create(
            parent_location=cls.course.location, category='chapter', hide_from_toc=True, display_name='Hidden',
        )
        BlockFactory.create(
            parent_location=hidden_chapter.location, category='sequential', graded=True, format='Homework',
            display_name='Homework in a hidden section',
        )

    def setUp(self):
        super().setUp()
        warm_read_caches(self.course_key)

    def login(self, user):
        """Sign ``user`` in with a browser session."""
        self.client.login(username=user.username, password=self.password)

    def user_with_role(self, role, key=None):
        """Return a new user holding ``role`` on ``key``, by default the test course."""
        user = UserFactory(password=self.password)
        role(key or self.course_key).add_users(user)
        return user

    def v1_grading_info_url(self, course_key=None, **query):
        url = reverse(
            'grades_api:v1:course_gradebook_grading_info', kwargs={'course_id': str(course_key or self.course_key)},
        )
        return with_query(url, **query)

    def v1_policy_url(self, course_key=None):
        return reverse('grades_api:v1:course_grading_policy', kwargs={'course_id': str(course_key or self.course_key)})


@ddt.ddt
class GradingPolicyAccessTest(GradingPolicyTestBase):
    """
    The policy admits every caller either v1 endpoint it replaces admits, and nobody else.

    v1 split the policy over two endpoints: one for course staff, and one for
    callers who may manage the gradebook.
    """

    def actor(self, kind):
        """Return a user holding the named kind of access to the test course."""
        return {
            'learner': lambda: self.student,
            'beta_tester': lambda: self.user_with_role(CourseBetaTesterRole),
            'staff_of_another_course': lambda: self.user_with_role(CourseStaffRole, self.empty_course.id),
            'course_staff': lambda: self.user_with_role(CourseStaffRole),
            'course_instructor': lambda: self.user_with_role(CourseInstructorRole),
            'limited_staff': lambda: self.user_with_role(CourseLimitedStaffRole),
            'org_staff': lambda: self.user_with_role(OrgStaffRole, self.course_key.org),
            'global_staff': lambda: self.global_staff,
        }[kind]()

    def statuses(self, masquerading=False):
        """
        Return the statuses of the v1 policy, v1 grading information, v2 default
        and v2 minimal reads.
        """
        with patch('lms.djangoapps.courseware.access.is_masquerading_as_student', return_value=masquerading):
            return reads_of_each_policy_address(self.client, self.course_key)

    @ddt.data(
        ('learner', False, (403, 403, 403, 403)),
        ('beta_tester', False, (403, 403, 403, 403)),
        ('staff_of_another_course', False, (403, 403, 403, 403)),
        ('course_staff', False, (200, 200, 200, 200)),
        ('course_staff', True, (403, 200, 200, 200)),
        ('course_instructor', False, (200, 200, 200, 200)),
        ('limited_staff', False, (200, 200, 200, 200)),
        ('org_staff', False, (200, 200, 200, 200)),
        ('global_staff', False, (200, 200, 200, 200)),
    )
    @ddt.unpack
    def test_each_view_admits_exactly_whom_its_v1_endpoint_admits(self, kind, masquerading, expected):
        self.login(self.actor(kind))
        statuses = self.statuses(masquerading)
        assert statuses == expected
        assert_views_follow_v1(*statuses)

    def test_anonymous_caller_is_refused(self):
        assert_error_envelope(
            self.client.get(course_grading_policy_url(self.course_key)),
            expected_status=401, expected_type_slug='authn',
        )

    def test_refusal_carries_the_error_body(self):
        self.login(self.student)
        assert_error_envelope(
            self.client.get(course_grading_policy_url(self.course_key)),
            expected_status=403, expected_type_slug='authz',
        )

    def test_caller_without_access_cannot_learn_whether_a_course_exists(self):
        self.login(self.student)
        assert_error_envelope(
            self.client.get(course_grading_policy_url(MISSING_COURSE_KEY)),
            expected_status=403, expected_type_slug='authz',
        )

    def test_missing_course_is_not_found_for_a_caller_with_access(self):
        self.login(self.global_staff)
        assert_error_envelope(
            self.client.get(course_grading_policy_url(MISSING_COURSE_KEY)),
            expected_status=404, expected_type_slug='not-found',
        )


class GradingPolicyCcxTest(CcxTestCase, APITestCase):
    """The grading policy of a CCX course."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))

    def test_coach_is_admitted(self):
        self.client.force_authenticate(self.coach)
        response = self.client.get(course_grading_policy_url(self.ccx_key))
        assert response.status_code == status.HTTP_200_OK, response.content
        assert {subsection['usage_key'].split(':')[0] for subsection in response.json()['subsections']} == {
            'ccx-block-v1',
        }

    def test_outsider_is_refused(self):
        self.client.force_authenticate(UserFactory())
        assert_error_envelope(
            self.client.get(course_grading_policy_url(self.ccx_key)), expected_status=403, expected_type_slug='authz',
        )

    def test_ccx_staff_keep_the_minimal_view_only_when_custom_courses_are_off(self):
        """
        With custom courses off, CCX staff may no longer manage the gradebook,
        but they are still course staff, whom v1's policy endpoint admitted.
        """
        staff = UserFactory()
        CourseStaffRole(self.ccx_key).add_users(staff)
        self.client.force_authenticate(staff)
        with override_settings(CUSTOM_COURSES_EDX=False):
            assert reads_of_each_policy_address(self.client, self.ccx_key) == (200, 403, 403, 200)

    def test_master_organization_staff_keep_the_minimal_view_only_when_custom_courses_are_off(self):
        staff = UserFactory()
        OrgStaffRole(self.course.id.org).add_users(staff)
        self.client.force_authenticate(staff)
        with override_settings(CUSTOM_COURSES_EDX=False):
            assert reads_of_each_policy_address(self.client, self.ccx_key) == (200, 403, 403, 200)


@ddt.ddt
class GradingPolicyCcxAccessTest(CcxTestCase, APITestCase):
    """On a CCX course, each view admits exactly the callers of the v1 endpoint whose data it serves."""

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))

    def actor(self, kind):
        """Return a user holding the named kind of access to the CCX course."""
        if kind == 'coach':
            return self.coach
        if kind == 'site_staff':
            return UserFactory(is_staff=True)
        user = UserFactory()
        role = {
            'ccx_staff': lambda: CourseStaffRole(self.ccx_key),
            'ccx_instructor': lambda: CourseInstructorRole(self.ccx_key),
            'master_staff': lambda: CourseStaffRole(self.course.id),
            'master_org_staff': lambda: OrgStaffRole(self.course.id.org),
        }.get(kind)
        if role:
            role().add_users(user)
        return user

    @ddt.data(
        *[(kind, True) for kind in (
            'coach', 'ccx_staff', 'ccx_instructor', 'master_staff', 'master_org_staff', 'site_staff', 'outsider',
        )],
        *[(kind, False) for kind in (
            'coach', 'ccx_staff', 'ccx_instructor', 'master_staff', 'master_org_staff', 'site_staff', 'outsider',
        )],
    )
    @ddt.unpack
    def test_each_view_follows_v1(self, kind, custom_courses):
        self.client.force_authenticate(self.actor(kind))
        with override_settings(CUSTOM_COURSES_EDX=custom_courses):
            statuses = reads_of_each_policy_address(self.client, self.ccx_key)
        assert set(statuses) <= {status.HTTP_200_OK, status.HTTP_403_FORBIDDEN}, statuses
        assert_views_follow_v1(*statuses)

    def test_master_course_that_disables_ccx_leaves_its_staff_the_minimal_view_only(self):
        master_course = self.store.get_course(self.course.id)
        master_course.enable_ccx = False
        staff = UserFactory()
        OrgStaffRole(self.course.id.org).add_users(staff)
        self.client.force_authenticate(staff)
        with patch('lms.djangoapps.grades.rest_api.v1.gradebook_views.get_course_by_id', return_value=master_course):
            statuses = reads_of_each_policy_address(self.client, self.ccx_key)
        assert statuses == (200, 403, 403, 200)


class GradingPolicyContentTest(GradingPolicyTestBase):
    """What the policy says about the course."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def policy(self, **query):
        response = self.client.get(course_grading_policy_url(self.course_key, **query))
        assert response.status_code == status.HTTP_200_OK, response.content
        return response.json()

    def test_default_view(self):
        body = self.policy()
        assert set(body) == {
            'grade_cutoffs', 'assignment_types', 'subsections', 'grades_frozen', 'bulk_management_enabled',
        }
        assert (body['grade_cutoffs'], body['assignment_types']) == ({'Pass': 0.5}, DEFAULT_ASSIGNMENT_TYPES)
        assert (body['grades_frozen'], body['bulk_management_enabled']) == (False, False)
        first = body['subsections'][0]
        assert first == {
            'usage_key': first['usage_key'],
            'display_name': 'Sequential Homework 0',
            'graded': True,
            'assignment_type': 'Homework',
            'short_label': 'HW 01',
        }
        assert body['subsections'][-1] == {
            'usage_key': str(self.ungraded.location),
            'display_name': 'Reading',
            'graded': False,
            'assignment_type': None,
            'short_label': None,
        }

    def test_staff_only_and_hidden_subsections_are_left_out(self):
        names = [subsection['display_name'] for subsection in self.policy()['subsections']]
        assert len(names) == GRADED_SUBSECTIONS + 1
        assert 'Staff-only homework' not in names
        assert 'Homework in a hidden section' not in names

    def test_graded_only(self):
        subsections = self.policy(graded_only='true')['subsections']
        assert len(subsections) == GRADED_SUBSECTIONS
        assert {subsection['graded'] for subsection in subsections} == {True}

    def test_minimal_view(self):
        assert self.policy(view='minimal') == {
            'grade_cutoffs': {'Pass': 0.5}, 'assignment_types': DEFAULT_ASSIGNMENT_TYPES,
        }

    def test_minimal_view_reads_the_course_one_level_deep(self):
        with patch('lms.djangoapps.grades.rest_api.v2.services.get_course_by_id', wraps=get_course_by_id) as get:
            self.policy(view='minimal')
        assert get.call_args.kwargs == {'depth': 0}

    def test_bad_query_values_are_refused(self):
        for query, parameter in (({'view': 'full'}, 'view'), ({'graded_only': 'perhaps'}, 'graded_only')):
            response = self.client.get(course_grading_policy_url(self.course_key, **query))
            body = assert_error_envelope(response, expected_status=400, expected_type_slug='validation')
            assert list(body['errors']) == [parameter]

    def test_bulk_management_follows_the_masters_track(self):
        CourseModeFactory(course_id=self.course_key, mode_slug='masters')
        assert self.policy()['bulk_management_enabled'] is True

    def test_bulk_management_follows_its_flag(self):
        with override_waffle_flag(BULK_MANAGEMENT, active=True):
            assert self.policy()['bulk_management_enabled'] is True

    def test_course_without_content_is_not_found(self):
        with patch('lms.djangoapps.grades.rest_api.v2.services.get_course_by_id', side_effect=Http404):
            response = self.client.get(course_grading_policy_url(self.course_key))
        assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')


@ddt.ddt
class GradingPolicyFreezeTest(GradingPolicyTestBase):
    """Whether grades are frozen agrees with the shared rule on every side of it."""

    @ddt.data(
        (True, -60, True),
        (True, 60, False),
        (True, None, False),
        (False, -60, False),
    )
    @ddt.unpack
    def test_agrees_with_the_shared_rule(self, enforced, end_in_days, frozen):
        end = None if end_in_days is None else timezone.now() + timedelta(days=end_in_days)
        with override_waffle_flag(ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, active=enforced), patch(
            'lms.djangoapps.grades.grade_utils.CourseOverview.get_from_id', return_value=MagicMock(end=end),
        ):
            assert (grades_frozen(self.course_key, end), are_grades_frozen(self.course_key)) == (frozen, frozen)


class GradingPolicyFrozenCourseTest(GradingPolicyTestBase):
    """The policy of a course that ended long ago reports its grades frozen."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        course = cls.store.get_course(cls.course_key)
        course.end = timezone.now() - timedelta(days=60)
        cls.store.update_item(course, ModuleStoreEnum.UserID.test)

    def test_frozen_only_where_freezing_is_switched_on(self):
        self.client.force_authenticate(self.global_staff)
        url = course_grading_policy_url(self.course_key)
        with override_waffle_flag(ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, active=True):
            assert self.client.get(url).json()['grades_frozen'] is True
        with override_waffle_flag(ENFORCE_FREEZE_GRADE_AFTER_COURSE_END, active=False):
            assert self.client.get(url).json()['grades_frozen'] is False


class GradingPolicyParityTest(GradingPolicyTestBase):
    """The v2 policy equals the two v1 bodies it replaces, apart from the declared differences."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_default_view_against_grading_information(self):
        legacy = self.client.get(self.v1_grading_info_url()).json()
        new = self.client.get(course_grading_policy_url(self.course_key)).json()
        assert list(legacy['assignment_types']) == [entry['type'] for entry in new['assignment_types']]
        legacy['assignment_types'] = list(legacy['assignment_types'].values())
        assert_parity(
            legacy, new,
            renames={'module_id': 'usage_key', 'can_see_bulk_management': 'bulk_management_enabled'},
        )

    def test_graded_only_against_grading_information(self):
        legacy = self.client.get(self.v1_grading_info_url(graded_only='true')).json()
        new = self.client.get(course_grading_policy_url(self.course_key, graded_only='true')).json()
        assert [s['module_id'] for s in legacy['subsections']] == [s['usage_key'] for s in new['subsections']]

    def test_minimal_view_against_the_policy(self):
        legacy = self.client.get(self.v1_policy_url()).json()
        new = self.client.get(course_grading_policy_url(self.course_key, view='minimal')).json()
        assert_parity(
            {'assignment_types': legacy}, new,
            renames={'assignment_type': 'type', 'count': 'min_count', 'dropped': 'drop_count'},
            differences=[
                ('grade_cutoffs', 'the minimal view adds the grade cutoffs'),
                ('assignment_types[*].short_label', 'each assignment type carries its short label'),
            ],
        )


class GradingPolicyUnnamedSubsectionTest(GradingPolicyTestBase):
    """A subsection the course gives no display name is listed with a null name, as v1 lists it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        chapter = cls.store.get_course(cls.course_key).get_children()[0]
        unnamed = BlockFactory.create(parent_location=chapter.location, category='sequential', display_name=None)
        cls.unnamed_key = str(unnamed.location)

    def test_display_name_is_null(self):
        self.client.force_authenticate(self.global_staff)
        new = self.client.get(course_grading_policy_url(self.course_key)).json()
        legacy = self.client.get(self.v1_grading_info_url()).json()
        assert [s['display_name'] for s in new['subsections'] if s['usage_key'] == self.unnamed_key] == [None]
        assert [s['display_name'] for s in legacy['subsections'] if s['module_id'] == self.unnamed_key] == [None]


class GradingPolicyReadOnlyTest(ReadOnlyRequestMixin, GradingPolicyTestBase):
    """Reading the policy changes nothing and announces nothing."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def test_each_view(self):
        with self.assert_nothing_is_written():
            for query in ({}, {'view': 'minimal'}, {'graded_only': 'true'}):
                assert self.client.get(course_grading_policy_url(self.course_key, **query)).status_code == 200
            assert self.client.head(course_grading_policy_url(self.course_key)).status_code == 200

    def test_course_without_an_overview_is_not_created_one(self):
        """
        A course whose overview is missing is still read from its content, and
        no overview is created; v1's grading information creates one.
        """
        CourseOverview.objects.filter(id=self.course_key).delete()
        with self.assert_nothing_is_written():
            response = self.client.get(course_grading_policy_url(self.course_key))
        assert response.status_code == status.HTTP_200_OK
        assert response.json()['assignment_types'] == DEFAULT_ASSIGNMENT_TYPES
        assert not CourseOverview.objects.filter(id=self.course_key).exists()
        assert self.client.get(self.v1_grading_info_url()).status_code == status.HTTP_200_OK
        assert CourseOverview.objects.filter(id=self.course_key).exists()


class GradingPolicyCohortAssignmentTest(CohortedCourseMixin, GradingPolicyTestBase):
    """On a course whose content groups follow cohorts, reading the policy assigns nobody to a cohort."""

    def test_each_view(self):
        self.client.force_authenticate(self.global_staff)
        for query in ({}, {'view': 'minimal'}, {'graded_only': 'true'}):
            with capture_cohort_assignment() as record:
                response = self.client.get(course_grading_policy_url(self.course_key, **query))
            assert response.status_code == status.HTTP_200_OK
            assert (record.tables, record.memberships, record.tracking) == ([], [], [])
        assert self.cohort_of(self.uncohorted) is None


class GradingPolicyQueryCountTest(GradingPolicyTestBase):
    """
    The queries each view issues, against the v1 read it replaces. Each address is requested once first.

    The default view issues two more than v1's grading information: v1 checks
    no course existence, and reads the course modes through a cached lookup
    that the first request fills, where v2 queries them directly.
    """

    #: Measured on this fixture; a change is a change in the data path.
    DEFAULT_QUERIES = 14
    V1_GRADING_INFO_QUERIES = 12
    MINIMAL_QUERIES = 9
    V1_POLICY_QUERIES = 9

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.global_staff)

    def measure(self, url):
        assert self.client.get(url).status_code == status.HTTP_200_OK
        response, count = count_queries(self.client, url)
        assert response.status_code == status.HTTP_200_OK
        return count

    def test_default_view(self):
        legacy = self.measure(self.v1_grading_info_url())
        new = self.measure(course_grading_policy_url(self.course_key))
        assert (new, legacy) == (self.DEFAULT_QUERIES, self.V1_GRADING_INFO_QUERIES)

    def test_minimal_view(self):
        legacy = self.measure(self.v1_policy_url())
        new = self.measure(course_grading_policy_url(self.course_key, view='minimal'))
        assert (new, legacy) == (self.MINIMAL_QUERIES, self.V1_POLICY_QUERIES)


@override_settings(DEBUG=False)
class GradingPolicyUrlTest(GradingPolicyTestBase):
    """The address, its name, and the not-found body for addresses no route matches."""

    COURSE_KEY = 'course-v1:edX+DemoX+Demo_Course'

    def test_reverse_literal(self):
        assert reverse('grade_v2:course_grading_policy', kwargs={'course_key': self.COURSE_KEY}) == (
            f'/api/grade/v2/courses/{self.COURSE_KEY}/grading_policy/'
        )

    def test_unmatched_addresses_get_a_json_not_found(self):
        self.client.force_authenticate(self.global_staff)
        for path in (
            '/api/grade/v2/courses/not-a-course-key/grading_policy/',
            '/api/grade/v2/courses/edX/DemoX/Demo/grading_policy/',
            f'/api/grade/v2/courses/{self.course_key}/grading_policy/extra/',
        ):
            response = self.client.get(path)
            assert response['Content-Type'] == 'application/json', path
            assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')
