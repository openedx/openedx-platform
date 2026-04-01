"""
Tests for the milestones helpers library, which is the integration point for the edx_milestones API
"""
from unittest.mock import patch

import ddt
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings
from milestones import api as milestones_api
from milestones.exceptions import InvalidCourseKeyException, InvalidUserException
from milestones.models import MilestoneRelationshipType

from common.djangoapps.util import milestones_helpers
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@override_settings(MILESTONES_APP=False)
@ddt.ddt
class MilestonesHelpersTestCase(ModuleStoreTestCase):
    """
    Main test suite for Milestones API client library
    """

    CREATE_USER = False

    def setUp(self):
        """
        Test case scaffolding
        """
        super().setUp()
        self.course = CourseFactory.create(
            metadata={
                'entrance_exam_enabled': True,
            }
        )

        self.user = {'id': '123'}

        self.milestone = milestones_api.add_milestone({
            'name': 'Test Milestone',
            'namespace': 'doesnt.matter',
            'description': 'Testing Milestones Helpers Library',
        })

        MilestoneRelationshipType.objects.get_or_create(name='requires')
        MilestoneRelationshipType.objects.get_or_create(name='fulfills')

    @ddt.data(
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, True),
    )
    def test_pre_requisite_courses_enabled(self, feature_flags):
        """
        Tests is_prerequisite_courses_enabled function with a set of possible values for
        ENABLE_PREREQUISITE_COURSES and MILESTONES_APP feature flags.
        """

        with patch.dict("django.conf.settings.FEATURES", {
            'ENABLE_PREREQUISITE_COURSES': feature_flags[0],
        }), override_settings(MILESTONES_APP=feature_flags[1]):
            assert feature_flags[2] == milestones_helpers.is_prerequisite_courses_enabled()

    def test_add_milestone_returns_none_when_app_disabled(self):
        response = milestones_helpers.add_milestone(milestone_data=self.milestone)
        assert response is None

    def test_get_milestones_returns_none_when_app_disabled(self):
        response = milestones_helpers.get_milestones(namespace="whatever")
        assert len(response) == 0

    def test_get_milestone_relationship_types_returns_none_when_app_disabled(self):
        response = milestones_helpers.get_milestone_relationship_types()
        assert len(response) == 0

    def test_add_course_milestone_returns_none_when_app_disabled(self):
        response = milestones_helpers.add_course_milestone(str(self.course.id), 'requires', self.milestone)
        assert response is None

    def test_get_course_milestones_returns_none_when_app_disabled(self):
        response = milestones_helpers.get_course_milestones(str(self.course.id))
        assert len(response) == 0

    def test_add_course_content_milestone_returns_none_when_app_disabled(self):
        response = milestones_helpers.add_course_content_milestone(
            str(self.course.id),
            'i4x://any/content/id',
            'requires',
            self.milestone
        )
        assert response is None

    def test_get_course_content_milestones_returns_none_when_app_disabled(self):
        response = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            'i4x://doesnt/matter/for/this/test',
            'requires'
        )
        assert len(response) == 0

    def test_remove_content_references_returns_none_when_app_disabled(self):
        response = milestones_helpers.remove_content_references("i4x://any/content/id/will/do")
        assert response is None

    def test_get_namespace_choices_returns_values_when_app_disabled(self):
        response = milestones_helpers.get_namespace_choices()
        assert 'ENTRANCE_EXAM' in response

    def test_get_course_milestones_fulfillment_paths_returns_none_when_app_disabled(self):
        response = milestones_helpers.get_course_milestones_fulfillment_paths(str(self.course.id), self.user)
        assert response is None

    def test_add_user_milestone_returns_none_when_app_disabled(self):
        response = milestones_helpers.add_user_milestone(self.user, self.milestone)
        assert response is None

    def test_get_service_returns_none_when_app_disabled(self):
        """MilestonesService is None when app disabled"""
        response = milestones_helpers.get_service()
        assert response is None

    @override_settings(MILESTONES_APP=True)
    def test_any_unfulfilled_milestones(self):
        """
        Tests any_unfulfilled_milestones for invalid arguments with the app enabled.
        """

        # Should not raise any exceptions
        milestones_helpers.any_unfulfilled_milestones(self.course.id, self.user['id'])

        with pytest.raises(InvalidCourseKeyException):
            milestones_helpers.any_unfulfilled_milestones(None, self.user['id'])
        with pytest.raises(InvalidUserException):
            milestones_helpers.any_unfulfilled_milestones(self.course.id, None)

    @override_settings(MILESTONES_APP=True)
    def test_get_required_content_with_anonymous_user(self):
        course = CourseFactory()

        required_content = milestones_helpers.get_required_content(course.id, AnonymousUser())
        assert not required_content

        # NOTE (CCB): The initial version of anonymous courseware access is very simple. We avoid accidentally
        # exposing locked content by simply avoiding anonymous access altogether for courses runs with milestones.
        milestone = milestones_api.add_milestone({
            'name': 'test',
            'namespace': 'test',
        })
        milestones_helpers.add_course_milestone(str(course.id), 'requires', milestone)
        with pytest.raises(InvalidUserException):
            milestones_helpers.get_required_content(course.id, AnonymousUser())
