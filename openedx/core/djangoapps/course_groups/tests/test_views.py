"""
Tests for course group views
"""
# pylint: disable=attribute-defined-outside-init
# pylint: disable=no-member


import json
from collections import namedtuple

import pytest
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.http import Http404
from django.test.client import RequestFactory
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import InstructorFactory, StaffFactory, UserFactory
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..cohorts import DEFAULT_COHORT_NAME, get_cohort, get_cohort_by_id, get_cohort_by_name, get_group_info_for_cohort
from ..models import CourseCohort, CourseUserGroup
from ..views import (
    add_users_to_cohort,
    cohort_handler,
    course_cohort_settings_handler,
    link_cohort_to_partition_group,
    remove_user_from_cohort,
    users_in_cohort,
)
from .helpers import CohortFactory, CourseCohortFactory, config_course_cohorts, config_course_cohorts_legacy


class CohortViewsTestCase(ModuleStoreTestCase):
    """
    Base class which sets up a course and staff/non-staff users.
    """
    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.staff_user = UserFactory(is_staff=True, username="staff")
        self.non_staff_user = UserFactory(username="nonstaff")

    def _enroll_users(self, users, course_key):
        """Enroll each user in the specified course"""
        for user in users:
            CourseEnrollment.enroll(user, course_key)

    def _create_cohorts(self):
        """Creates cohorts for testing"""
        self.cohort1_users = [UserFactory() for _ in range(3)]
        self.cohort2_users = [UserFactory() for _ in range(2)]
        self.cohort3_users = [UserFactory() for _ in range(2)]
        self.cohort4_users = [UserFactory() for _ in range(2)]
        self.cohortless_users = [UserFactory() for _ in range(3)]
        self.unenrolled_users = [UserFactory() for _ in range(3)]
        self._enroll_users(
            self.cohort1_users + self.cohort2_users + self.cohort3_users + self.cohortless_users + self.cohort4_users,
            self.course.id
        )
        self.cohort1 = CohortFactory(course_id=self.course.id, users=self.cohort1_users)
        self.cohort2 = CohortFactory(course_id=self.course.id, users=self.cohort2_users)
        self.cohort3 = CohortFactory(course_id=self.course.id, users=self.cohort3_users)
        self.cohort4 = CohortFactory(course_id=self.course.id, users=self.cohort4_users)

        CourseCohortFactory(course_user_group=self.cohort1)
        CourseCohortFactory(course_user_group=self.cohort2)
        CourseCohortFactory(course_user_group=self.cohort3)
        CourseCohortFactory(course_user_group=self.cohort4, assignment_type=CourseCohort.RANDOM)

    def _user_in_cohort(self, username, cohort):
        """
        Return true iff a user with `username` exists in `cohort`.
        """
        return username in [user.username for user in cohort.users.all()]

    def _verify_non_staff_cannot_access(self, view, request_method, view_args):
        """
        Verify that a non-staff user cannot access a given view.

        `view` is the view to test.
        `view_args` is a list of arguments (not including the request) to pass
            to the view.
        """
        if request_method == "GET":
            request = RequestFactory().get("dummy_url")
        elif request_method == "POST":
            request = RequestFactory().post("dummy_url")
        else:
            request = RequestFactory().request()
        request.user = self.non_staff_user
        view_args.insert(0, request)
        self.assertRaises(Http404, view, *view_args)

    def get_handler(self, course, cohort=None, expected_response_code=200, handler=cohort_handler, user=None):
        """
        Call a GET on `handler` for a given `course` and return its response as a dict.
        Raise an exception if response status code is not as expected.
        """
        request = RequestFactory().get("dummy_url")
        if not user:
            user = self.staff_user
        request.user = user
        if cohort:
            response = handler(request, str(course.id), cohort.id)
        else:
            response = handler(request, str(course.id))
        assert response.status_code == expected_response_code
        return json.loads(response.content.decode('utf-8'))

    def put_handler(self, course, cohort=None, data=None, expected_response_code=200, handler=cohort_handler):
        """
        Call a PUT on `handler` for a given `course` and return its response as a dict.
        Raise an exception if response status code is not as expected.
        """
        if not isinstance(data, str):
            data = json.dumps(data or {})
        request = RequestFactory().put(path="dummy path", data=data, content_type="application/json")
        request.user = self.staff_user
        if cohort:
            response = handler(request, str(course.id), cohort.id)
        else:
            response = handler(request, str(course.id))
        assert response.status_code == expected_response_code
        return json.loads(response.content.decode('utf-8'))

    def patch_handler(self, course, cohort=None, data=None, expected_response_code=200, handler=cohort_handler):
        """
        Call a PATCH on `handler` for a given `course` and return its response as a dict.
        Raise an exception if response status code is not as expected.
        """
        if not isinstance(data, str):
            data = json.dumps(data or {})

        request = RequestFactory().patch(path="dummy path", data=data, content_type="application/json")
        request.user = self.staff_user
        if cohort:
            response = handler(request, str(course.id), cohort.id)
        else:
            response = handler(request, str(course.id))
        assert response.status_code == expected_response_code
        return json.loads(response.content.decode('utf-8'))


