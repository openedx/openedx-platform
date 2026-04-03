# pylint: disable=missing-docstring


import pytest
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.course_groups.cohorts import CourseCohortsSettings
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings, Role
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


class RoleAssignmentTest(TestCase):
    """
    Basic checks to make sure our Roles get assigned and unassigned as students
    are enrolled and unenrolled from a course.
    """

    def setUp(self):
        super().setUp()
        # Check a staff account because those used to get the Moderator role
        self.staff_user = UserFactory.create(
            username="patty",
            email="patty@fake.edx.org",
        )
        self.staff_user.is_staff = True

        self.student_user = UserFactory.create(
            username="hacky",
            email="hacky@fake.edx.org",
        )
        self.course_key = CourseLocator("edX", "Fake101", "2012")
        CourseEnrollment.enroll(self.staff_user, self.course_key)
        CourseEnrollment.enroll(self.student_user, self.course_key)

    def test_enrollment_auto_role_creation(self):
        student_role = Role.objects.get(
            course_id=self.course_key,
            name="Student"
        )

        assert [student_role] == list(self.staff_user.roles.all())
        assert [student_role] == list(self.student_user.roles.all())

    # The following was written on the assumption that unenrolling from a course
    # should remove all forum Roles for that student for that course. This is
    # not necessarily the case -- please see comments at the top of
    # django_comment_client.models.assign_default_role(). Leaving it for the
    # forums team to sort out.
    #
    # def test_unenrollment_auto_role_removal(self):
    #     another_student = User.objects.create_user("sol", "sol@fake.edx.org")
    #     CourseEnrollment.enroll(another_student, self.course_id)
    #
    #     CourseEnrollment.unenroll(self.student_user, self.course_id)
    #     # Make sure we didn't delete the actual Role
    #     student_role = Role.objects.get(
    #         course_id=self.course_id,
    #         name="Student"
    #     )
    #     self.assertNotIn(student_role, self.student_user.roles.all())
    #     self.assertIn(student_role, another_student.roles.all())


class CourseDiscussionSettingsTest(ModuleStoreTestCase):

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()

    def test_get_course_discussion_settings(self):
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.NONE == discussion_settings.division_scheme
        assert [] == discussion_settings.divided_discussions
        assert not discussion_settings.always_divide_inline_discussions

    def test_get_course_discussion_settings_legacy_settings(self):
        self.course.cohort_config = {
            'cohorted': True,
            'always_cohort_inline_discussions': True,
            'cohorted_discussions': ['foo']
        }
        modulestore().update_item(self.course, ModuleStoreEnum.UserID.system)
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.COHORT == discussion_settings.division_scheme
        assert ['foo'] == discussion_settings.divided_discussions
        assert discussion_settings.always_divide_inline_discussions

    def test_get_course_discussion_settings_cohort_settings(self):
        CourseCohortsSettings.objects.get_or_create(
            course_id=self.course.id,
            defaults={
                'is_cohorted': True,
                'always_cohort_inline_discussions': True,
                'cohorted_discussions': ['foo', 'bar']
            }
        )
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.COHORT == discussion_settings.division_scheme
        assert ['foo', 'bar'] == discussion_settings.divided_discussions
        assert discussion_settings.always_divide_inline_discussions

    def test_update_course_discussion_settings(self):
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        discussion_settings.update({
            'divided_discussions': ['cohorted_topic'],
            'division_scheme': CourseDiscussionSettings.ENROLLMENT_TRACK,
            'always_divide_inline_discussions': True,
        })
        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        assert CourseDiscussionSettings.ENROLLMENT_TRACK == discussion_settings.division_scheme
        assert ['cohorted_topic'] == discussion_settings.divided_discussions
        assert discussion_settings.always_divide_inline_discussions

    def test_invalid_data_types(self):
        exception_msg_template = "Incorrect field type for `{}`. Type must be `{}`"
        fields = [
            {'name': 'division_scheme', 'type': (str,)[0]},
            {'name': 'always_divide_inline_discussions', 'type': bool},
            {'name': 'divided_discussions', 'type': list}
        ]
        invalid_value = 3.14

        discussion_settings = CourseDiscussionSettings.get(self.course.id)
        for field in fields:
            with pytest.raises(ValueError) as value_error:
                discussion_settings.update({field['name']: invalid_value})

            assert str(value_error.value) == exception_msg_template.format(field['name'], field['type'].__name__)
