"""
Unit tests for contentstore.views.library

More important high-level tests are in contentstore/tests/test_libraries.py
"""


from unittest import mock
from unittest.mock import patch

import ddt
from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from opaque_keys.edx.locator import CourseKey, LibraryLocator
from organizations.api import get_organization_by_short_name
from organizations.exceptions import InvalidOrganizationException

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, CourseTestCase, parse_json
from cms.djangoapps.contentstore.utils import reverse_course_url, reverse_library_url
from cms.djangoapps.course_creators.views import add_user_with_status_granted as grant_course_creator_status
from common.djangoapps.student.roles import LibraryUserRole, CourseStaffRole, CourseInstructorRole
from xmodule.modulestore.tests.factories import LibraryFactory  # lint-amnesty, pylint: disable=wrong-import-order
from cms.djangoapps.course_creators.models import CourseCreator

from common.djangoapps.student import auth

from ..component import get_component_templates
from ..library import user_can_create_library
from ..course import get_allowed_organizations_for_libraries

LIBRARY_REST_URL = '/library/'  # URL for GET/POST requests involving libraries


def make_url_for_lib(key):
    """ Get the RESTful/studio URL for testing the given library """
    if isinstance(key, LibraryLocator):
        key = str(key)
    return LIBRARY_REST_URL + key


