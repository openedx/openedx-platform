"""
Unit tests for home page view.
"""
from collections import OrderedDict
from datetime import datetime, timedelta

import ddt
import pytz
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from opaque_keys.edx.locator import LibraryLocatorV2
from openedx_content import api as content_api
from organizations.tests.factories import OrganizationFactory
from rest_framework import status

from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.modulestore_migrator import api as migrator_api
from cms.djangoapps.modulestore_migrator.data import CompositionLevel, RepeatHandlingStrategy
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from openedx.core.djangoapps.content_libraries import api as lib_api


@ddt.ddt
class HomePageViewTest(CourseTestCase):
    """
    Tests for HomePageCoursesView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("cms.djangoapps.contentstore:v1:home")
        self.expected_response = {
            "allow_course_reruns": True,
            "allow_to_create_new_org": True,
            "allow_unicode_course_id": False,
            "allowed_organizations": [],
            "allowed_organizations_for_libraries": [],
            "archived_courses": [],
            "can_access_advanced_settings": True,
            "can_create_organizations": True,
            "course_creator_status": "granted",
            "courses": [],
            "in_process_course_actions": [],
            "libraries": [],
            "libraries_enabled": True,
            "libraries_v1_enabled": True,
            "libraries_v2_enabled": False,
            "taxonomies_enabled": True,
            "taxonomy_list_mfe_url": 'http://course-authoring-mfe/taxonomies',
            "request_course_creator_url": "/request_course_creator",
            "rerun_creator_status": True,
            "show_new_library_button": True,
            "show_new_library_v2_button": True,
            "split_studio_home": False,
            "studio_name": settings.STUDIO_NAME,
            "studio_short_name": settings.STUDIO_SHORT_NAME,
            "studio_request_email": "",
            "tech_support_email": "technical@example.com",
            "platform_name": settings.PLATFORM_NAME,
            "user_is_active": True,
        }

    def test_home_page_studio_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(self.expected_response, response.data)

    @override_settings(MEILISEARCH_ENABLED=True)
    def test_home_page_studio_with_meilisearch_enabled(self):
        """Check response content when Meilisearch is enabled"""
        response = self.client.get(self.url)

        expected_response = self.expected_response
        expected_response["libraries_v2_enabled"] = True

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    @override_settings(ORGANIZATIONS_AUTOCREATE=False)
    def test_home_page_studio_with_org_autocreate_disabled(self):
        """Check response content when Organization autocreate is disabled"""
        response = self.client.get(self.url)

        expected_response = self.expected_response
        expected_response["allow_to_create_new_org"] = False

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    def test_taxonomy_list_link(self):
        response = self.client.get(self.url)
        self.assertTrue(response.data['taxonomies_enabled'])
        self.assertEqual(
            response.data['taxonomy_list_mfe_url'],
            f'{settings.COURSE_AUTHORING_MICROFRONTEND_URL}/taxonomies'
        )


@ddt.ddt
class HomePageCoursesViewTest(CourseTestCase):
    """
    Tests for HomePageView.
    """

    def setUp(self):
        super().setUp()
        self.url = reverse("cms.djangoapps.contentstore:v1:courses")
        self.course_overview = CourseOverviewFactory.create(
            id=self.course.id,
            org=self.course.org,
            display_name=self.course.display_name,
            display_number_with_default=self.course.number,
        )
        self.non_staff_client, _ = self.create_non_staff_authed_user_client()

    def test_home_page_response(self):
        """Check successful response content"""
        response = self.client.get(self.url)
        course_id = str(self.course.id)

        expected_response = {
            "archived_courses": [],
            "courses": [{
                "course_key": course_id,
                "display_name": self.course.display_name,
                "lms_link": f'{settings.LMS_ROOT_URL}/courses/{course_id}/jump_to/{self.course.location}',
                "number": self.course.number,
                "org": self.course.org,
                "rerun_link": f'/course_rerun/{course_id}',
                "run": self.course.id.run,
                "url": f'/course/{course_id}',
            }],
            "in_process_course_actions": [],
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    def test_home_page_response_with_api_v2(self):
        """Check successful response content with api v2 modifications.

        When the feature flag is enabled, the courses are exclusively fetched from the CourseOverview model, so
        the values in the courses' list are OrderedDicts instead of the default dictionaries.
        """
        course_id = str(self.course.id)
        expected_response = {
            "archived_courses": [],
            "courses": [
                OrderedDict([
                    ("course_key", course_id),
                    ("display_name", self.course.display_name),
                    ("lms_link", f'{settings.LMS_ROOT_URL}/courses/{course_id}/jump_to/{self.course.location}'),
                    ("number", self.course.number),
                    ("org", self.course.org),
                    ("rerun_link", f'/course_rerun/{course_id}'),
                    ("run", self.course.id.run),
                    ("url", f'/course/{course_id}'),
                ]),
            ],
            "in_process_course_actions": [],
        }

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.data)

    @ddt.data(
        ("active_only", "true", 2, 0),
        ("archived_only", "true", 0, 1),
        ("search", "sample", 1, 0),
        ("search", "demo", 0, 1),
        ("order", "org", 2, 1),
        ("order", "display_name", 2, 1),
        ("order", "number", 2, 1),
        ("order", "run", 2, 1)
    )
    @ddt.unpack
    def test_filter_and_ordering_courses(
        self,
        filter_key,
        filter_value,
        expected_active_length,
        expected_archived_length
    ):
        """Test home page with org filter and ordering for a staff user.

        The test creates an active/archived course, and then filters/orders them using the query parameters.
        """
        archived_course_key = self.store.make_course_key("demo-org", "demo-number", "demo-run")
        CourseOverviewFactory.create(
            display_name="Course (Demo)",
            id=archived_course_key,
            org=archived_course_key.org,
            end=(datetime.now() - timedelta(days=365)).replace(tzinfo=pytz.UTC),
        )
        active_course_key = self.store.make_course_key("sample-org", "sample-number", "sample-run")
        CourseOverviewFactory.create(
            display_name="Course (Sample)",
            id=active_course_key,
            org=active_course_key.org,
        )

        response = self.client.get(self.url, {filter_key: filter_value})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["archived_courses"]), expected_archived_length)
        self.assertEqual(len(response.data["courses"]), expected_active_length)

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
    )
    @ddt.unpack
    def test_filter_and_ordering_no_courses_staff(self, filter_key, filter_value):
        """Test home page with org filter and ordering when there are no courses for a staff user."""
        self.course_overview.delete()

        response = self.client.get(self.url, {filter_key: filter_value})

        self.assertEqual(len(response.data["courses"]), 0)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @ddt.data(
        ("active_only", "true"),
        ("archived_only", "true"),
        ("search", "sample"),
        ("order", "org"),
    )
    @ddt.unpack
    def test_home_page_response_no_courses_non_staff(self, filter_key, filter_value):
        """Test home page with org filter and ordering when there are no courses for a non-staff user."""
        self.course_overview.delete()

        response = self.non_staff_client.get(self.url, {filter_key: filter_value})

        self.assertEqual(len(response.data["courses"]), 0)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@ddt.ddt
class HomePageLibrariesViewTest(LibraryTestCase):
    """
    Tests for HomePageLibrariesView.
    """

    def setUp(self):
        super().setUp()
        # Create an two additional legacy libaries
        self.lib_key_1 = self._create_library(library="lib1")
        self.lib_key_2 = self._create_library(library="lib2")
        self.organization = OrganizationFactory()

        # Create a new v2 library
        self.lib_key_v2 = LibraryLocatorV2.from_string(
            f"lib:{self.organization.short_name}:test-key"
        )
        lib_api.create_library(
            org=self.organization,
            slug=self.lib_key_v2.slug,
            title="Test Library",
        )
        library = lib_api.ContentLibrary.objects.get(slug=self.lib_key_v2.slug)
        learning_package = library.learning_package
        # Create a migration source for the legacy library
        self.url = reverse("cms.djangoapps.contentstore:v1:libraries")
        # Create a collection to migrate this library to
        collection_key = "test-collection"
        content_api.create_collection(
            learning_package_id=learning_package.id,
            key=collection_key,
            title="Test Collection",
            created_by=self.user.id,
        )

        # Migrate both lib_key_1 and lib_key_2 to v2
        # Only make lib_key_1 a "forwarding" migration.
        migrator_api.start_migration_to_library(
            user=self.user,
            source_key=self.lib_key_1,
            target_library_key=self.lib_key_v2,
            target_collection_slug=collection_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=True,
        )
        migrator_api.start_migration_to_library(
            user=self.user,
            source_key=self.lib_key_2,
            target_library_key=self.lib_key_v2,
            target_collection_slug=collection_key,
            composition_level=CompositionLevel.Component,
            repeat_handling_strategy=RepeatHandlingStrategy.Skip,
            preserve_url_slugs=True,
            forward_source_to_target=False,
        )

    def test_home_page_libraries_response(self):
        """Check sucessful response content"""
        self.maxDiff = None
        response = self.client.get(self.url)

        expected_response = {
            "libraries": [
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib',
                    'url': '/library/library-v1:org+lib',
                    'org': 'org',
                    'number': 'lib',
                    'can_edit': True,
                    'is_migrated': False,
                },
                # Second legacy library was migrated so it will include
                # migrated_to_title and migrated_to_key as well
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib1',
                    'url': '/library/library-v1:org+lib1',
                    'org': 'org',
                    'number': 'lib1',
                    'can_edit': True,
                    'is_migrated': True,
                    'migrated_to_title': 'Test Library',
                    'migrated_to_key': 'lib:name0:test-key',
                    'migrated_to_collection_key': 'test-collection',
                    'migrated_to_collection_title': 'Test Collection',
                },
                # Third library was migrated, but not with forwarding.
                # So, it appears just like the unmigrated library.
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib2',
                    'url': '/library/library-v1:org+lib2',
                    'org': 'org',
                    'number': 'lib2',
                    'can_edit': True,
                    'is_migrated': False,
                },
            ]
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.json())

        # Fetch legacy libraries that were migrated to v2
        response = self.client.get(self.url + '?is_migrated=true')

        expected_response = {
            "libraries": [
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib1',
                    'url': '/library/library-v1:org+lib1',
                    'org': 'org',
                    'number': 'lib1',
                    'can_edit': True,
                    'is_migrated': True,
                    'migrated_to_title': 'Test Library',
                    'migrated_to_key': 'lib:name0:test-key',
                    'migrated_to_collection_key': 'test-collection',
                    'migrated_to_collection_title': 'Test Collection',
                }
            ],
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.json())

        # Fetch legacy libraries that were not migrated to v2
        response = self.client.get(self.url + '?is_migrated=false')

        expected_response = {
            "libraries": [
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib',
                    'url': '/library/library-v1:org+lib',
                    'org': 'org',
                    'number': 'lib',
                    'can_edit': True,
                    'is_migrated': False,
                },
                {
                    'display_name': 'Test Library',
                    'library_key': 'library-v1:org+lib2',
                    'url': '/library/library-v1:org+lib2',
                    'org': 'org',
                    'number': 'lib2',
                    'can_edit': True,
                    'is_migrated': False,
                },
            ],
        }

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertDictEqual(expected_response, response.json())