class CourseCohortSettingsHandlerTestCase(CohortViewsTestCase):
    """
    Tests the `course_cohort_settings_handler` view.
    """

    def get_expected_response(self):
        """
        Returns the static response dict.
        """
        return {
            'is_cohorted': True,
            'id': 1
        }

    def test_non_staff(self):
        """
        Verify that we cannot access course_cohort_settings_handler if we're a non-staff user.
        """
        self._verify_non_staff_cannot_access(course_cohort_settings_handler, "GET", [str(self.course.id)])
        self._verify_non_staff_cannot_access(course_cohort_settings_handler, "PATCH", [str(self.course.id)])

    def test_update_is_cohorted_settings(self):
        """
        Verify that course_cohort_settings_handler is working for is_cohorted via HTTP PATCH.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.get_handler(self.course, handler=course_cohort_settings_handler)

        expected_response = self.get_expected_response()

        assert response == expected_response

        expected_response['is_cohorted'] = False
        response = self.patch_handler(self.course, data=expected_response, handler=course_cohort_settings_handler)

        assert response == expected_response

    def test_enabling_cohorts_does_not_change_division_scheme(self):
        """
        Verify that enabling cohorts on a course does not automatically set the discussion division_scheme
        to cohort.
        """
        config_course_cohorts(self.course, is_cohorted=False, discussion_division_scheme=CourseDiscussionSettings.NONE)

        response = self.get_handler(self.course, handler=course_cohort_settings_handler)

        expected_response = self.get_expected_response()
        expected_response['is_cohorted'] = False
        assert response == expected_response
        assert CourseDiscussionSettings.NONE == CourseDiscussionSettings.get(self.course.id).division_scheme

        expected_response['is_cohorted'] = True
        response = self.patch_handler(self.course, data=expected_response, handler=course_cohort_settings_handler)

        assert response == expected_response
        assert CourseDiscussionSettings.NONE == CourseDiscussionSettings.get(self.course.id).division_scheme

    def test_update_settings_with_missing_field(self):
        """
        Verify that course_cohort_settings_handler return HTTP 400 if required data field is missing from post data.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.patch_handler(self.course, expected_response_code=400, handler=course_cohort_settings_handler)
        assert 'Bad Request' == response.get('error')

    def test_update_settings_with_invalid_field_data_type(self):
        """
        Verify that course_cohort_settings_handler return HTTP 400 if field data type is incorrect.
        """
        config_course_cohorts(self.course, is_cohorted=True)

        response = self.patch_handler(
            self.course,
            data={'is_cohorted': ''},
            expected_response_code=400,
            handler=course_cohort_settings_handler
        )
        assert 'Cohorted must be a boolean' == response.get('error')


