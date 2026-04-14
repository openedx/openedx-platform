"""
Tests for (some of) the view utilities.
"""
from django.test.utils import override_settings
from django.urls import re_path
from rest_framework.response import Response
from rest_framework.test import APITestCase
from rest_framework.views import APIView

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..view_utils import DeveloperErrorViewMixin, verify_course_exists


class MockAPIView(DeveloperErrorViewMixin, APIView):
    """
    Mock API view for testing.
    """

    @verify_course_exists()
    def get(self, request, course_id):
        """
        Mock GET handler for testing.
        """
        return Response(f"Success {course_id}")

urlpatterns = [
    re_path(r'^mock/(?P<course_id>.*)/$', MockAPIView.as_view()),  # Only works with new-style course keys
]


@override_settings(ROOT_URLCONF=__name__)
class VerifyCourseExistsTestCase(SharedModuleStoreTestCase, APITestCase):
    """
    Tests for behavior of @verify_course_exists.
    """
    def test_bad_key_404(self):
        response = self.client.get('/mock/this-is-a-bad-key/')
        assert response.status_code == 404

    def test_nonexistent_course_404(self):
        CourseFactory()
        response = self.client.get('/mock/course-v1:Non+Existent+Course/')
        assert response.status_code == 404

    def test_course_exists_200(self):
        course = CourseFactory(
            org="This", course="IsA", run="Course",
            default_store=ModuleStoreEnum.Type.split,
        )
        response = self.client.get(f'/mock/{course.id}/')
        assert response.status_code == 200

    def test_course_with_outdated_overview_200(self):
        course = CourseFactory(
            org="This", course="IsAnother", run="Course",
            default_store=ModuleStoreEnum.Type.split,
        )
        course_overview = CourseOverview.get_from_id(course.id)
        course_overview.version = CourseOverview.VERSION - 1
        course_overview.save()
        response = self.client.get(f'/mock/{course.id}/')
        assert response.status_code == 200
