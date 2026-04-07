"""
Tests for proctoring course app.
"""
from unittest.mock import patch

import ddt
from django.conf import settings

from lms.djangoapps.courseware.plugins import ProctoringCourseApp
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (  # lint-amnesty, pylint: disable=wrong-import-order
    CourseUserType,
    ModuleStoreTestCase,
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@skip_unless_cms
@patch.dict(settings.FEATURES, {'ENABLE_PROCTORED_EXAMS': True})
@ddt.ddt
class ProctoringCourseAppTestCase(ModuleStoreTestCase):
    """Test cases for proctoring CourseApp."""

    def setUp(self):
        """Setup proctoring course app tests."""
        super().setUp()

        self.course = CourseFactory.create(
            org='edX',
            course='900',
            run='test_run',
            enable_proctored_exams=True,
            proctoring_provider=settings.PROCTORING_BACKENDS['DEFAULT'],
        )
        self.instructor = self.create_user_for_course(self.course, CourseUserType.COURSE_INSTRUCTOR)

    @ddt.data(True, False)
    def test_proctoring_is_available_with_feature_flag(self, available_status):
        """
        Test that proctoring card's availability can configured using feature flag.
        """
        with patch.dict("django.conf.settings.FEATURES", {'ENABLE_PROCTORED_EXAMS': available_status}):
            assert self.course.enable_proctored_exams is True
            assert ProctoringCourseApp().is_available(self.course.id) == available_status

    @ddt.data(True, False)
    def test_proctoring_app_is_enabled(self, proctored_exam_status):
        """
        Test that proctoring card can be enabled and disabled by setting course
         proctored exam flag.
        """
        self.course.enable_proctored_exams = proctored_exam_status
        course = self.update_course(self.course, self.instructor.id)

        assert course.enable_proctored_exams == proctored_exam_status
        assert ProctoringCourseApp().is_enabled(course.id) == proctored_exam_status

    def test_proctoring_app_set_enabled(self):
        """
        Test that setting enable status on proctoring app should raise exception.
        """
        with self.assertRaisesRegex(ValueError, "Proctoring cannot be enabled/disabled via this API."):
            ProctoringCourseApp().set_enabled(
                course_key=self.course.id,
                enabled=True,
                user=self.instructor
            )
