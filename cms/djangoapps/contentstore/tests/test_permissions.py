"""
Test CRUD for authorization.
"""

import copy

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from cms.djangoapps.contentstore.utils import reverse_url
from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseInstructorRole, CourseStaffRole, OrgInstructorRole, OrgStaffRole
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)


class TestCourseAccess(ModuleStoreTestCase):
    """
    Course-based access (as opposed to access of a non-course xblock)
    """
    def setUp(self):
        """
        Create a staff user and log them in (creating the client).

        Create a pool of users w/o granting them any permissions
        """
        super().setUp()

        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.user_password)

        # create a course via the view handler which has a different strategy for permissions than the factory
        self.course_key = self.store.make_course_key('myu', 'mydept.mycourse', 'myrun')
        course_url = reverse_url('course_handler')
        self.client.ajax_post(
            course_url,
            {
                'org': self.course_key.org,
                'number': self.course_key.course,
                'display_name': 'My favorite course',
                'run': self.course_key.run,
            },
        )

        self.users = self._create_users()

    def _create_users(self):
        """
        Create 8 users and return them
        """
        users = []
        for i in range(8):
            username = f"user{i}"
            email = f"test+user{i}@edx.org"
            user = UserFactory.create(username=username, email=email, password='foo')
            user.is_active = True
            user.save()
            users.append(user)
        return users

    def tearDown(self):
        """
        Reverse the setup
        """
        self.client.logout()
        ModuleStoreTestCase.tearDown(self)  # pylint: disable=non-parent-method-called

    def test_get_all_users(self):
        """
        Test getting all authors for a course where their permissions run the gamut of allowed group
        types.
        """
        # first check the course creator.has explicit access (don't use has_access as is_staff
        # will trump the actual test)
        self.assertTrue(  # noqa: PT009
            CourseInstructorRole(self.course_key).has_user(self.user),
            "Didn't add creator as instructor."
        )
        users = copy.copy(self.users)
        # doesn't use role.users_with_role b/c it's verifying the roles.py behavior
        user_by_role = {}
        # add the misc users to the course in different groups
        for role in [CourseInstructorRole, CourseStaffRole, OrgStaffRole, OrgInstructorRole]:
            user_by_role[role] = []
            # Org-based roles are created via org name, rather than course_key
            if (role is OrgStaffRole) or (role is OrgInstructorRole):
                group = role(self.course_key.org)
            else:
                group = role(self.course_key)
            # NOTE: this loop breaks the roles.py abstraction by purposely assigning
            # users to one of each possible groupname in order to test that has_course_author_access
            # and remove_user work
            user = users.pop()
            group.add_users(user)
            user_by_role[role].append(user)
            self.assertTrue(auth.has_course_author_access(user, self.course_key), f"{user} does not have access")  # pylint: disable=line-too-long  # noqa: PT009

        # test copying course permissions
        copy_course_key = self.store.make_course_key('copyu', 'copydept.mycourse', 'myrun')
        for role in [CourseInstructorRole, CourseStaffRole, OrgStaffRole, OrgInstructorRole]:
            if (role is OrgStaffRole) or (role is OrgInstructorRole):
                auth.add_users(
                    self.user,
                    role(copy_course_key.org),
                    *role(self.course_key.org).users_with_role()
                )
            else:
                auth.add_users(
                    self.user,
                    role(copy_course_key),
                    *role(self.course_key).users_with_role()
                )
        # verify access in copy course and verify that removal from source course w/ the various
        # groupnames works
        for role in [CourseInstructorRole, CourseStaffRole, OrgStaffRole, OrgInstructorRole]:
            for user in user_by_role[role]:
                # forcefully decache the groups: premise is that any real request will not have
                # multiple objects repr the same user but this test somehow uses different instance
                # in above add_users call
                if hasattr(user, '_roles'):
                    del user._roles

                self.assertTrue(auth.has_course_author_access(user, copy_course_key), f"{user} no copy access")  # noqa: PT009  # pylint: disable=line-too-long
                if (role is OrgStaffRole) or (role is OrgInstructorRole):
                    auth.remove_users(self.user, role(self.course_key.org), user)
                else:
                    auth.remove_users(self.user, role(self.course_key), user)
                self.assertFalse(auth.has_course_author_access(user, self.course_key), f"{user} remove didn't work")  # pylint: disable=line-too-long  # noqa: PT009
