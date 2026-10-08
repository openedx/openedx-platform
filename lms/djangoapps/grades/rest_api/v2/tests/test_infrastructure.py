"""Tests for the pieces the Grades API v2 endpoints share."""

from unittest.mock import patch

import ddt
import pytest
from ccx_keys.locator import CCXLocator
from django.test import SimpleTestCase
from django.test.utils import override_settings
from edx_rest_framework_extensions.errors import classify_error
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

from common.djangoapps.student.roles import CourseInstructorRole, CourseLimitedStaffRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.ccx.tests.utils import CcxTestCase
from lms.djangoapps.grades.config.waffle import WRITABLE_GRADEBOOK
from lms.djangoapps.grades.rest_api.v1.gradebook_views import course_author_access_required
from lms.djangoapps.grades.rest_api.v2.errors import GradesFrozen, SubsectionUnavailable, WritableGradebookDisabled
from lms.djangoapps.grades.rest_api.v2.pagination import GradePagination
from lms.djangoapps.grades.rest_api.v2.permissions import CourseExists, HasGradebookAccess, WritableGradebookEnabled
from lms.djangoapps.grades.rest_api.v2.tests.parity import assert_parity
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory


class _CourseView:
    """Stands in for a view that names one course."""

    def __init__(self, course_key):
        self.course_key = course_key

    def get_course_key(self):
        return self.course_key


class _LegacyGradebookAuthView(DeveloperErrorViewMixin, APIView):
    """A view guarded by the v1 gradebook access decorator, to compare the v2 class against."""

    # The decorator under comparison is the only check.
    permission_classes = (AllowAny,)

    @course_author_access_required
    def get(self, request, course_key):  # pylint: disable=arguments-differ
        return Response({'course_key': str(course_key)})


class ErrorTypeTest(SimpleTestCase):
    """Each domain error has its own type, and registering it leaves its parent's type alone."""

    def test_writable_gradebook_disabled(self):
        assert classify_error(WritableGradebookDisabled()) == (
            'grades/writable-gradebook-disabled', 'Writable Gradebook Disabled',
        )

    def test_grades_frozen(self):
        assert classify_error(GradesFrozen()) == ('grades/grades-frozen', 'Grades Frozen')

    def test_subsection_unavailable(self):
        assert classify_error(SubsectionUnavailable()) == ('grades/subsection-unavailable', 'Subsection Unavailable')

    def test_parent_types_keep_their_catalog_entry(self):
        assert classify_error(PermissionDenied()) == ('authz', 'Permission Denied')
        assert classify_error(NotFound()) == ('not-found', 'Not Found')


class ParityHarnessTest(SimpleTestCase):
    """The parity comparison fails on every kind of drift it exists to catch."""

    LEGACY = {'course_id': 'c', 'percent': 0.5, 'rows': [{'module_id': 'm', 'x': 1}]}

    def test_passes_when_only_declared_changes_occur(self):
        new = {'course_key': 'c', 'percent': 0.5, 'rows': [{'usage_key': 'm'}]}
        assert_parity(
            self.LEGACY, new,
            renames={'course_id': 'course_key', 'module_id': 'usage_key'},
            differences=[('rows[*].x', 'dropped')],
        )

    def test_fails_on_an_undeclared_value_change(self):
        new = {'course_key': 'c', 'percent': 0.6, 'rows': [{'usage_key': 'm', 'x': 1}]}
        with pytest.raises(AssertionError, match='undeclared differences'):
            assert_parity(self.LEGACY, new, renames={'course_id': 'course_key', 'module_id': 'usage_key'})

    def test_fails_when_a_renamed_value_changes(self):
        new = {'course_key': 'other', 'percent': 0.5, 'rows': [{'usage_key': 'm', 'x': 1}]}
        with pytest.raises(AssertionError, match='undeclared differences'):
            assert_parity(self.LEGACY, new, renames={'course_id': 'course_key', 'module_id': 'usage_key'})

    def test_fails_when_a_declared_difference_does_not_occur(self):
        new = {'course_key': 'c', 'percent': 0.5, 'rows': [{'usage_key': 'm', 'x': 1}]}
        with pytest.raises(AssertionError, match='declared difference did not occur'):
            assert_parity(
                self.LEGACY, new,
                renames={'course_id': 'course_key', 'module_id': 'usage_key'},
                differences=[('rows[*].x', 'dropped')],
            )

    def test_fails_when_a_declared_rename_does_not_occur(self):
        with pytest.raises(AssertionError, match='declared renames did not occur'):
            assert_parity(self.LEGACY, self.LEGACY, renames={'user_id': 'username'})


class GradePageSchemaTest(SimpleTestCase):
    """The paginator describes all seven members of the body it builds."""

    def test_schema_lists_every_page_member(self):
        schema = GradePagination().get_paginated_response_schema({'type': 'array'})
        assert set(schema['properties']) == {
            'count', 'num_pages', 'current_page', 'start', 'next', 'previous', 'results',
        }
        assert set(schema['required']) == set(schema['properties'])
        assert schema['properties']['results'] == {'type': 'array'}

    def test_defaults(self):
        paginator = GradePagination()
        assert (paginator.page_size, paginator.max_page_size, paginator.page_size_query_param) == (
            10, 100, 'page_size',
        )


