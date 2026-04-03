"""
Tests course_creators.views.py.
"""


from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from cms.djangoapps.course_creators.views import (
    add_user_with_status_granted,
    add_user_with_status_unrequested,
    get_course_creator_status,
    update_course_creator_group,
    update_org_content_creator_role,
    user_requested_access,
)
from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseCreatorRole, OrgContentCreatorRole
from common.djangoapps.student.tests.factories import UserFactory


class CourseCreatorView(TestCase):
    """
    Tests for modifying the course creator table.
    """

    def setUp(self):
        """ Test case setup """
        super().setUp()
        self.user = UserFactory.create(
            username='test_user',
            email='test_user+courses@edx.org',
            password='foo',
        )
        self.admin = UserFactory.create(
            username='Mark',
            email='admin+courses@edx.org',
            password='foo',
        )
        self.admin.is_staff = True
        self.org = "Edx"

    def test_staff_permission_required(self):
        """
        Tests that any method changing the course creator authz group must be called with staff permissions.
        """
        with self.assertRaises(PermissionDenied):
            add_user_with_status_granted(self.user, self.user)

        with self.assertRaises(PermissionDenied):
            update_course_creator_group(self.user, self.user, True)

    def test_table_initially_empty(self):
        self.assertIsNone(get_course_creator_status(self.user))

    def test_add_unrequested(self):
        add_user_with_status_unrequested(self.user)
        self.assertEqual('unrequested', get_course_creator_status(self.user))

        # Calling add again will be a no-op (even if state is different).
        add_user_with_status_granted(self.admin, self.user)
        self.assertEqual('unrequested', get_course_creator_status(self.user))

    def test_add_granted(self):
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            # Calling add_user_with_status_granted impacts is_user_in_course_group_role.
            self.assertFalse(auth.user_has_role(self.user, CourseCreatorRole()))

            add_user_with_status_granted(self.admin, self.user)
            self.assertEqual('granted', get_course_creator_status(self.user))

            # Calling add again will be a no-op (even if state is different).
            add_user_with_status_unrequested(self.user)
            self.assertEqual('granted', get_course_creator_status(self.user))

            self.assertTrue(auth.user_has_role(self.user, CourseCreatorRole()))

    def test_update_creator_group(self):
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            self.assertFalse(auth.user_has_role(self.user, CourseCreatorRole()))
            update_course_creator_group(self.admin, self.user, True)
            self.assertTrue(auth.user_has_role(self.user, CourseCreatorRole()))
            update_course_creator_group(self.admin, self.user, False)
            self.assertFalse(auth.user_has_role(self.user, CourseCreatorRole()))

    def test_update_org_content_creator_role(self):
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            self.assertFalse(auth.user_has_role(self.user, OrgContentCreatorRole(self.org)))
            update_org_content_creator_role(self.admin, self.user, [self.org])
            self.assertTrue(auth.user_has_role(self.user, OrgContentCreatorRole(self.org)))
            update_org_content_creator_role(self.admin, self.user, [])
            self.assertFalse(auth.user_has_role(self.user, OrgContentCreatorRole(self.org)))

    def test_user_requested_access(self):
        add_user_with_status_unrequested(self.user)
        self.assertEqual('unrequested', get_course_creator_status(self.user))

        self.client.login(username=self.user.username, password='foo')

        # The user_requested_access function renders a template that requires
        # request-specific information. Use the django TestClient to supply
        # the appropriate request context.
        self.client.post(reverse('request_course_creator'))
        self.assertEqual('pending', get_course_creator_status(self.user))

    def test_user_requested_already_granted(self):
        add_user_with_status_granted(self.admin, self.user)
        self.assertEqual('granted', get_course_creator_status(self.user))
        # Will not "downgrade" to pending because that would require removing the
        # user from the authz course creator group (and that can only be done by an admin).
        user_requested_access(self.user)
        self.assertEqual('granted', get_course_creator_status(self.user))

    def test_add_user_unrequested_staff(self):
        # Users marked as is_staff will not be added to the course creator table.
        add_user_with_status_unrequested(self.admin)
        self.assertIsNone(get_course_creator_status(self.admin))

    def test_add_user_granted_staff(self):
        # Users marked as is_staff will not be added to the course creator table.
        add_user_with_status_granted(self.admin, self.admin)
        self.assertIsNone(get_course_creator_status(self.admin))