@ddt.ddt
@mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_COURSE_CREATION': False})
class UnitTestLibraries(CourseTestCase):
    """
    Unit tests for library views
    """

    def setUp(self):
        super().setUp()

        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.user_password)

    ######################################################
    # Tests for /library/ - list and create libraries:

    # When libraries are disabled, nobody can create libraries
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", False)
    def test_library_creator_status_libraries_not_enabled(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        self.assertEqual(user_can_create_library(nostaff_user, None), False)

    # When creator group is disabled, non-staff users can create libraries
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_no_course_creator_role(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        self.assertEqual(user_can_create_library(nostaff_user, 'An Org'), True)

    # When creator group is enabled, Non staff users cannot create libraries
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_for_enabled_creator_group_setting_for_non_staff_users(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            self.assertEqual(user_can_create_library(nostaff_user, None), False)

    # Global staff can create libraries for any org, even ones that don't exist.
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_is_staff_user(self):
        print(self.user.is_staff)
        self.assertEqual(user_can_create_library(self.user, 'aNyOrg'), True)

    # Global staff can create libraries for any org, but an org has to be supplied.
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_is_staff_user_no_org(self):
        print(self.user.is_staff)
        self.assertEqual(user_can_create_library(self.user, None), False)

    # When creator groups are enabled, global staff can create libraries in any org
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_for_enabled_creator_group_setting_with_is_staff_user(self):
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            self.assertEqual(user_can_create_library(self.user, 'RandomOrg'), True)

    # When creator groups are enabled, course creators can create libraries in any org.
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_course_creator_role_for_enabled_creator_group_setting(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            grant_course_creator_status(self.user, nostaff_user)
            self.assertEqual(user_can_create_library(nostaff_user, 'soMeRandOmoRg'), True)

    # When creator groups are enabled, course staff members can create libraries
    # but only in the org they are course staff for.
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_course_staff_role_for_enabled_creator_group_setting(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            auth.add_users(self.user, CourseStaffRole(self.course.id), nostaff_user)
            self.assertEqual(user_can_create_library(nostaff_user, self.course.org), True)
            self.assertEqual(user_can_create_library(nostaff_user, 'SomEOtherOrg'), False)

    # When creator groups are enabled, course instructor members can create libraries
    # but only in the org they are course staff for.
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_course_instructor_role_for_enabled_creator_group_setting(self):
        _, nostaff_user = self.create_non_staff_authed_user_client()
        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
            auth.add_users(self.user, CourseInstructorRole(self.course.id), nostaff_user)
            self.assertEqual(user_can_create_library(nostaff_user, self.course.org), True)
            self.assertEqual(user_can_create_library(nostaff_user, 'SomEOtherOrg'), False)

    @ddt.data(
        (False, False, True),
        (False, True, False),
        (True, False, True),
        (True, True, False),
        (True, None, False),
        (False, None, True)
    )
    @ddt.unpack
    def test_library_creator_status_settings(self, disable_course, disable_library, expected_status):
        """
        Ensure that the setting DISABLE_LIBRARY_CREATION overrides DISABLE_COURSE_CREATION as expected.
        """
        _, nostaff_user = self.create_non_staff_authed_user_client()
        with mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True):
            with mock.patch.dict(
                "django.conf.settings.FEATURES",
                {
                    "DISABLE_COURSE_CREATION": disable_course,
                    "DISABLE_LIBRARY_CREATION": disable_library
                }
            ):
                self.assertEqual(user_can_create_library(nostaff_user, 'SomEOrg'), expected_status)

    @mock.patch.dict('django.conf.settings.FEATURES', {'DISABLE_COURSE_CREATION': True})
    @mock.patch("cms.djangoapps.contentstore.toggles.libraries_v1_enabled", True)
    def test_library_creator_status_with_no_course_creator_role_and_disabled_nonstaff_course_creation(self):
        """
        Ensure that `DISABLE_COURSE_CREATION` feature works with libraries as well.
        """
        nostaff_client, nostaff_user = self.create_non_staff_authed_user_client()
        self.assertFalse(user_can_create_library(nostaff_user, 'SomEOrg'))

        # To be explicit, this user can GET, but not POST
        get_response = nostaff_client.get_json(LIBRARY_REST_URL)
        post_response = nostaff_client.ajax_post(LIBRARY_REST_URL, {
            'org': 'org', 'library': 'lib', 'display_name': "New Library",
        })
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.status_code, 403)

    @override_settings(ENABLE_CONTENT_LIBRARIES=False)
    def test_with_libraries_disabled(self):
        """
        The library URLs should return 404 if libraries are disabled.
        """
        response = self.client.get_json(LIBRARY_REST_URL)
        self.assertEqual(response.status_code, 404)

    def test_list_libraries(self):
        """
        Test that we can GET /library/ to list all libraries visible to the current user.
        """
        # Create some more libraries
        libraries = [LibraryFactory.create() for _ in range(3)]
        lib_dict = {lib.location.library_key: lib for lib in libraries}

        response = self.client.get_json(LIBRARY_REST_URL)
        self.assertEqual(response.status_code, 200)
        lib_list = parse_json(response)
        self.assertEqual(len(lib_list), len(libraries))
        for entry in lib_list:
            self.assertIn("library_key", entry)
            self.assertIn("display_name", entry)
            key = CourseKey.from_string(entry["library_key"])
            self.assertIn(key, lib_dict)
            self.assertEqual(entry["display_name"], lib_dict[key].display_name)
            del lib_dict[key]  # To ensure no duplicates are matched

    @ddt.data("delete", "put")
    def test_bad_http_verb(self, verb):
        """
        We should get an error if we do weird requests to /library/
        """
        response = getattr(self.client, verb)(LIBRARY_REST_URL)
        self.assertEqual(response.status_code, 405)

    def test_create_library(self):
        """ Create a library. """
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': 'org',
            'library': 'lib',
            'display_name': "New Library",
        })
        self.assertEqual(response.status_code, 200)
        # That's all we check. More detailed tests are in contentstore.tests.test_libraries...

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_CREATOR_GROUP': True})
    def test_lib_create_permission(self):
        """
        Users who are given course creator roles should be able to create libraries.
        """
        self.client.logout()
        ns_user, password = self.create_non_staff_user()
        self.client.login(username=ns_user.username, password=password)
        grant_course_creator_status(self.user, ns_user)
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': 'org', 'library': 'lib', 'display_name': "New Library",
        })
        self.assertEqual(response.status_code, 200)

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_CREATOR_GROUP': False})
    def test_lib_create_permission_no_course_creator_role_and_no_course_creator_group(self):
        """
        Users who are not given course creator roles should still be able to create libraries
        if ENABLE_CREATOR_GROUP is not enabled.
        """
        self.client.logout()
        ns_user, password = self.create_non_staff_user()
        self.client.login(username=ns_user.username, password=password)
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': 'org', 'library': 'lib', 'display_name': "New Library",
        })
        self.assertEqual(response.status_code, 200)

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_CREATOR_GROUP': True})
    def test_lib_create_permission_no_course_creator_role_and_no_course_creator_group_and_no_course_staff_role(self):
        """
        Users who are not given course creator roles or course staff role should not be able to create libraries
        if ENABLE_CREATOR_GROUP is enabled.
        """
        self.client.logout()
        ns_user, password = self.create_non_staff_user()
        self.client.login(username=ns_user.username, password=password)
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': 'org', 'library': 'lib', 'display_name': "New Library",
        })
        self.assertEqual(response.status_code, 403)

    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_CREATOR_GROUP': True})
    def test_lib_create_permission_course_staff_role(self):
        """
        Users who are staff on any existing course should able to create libraries
        if ENABLE_CREATOR_GROUP is enabled.
        """
        self.client.logout()
        ns_user, password = self.create_non_staff_user()
        self.client.login(username=ns_user.username, password=password)

        auth.add_users(self.user, CourseStaffRole(self.course.id), ns_user)
        self.assertTrue(auth.user_has_role(ns_user, CourseStaffRole(self.course.id)))
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': self.course.org, 'library': 'lib', 'display_name': "New Library",
        })
        self.assertEqual(response.status_code, 200)

    @ddt.data(
        {},
        {'org': 'org'},
        {'library': 'lib'},
        {'org': 'C++', 'library': 'lib', 'display_name': 'Lib with invalid characters in key'},
        {'org': 'Org', 'library': 'Wh@t?', 'display_name': 'Lib with invalid characters in key'},
    )
    def test_create_library_invalid(self, data):
        """
        Make sure we are prevented from creating libraries with invalid keys/data
        """
        response = self.client.ajax_post(LIBRARY_REST_URL, data)
        self.assertEqual(response.status_code, 400)

    def test_no_duplicate_libraries(self):
        """
        We should not be able to create multiple libraries with the same key
        """
        lib = LibraryFactory.create()
        lib_key = lib.location.library_key
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': lib_key.org,
            'library': lib_key.library,
            'display_name': "A Duplicate key, same as 'lib'",
        })
        self.assertIn('already a library defined', parse_json(response)['ErrMsg'])
        self.assertEqual(response.status_code, 400)

    @override_settings(ORGANIZATIONS_AUTOCREATE=True)
    def test_library_with_unknown_organization_autocreation(self):
        """
        Test that when automatic organization creation is enabled,
        creating a content library with an unknown organization auto-creates
        said organization.
        """
        with self.assertRaises(InvalidOrganizationException):
            get_organization_by_short_name("org_xyz")
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': "org_xyz",
            'library': "org_test_lib",
            'display_name': "This library's organization doesn't exist... yet.",
        })
        assert response.status_code == 200
        assert get_organization_by_short_name("org_xyz")

    @override_settings(ORGANIZATIONS_AUTOCREATE=False)
    def test_library_with_unknown_organization_validation_error(self):
        """
        Test that when automatic organization creation is disabled,
        creating a content library with an unknown organization raises an error.
        """
        with self.assertRaises(InvalidOrganizationException):
            get_organization_by_short_name("org_xyz")
        response = self.client.ajax_post(LIBRARY_REST_URL, {
            'org': "org_xyz",
            'library': "org_test_lib",
            'display_name': "This library's organization doesn't exist!",
        })
        assert response.status_code == 400
        assert "'org_xyz' is not a valid organization identifier" in parse_json(response)['ErrMsg']
        with self.assertRaises(InvalidOrganizationException):
            get_organization_by_short_name("org_xyz")

    ######################################################
    # Tests for /library/:lib_key/ - get a specific library as JSON or HTML editing view

    def test_get_lib_info(self):
        """
        Test that we can get data about a library (in JSON format) using /library/:key/
        """
        # Create a library
        lib_key = LibraryFactory.create().location.library_key
        # Re-load the library from the modulestore, explicitly including version information:
        lib = self.store.get_library(lib_key, remove_version=False, remove_branch=False)
        version = lib.location.library_key.version_guid
        self.assertNotEqual(version, None)

        response = self.client.get_json(make_url_for_lib(lib_key))
        self.assertEqual(response.status_code, 200)
        info = parse_json(response)
        self.assertEqual(info['display_name'], lib.display_name)
        self.assertEqual(info['library_id'], str(lib_key))
        self.assertEqual(info['previous_version'], None)
        self.assertNotEqual(info['version'], None)
        self.assertNotEqual(info['version'], '')
        self.assertEqual(info['version'], str(version))

    def test_get_lib_edit_html(self):
        """
        Test that we can get the studio view for editing a library using /library/:key/
        """
        lib = LibraryFactory.create()

        response = self.client.get(make_url_for_lib(lib.location.library_key))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<html")
        self.assertContains(response, lib.display_name)

    @ddt.data('library-v1:Nonexistent+library', 'course-v1:Org+Course', 'course-v1:Org+Course+Run', 'invalid')
    def test_invalid_keys(self, key_str):
        """
        Check that various Nonexistent/invalid keys give 404 errors
        """
        response = self.client.get_json(make_url_for_lib(key_str))
        self.assertEqual(response.status_code, 404)

    def test_bad_http_verb_with_lib_key(self):
        """
        We should get an error if we do weird requests to /library/
        """
        lib = LibraryFactory.create()
        for verb in ("post", "delete", "put"):
            response = getattr(self.client, verb)(make_url_for_lib(lib.location.library_key))
            self.assertEqual(response.status_code, 405)

    def test_no_access(self):
        user, password = self.create_non_staff_user()
        self.client.login(username=user, password=password)

        lib = LibraryFactory.create()
        response = self.client.get(make_url_for_lib(lib.location.library_key))
        self.assertEqual(response.status_code, 403)

    def test_get_component_templates(self):
        """
        Verify that templates for adding discussion and advanced components to
        content libraries are not provided.
        """
        lib = LibraryFactory.create()
        lib.advanced_modules = ['lti']
        lib.save()
        templates = [template['type'] for template in get_component_templates(lib, library=True)]
        self.assertIn('problem', templates)
        self.assertNotIn('discussion', templates)
        self.assertNotIn('advanced', templates)
        self.assertNotIn('openassessment', templates)
        self.assertNotIn('library', templates)
        self.assertNotIn('library_v2', templates)
        self.assertNotIn('itembank', templates)

    def test_advanced_problem_types(self):
        """
        Verify that advanced problem types are not provided in problem component for libraries.
        """
        lib = LibraryFactory.create()
        lib.save()

        problem_type_templates = next(
            (
                component['templates']
                for component in get_component_templates(lib, library=True)
                if component['type'] == 'problem'
            ),
            []
        )
        # Each problem template has a category which shows whether problem is a 'problem'
        # or which of the advanced problem type (e.g drag-and-drop-v2).
        problem_type_categories = [problem_template['category'] for problem_template in problem_type_templates]

        for advance_problem_type in settings.ADVANCED_PROBLEM_TYPES:
            self.assertNotIn(advance_problem_type['component'], problem_type_categories)

    def test_manage_library_users(self):
        """
        Simple test that the Library "User Access" view works.
        Also tests that we can use the REST API to assign a user to a library.
        """
        library = LibraryFactory.create()
        extra_user, _ = self.create_non_staff_user()
        manage_users_url = reverse_library_url('manage_library_users', str(library.location.library_key))

        response = self.client.get(manage_users_url)
        self.assertEqual(response.status_code, 200)
        # extra_user has not been assigned to the library so should not show up in the list:
        self.assertNotContains(response, extra_user.username)

        # Now add extra_user to the library:
        user_details_url = reverse_course_url(
            'course_team_handler',
            library.location.library_key, kwargs={'email': extra_user.email}
        )
        edit_response = self.client.ajax_post(user_details_url, {"role": LibraryUserRole.ROLE})
        self.assertIn(edit_response.status_code, (200, 204))

        # Now extra_user should apear in the list:
        response = self.client.get(manage_users_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, extra_user.username)

    def test_component_limits(self):
        """
        Test that component limits in libraries are respected.
        """
        with self.settings(MAX_BLOCKS_PER_CONTENT_LIBRARY=1):
            library = LibraryFactory.create()
            data = {
                'parent_locator': str(library.location),
                'category': 'html'
            }
            response = self.client.ajax_post(reverse('xblock_handler'), data)
            self.assertEqual(response.status_code, 200)

            # Adding another component should cause failure:
            response = self.client.ajax_post(reverse('xblock_handler'), data)
            self.assertEqual(response.status_code, 400)
            self.assertIn('cannot have more than 1 component', parse_json(response)['error'])

    def test_allowed_organizations_for_library(self):
        """
        Test the different organizations that a user can select for creating a library, depending
        on Feature Flags and on user role.
        With organization staff access enabled, a user should be able to select organizations they
        are a staff member of. Else, with creator groups enabled, the user should be able to select
        organizations they are course creator for.
        """
        course_creator = CourseCreator.objects.create(user=self.user, all_organizations=True)
        with patch('cms.djangoapps.course_creators.models.CourseCreator.objects.filter') as mock_filter:
            mock_filter.return_value.first.return_value = course_creator
            with patch('organizations.models.Organization.objects.all') as mock_all:
                mock_all.return_value.values_list.return_value = ['org1', 'org2']
                with patch('common.djangoapps.student.roles.OrgStaffRole.get_orgs_for_user') as get_user_orgs:
                    get_user_orgs.return_value = ['org3']
                    # Call the method under test
                    with mock.patch.dict(
                        'django.conf.settings.FEATURES',
                        {"ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES": False}
                    ):
                        with mock.patch.dict(
                            'django.conf.settings.FEATURES',
                            {"ENABLE_CREATOR_GROUP": False}
                        ):
                            organizations = get_allowed_organizations_for_libraries(self.user)
                            # Assert that the method returned the expected value
                            self.assertEqual(organizations, [])
                        with mock.patch.dict('django.conf.settings.FEATURES', {"ENABLE_CREATOR_GROUP": True}):
                            # Assert that correct org values are returned based on course creator state
                            for course_creator_state in CourseCreator.STATES:
                                course_creator.state = course_creator_state
                                organizations = get_allowed_organizations_for_libraries(self.user)
                                if course_creator_state != CourseCreator.GRANTED:
                                    self.assertEqual(organizations, [])
                                else:
                                    self.assertEqual(organizations, ['org1', 'org2'])
                    with mock.patch.dict(
                        'django.conf.settings.FEATURES',
                        {"ENABLE_ORGANIZATION_STAFF_ACCESS_FOR_CONTENT_LIBRARIES": True}
                    ):
                        organizations = get_allowed_organizations_for_libraries(self.user)
                        self.assertEqual(organizations, ['org3'])
