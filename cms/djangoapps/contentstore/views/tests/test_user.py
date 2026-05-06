"""
Tests for contentstore/views/user.py.
"""
import json

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from edx_toggles.toggles.testutils import override_waffle_flag

from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url
from common.djangoapps.student import auth
from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole
from common.djangoapps.student.tests.factories import UserFactory


class UsersTestCase(CourseTestCase):  # pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.ext_user = UserFactory.create(
            username="joe", email="joe@comedycentral.com", password="haha",
        )
        self.ext_user.is_active = True
        self.ext_user.is_staff = False
        self.ext_user.save()
        self.inactive_user = UserFactory.create(
            username="carl", email="carl@comedycentral.com", password="haha",
        )
        self.inactive_user.is_active = False
        self.inactive_user.is_staff = False
        self.inactive_user.save()

        self.index_url = self.course_team_url()
        self.detail_url = self.course_team_url(email=self.ext_user.email)
        self.inactive_detail_url = self.course_team_url(email=self.inactive_user.email)
        self.invalid_detail_url = self.course_team_url(email='nonexistent@user.com')

    def course_team_url(self, email=None):
        return reverse_course_url(
            'course_team_handler', self.course.id,
            kwargs={'email': email} if email else {}
        )

    @override_waffle_flag(toggles.LEGACY_STUDIO_COURSE_TEAM, True)
    def test_index(self):
        resp = self.client.get(self.index_url, HTTP_ACCEPT='text/html')
        # ext_user is not currently a member of the course team, and so should
        # not show up on the page.
        self.assertNotContains(resp, self.ext_user.email)

    @override_waffle_flag(toggles.LEGACY_STUDIO_COURSE_TEAM, True)
    def test_index_member(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.ext_user)

        resp = self.client.get(self.index_url, HTTP_ACCEPT='text/html')
        self.assertContains(resp, self.ext_user.email)

    def test_detail(self):
        resp = self.client.get(self.detail_url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(result["role"], None)  # noqa: PT009
        self.assertTrue(result["active"])  # noqa: PT009

    def test_detail_inactive(self):
        resp = self.client.get(self.inactive_detail_url)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertFalse(result["active"])  # noqa: PT009

    def test_detail_invalid(self):
        resp = self.client.get(self.invalid_detail_url)
        self.assertEqual(resp.status_code, 404)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009

    def test_detail_post(self):
        resp = self.client.post(
            self.detail_url,
            data={"role": ""},
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        # no content: should not be in any roles
        self.assertFalse(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009
        self.assertFalse(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assert_not_enrolled()

    def test_detail_post_staff(self):
        resp = self.client.post(
            self.detail_url,
            data=json.dumps({"role": "staff"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009
        self.assertFalse(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assert_enrolled()

    def test_detail_post_staff_other_inst(self):
        auth.add_users(self.user, CourseInstructorRole(self.course.id), self.user)

        resp = self.client.post(
            self.detail_url,
            data=json.dumps({"role": "staff"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009
        self.assertFalse(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assert_enrolled()
        # check that other user is unchanged
        user = User.objects.get(email=self.user.email)
        self.assertTrue(auth.user_has_role(user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assertFalse(CourseStaffRole(self.course.id).has_user(user))  # noqa: PT009

    def test_detail_post_instructor(self):
        resp = self.client.post(
            self.detail_url,
            data=json.dumps({"role": "instructor"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assertFalse(CourseStaffRole(self.course.id).has_user(ext_user))  # noqa: PT009
        self.assert_enrolled()

    def test_detail_post_missing_role(self):
        resp = self.client.post(
            self.detail_url,
            data=json.dumps({"toys": "fun"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009
        self.assert_not_enrolled()

    def test_detail_post_no_json(self):
        resp = self.client.post(
            self.detail_url,
            data={"role": "staff"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009
        self.assertFalse(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009
        self.assert_enrolled()

    def test_detail_delete_staff(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.ext_user)

        resp = self.client.delete(
            self.detail_url,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertFalse(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009

    def test_detail_delete_instructor(self):
        auth.add_users(self.user, CourseInstructorRole(self.course.id), self.ext_user, self.user)

        resp = self.client.delete(
            self.detail_url,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertFalse(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009

    def test_delete_last_instructor(self):
        auth.add_users(self.user, CourseInstructorRole(self.course.id), self.ext_user)

        resp = self.client.delete(
            self.detail_url,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009

    def test_post_last_instructor(self):
        auth.add_users(self.user, CourseInstructorRole(self.course.id), self.ext_user)

        resp = self.client.post(
            self.detail_url,
            data={"role": "staff"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseInstructorRole(self.course.id)))  # noqa: PT009

    def test_permission_denied_self(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.user)
        self.user.is_staff = False
        self.user.save()

        self_url = self.course_team_url(email=self.user.email)

        resp = self.client.post(
            self_url,
            data={"role": "instructor"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009

    def test_permission_denied_other(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.user)
        self.user.is_staff = False
        self.user.save()

        resp = self.client.post(
            self.detail_url,
            data={"role": "instructor"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 403)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009

    def test_staff_can_delete_self(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.user)
        self.user.is_staff = False
        self.user.save()

        self_url = self.course_team_url(email=self.user.email)

        resp = self.client.delete(self_url)
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        # reload user from DB
        user = User.objects.get(email=self.user.email)
        self.assertFalse(auth.user_has_role(user, CourseStaffRole(self.course.id)))  # noqa: PT009

    def test_staff_cannot_delete_other(self):
        auth.add_users(self.user, CourseStaffRole(self.course.id), self.user, self.ext_user)
        self.user.is_staff = False
        self.user.save()

        resp = self.client.delete(self.detail_url)
        self.assertEqual(resp.status_code, 403)  # noqa: PT009
        result = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", result)  # noqa: PT009
        # reload user from DB
        ext_user = User.objects.get(email=self.ext_user.email)
        self.assertTrue(auth.user_has_role(ext_user, CourseStaffRole(self.course.id)))  # noqa: PT009

    def test_user_not_initially_enrolled(self):
        # Verify that ext_user is not enrolled in the new course before being added as a staff member.
        self.assert_not_enrolled()

    def test_remove_staff_does_not_unenroll(self):
        # Add user with staff permissions.
        self.client.post(
            self.detail_url,
            data=json.dumps({"role": "staff"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assert_enrolled()
        # Remove user from staff on course. Will not un-enroll them from the course.
        resp = self.client.delete(
            self.detail_url,
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        self.assert_enrolled()

    def test_staff_to_instructor_still_enrolled(self):
        # Add user with staff permission.
        self.client.post(
            self.detail_url,
            data=json.dumps({"role": "staff"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assert_enrolled()
        # Now add with instructor permission. Verify still enrolled.
        resp = self.client.post(
            self.detail_url,
            data=json.dumps({"role": "instructor"}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        self.assert_enrolled()

    def assert_not_enrolled(self):
        """ Asserts that self.ext_user is not enrolled in self.course. """
        self.assertFalse(  # noqa: PT009
            CourseEnrollment.is_enrolled(self.ext_user, self.course.id),
            'Did not expect ext_user to be enrolled in course'
        )

    def assert_enrolled(self):
        """ Asserts that self.ext_user is enrolled in self.course. """
        self.assertTrue(  # noqa: PT009
            CourseEnrollment.is_enrolled(self.ext_user, self.course.id),
            'User ext_user should have been enrolled in the course'
        )