class CohortHandlerTestCase(CohortViewsTestCase):
    """
    Tests the `cohort_handler` view.
    """
    def setUp(self):
        super().setUp()
        self.course_staff_user = StaffFactory(
            username="coursestaff",
            course_key=self.course.id
        )
        self.course_instructor_user = InstructorFactory(
            username='courseinstructor',
            course_key=self.course.id
        )

    def verify_lists_expected_cohorts(self, expected_cohorts, response_dict=None, user=None):
        """
        Verify that the server response contains the expected_cohorts.
        If response_dict is None, the list of cohorts is requested from the server.
        """
        if response_dict is None:
            response_dict = self.get_handler(self.course, user=user)

        assert response_dict.get('cohorts') == [{'name': cohort.name, 'id': cohort.id, 'user_count': cohort.user_count,
                                                 'assignment_type': cohort.assignment_type, 'user_partition_id': None,
                                                 'group_id': None} for cohort in expected_cohorts]

    @staticmethod
    def create_expected_cohort(cohort, user_count, assignment_type, user_partition_id=None, group_id=None):
        """
        Create a tuple storing the expected cohort information.
        """
        cohort_tuple = namedtuple("Cohort", "name id user_count assignment_type user_partition_id group_id")
        return cohort_tuple(
            name=cohort.name, id=cohort.id, user_count=user_count, assignment_type=assignment_type,
            user_partition_id=user_partition_id, group_id=group_id
        )

    def test_non_staff(self):
        """
        Verify that we cannot access cohort_handler if we're a non-staff user.
        """
        self._verify_non_staff_cannot_access(cohort_handler, "POST", [str(self.course.id)])
        self._verify_non_staff_cannot_access(cohort_handler, "PUT", [str(self.course.id)])

    def test_course_writers(self):
        """
        Verify course staff and course instructors can access cohort_handler view
        """
        self.verify_lists_expected_cohorts([], user=self.course_staff_user)
        self.verify_lists_expected_cohorts([], user=self.course_instructor_user)

    def test_no_cohorts(self):
        """
        Verify that no cohorts are in response for a course with no cohorts.
        """
        self.verify_lists_expected_cohorts([])

    def test_some_cohorts(self):
        """
        Verify that cohorts are in response for a course with some cohorts.
        """
        self._create_cohorts()
        expected_cohorts = [
            CohortHandlerTestCase.create_expected_cohort(self.cohort1, 3, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort2, 2, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort3, 2, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort4, 2, CourseCohort.RANDOM),
        ]
        self.verify_lists_expected_cohorts(expected_cohorts)

    def test_auto_cohorts(self):
        """
        Verify that auto cohorts are included in the response.
        """
        config_course_cohorts(self.course, is_cohorted=True, auto_cohorts=["AutoGroup1", "AutoGroup2"])

        # Will create manual cohorts cohort1, cohort2, and cohort3.
        self._create_cohorts()
        actual_cohorts = self.get_handler(self.course)
        # Get references to the created auto cohorts.
        auto_cohort_1 = get_cohort_by_name(self.course.id, "AutoGroup1")
        auto_cohort_2 = get_cohort_by_name(self.course.id, "AutoGroup2")
        expected_cohorts = [
            CohortHandlerTestCase.create_expected_cohort(auto_cohort_1, 0, CourseCohort.RANDOM),
            CohortHandlerTestCase.create_expected_cohort(auto_cohort_2, 0, CourseCohort.RANDOM),
            CohortHandlerTestCase.create_expected_cohort(self.cohort1, 3, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort2, 2, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort3, 2, CourseCohort.MANUAL),
            CohortHandlerTestCase.create_expected_cohort(self.cohort4, 2, CourseCohort.RANDOM),
        ]
        self.verify_lists_expected_cohorts(expected_cohorts, actual_cohorts)

    def test_default_cohort(self):
        """
        Verify that the default cohort is not created and included in the response until students are assigned to it.
        """
        # verify the default cohort is not created when the course is not cohorted
        self.verify_lists_expected_cohorts([])

        # create a cohorted course without any auto_cohorts
        config_course_cohorts(self.course, is_cohorted=True)

        # verify the default cohort is not yet created until a user is assigned
        self.verify_lists_expected_cohorts([])

        # create enrolled users
        users = [UserFactory() for _ in range(3)]
        self._enroll_users(users, self.course.id)

        # mimic users accessing the discussion forum
        # Default Cohort will be created here
        for user in users:
            get_cohort(user, self.course.id)

        # verify the default cohort is automatically created
        default_cohort = get_cohort_by_name(self.course.id, DEFAULT_COHORT_NAME)
        actual_cohorts = self.get_handler(self.course)
        self.verify_lists_expected_cohorts(
            [CohortHandlerTestCase.create_expected_cohort(default_cohort, len(users), CourseCohort.RANDOM)],
            actual_cohorts,
        )

        # set auto_cohort_groups
        # these cohort config will have not effect on lms side as we are already done with migrations
        config_course_cohorts_legacy(self.course, cohorted=True, auto_cohort_groups=["AutoGroup"])

        # We should expect the DoesNotExist exception because above cohort config have
        # no effect on lms side so as a result there will be no AutoGroup cohort present
        with pytest.raises(CourseUserGroup.DoesNotExist):
            get_cohort_by_name(self.course.id, "AutoGroup")

    def test_get_single_cohort(self):
        """
        Tests that information for just a single cohort can be requested.
        """
        self._create_cohorts()
        response_dict = self.get_handler(self.course, self.cohort2)
        assert response_dict == {'name': self.cohort2.name, 'id': self.cohort2.id, 'user_count': 2,
                                 'assignment_type': CourseCohort.MANUAL, 'user_partition_id': None, 'group_id': None}

    ############### Tests of adding a new cohort ###############

    def verify_contains_added_cohort(
            self, response_dict, cohort_name, assignment_type=CourseCohort.MANUAL,
            expected_user_partition_id=None, expected_group_id=None
    ):
        """
        Verifies that the cohort was created properly and the correct response was returned.
        """
        created_cohort = get_cohort_by_name(self.course.id, cohort_name)
        assert created_cohort is not None
        assert response_dict == {'name': cohort_name, 'id': created_cohort.id, 'user_count': 0,
                                 'assignment_type': assignment_type,
                                 'user_partition_id': expected_user_partition_id,
                                 'group_id': expected_group_id}

        assert (expected_group_id, expected_user_partition_id) == get_group_info_for_cohort(created_cohort)

    def test_create_new_cohort(self):
        """
        Verify that a new cohort can be created, with and without user_partition_id/group_id information.
        """
        new_cohort_name = "New cohort unassociated to content groups"
        request_data = {'name': new_cohort_name, 'assignment_type': CourseCohort.RANDOM}
        response_dict = self.put_handler(self.course, data=request_data)
        self.verify_contains_added_cohort(response_dict, new_cohort_name, assignment_type=CourseCohort.RANDOM)

        new_cohort_name = "New cohort linked to group"
        data = {
            'name': new_cohort_name,
            'assignment_type': CourseCohort.MANUAL,
            'user_partition_id': 1,
            'group_id': 2
        }
        response_dict = self.put_handler(self.course, data=data)
        self.verify_contains_added_cohort(
            response_dict,
            new_cohort_name,
            expected_user_partition_id=1,
            expected_group_id=2
        )

    def test_create_new_cohort_missing_name(self):
        """
        Verify that we cannot create a cohort without specifying a name.
        """
        response_dict = self.put_handler(self.course, expected_response_code=400)
        assert 'Cohort name must be specified.' == response_dict.get('error')

    def test_create_new_cohort_missing_assignment_type(self):
        """
        Verify that we cannot create a cohort without specifying an assignment type.
        """
        response_dict = self.put_handler(self.course, data={'name': 'COHORT NAME'}, expected_response_code=400)
        assert 'Assignment type must be specified.' == response_dict.get('error')

    def test_create_new_cohort_existing_name(self):
        """
        Verify that we cannot add a cohort with the same name as an existing cohort.
        """
        self._create_cohorts()
        response_dict = self.put_handler(
            self.course, data={'name': self.cohort1.name, 'assignment_type': CourseCohort.MANUAL},
            expected_response_code=400
        )
        assert 'You cannot create two cohorts with the same name' == response_dict.get('error')

    def test_create_new_cohort_missing_user_partition_id(self):
        """
        Verify that we cannot create a cohort with a group_id if the user_partition_id is not also specified.
        """
        data = {'name': "Cohort missing user_partition_id", 'assignment_type': CourseCohort.MANUAL, 'group_id': 2}
        response_dict = self.put_handler(self.course, data=data, expected_response_code=400)
        assert 'If group_id is specified, user_partition_id must also be specified.' == response_dict.get('error')

    ############### Tests of updating an existing cohort ###############

    def test_update_manual_cohort_name(self):
        """
        Test that it is possible to update the name of an existing manual cohort.
        """
        self._create_cohorts()
        updated_name = self.cohort1.name + "_updated"
        data = {'name': updated_name, 'assignment_type': CourseCohort.MANUAL}
        response_dict = self.put_handler(self.course, self.cohort1, data=data)
        assert updated_name == get_cohort_by_id(self.course.id, self.cohort1.id).name
        assert updated_name == response_dict.get('name')
        assert CourseCohort.MANUAL == response_dict.get('assignment_type')

    def test_update_random_cohort_name(self):
        """
        Test that it is possible to update the name of an existing random cohort.
        """
        # Create a new cohort with random assignment
        cohort_name = 'I AM A RANDOM COHORT'
        data = {'name': cohort_name, 'assignment_type': CourseCohort.RANDOM}
        response_dict = self.put_handler(self.course, data=data)

        assert cohort_name == response_dict.get('name')
        assert CourseCohort.RANDOM == response_dict.get('assignment_type')

        # Update the newly created random cohort
        newly_created_cohort = get_cohort_by_name(self.course.id, cohort_name)
        cohort_name = 'I AM AN UPDATED RANDOM COHORT'
        data = {'name': cohort_name, 'assignment_type': CourseCohort.RANDOM}
        response_dict = self.put_handler(self.course, newly_created_cohort, data=data)

        assert cohort_name == get_cohort_by_id(self.course.id, newly_created_cohort.id).name
        assert cohort_name == response_dict.get('name')
        assert CourseCohort.RANDOM == response_dict.get('assignment_type')

    def test_cannot_update_assignment_type_of_single_random_cohort(self):
        """
        Test that it is not possible to update the assignment type of a single random cohort.
        """
        # Create a new cohort with random assignment
        cohort_name = 'I AM A RANDOM COHORT'
        data = {'name': cohort_name, 'assignment_type': CourseCohort.RANDOM}
        response_dict = self.put_handler(self.course, data=data)

        assert cohort_name == response_dict.get('name')
        assert CourseCohort.RANDOM == response_dict.get('assignment_type')

        # Try to update the assignment type of newly created random cohort
        cohort = get_cohort_by_name(self.course.id, cohort_name)
        data = {'name': cohort_name, 'assignment_type': CourseCohort.MANUAL}
        response_dict = self.put_handler(self.course, cohort, data=data, expected_response_code=400)
        assert 'There must be one cohort to which students can automatically be assigned.' == response_dict.get('error')

    def test_update_cohort_group_id(self):
        """
        Test that it is possible to update the user_partition_id/group_id of an existing cohort.
        """
        self._create_cohorts()
        assert (None, None) == get_group_info_for_cohort(self.cohort1)
        data = {
            'name': self.cohort1.name,
            'assignment_type': CourseCohort.MANUAL,
            'group_id': 2,
            'user_partition_id': 3
        }
        response_dict = self.put_handler(self.course, self.cohort1, data=data)
        assert (2, 3) == get_group_info_for_cohort(self.cohort1)
        assert 2 == response_dict.get('group_id')
        assert 3 == response_dict.get('user_partition_id')
        # Check that the name didn't change.
        assert self.cohort1.name == response_dict.get('name')

    def test_update_cohort_remove_group_id(self):
        """
        Test that it is possible to remove the user_partition_id/group_id linking of an existing cohort.
        """
        self._create_cohorts()
        link_cohort_to_partition_group(self.cohort1, 5, 0)
        assert (0, 5) == get_group_info_for_cohort(self.cohort1)
        data = {'name': self.cohort1.name, 'assignment_type': CourseCohort.RANDOM, 'group_id': None}
        response_dict = self.put_handler(self.course, self.cohort1, data=data)
        assert (None, None) == get_group_info_for_cohort(self.cohort1)
        assert response_dict.get('group_id') is None
        assert response_dict.get('user_partition_id') is None

    def test_change_cohort_group_id(self):
        """
        Test that it is possible to change the user_partition_id/group_id of an existing cohort to a
        different group_id.
        """
        self._create_cohorts()
        assert (None, None) == get_group_info_for_cohort(self.cohort4)
        data = {
            'name': self.cohort4.name,
            'assignment_type': CourseCohort.RANDOM,
            'group_id': 2,
            'user_partition_id': 3
        }
        self.put_handler(self.course, self.cohort4, data=data)
        assert (2, 3) == get_group_info_for_cohort(self.cohort4)

        data = {
            'name': self.cohort4.name,
            'assignment_type': CourseCohort.RANDOM,
            'group_id': 1,
            'user_partition_id': 3
        }
        self.put_handler(self.course, self.cohort4, data=data)
        assert (1, 3) == get_group_info_for_cohort(self.cohort4)

    def test_update_cohort_missing_user_partition_id(self):
        """
        Verify that we cannot update a cohort with a group_id if the user_partition_id is not also specified.
        """
        self._create_cohorts()
        data = {'name': self.cohort1.name, 'assignment_type': CourseCohort.RANDOM, 'group_id': 2}
        response_dict = self.put_handler(self.course, self.cohort1, data=data, expected_response_code=400)
        assert 'If group_id is specified, user_partition_id must also be specified.' == response_dict.get('error')