@ddt.ddt
class HasGradebookAccessCcxTest(CcxTestCase):
    """
    On CCX courses, the v2 gradebook permission admits exactly the callers the v1
    gradebook decorator admits.
    """

    def setUp(self):
        super().setUp()
        self.make_coach()
        self.ccx = self.make_ccx()
        self.ccx_key = CCXLocator.from_course_locator(self.course.id, str(self.ccx.id))
        self.factory = APIRequestFactory()

    def _actor(self, kind):
        """Return a user holding the named kind of access to ``self.ccx_key``."""
        if kind == 'coach':
            return self.coach
        if kind == 'site_staff':
            return UserFactory.create(is_staff=True)
        user = UserFactory.create()
        if kind == 'ccx_staff':
            CourseStaffRole(self.ccx_key).add_users(user)
        elif kind == 'ccx_instructor':
            CourseInstructorRole(self.ccx_key).add_users(user)
        elif kind == 'master_staff':
            CourseStaffRole(self.course.id).add_users(user)
        return user

    def _legacy_allows(self, user, course_key):
        """Return whether the v1 decorator lets ``user`` through for ``course_key``."""
        request = self.factory.get('/gradebook/')
        force_authenticate(request, user=user)
        response = _LegacyGradebookAuthView.as_view()(request, course_id=str(course_key))
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_403_FORBIDDEN)
        return response.status_code == status.HTTP_200_OK

    def _v2_allows(self, user, course_key):
        """Return whether the v2 permission class lets ``user`` through for ``course_key``."""
        request = self.factory.get('/gradebook/')
        request.user = user
        return HasGradebookAccess().has_permission(request, _CourseView(course_key))

    @ddt.data(
        ('coach', True),
        ('ccx_staff', True),
        ('ccx_instructor', True),
        ('site_staff', True),
        ('master_staff', True),
        ('outsider', False),
    )
    @ddt.unpack
    def test_matches_v1_on_a_ccx_course(self, kind, expected):
        user = self._actor(kind)
        assert self._v2_allows(user, self.ccx_key) is expected
        assert self._legacy_allows(user, self.ccx_key) is expected

    @ddt.data('coach', 'ccx_staff', 'site_staff')
    def test_nobody_is_admitted_when_custom_courses_are_off(self, kind):
        user = self._actor(kind)
        with override_settings(CUSTOM_COURSES_EDX=False):
            assert self._v2_allows(user, self.ccx_key) is False
            assert self._legacy_allows(user, self.ccx_key) is False

    def test_nobody_is_admitted_when_the_master_course_disables_ccx(self):
        master_course = self.store.get_course(self.course.id)
        master_course.enable_ccx = False
        with patch(
            'lms.djangoapps.grades.rest_api.v1.gradebook_views.get_course_by_id',
            return_value=master_course,
        ):
            assert self._v2_allows(self.coach, self.ccx_key) is False
            assert self._legacy_allows(self.coach, self.ccx_key) is False

    def test_ccx_coach_has_no_access_to_the_master_course_itself(self):
        assert self._v2_allows(self.coach, self.course.id) is False
        assert self._legacy_allows(self.coach, self.course.id) is False


@ddt.ddt
class HasGradebookAccessCourseTest(SharedModuleStoreTestCase):
    """On an ordinary course, the v2 gradebook permission admits exactly the callers v1 admits."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def setUp(self):
        super().setUp()
        self.factory = APIRequestFactory()

    def _actor(self, kind):
        """Return a user holding the named role on ``self.course``."""
        if kind == 'global_staff':
            return UserFactory.create(is_staff=True)
        user = UserFactory.create()
        role = {
            'course_staff': CourseStaffRole,
            'course_instructor': CourseInstructorRole,
            'course_limited_staff': CourseLimitedStaffRole,
        }.get(kind)
        if role:
            role(self.course.id).add_users(user)
        return user

    @ddt.data(
        ('learner', False),
        ('course_staff', True),
        ('course_instructor', True),
        ('course_limited_staff', True),
        ('global_staff', True),
    )
    @ddt.unpack
    def test_matches_v1(self, kind, expected):
        user = self._actor(kind)
        request = self.factory.get('/gradebook/')
        request.user = user
        assert HasGradebookAccess().has_permission(request, _CourseView(self.course.id)) is expected

        legacy_request = self.factory.get('/gradebook/')
        force_authenticate(legacy_request, user=user)
        response = _LegacyGradebookAuthView.as_view()(legacy_request, course_id=str(self.course.id))
        assert response.status_code == (status.HTTP_200_OK if expected else status.HTTP_403_FORBIDDEN)


class CourseExistsTest(SharedModuleStoreTestCase):
    """A course that exists passes; one that does not is not found."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_existing_course_passes(self):
        assert CourseExists().has_permission(None, _CourseView(self.course.id)) is True

    def test_missing_course_is_not_found(self):
        missing = CourseKey.from_string('course-v1:NoSuch+Course+Run')
        with pytest.raises(NotFound):
            CourseExists().has_permission(None, _CourseView(missing))


class WritableGradebookEnabledTest(SharedModuleStoreTestCase):
    """The permission follows the course's writable-gradebook flag."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()

    def test_flag_on_passes(self):
        with override_waffle_flag(WRITABLE_GRADEBOOK, active=True):
            assert WritableGradebookEnabled().has_permission(None, _CourseView(self.course.id)) is True

    def test_flag_off_is_refused_with_its_own_type(self):
        with override_waffle_flag(WRITABLE_GRADEBOOK, active=False):
            with pytest.raises(WritableGradebookDisabled):
                WritableGradebookEnabled().has_permission(None, _CourseView(self.course.id))
