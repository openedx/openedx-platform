"""
Tests for permissions defined in courseware.rules
"""


from unittest.mock import patch

import ddt

from common.djangoapps.course_modes.tests.factories import CourseModeFactory
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
@patch.dict(
    'django.conf.settings.FEATURES',
    {
        'ENABLE_SPECIAL_EXAMS': True,
        'ENABLE_PROCTORED_EXAMS': True,
    }
)
class PermissionTests(ModuleStoreTestCase):
    """
    Tests for permissions defined in courseware.rules
    """
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course = CourseFactory(enable_proctored_exams=True)

        self.course_id = self.course.id  # pylint: disable=no-member
        CourseModeFactory(mode_slug='verified', course_id=self.course_id)
        CourseModeFactory(mode_slug='masters', course_id=self.course_id)
        CourseModeFactory(mode_slug='professional', course_id=self.course_id)
        CourseEnrollment.unenroll(self.user, self.course_id)

    def tearDown(self):
        super().tearDown()
        self.user.delete()

    @ddt.data(
        ('audit', False),
        ('verified', True),
        ('masters', True),
        ('professional', True),
        ('no-id-professional', False),
    )
    @ddt.unpack
    def test_proctoring_perm(self, mode, should_have_perm):
        """
        Test that the user has the edx_proctoring.can_take_proctored_exam permission
        """
        if mode is not None:
            CourseEnrollment.enroll(self.user, self.course_id, mode=mode)
        has_perm = self.user.has_perm(
            'edx_proctoring.can_take_proctored_exam', {'course_id': str(self.course_id)}
        )
        assert has_perm == should_have_perm

    def test_proctoring_perm_no_enrollment(self):
        """
        Test that the user does not have the edx_proctoring.can_take_proctored_exam permission if they
        are not enrolled in the course
        """
        has_perm = self.user.has_perm(
            'edx_proctoring.can_take_proctored_exam', {'course_id': str(self.course_id)}
        )
        assert not has_perm

    @patch.dict(
        'django.conf.settings.PROCTORING_BACKENDS',
        {'mock_proctoring_allow_honor_mode': {'allow_honor_mode': True}}
    )
    def test_proctoring_perm_with_honor_mode_permission(self):
        """
        Test that the user has the edx_proctoring.can_take_proctored_exam permission in honor enrollment mode.

        If proctoring backend configuration allows exam in honor mode {`allow_honor_mode`: True} the user is
        granted proctored exam permission.
        """
        course_allow_honor = CourseFactory(
            enable_proctored_exams=True, proctoring_provider='mock_proctoring_allow_honor_mode'
        )
        CourseEnrollment.enroll(self.user, course_allow_honor.id, mode='honor')
        assert self.user.has_perm('edx_proctoring.can_take_proctored_exam',
                                  {'course_id': str(course_allow_honor.id),
                                   'backend': 'mock_proctoring_allow_honor_mode', 'is_proctored': True})