class UsersInCohortTestCase(CohortViewsTestCase):
    """
    Tests the `users_in_cohort` view.
    """
    def request_users_in_cohort(self, cohort, course, requested_page, should_return_bad_request=False):
        """
        Call `users_in_cohort` for a given cohort/requested page, and return
        its response as a dict.  When `should_return_bad_request` is True,
        verify that the response indicates a bad request.
        """
        request = RequestFactory().get("dummy_url", {"page": requested_page})
        request.user = self.staff_user
        response = users_in_cohort(request, str(course.id), cohort.id)

        if should_return_bad_request:
            assert response.status_code == 400
            return

        assert response.status_code == 200
        return json.loads(response.content.decode('utf-8'))

    def verify_users_in_cohort_and_response(self, cohort, response_dict, expected_users, expected_page,
                                            expected_num_pages):
        """
        Check that the `users_in_cohort` response contains the expected list of
        users, page number, and total number of pages for a given cohort.  Also
        verify that those users are actually in the given cohort.
        """
        assert response_dict.get('success')
        assert response_dict.get('page') == expected_page
        assert response_dict.get('num_pages') == expected_num_pages

        returned_users = User.objects.filter(username__in=[user.get("username") for user in response_dict.get("users")])
        assert len(returned_users) == len(expected_users)
        assert set(returned_users) == set(expected_users)
        assert set(returned_users).issubset(cohort.users.all())

    def test_non_staff(self):
        """
        Verify that non-staff users cannot access `check_users_in_cohort`.
        """
        cohort = CohortFactory(course_id=self.course.id, users=[])
        self._verify_non_staff_cannot_access(users_in_cohort, "GET", [str(self.course.id), cohort.id])

    def test_no_users(self):
        """
        Verify that we don't get back any users for a cohort with no users.
        """
        cohort = CohortFactory(course_id=self.course.id, users=[])
        response_dict = self.request_users_in_cohort(cohort, self.course, 1)
        self.verify_users_in_cohort_and_response(
            cohort,
            response_dict,
            expected_users=[],
            expected_page=1,
            expected_num_pages=1
        )

    def test_few_users(self):
        """
        Verify that we get back all users for a cohort when the cohort has
        <=100 users.
        """
        users = [UserFactory() for _ in range(5)]
        cohort = CohortFactory(course_id=self.course.id, users=users)
        response_dict = self.request_users_in_cohort(cohort, self.course, 1)
        self.verify_users_in_cohort_and_response(
            cohort,
            response_dict,
            expected_users=users,
            expected_page=1,
            expected_num_pages=1
        )

    def test_many_users(self):
        """
        Verify that pagination works correctly for cohorts with >100 users.
        """
        users = [UserFactory() for _ in range(101)]
        cohort = CohortFactory(course_id=self.course.id, users=users)
        response_dict_1 = self.request_users_in_cohort(cohort, self.course, 1)
        response_dict_2 = self.request_users_in_cohort(cohort, self.course, 2)
        self.verify_users_in_cohort_and_response(
            cohort,
            response_dict_1,
            expected_users=users[:100],
            expected_page=1,
            expected_num_pages=2
        )
        self.verify_users_in_cohort_and_response(
            cohort,
            response_dict_2,
            expected_users=users[100:],
            expected_page=2,
            expected_num_pages=2
        )

    def test_out_of_range(self):
        """
        Verify that we get a blank page of users when requesting page 0 or a
        page greater than the actual number of pages.
        """
        users = [UserFactory() for _ in range(5)]
        cohort = CohortFactory(course_id=self.course.id, users=users)
        response = self.request_users_in_cohort(cohort, self.course, 0)
        self.verify_users_in_cohort_and_response(
            cohort,
            response,
            expected_users=[],
            expected_page=0,
            expected_num_pages=1
        )
        response = self.request_users_in_cohort(cohort, self.course, 2)
        self.verify_users_in_cohort_and_response(
            cohort,
            response,
            expected_users=[],
            expected_page=2,
            expected_num_pages=1
        )

    def test_non_positive_page(self):
        """
        Verify that we get a `HttpResponseBadRequest` (bad request) when the
        page we request isn't a positive integer.
        """
        users = [UserFactory() for _ in range(5)]
        cohort = CohortFactory(course_id=self.course.id, users=users)
        self.request_users_in_cohort(cohort, self.course, "invalid", should_return_bad_request=True)
        self.request_users_in_cohort(cohort, self.course, -1, should_return_bad_request=True)


class AddUsersToCohortTestCase(CohortViewsTestCase):
    """
    Tests the `add_users_to_cohort` view.
    """
    def setUp(self):
        super().setUp()
        self._create_cohorts()

    def request_add_users_to_cohort(self, users_string, cohort, course, should_raise_404=False):
        """
        Call `add_users_to_cohort` for a given cohort, course, and list of
        users, returning its response as a dict.  When `should_raise_404` is
        True, verify that the request raised a Http404.
        """
        request = RequestFactory().post("dummy_url", {"users": users_string})
        request.user = self.staff_user
        if should_raise_404:
            pytest.raises(Http404, (lambda: add_users_to_cohort(request, str(course.id), cohort.id)))
        else:
            response = add_users_to_cohort(request, str(course.id), cohort.id)
            assert response.status_code == 200

            return json.loads(response.content.decode('utf-8'))

    def verify_added_users_to_cohort(self, response_dict, cohort, course, expected_added, expected_changed,
                                     expected_present, expected_unknown, expected_preassigned, expected_invalid):
        """
        Check that add_users_to_cohort returned the expected response and has
        the expected side effects.

        `expected_added` is a list of users
        `expected_changed` is a list of (user, previous_cohort) tuples
        `expected_present` is a list of (user, email/username) tuples where
            email/username corresponds to the input
        `expected_unknown` is a list of strings corresponding to the input
        'expected_preassigned' is a list of email addresses
        'expected_invalid' is a list of email addresses
        """
        assert response_dict.get('success')
        assert response_dict.get('added') == [{'username': user.username,
                                               'email': user.email} for user in expected_added]
        assert response_dict.get('changed') == [{'username': user.username, 'email': user.email,
                                                 'previous_cohort': previous_cohort} for (user, previous_cohort)
                                                in expected_changed]
        assert response_dict.get('present') == [username_or_email for (_, username_or_email) in expected_present]
        assert response_dict.get('unknown') == expected_unknown
        assert response_dict.get('invalid') == expected_invalid
        assert response_dict.get('preassigned') == expected_preassigned
        for user in expected_added + [user for (user, _) in expected_changed + expected_present]:
            assert CourseUserGroup.objects.get(course_id=course.id,
                                               group_type=CourseUserGroup.COHORT,
                                               users__id=user.id) == cohort

    def test_non_staff(self):
        """
        Verify that non-staff users cannot access `check_users_in_cohort`.
        """
        cohort = CohortFactory(course_id=self.course.id, users=[])
        self._verify_non_staff_cannot_access(
            add_users_to_cohort,
            "POST",
            [str(self.course.id), cohort.id]
        )

    def test_empty(self):
        """
        Verify that adding an empty list of users to a cohort has no result.
        """
        response_dict = self.request_add_users_to_cohort("", self.cohort1, self.course)
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=[],
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_only_added(self):
        """
        Verify that we can add users to their first cohort.
        """
        response_dict = self.request_add_users_to_cohort(
            ",".join([user.username for user in self.cohortless_users]),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=self.cohortless_users,
            expected_changed=[],
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_only_changed(self):
        """
        Verify that we can move users to a different cohort.
        """
        response_dict = self.request_add_users_to_cohort(
            ",".join([user.username for user in self.cohort2_users + self.cohort3_users]),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=(
                [(user, self.cohort2.name) for user in self.cohort2_users] +
                [(user, self.cohort3.name) for user in self.cohort3_users]
            ),
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_only_present(self):
        """
        Verify that we can 'add' users to their current cohort.
        """
        usernames = [user.username for user in self.cohort1_users]
        response_dict = self.request_add_users_to_cohort(
            ",".join(usernames),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=[],
            expected_present=[(user, user.username) for user in self.cohort1_users],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_only_unknown(self):
        """
        Verify that non-existent users are not added.
        """
        usernames = [f"unknown_user{i}" for i in range(3)]
        response_dict = self.request_add_users_to_cohort(
            ",".join(usernames),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=[],
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=usernames,
            expected_invalid=[]
        )

    def test_preassigned_users(self):
        """
        Verify that email addresses can be preassigned for a cohort if the user associated with that email
        address does not yet exist.
        """
        email_addresses = ["email@example.com", "email2@example.com", "email3@example.com"]
        response_dict = self.request_add_users_to_cohort(
            ",".join(email_addresses),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=[],
            expected_present=[],
            expected_preassigned=email_addresses,
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_invalid_email_addresses(self):
        """
        Verify that invalid email addresses return an error.
        """
        email_addresses = ["email@", "@email", "invalid@email."]
        response_dict = self.request_add_users_to_cohort(
            ",".join(email_addresses),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[],
            expected_changed=[],
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=email_addresses
        )

    def check_user_count(self, expected_count, cohort):
        """
        Check that the expected number of users are present in the user_count returned by the view handler
        """
        cohort_listed = False
        for c in self.get_handler(self.course)['cohorts']:
            if c['name'] == cohort.name:
                cohort_listed = True
                assert expected_count == c['user_count']
        assert cohort_listed

    def test_all(self):
        """
        Test all adding conditions together.
        """
        unknowns = [f"unknown_user{i}" for i in range(3)]
        valid_emails = ["email@example.com", "email2@example.com", "email3@example.com"]
        new_users = self.cohortless_users + self.cohort1_users + self.cohort2_users + self.cohort3_users
        response_dict = self.request_add_users_to_cohort(
            ",".join(
                unknowns +
                valid_emails +
                [
                    user.username
                    for user in new_users
                ]
            ),
            self.cohort1,
            self.course
        )

        self.check_user_count(expected_count=len(new_users), cohort=self.cohort1)

        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=self.cohortless_users,
            expected_changed=(
                [(user, self.cohort2.name) for user in self.cohort2_users] +
                [(user, self.cohort3.name) for user in self.cohort3_users]
            ),
            expected_present=[(user, user.username) for user in self.cohort1_users],
            expected_preassigned=valid_emails,
            expected_unknown=unknowns,
            expected_invalid=[]
        )

    def test_emails(self):
        """
        Verify that we can use emails to identify users.
        Expect unknown email address not associated with an account to be preassigned.
        Expect unknown user (neither an email address nor a username) to not be added.
        """
        valid_email_no_account = "unknown_user@example.com"
        unknown_user = "unknown"
        response_dict = self.request_add_users_to_cohort(
            ",".join([
                self.cohort1_users[0].email,
                self.cohort2_users[0].email,
                self.cohortless_users[0].email,
                valid_email_no_account,
                unknown_user
            ]),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[self.cohortless_users[0]],
            expected_changed=[(self.cohort2_users[0], self.cohort2.name)],
            expected_present=[(self.cohort1_users[0], self.cohort1_users[0].email)],
            expected_preassigned=[valid_email_no_account],
            expected_unknown=[unknown_user],
            expected_invalid=[]
        )

    def test_delimiters(self):
        """
        Verify that we can use different types of whitespace to delimit
        usernames in the user string.
        """
        unknown = "unknown_user"
        response_dict = self.request_add_users_to_cohort(
            " {} {}\t{}, \r\n{}".format(
                unknown,
                self.cohort1_users[0].username,
                self.cohort2_users[0].username,
                self.cohortless_users[0].username
            ),
            self.cohort1,
            self.course
        )
        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=[self.cohortless_users[0]],
            expected_changed=[(self.cohort2_users[0], self.cohort2.name)],
            expected_present=[(self.cohort1_users[0], self.cohort1_users[0].username)],
            expected_preassigned=[],
            expected_unknown=[unknown],
            expected_invalid=[]
        )

    def test_can_cohort_unenrolled_users(self):
        """
        Verify that users can be added to a cohort of a course they're not
        enrolled in.  This feature is currently used to pre-cohort users that
        are expected to enroll in a course. Also tests that adding unenrolled
        users does not alter the number of "active" users in the user_count.
        """
        start_count = self.cohort1.users.count()
        unenrolled_usernames = [user.username for user in self.unenrolled_users]
        response_dict = self.request_add_users_to_cohort(
            ",".join(unenrolled_usernames),
            self.cohort1,
            self.course
        )

        self.check_user_count(expected_count=start_count, cohort=self.cohort1)

        self.verify_added_users_to_cohort(
            response_dict,
            self.cohort1,
            self.course,
            expected_added=self.unenrolled_users,
            expected_changed=[],
            expected_present=[],
            expected_preassigned=[],
            expected_unknown=[],
            expected_invalid=[]
        )

    def test_non_existent_cohort(self):
        """
        Verify that an error is raised when trying to add users to a cohort
        which does not belong to the given course.
        """
        users = [UserFactory(username=f"user{i}") for i in range(3)]
        usernames = [user.username for user in users]
        wrong_course_key = CourseLocator("some", "arbitrary", "course")
        wrong_course_cohort = CohortFactory(name="wrong_cohort", course_id=wrong_course_key, users=[])
        self.request_add_users_to_cohort(
            ",".join(usernames),
            wrong_course_cohort,
            self.course,
            should_raise_404=True
        )


class RemoveUserFromCohortTestCase(CohortViewsTestCase):
    """
    Tests the `remove_user_from_cohort` view.
    """
    def request_remove_user_from_cohort(self, username, cohort):
        """
        Call `remove_user_from_cohort` with the given username and cohort.
        """
        if username is not None:
            request = RequestFactory().post("dummy_url", {"username": username})
        else:
            request = RequestFactory().post("dummy_url")
        request.user = self.staff_user
        response = remove_user_from_cohort(request, str(self.course.id), cohort.id)
        assert response.status_code == 200
        return json.loads(response.content.decode('utf-8'))

    def verify_removed_user_from_cohort(self, username, response_dict, cohort, expected_error_msg=None):
        """
        Check that `remove_user_from_cohort` properly removes a user from a
        cohort and returns appropriate success.  If the removal should fail,
        verify that the returned error message matches the expected one.
        """
        if expected_error_msg is None:
            assert response_dict.get('success')
            assert response_dict.get('msg') is None
            assert not self._user_in_cohort(username, cohort)
        else:
            assert not response_dict.get('success')
            assert response_dict.get('msg') == expected_error_msg

    def test_non_staff(self):
        """
        Verify that non-staff users cannot access `check_users_in_cohort`.
        """
        cohort = CohortFactory(course_id=self.course.id, users=[])
        self._verify_non_staff_cannot_access(
            remove_user_from_cohort,
            "POST",
            [str(self.course.id), cohort.id]
        )

    def test_no_username_given(self):
        """
        Verify that we get an error message when omitting a username.
        """
        cohort = CohortFactory(course_id=self.course.id, users=[])
        response_dict = self.request_remove_user_from_cohort(None, cohort)
        self.verify_removed_user_from_cohort(
            None,
            response_dict,
            cohort,
            expected_error_msg='No username specified'
        )

    def test_user_does_not_exist(self):
        """
        Verify that we get an error message when the requested user to remove
        does not exist.
        """
        username = "bogus"
        cohort = CohortFactory(course_id=self.course.id, users=[])
        response_dict = self.request_remove_user_from_cohort(
            username,
            cohort
        )
        self.verify_removed_user_from_cohort(
            username,
            response_dict,
            cohort,
            expected_error_msg=f'No user \'{username}\''
        )

    def test_can_remove_user_not_in_cohort(self):
        """
        Verify that we can "remove" a user from a cohort even if they are not a
        member of that cohort.
        """
        user = UserFactory()
        cohort = CohortFactory(course_id=self.course.id, users=[])
        response_dict = self.request_remove_user_from_cohort(user.username, cohort)
        self.verify_removed_user_from_cohort(user.username, response_dict, cohort)

    def test_can_remove_user_from_cohort(self):
        """
        Verify that we can remove a user from a cohort.
        """
        user = UserFactory()
        cohort = CohortFactory(course_id=self.course.id, users=[user])
        response_dict = self.request_remove_user_from_cohort(user.username, cohort)
        self.verify_removed_user_from_cohort(user.username, response_dict, cohort)
