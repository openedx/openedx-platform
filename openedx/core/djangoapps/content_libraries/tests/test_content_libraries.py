"""
Tests for openedx_content-based Content Libraries
"""
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from io import StringIO
from unittest import skip
from unittest.mock import ANY, patch

import ddt
import tomlkit
from bridgekeeper import perms
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Q
from django.test import override_settings
from django.test.client import Client
from freezegun import freeze_time
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from openedx_authz import api as authz_api
from openedx_authz.constants import roles
from openedx_authz.constants.permissions import VIEW_LIBRARY
from openedx_authz.engine.enforcer import AuthzEnforcer
from openedx_content.models_api import LearningPackage
from organizations.models import Organization
from rest_framework import status
from rest_framework.test import APITestCase
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.content_libraries.constants import CC_4_BY
from openedx.core.djangoapps.content_libraries.tasks import LibraryRestoreTask
from openedx.core.djangoapps.content_libraries.tests.base import (
    URL_BLOCK_GET_HANDLER_URL,
    URL_BLOCK_METADATA_URL,
    URL_BLOCK_RENDER_VIEW,
    URL_BLOCK_XBLOCK_HANDLER,
    ContentLibrariesRestApiTest,
)
from openedx.core.djangoapps.xblock import api as xblock_api
from openedx.core.djangolib.testing.utils import skip_unless_cms

from ..models import ContentLibrary, ContentLibraryPermission
from ..permissions import CAN_VIEW_THIS_CONTENT_LIBRARY, HasPermissionInContentLibraryScope


@skip_unless_cms
@ddt.ddt
class ContentLibrariesTestCase(ContentLibrariesRestApiTest):
    """
    General tests for openedx_content-based Content Libraries

    These tests use the REST API, which in turn relies on the Python API.
    Some tests may use the python API directly if necessary to provide
    coverage of any code paths not accessible via the REST API.

    In general, these tests should
    (1) Use public APIs only - don't directly create data using other methods,
        which results in a less realistic test and ties the test suite too
        closely to specific implementation details.
        (Exception: users can be provisioned using a user factory)
    (2) Assert that fields are present in responses, but don't assert that the
        entire response has some specific shape. That way, things like adding
        new fields to an API response, which are backwards compatible, won't
        break any tests, but backwards-incompatible API changes will.

    WARNING: every test should have a unique library slug, because even though
    the django/mysql database gets reset for each test case, the lookup between
    library slug and bundle UUID does not because it's assumed to be immutable
    and cached forever.
    """

    def test_library_crud(self):
        """
        Test Create, Read, Update, and Delete of a Content Library

        Tests with some non-ASCII chars in slug, title, description.
        """
        # Create:
        lib = self._create_library(
            slug="téstlꜟط", title="A Tést Lꜟطrary", description="Just Téstꜟng", license_type=CC_4_BY,
        )
        expected_data = {
            "id": "lib:CL-TEST:téstlꜟط",
            "org": "CL-TEST",
            "slug": "téstlꜟط",
            "title": "A Tést Lꜟطrary",
            "description": "Just Téstꜟng",
            "license": CC_4_BY,
            "has_unpublished_changes": False,
            "has_unpublished_deletes": False,
        }

        self.assertDictContainsEntries(lib, expected_data)
        # Read:
        lib2 = self._get_library(lib["id"])
        self.assertDictContainsEntries(lib2, expected_data)

        # Update:
        lib3 = self._update_library(lib["id"], title="New Title")
        expected_data["title"] = "New Title"
        self.assertDictContainsEntries(lib3, expected_data)

        # Delete:
        self._delete_library(lib["id"])
        # And confirm it is deleted:
        self._get_library(lib["id"], expect_response=404)
        self._delete_library(lib["id"], expect_response=404)

    def test_library_validation(self):
        """
        You can't create a library with the same slug as an existing library,
        or an invalid slug.
        """
        self._create_library(slug="some-slug", title="Existing Library")

        # Try to create a library+bundle with a duplicate slug
        response = self._create_library(slug="some-slug", title="Duplicate Library", expect_response=400)
        assert response == {
            'slug': 'A library with that ID already exists.',
        }

        response = self._create_library(slug="Invalid Slug!", title="Library with Bad Slug", expect_response=400)
        assert response == {
            'slug': ['Enter a valid “slug” consisting of Unicode letters, numbers, underscores, or hyphens.'],
        }

    def test_library_org_validation(self):
        """
        Staff users can create libraries in any existing or auto-created organization.
        """
        assert Organization.objects.filter(short_name='auto-created-org').count() == 0
        self._create_library(slug="auto-created-org-1", title="Library in an auto-created org", org='auto-created-org')
        assert Organization.objects.filter(short_name='auto-created-org').count() == 1
        self._create_library(slug="existing-org-1", title="Library in an existing org", org="CL-TEST")

    @patch(
        "openedx.core.djangoapps.content_libraries.rest_api.libraries.user_can_create_organizations",
    )
    @patch(
        "openedx.core.djangoapps.content_libraries.rest_api.libraries.get_allowed_organizations_for_libraries",
    )
    @override_settings(ORGANIZATIONS_AUTOCREATE=False)
    def test_library_org_no_autocreate(self, mock_get_allowed_organizations, mock_can_create_organizations):
        """
        When org auto-creation is disabled, user must use one of their allowed orgs.
        """
        mock_can_create_organizations.return_value = False
        mock_get_allowed_organizations.return_value = ["CL-TEST"]
        assert Organization.objects.filter(short_name='auto-created-org').count() == 0
        response = self._create_library(
            slug="auto-created-org-2",
            org="auto-created-org",
            title="Library in an auto-created org",
            expect_response=400,
        )
        assert response == {
            'org': "No such organization 'auto-created-org' found.",
        }

        Organization.objects.get_or_create(
            short_name="not-allowed-org",
            defaults={"name": "Content Libraries Test Org Membership"},
        )
        response = self._create_library(
            slug="not-allowed-org",
            org="not-allowed-org",
            title="Library in an not-allowed org",
            expect_response=400,
        )
        assert response == {
            'org': "User not allowed to create libraries in 'not-allowed-org'.",
        }
        assert mock_can_create_organizations.call_count == 1
        assert mock_get_allowed_organizations.call_count == 1

        self._create_library(
            slug="allowed-org-2",
            org="CL-TEST",
            title="Library in an allowed org",
        )
        assert mock_can_create_organizations.call_count == 2
        assert mock_get_allowed_organizations.call_count == 2

    @skip("This endpoint shouldn't support num_blocks and has_unpublished_*.")
    @patch(
        "openedx.core.djangoapps.content_libraries.rest_api.libraries.LibraryRootView.pagination_class.page_size",
        new=2,
    )
    def test_list_library(self):
        """
        Test the /libraries API and its pagination

        TODO: This test will technically pass, but it's not really meaningful
        because we don't have real data behind num_blocks, last_published,
        has_published_changes, and has_unpublished_deletes. The has_* in
        particular are going to be expensive to compute, particularly if we have
        many large libraries. We also don't use that data for the library list
        page yet.

        We're looking at re-doing a lot of the UX right now, and so I'm holding
        off on making deeper changes. We should either make sure we don't need
        those fields and remove them from the returned results, or else we
        should figure out how to make them more performant.

        I've marked this as @skip to flag it for future review.
        """
        lib1 = self._create_library(slug="some-slug-1", title="Existing Library")
        lib2 = self._create_library(slug="some-slug-2", title="Existing Library")
        lib1['num_blocks'] = lib2['num_blocks'] = 0
        lib1['last_published'] = lib2['last_published'] = None
        lib1['version'] = lib2['version'] = None
        lib1['has_unpublished_changes'] = lib2['has_unpublished_changes'] = False
        lib1['has_unpublished_deletes'] = lib2['has_unpublished_deletes'] = False

        result = self._list_libraries()
        assert len(result) == 2
        assert lib1 in result
        assert lib2 in result
        result = self._list_libraries({'pagination': 'true'})
        assert len(result['results']) == 2
        assert result['next'] is None

        # Create another library which causes number of libraries to exceed the page size
        self._create_library(slug="some-slug-3", title="Existing Library")
        # Verify that if `pagination` param isn't sent, API still honors the max page size.
        # This is for maintaining compatibility with older non pagination-aware clients.
        result = self._list_libraries()
        assert len(result) == 2

        # Pagination enabled:
        # Verify total elements and valid 'next' in page 1
        result = self._list_libraries({'pagination': 'true'})
        assert len(result['results']) == 2
        assert 'page=2' in result['next']
        assert 'pagination=true' in result['next']
        # Verify total elements and null 'next' in page 2
        result = self._list_libraries({'pagination': 'true', 'page': '2'})
        assert len(result['results']) == 1
        assert result['next'] is None

    def test_library_filters(self):
        """
        Test the filters in the list libraries API
        """
        self._create_library(
            slug="test-lib-filter-1", title="Fob", description="Bar",
        )
        self._create_library(
            slug="test-lib-filter-2", title="Library-Title-2", description="Bar-2",
        )
        self._create_library(
            slug="l3", title="Library-Title-3", description="Description",
        )

        Organization.objects.get_or_create(
            short_name="org-test",
            defaults={"name": "Content Libraries Tachyon Exploration & Survey Team"},
        )
        self._create_library(
            slug="l4", title="Library-Title-4",
            description="Library-Description", org='org-test',
        )
        self._create_library(
            slug="l5", title="Library-Title-5", description="Library-Description",
            org='org-test',
        )

        assert len(self._list_libraries()) == 5
        assert len(self._list_libraries({'org': 'org-test'})) == 2
        assert len(self._list_libraries({'text_search': 'test-lib-filter'})) == 2
        assert len(self._list_libraries({'text_search': 'library-title'})) == 4
        assert len(self._list_libraries({'text_search': 'bar'})) == 2
        assert len(self._list_libraries({'text_search': 'org-test'})) == 2
        assert len(self._list_libraries({'org': 'org-test',
                                         'text_search': 'library-title-4'})) == 1

        self.assertOrderEqual(
            self._list_libraries({'order': 'title'}),
            ["test-lib-filter-1", "test-lib-filter-2", "l3", "l4", "l5"],
        )
        self.assertOrderEqual(
            self._list_libraries({'order': '-title'}),
            ["l5", "l4", "l3", "test-lib-filter-2", "test-lib-filter-1"],
        )
        self.assertOrderEqual(
            self._list_libraries({'order': 'created'}),
            ["test-lib-filter-1", "test-lib-filter-2", "l3", "l4", "l5"],
        )
        self.assertOrderEqual(
            self._list_libraries({'order': '-created'}),
            ["l5", "l4", "l3", "test-lib-filter-2", "test-lib-filter-1"],
        )
        # An invalid order doesn't apply any specific ordering to the result, so just
        # check if successfully returned libraries
        assert len(self._list_libraries({'order': 'invalid'})) == 5
        assert len(self._list_libraries({'order': '-invalid'})) == 5

    # General Content Library XBlock tests:

    def test_library_blocks(self):  # pylint: disable=too-many-statements
        """
        Test the happy path of creating and working with XBlocks in a content
        library.

        Tests with some non-ASCII chars in slugs, titles, descriptions.
        """
        admin = UserFactory.create(username="Admin", email="admin@example.com", is_staff=True)  # noqa: F841

        lib = self._create_library(slug="téstlꜟط", title="A Tést Lꜟطrary", description="Tésting XBlocks")
        lib_id = lib["id"]
        assert lib['has_unpublished_changes'] is False

        # A library starts out empty:
        assert self._get_library_blocks(lib_id)['results'] == []

        # Add a 'problem' XBlock to the library:
        create_date = datetime(2024, 6, 6, 6, 6, 6, tzinfo=UTC)
        with freeze_time(create_date):
            block_data = self._add_block_to_library(lib_id, "problem", "problem1")
        self.assertDictContainsEntries(block_data, {
            "id": "lb:CL-TEST:téstlꜟط:problem:problem1",
            "display_name": "Blank Problem",
            "block_type": "problem",
            "has_unpublished_changes": True,
            "last_published": None,
            "published_by": None,
            "last_draft_created": create_date.isoformat().replace('+00:00', 'Z'),
            "last_draft_created_by": "Bob",
        })
        block_id = block_data["id"]

        # now the library should contain one block and have unpublished changes:
        assert self._get_library_blocks(lib_id)['results'] == [block_data]
        assert self._get_library(lib_id)['has_unpublished_changes'] is True

        # Publish the changes:
        publish_date = datetime(2024, 7, 7, 7, 7, 7, tzinfo=UTC)
        with freeze_time(publish_date):
            self._commit_library_changes(lib_id)
        assert self._get_library(lib_id)['has_unpublished_changes'] is False
        # And now the block information should also show that block has no unpublished changes:
        block_data["has_unpublished_changes"] = False
        block_data["last_published"] = publish_date.isoformat().replace('+00:00', 'Z')
        block_data["published_by"] = "Bob"
        block_data["published_display_name"] = "Blank Problem"
        self.assertDictContainsEntries(self._get_library_block(block_id), block_data)
        assert self._get_library_blocks(lib_id)['results'] == [block_data]

        # Now update the block's OLX:
        orig_olx = self._get_library_block_olx(block_id)
        assert '<problem' in orig_olx
        new_olx = """
        <problem display_name="New Multi Choice Question" max_attempts="5">
            <multiplechoiceresponse>
                <p>This is a normal capa problem with unicode 🔥. It has "maximum attempts" set to **5**.</p>
                <label>openedx_content is designed to store.</label>
                <choicegroup type="MultipleChoice">
                    <choice correct="false">XBlock metadata only</choice>
                    <choice correct="true">XBlock data/metadata and associated static asset files</choice>
                    <choice correct="false">Static asset files for XBlocks and courseware</choice>
                    <choice correct="false">XModule metadata only</choice>
                </choicegroup>
            </multiplechoiceresponse>
        </problem>
        """.strip()
        update_date = datetime(2024, 8, 8, 8, 8, 8, tzinfo=UTC)
        with freeze_time(update_date):
            self._set_library_block_olx(block_id, new_olx)
        # now reading it back, we should get that exact OLX (no change to whitespace etc.):
        assert self._get_library_block_olx(block_id) == new_olx
        # And the display name and "unpublished changes" status of the block should be updated:
        self.assertDictContainsEntries(self._get_library_block(block_id), {
            "display_name": "New Multi Choice Question",
            "has_unpublished_changes": True,
            "last_draft_created": update_date.isoformat().replace('+00:00', 'Z')
        })

        # Now view the XBlock's student_view (including draft changes):
        fragment = self._render_block_view(block_id, "student_view")
        assert 'resources' in fragment
        assert 'openedx_content is designed to store.' in fragment['content']

        # Also call a handler to make sure that's working:
        handler_url = self._get_block_handler_url(block_id, "xmodule_handler") + "problem_get"
        problem_get_response = self.client.get(handler_url)
        assert problem_get_response.status_code == 200
        assert 'You have used 0 of 5 attempts' in problem_get_response.content.decode('utf-8')

        # Now delete the block:
        assert self._get_library(lib_id)['has_unpublished_deletes'] is False
        self._delete_library_block(block_id)
        # Confirm it's deleted:
        self._render_block_view(block_id, "student_view", expect_response=404)
        self._get_library_block(block_id, expect_response=404)
        assert self._get_library(lib_id)['has_unpublished_deletes'] is True

        # Now revert all the changes back until the last publish:
        self._revert_library_changes(lib_id)
        assert self._get_library(lib_id)['has_unpublished_deletes'] is False
        assert self._get_library_block_olx(block_id) == orig_olx

        # Now edit and publish the single block instead of the whole library:
        new_olx = "<problem><p>Edited OLX</p></problem>"
        self._set_library_block_olx(block_id, new_olx)
        assert self._get_library_block_olx(block_id) == new_olx
        unpublished_block_data = self._get_library_block(block_id)
        assert unpublished_block_data['has_unpublished_changes'] is True
        block_update_date = datetime(2024, 8, 8, 8, 8, 9, tzinfo=UTC)
        with freeze_time(block_update_date):
            self._publish_library_block(block_id)
        # Confirm the block is now published:
        published_block_data = self._get_library_block(block_id)
        assert published_block_data['last_published'] == block_update_date.isoformat().replace('+00:00', 'Z')
        assert published_block_data['published_by'] == "Bob"
        assert published_block_data['has_unpublished_changes'] is False

        # fin

    def test_library_blocks_studio_view(self):
        """
        Test the happy path of working with an HTML XBlock in a the studio_view of a content library.
        """
        lib = self._create_library(slug="testlib2", title="A Test Library", description="Testing XBlocks")
        lib_id = lib["id"]
        assert lib['has_unpublished_changes'] is False

        # A library starts out empty:
        assert self._get_library_blocks(lib_id)['results'] == []

        # Add a 'html' XBlock to the library:
        create_date = datetime(2024, 6, 6, 6, 6, 6, tzinfo=UTC)
        with freeze_time(create_date):
            block_data = self._add_block_to_library(lib_id, "problem", "problem1")
        self.assertDictContainsEntries(block_data, {
            "id": "lb:CL-TEST:testlib2:problem:problem1",
            "display_name": "Blank Problem",
            "block_type": "problem",
            "has_unpublished_changes": True,
            "last_published": None,
            "published_by": None,
            "last_draft_created": create_date.isoformat().replace('+00:00', 'Z'),
            "last_draft_created_by": "Bob",
        })
        block_id = block_data["id"]

        # now the library should contain one block and have unpublished changes:
        assert self._get_library_blocks(lib_id)['results'] == [block_data]
        assert self._get_library(lib_id)['has_unpublished_changes'] is True

        # Publish the changes:
        publish_date = datetime(2024, 7, 7, 7, 7, 7, tzinfo=UTC)
        with freeze_time(publish_date):
            self._commit_library_changes(lib_id)
        assert self._get_library(lib_id)['has_unpublished_changes'] is False
        # And now the block information should also show that block has no unpublished changes:
        block_data["has_unpublished_changes"] = False
        block_data["last_published"] = publish_date.isoformat().replace('+00:00', 'Z')
        block_data["published_by"] = "Bob"
        block_data["published_display_name"] = "Blank Problem"
        self.assertDictContainsEntries(self._get_library_block(block_id), block_data)
        assert self._get_library_blocks(lib_id)['results'] == [block_data]

        # Now update the block's OLX:
        orig_olx = self._get_library_block_olx(block_id)
        assert '<problem' in orig_olx
        new_olx = "<problem><b>Hello world!</b></problem>"

        update_date = datetime(2024, 8, 8, 8, 8, 8, tzinfo=UTC)
        with freeze_time(update_date):
            self._set_library_block_olx(block_id, new_olx)
        # now reading it back, we should get that exact OLX (no change to whitespace etc.):
        assert self._get_library_block_olx(block_id) == new_olx
        # And the display name and "unpublished changes" status of the block should be updated:
        self.assertDictContainsEntries(self._get_library_block(block_id), {
            "display_name": "Blank Problem",
            "has_unpublished_changes": True,
            "last_draft_created": update_date.isoformat().replace('+00:00', 'Z')
        })

    @patch(
        "openedx.core.djangoapps.content_libraries.rest_api.libraries.LibraryBlocksView.pagination_class.page_size",
        new=2,
    )
    def test_list_library_blocks(self):
        """
        Test the /libraries/{lib_key_str}/blocks API and its pagination
        """
        lib = self._create_library(slug="list_blocks-slug", title="Library 1")
        block1 = self._add_block_to_library(lib["id"], "problem", "problem1")
        self._add_block_to_library(lib["id"], "html", "html1")

        response = self._get_library_blocks(lib["id"])
        result = response['results']
        assert len(response['results']) == 2
        assert block1 in result
        assert response['next'] is None

        self._add_block_to_library(lib["id"], "problem", "problem3")

        # Test pagination
        result = self._get_library_blocks(lib["id"])
        assert len(result['results']) == 2

        assert 'page=2' in result['next']
        result = self._get_library_blocks(lib["id"], {'page': '2'})
        assert len(result['results']) == 1
        assert result['next'] is None

    def test_library_blocks_filters(self):
        """
        Test the filters in the list libraries API
        """
        lib = self._create_library(slug="test-lib-blocks", title="Title")
        block1 = self._add_block_to_library(lib["id"], "problem", "foo-bar")
        self._add_block_to_library(lib["id"], "video", "vid-baz")
        self._add_block_to_library(lib["id"], "html", "html-baz")
        self._add_block_to_library(lib["id"], "problem", "foo-baz")
        self._add_block_to_library(lib["id"], "problem", "bar-baz")

        self._set_library_block_olx(block1["id"], "<problem display_name=\"DisplayName\"></problem>")

        assert len(self._get_library_blocks(lib['id'])['results']) == 5
        assert len(self._get_library_blocks(lib['id'], {'text_search': 'Foo'})['results']) == 2
        assert len(self._get_library_blocks(lib['id'], {'text_search': 'Display'})['results']) == 1
        assert len(self._get_library_blocks(lib['id'], {'text_search': 'Video'})['results']) == 1
        assert len(self._get_library_blocks(lib['id'], {'text_search': 'Foo', 'block_type': 'video'})['results']) == 0
        assert len(self._get_library_blocks(lib['id'], {'text_search': 'Baz', 'block_type': 'video'})['results']) == 1
        assert 2 == len(
            self._get_library_blocks(
                lib['id'],
                {'text_search': 'Baz', 'block_type': ['video', 'html']}
            )['results']
        )
        assert len(self._get_library_blocks(lib['id'], {'block_type': 'video'})['results']) == 1
        assert len(self._get_library_blocks(lib['id'], {'block_type': 'problem'})['results']) == 3
        assert len(self._get_library_blocks(lib['id'], {'block_type': 'squirrel'})['results']) == 0

    def test_library_not_found(self):
        """Test that requests fail with 404 when the library does not exist"""
        valid_not_found_key = 'lb:valid:key:video:1'
        response = self.client.get(URL_BLOCK_METADATA_URL.format(block_key=valid_not_found_key))
        self.assertEqual(response.status_code, 404)  # noqa: PT009
        self.assertEqual(response.json(), {  # noqa: PT009
            'detail': "Content Library 'lib:valid:key' does not exist",
        })

    def test_block_not_found(self):
        """Test that requests fail with 404 when the library exists but the XBlock does not"""
        lib = self._create_library(
            slug="test_lib_block_event_delete",
            title="Event Test Library",
            description="Testing event in library"
        )
        library_key = LibraryLocatorV2.from_string(lib['id'])
        non_existent_block_key = LibraryUsageLocatorV2(lib_key=library_key, block_type='video', usage_id='123')
        response = self.client.get(URL_BLOCK_METADATA_URL.format(block_key=non_existent_block_key))
        self.assertEqual(response.status_code, 404)  # noqa: PT009
        self.assertEqual(response.json(), {  # noqa: PT009
            'detail': f"The component '{non_existent_block_key}' does not exist.",
        })

    # Test that permissions are enforced for content libraries

    def test_library_permissions(self):  # pylint: disable=too-many-statements
        """
        Test that permissions are enforced for content libraries, and that
        permissions can be read and manipulated using the REST API (which in
        turn tests the python API).

        This is a single giant test case, because that optimizes for the fastest
        test run time, even though it can make debugging failures harder.

        TODO: The asset permissions part of this test have been commented out
        for now. These should be re-enabled after we re-implement them over
        openedx_content data models.
        """
        # Create a few users to use for all of these tests:
        admin = UserFactory.create(username="Admin", email="admin@example.com", is_staff=True)
        author = UserFactory.create(username="Author", email="author@example.com")
        reader = UserFactory.create(username="Reader", email="reader@example.com")
        group = Group.objects.create(name="group1")
        author_group_member = UserFactory.create(username="GroupMember", email="groupmember@example.com")
        author_group_member.groups.add(group)
        random_user = UserFactory.create(username="Random", email="random@example.com")
        never_added = UserFactory.create(username="Never", email="never@example.com")

        # Library CRUD #########################################################

        # Create a library, owned by "Admin"
        with self.as_user(admin):
            lib = self._create_library(slug="permtest", title="Permission Test Library", description="Testing")
            lib_id = lib["id"]
            # By default, "public learning" and public read access are disallowed.
            assert lib['allow_public_learning'] is False
            assert lib['allow_public_read'] is False

            # By default, the creator of a new library is the only admin
            data = self._get_library_team(lib_id)
            assert len(data) == 1
            self.assertDictContainsEntries(data[0], {
                "username": admin.username, "group_name": None, "access_level": "admin",
            })

            # Add the other users to the content library:
            self._set_user_access_level(lib_id, author.username, access_level="author")
            # Delete it, add it again.
            self._remove_user_access(lib_id, author.username)
            self._set_user_access_level(lib_id, author.username, access_level="author")
            # Add one of them via the email-based creation endpoint.
            self._add_user_by_email(lib_id, reader.email, access_level="read")
            self._set_group_access_level(lib_id, group.name, access_level="author")

            team_response = self._get_library_team(lib_id)
            assert len(team_response) == 4
            # We'll use this one later.
            reader_grant = {"username": reader.username, "group_name": None, "access_level": "read"}
            # The response should also always be sorted in a specific order (by username and group name):
            expected_response = [
                {"username": None, "group_name": "group1", "access_level": "author"},
                {"username": admin.username, "group_name": None, "access_level": "admin"},
                {"username": author.username, "group_name": None, "access_level": "author"},
                reader_grant,
            ]
            for entry, expected in zip(team_response, expected_response):  # noqa: B905
                self.assertDictContainsEntries(entry, expected)

        # A random user cannot get the library nor its team:
        with self.as_user(random_user):
            self._get_library(lib_id, expect_response=403)
            self._get_library_team(lib_id, expect_response=403)
            self._add_user_by_email(lib_id, never_added.email, access_level="read", expect_response=403)

        # But every authorized user can:
        for user in [admin, author, author_group_member]:
            with self.as_user(user):
                self._get_library(lib_id)
                data = self._get_library_team(lib_id)
                assert data == team_response
                data = self._get_user_access_level(lib_id, reader.username)
                assert data == {**reader_grant, 'username': 'Reader', 'email': 'reader@example.com'}

        # A user with only read permission can get data about the library but not the team:
        with self.as_user(reader):
            self._get_library(lib_id)
            self._get_library_team(lib_id, expect_response=403)
            self._get_user_access_level(lib_id, author.username, expect_response=403)
            self._add_user_by_email(lib_id, never_added.email, access_level="read", expect_response=403)

        # Users without admin access cannot delete the library nor change its team:
        for user in [author, reader, author_group_member, random_user]:
            with self.as_user(user):
                self._delete_library(lib_id, expect_response=403)
                self._set_user_access_level(lib_id, author.username, access_level="admin", expect_response=403)
                self._set_user_access_level(lib_id, admin.username, access_level=None, expect_response=403)
                self._set_user_access_level(lib_id, random_user.username, access_level="read", expect_response=403)
                self._remove_user_access(lib_id, admin.username, expect_response=403)
                self._add_user_by_email(lib_id, never_added.email, access_level="read", expect_response=403)

        # Users with author access (or higher) can edit the library's properties:
        with self.as_user(author):
            self._update_library(lib_id, description="Revised description")
        with self.as_user(author_group_member):
            self._update_library(lib_id, title="New Library Title")
        # But other users cannot:
        with self.as_user(reader):
            self._update_library(lib_id, description="Prohibited description", expect_response=403)
        with self.as_user(random_user):
            self._update_library(lib_id, title="I can't set this title", expect_response=403)
        # Verify the permitted changes were made:
        with self.as_user(admin):
            data = self._get_library(lib_id)
            assert data['description'] == 'Revised description'
            assert data['title'] == 'New Library Title'

        # Library XBlock editing ###############################################

        # users with read permission or less cannot add blocks:
        for user in [reader, random_user]:
            with self.as_user(user):
                self._add_block_to_library(lib_id, "problem", "problem1", expect_response=403)
        # But authors and admins can:
        with self.as_user(admin):
            self._add_block_to_library(lib_id, "problem", "problem1")
        with self.as_user(author):
            self._add_block_to_library(lib_id, "problem", "problem2")
        with self.as_user(author_group_member):
            block3_data = self._add_block_to_library(lib_id, "problem", "problem3")
            block3_key = block3_data["id"]

        # At this point, the library contains 3 draft problem XBlocks.

        # A random user cannot read OLX nor assets (this library has allow_public_read False):
        with self.as_user(random_user):
            self._get_library_block_olx(block3_key, expect_response=403)
            self._get_library_block_fields(block3_key, expect_response=403)
            self._get_library_block_assets(block3_key, expect_response=403)
            self._get_library_block_asset(block3_key, file_name="static/whatever.png", expect_response=403)
            # Nor can they preview the block:
            self._render_block_view(block3_key, view_name="student_view", expect_response=403)
        # Even if we grant allow_public_read, then they can't:
        with self.as_user(admin):
            self._update_library(lib_id, allow_public_read=True)
            self._set_library_block_asset(block3_key, "static/whatever.png", b"data")
        with self.as_user(random_user):
            self._get_library_block_olx(block3_key, expect_response=403)
            self._get_library_block_fields(block3_key, expect_response=403)
            # But he can preview the block:
            self._render_block_view(block3_key, view_name="student_view")
            # self._get_library_block_assets(block3_key)
            # self._get_library_block_asset(block3_key, file_name="whatever.png")

        # Users without authoring permission cannot edit nor publish nor delete XBlocks:
        for user in [reader, random_user]:
            with self.as_user(user):
                self._set_library_block_olx(block3_key, "<problem/>", expect_response=403)
                self._set_library_block_fields(block3_key, {"data": "<problem />", "metadata": {}}, expect_response=403)
                self._set_library_block_asset(block3_key, "static/test.txt", b"data", expect_response=403)
                self._publish_library_block(block3_key, expect_response=403)
                self._delete_library_block(block3_key, expect_response=403)
                self._commit_library_changes(lib_id, expect_response=403)
                self._revert_library_changes(lib_id, expect_response=403)

        # But users with author permission can:
        with self.as_user(author_group_member):
            olx = self._get_library_block_olx(block3_key)
            self._set_library_block_olx(block3_key, olx)
            self._set_library_block_fields(block3_key, {"data": olx, "metadata": {}})
            self._get_library_block_assets(block3_key)
            self._set_library_block_asset(block3_key, "static/test.txt", b"data")
            self._get_library_block_asset(block3_key, file_name="static/test.txt")
            self._delete_library_block(block3_key)
            self._publish_library_block(block3_key)
            self._commit_library_changes(lib_id)
            self._revert_library_changes(lib_id)  # This is a no-op after the commit, but should still have 200 response

        # Users without authoring permission cannot commit Xblock changes:
        # First we need to add some unpublished changes
        with self.as_user(admin):
            block4_data = self._add_block_to_library(lib_id, "problem", "problem4")
            block5_data = self._add_block_to_library(lib_id, "problem", "problem5")
            block4_key = block4_data["id"]
            block5_key = block5_data["id"]
            self._set_library_block_olx(block4_key, "<problem/>")
            self._set_library_block_olx(block5_key, "<problem/>")

    def test_no_lockout(self):
        """
        Test that administrators cannot be removed if they are the only administrator granted access.
        """
        admin = UserFactory.create(username="Admin", email="admin@example.com", is_staff=True)
        successor = UserFactory.create(username="Successor", email="successor@example.com")
        with self.as_user(admin):
            lib = self._create_library(slug="permtest", title="Permission Test Library", description="Testing")
            # Fail to downgrade permissions.
            self._remove_user_access(lib_key=lib['id'], username=admin.username, expect_response=400)
            # Promote another user.
            self._set_user_access_level(
                lib_key=lib['id'], username=successor.username, access_level="admin",
            )
            self._remove_user_access(lib_key=lib['id'], username=admin.username)

    def test_library_blocks_limit(self):
        """
        Test that libraries don't allow more than specified blocks
        """
        with self.settings(MAX_BLOCKS_PER_CONTENT_LIBRARY=1):
            lib = self._create_library(
                slug="test_lib_limits",
                title="Limits Test Library",
                description="Testing XBlocks limits in a library"
            )
            lib_id = lib["id"]
            self._add_block_to_library(lib_id, "html", "html1")
            # Second block should throw error
            self._add_block_to_library(lib_id, "problem", "problem1", expect_response=400)

    def test_library_paste_xblock(self):
        """
        Check the a new block is created in the library after pasting from clipboard.
        The content of the new block should match the content of the block in the clipboard.
        """
        # Importing here since this was failing when tests ran in the LMS
        from openedx.core.djangoapps.content_staging.api import save_xblock_to_user_clipboard

        # Create user to perform tests on
        author = UserFactory.create(username="Author", email="author@example.com", is_staff=True)
        with self.as_user(author):
            lib = self._create_library(
                slug="test_lib_paste_clipboard",
                title="Paste Clipboard Test Library",
                description="Testing pasting clipboard in library"
            )
            lib_id = lib["id"]

            # Add a 'problem' XBlock to the library:
            block_data = self._add_block_to_library(lib_id, "problem", "problem1")

            # Get the usage_key of the created block
            library_key = LibraryLocatorV2.from_string(lib_id)
            usage_key = LibraryUsageLocatorV2(
                lib_key=library_key,
                block_type="problem",
                usage_id="problem1"
            )

            # Add an asset to the block before copying
            self._set_library_block_asset(usage_key, "static/hello.txt", b"Hello World!")

            # Get the XBlock created in the previous step
            block = xblock_api.load_block(usage_key, user=author)

            # Copy the block to the user's clipboard
            save_xblock_to_user_clipboard(block, author.id)

            # Paste the content of the clipboard into the library
            paste_data = self._paste_clipboard_content_in_library(lib_id)
            pasted_usage_key = LibraryUsageLocatorV2.from_string(paste_data["id"])
            self._get_library_block_asset(pasted_usage_key, "static/hello.txt")

            # Compare the two text files
            src_data = self.client.get(f"/library_assets/blocks/{usage_key}/static/hello.txt").getvalue()
            dest_data = self.client.get(f"/library_assets/blocks/{pasted_usage_key}/static/hello.txt").getvalue()
            assert src_data == dest_data

            # Check that the new block was created after the paste and it's content matches
            # the the block in the clipboard
            self.assertDictContainsEntries(self._get_library_block(paste_data["id"]), {
                **block_data,
                "last_draft_created_by": None,
                "last_draft_created": paste_data["last_draft_created"],
                "created": paste_data["created"],
                "modified": paste_data["modified"],
                "id": f"lb:CL-TEST:test_lib_paste_clipboard:problem:{pasted_usage_key.block_id}",
            })

    def test_start_library_backup(self):
        """
        Test starting a backup operation on a content library.
        """
        author = UserFactory.create(username="Author", email="author@example.com", is_staff=True)
        with self.as_user(author):
            lib = self._create_library(
                slug="test_lib_backup",
                title="Backup Test Library",
                description="Testing backup for library"
            )
            lib_id = lib["id"]
            response = self._start_library_backup_task(lib_id)
            assert response["task_id"] is not None

    def test_get_library_backup_status(self):
        """
        Test getting the status of a backup operation on a content library.
        """
        author = UserFactory.create(username="Author", email="author@example.com", is_staff=True)
        with self.as_user(author):
            lib = self._create_library(
                slug="test_lib_backup_status",
                title="Backup Status Test Library",
                description="Testing backup status for library"
            )
            lib_id = lib["id"]
            response = self._start_library_backup_task(lib_id)
            task_id = response["task_id"]

            # Now check the status of the backup task
            status_response = self._get_library_backup_task(lib_id, task_id)
            assert status_response["state"] in ["Pending", "Exporting", "Succeeded", "Failed"]

    @override_settings(LIBRARY_ENABLED_BLOCKS=['problem', 'video', 'html'])
    def test_library_get_enabled_blocks(self):
        expected = [
            {"block_type": "html", "display_name": "Text"},
            {"block_type": "problem", "display_name": "Problem"},
            {"block_type": "video", "display_name": "Video"},
        ]

        author = UserFactory.create(username="Author", email="author@example.com", is_staff=True)
        with self.as_user(author):
            lib = self._create_library(
                slug="test_lib_enabled_blocks",
                title="Get Enabled Blocks Test Library",
                description="Testing get enabled blocks from library"
            )
            lib_id = lib["id"]
            block_types = self._get_library_block_types(lib_id)
            assert [dict(item) for item in block_types] == expected

    def test_draft_history_empty_after_publish(self):
        """
        A block with no unpublished changes since its last publish has an empty draft history.
        """
        lib = self._create_library(slug="draft-hist-empty", title="Draft History Empty")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)

        history = self._get_block_draft_history(block_key)
        assert history == []

    def test_draft_history_shows_unpublished_edits(self):
        """
        Draft history contains entries for edits made since the last publication,
        ordered most-recent-first, with the correct fields.
        """
        with freeze_time(datetime(2026, 1, 1, tzinfo=UTC)):
            lib = self._create_library(slug="draft-hist-edits", title="Draft History Edits")
            block = self._add_block_to_library(lib["id"], "problem", "prob1")
            block_key = block["id"]

        with freeze_time(datetime(2026, 2, 1, tzinfo=UTC)):
            self._publish_library_block(block_key)

        edit1_time = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
        with freeze_time(edit1_time):
            self._set_library_block_olx(block_key, "<problem><p>edit 1</p></problem>")

        edit2_time = datetime(2026, 4, 2, 10, 0, 0, tzinfo=UTC)
        with freeze_time(edit2_time):
            self._set_library_block_olx(block_key, "<problem><p>edit 2</p></problem>")

        history = self._get_block_draft_history(block_key)
        assert len(history) == 2
        assert history[0]["changed_at"] == edit2_time.isoformat().replace("+00:00", "Z")
        assert history[1]["changed_at"] == edit1_time.isoformat().replace("+00:00", "Z")
        entry = history[0]
        assert "contributor" in entry
        assert "title" in entry
        assert "action" in entry

    def test_draft_history_action_renamed(self):
        """
        When the title changes between versions, the action is 'renamed'.
        """
        lib = self._create_library(slug="draft-hist-rename", title="Draft History Rename")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)
        self._set_library_block_olx(
            block_key,
            '<problem display_name="New Title"><p>content</p></problem>',
        )

        history = self._get_block_draft_history(block_key)
        assert len(history) >= 1
        assert history[0]["action"] == "renamed"

    def test_draft_history_action_edited(self):
        """
        When only the content changes (not the title), the action is 'edited'.
        """
        lib = self._create_library(slug="draft-hist-edit", title="Draft History Edit")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)
        self._set_library_block_olx(block_key, "<problem><p>changed content</p></problem>")

        history = self._get_block_draft_history(block_key)
        assert len(history) >= 1
        assert history[0]["action"] == "edited"

    def test_draft_history_action_created(self):
        """
        When a block is first created (old_version=None), the action is 'created'.
        """
        lib = self._create_library(slug="draft-hist-create", title="Draft History Create")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        history = self._get_block_draft_history(block_key)
        assert len(history) >= 1
        assert history[-1]["action"] == "created"
        assert history[-1]["old_version"] == 0
        assert history[-1]["new_version"] is not None

    def test_draft_history_action_deleted(self):
        """
        When a block is soft-deleted (new_version=None), the action is 'deleted'.
        """
        lib = self._create_library(slug="draft-hist-delete", title="Draft History Delete")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)
        self._delete_library_block(block_key)

        history = self._get_block_draft_history(block_key)
        assert len(history) >= 1
        assert history[0]["action"] == "deleted"

    def test_draft_history_cleared_after_publish(self):
        """
        After publishing, the draft history resets to empty.
        """
        lib = self._create_library(slug="draft-hist-clear", title="Draft History Clear")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)
        self._set_library_block_olx(block_key, "<problem><p>unpublished</p></problem>")
        assert len(self._get_block_draft_history(block_key)) >= 1

        self._publish_library_block(block_key)
        assert self._get_block_draft_history(block_key) == []

    def test_draft_history_nonexistent_block(self):
        """
        Requesting draft history for a non-existent block returns 404.
        """
        self._get_block_draft_history("lb:CL-TEST:draft-hist-404:problem:nope", expect_response=404)

    def test_draft_history_permissions(self):
        """
        A user without library access receives 403.
        """
        lib = self._create_library(slug="draft-hist-auth", title="Draft History Auth")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._set_library_block_olx(block_key, "<problem><p>edit</p></problem>")

        unauthorized = UserFactory.create(username="noauth-draft", password="edx")
        with self.as_user(unauthorized):
            self._get_block_draft_history(block_key, expect_response=403)

    def test_publish_history_empty_before_first_publish(self):
        """
        A block that has never been published has an empty publish history.
        """
        lib = self._create_library(slug="hist-empty", title="History Empty")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        history = self._get_block_publish_history(block["id"])
        assert history == []

    def test_publish_history_after_single_publish(self):
        """
        Post-Verawood: After one direct component publish (direct=True) the history
        contains exactly one group with the correct publisher, timestamp, contributor,
        and a single entry in direct_published_entities for the component itself.
        """
        lib = self._create_library(slug="hist-single", title="History Single")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        publish_time = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
        with freeze_time(publish_time):
            self._publish_library_block(block_key)

        history = self._get_block_publish_history(block_key)
        assert len(history) == 1
        group = history[0]
        assert group["published_by"] == self.user.username
        assert group["published_at"] == publish_time.isoformat().replace("+00:00", "Z")
        assert isinstance(group["publish_log_uuid"], str)
        assert any(c["username"] == self.user.username for c in group["contributors"])
        # Post-Verawood: component was directly published → single entry for itself
        assert len(group["direct_published_entities"]) == 1
        entity = group["direct_published_entities"][0]
        assert entity["entity_key"] == block_key
        assert entity["entity_type"] == "problem"

    def test_publish_history_deleted_block_retains_title(self):
        """
        When a block is soft-deleted and published, the direct_published_entities
        entry shows the block's last known title (from old_version), not an empty string.
        """
        lib = self._create_library(slug="hist-delete-title", title="History Delete Title")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._set_library_block_olx(
            block_key,
            '<problem display_name="My Problem Title"><p>content</p></problem>',
        )
        self._publish_library_block(block_key)
        self._delete_library_block(block_key)
        self._publish_library_block(block_key)

        history = self._get_block_publish_history(block_key)
        # Most recent publish is the deletion
        deletion_group = history[0]
        assert len(deletion_group["direct_published_entities"]) == 1
        assert deletion_group["direct_published_entities"][0]["title"] == "My Problem Title"

    def test_publish_history_multiple_publishes(self):
        """
        Multiple publish events are returned newest-first.
        """
        lib = self._create_library(slug="hist-multi", title="History Multi")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        first_publish = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        with freeze_time(first_publish):
            self._publish_library_block(block_key)

        self._set_library_block_olx(block_key, "<problem><p>v2</p></problem>")

        second_publish = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
        with freeze_time(second_publish):
            self._publish_library_block(block_key)

        history = self._get_block_publish_history(block_key)
        assert len(history) == 2
        assert history[0]["published_at"] == second_publish.isoformat().replace("+00:00", "Z")
        assert history[1]["published_at"] == first_publish.isoformat().replace("+00:00", "Z")

    def test_publish_history_tracks_contributors(self):
        """
        Contributors for the first publish include the block creator.
        Note: set_library_block_olx does not record created_by, so OLX
        edits are not tracked as contributions.
        """
        lib = self._create_library(slug="hist-contrib", title="History Contributors")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        with freeze_time(datetime(2026, 1, 1, tzinfo=UTC)):
            self._publish_library_block(block_key)

        history = self._get_block_publish_history(block_key)
        assert len(history) == 1
        group = history[0]
        assert any(c["username"] == self.user.username for c in group["contributors"])

    def test_publish_history_entries(self):
        """
        The entries endpoint returns the individual draft change records for a publish event.
        """
        lib = self._create_library(slug="hist-entries", title="History Entries")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        with freeze_time(datetime(2026, 2, 15, tzinfo=UTC)):
            self._set_library_block_olx(block_key, "<problem><p>edit 1</p></problem>")
        with freeze_time(datetime(2026, 2, 20, tzinfo=UTC)):
            self._set_library_block_olx(block_key, "<problem><p>edit 2</p></problem>")

        with freeze_time(datetime(2026, 3, 1, tzinfo=UTC)):
            self._publish_library_block(block_key)

        history = self._get_block_publish_history(block_key)
        assert len(history) == 1
        publish_log_uuid = history[0]["publish_log_uuid"]

        entries = self._get_block_publish_history_entries(block_key, publish_log_uuid)
        assert len(entries) >= 1
        entry = entries[0]
        assert "contributor" in entry
        assert "changed_at" in entry
        assert "title" in entry
        assert "action" in entry
        assert "old_version" in entry
        assert "new_version" in entry

    def test_draft_history_deleted_has_null_new_version(self):
        """
        Deleted draft history entry exposes new_version as null.
        """
        lib = self._create_library(slug="draft-hist-delete-null", title="Draft History Delete Null")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        self._publish_library_block(block_key)
        self._delete_library_block(block_key)

        history = self._get_block_draft_history(block_key)
        assert len(history) >= 1
        assert history[0]["action"] == "deleted"
        assert history[0]["old_version"] > 0
        assert history[0]["new_version"] is None

    def test_publish_history_entries_unknown_uuid(self):
        """
        Requesting entries for a publish_log_uuid unrelated to this component returns an empty list.
        """
        lib = self._create_library(slug="hist-baduid", title="History Bad UUID")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        with freeze_time(datetime(2026, 1, 1, tzinfo=UTC)):
            self._publish_library_block(block_key)

        fake_uuid = str(uuid.uuid4())
        entries = self._get_block_publish_history_entries(block_key, fake_uuid, expect_response=200)
        assert entries == []

    def test_publish_history_nonexistent_block(self):
        """
        Requesting publish history for a non-existent block returns 404.
        """
        self._get_block_publish_history("lb:CL-TEST:hist-404:problem:nope", expect_response=404)

    def test_publish_history_permissions(self):
        """
        A user without library access receives 403.
        """
        lib = self._create_library(slug="hist-auth", title="History Auth")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        with freeze_time(datetime(2026, 1, 1, tzinfo=UTC)):
            self._publish_library_block(block_key)

        unauthorized = UserFactory.create(username="noauth-hist", password="edx")
        with self.as_user(unauthorized):
            self._get_block_publish_history(block_key, expect_response=403)

    # --- Post-Verawood publish history tests ---

    def test_post_verawood_component_published_directly(self):
        """
        Post-Verawood, direct=True: when a component is published directly,
        direct_published_entities has a single entry for the component itself.
        The component's own history and the container's history both show the
        component as the directly published entity.
        """
        lib = self._create_library(slug="pv-comp-direct", title="PV Comp Direct")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])

        # Publish the component directly (not the unit)
        self._publish_library_block(block_key)

        # Component history: direct_published_entities = [component]
        comp_history = self._get_block_publish_history(block_key)
        assert len(comp_history) == 1
        entities = comp_history[0]["direct_published_entities"]
        assert len(entities) == 1
        assert entities[0]["entity_key"] == block_key
        assert entities[0]["entity_type"] == "problem"
        # scope_entity_key is always the component itself for component history
        assert comp_history[0]["scope_entity_key"] == block_key

        # Container history: same publish log → same direct_published_entities
        unit_history = self._get_container_publish_history(unit_key)
        assert len(unit_history) == 1
        entities = unit_history[0]["direct_published_entities"]
        assert len(entities) == 1
        assert entities[0]["entity_key"] == block_key
        assert entities[0]["entity_type"] == "problem"
        # Post-Verawood container group: scope_entity_key is null (frontend uses current container)
        assert unit_history[0]["scope_entity_key"] is None

    def test_post_verawood_unit_published_directly(self):
        """
        Post-Verawood, direct=True on the Unit: when a Unit is published directly,
        the Unit's history shows the unit as directly published. The child component's
        history shows the unit as the directly published entity (direct=False on component).
        """
        lib = self._create_library(slug="pv-unit-direct", title="PV Unit Direct")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])

        # Publish the unit directly (component is published as a dependency)
        self._publish_container(unit_key)

        # Container history: 1 group, unit is direct
        unit_history = self._get_container_publish_history(unit_key)
        assert len(unit_history) == 1
        entities = unit_history[0]["direct_published_entities"]
        assert len(entities) == 1
        assert entities[0]["entity_key"] == unit_key
        assert entities[0]["entity_type"] == "unit"
        assert unit_history[0]["scope_entity_key"] is None

        # Component history: 1 group, unit is the directly published entity
        comp_history = self._get_block_publish_history(block_key)
        assert len(comp_history) == 1
        entities = comp_history[0]["direct_published_entities"]
        assert len(entities) == 1
        assert entities[0]["entity_key"] == unit_key
        assert entities[0]["entity_type"] == "unit"
        assert comp_history[0]["scope_entity_key"] == block_key

    def test_post_verawood_container_history_merges_same_publish_log(self):
        """
        Post-Verawood: when the Unit and a Component are both touched in the same
        PublishLog, the container history returns ONE merged group (not two separate
        groups as in Pre-Verawood).
        """
        lib = self._create_library(slug="pv-merged", title="PV Merged")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])

        self._publish_container(unit_key)

        unit_history = self._get_container_publish_history(unit_key)
        # Post-Verawood: ONE merged group for the entire PublishLog
        assert len(unit_history) == 1

    def test_post_verawood_container_history_entries_scope(self):
        """
        Post-Verawood: the entries endpoint for a container returns entries for all
        entities in scope (container + descendants) that participated in the PublishLog,
        not just the container itself.
        """
        lib = self._create_library(slug="pv-entries-scope", title="PV Entries Scope")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])

        self._publish_container(unit_key)

        unit_history = self._get_container_publish_history(unit_key)
        assert len(unit_history) == 1
        publish_log_uuid = unit_history[0]["publish_log_uuid"]

        entries = self._get_container_publish_history_entries(unit_key, publish_log_uuid)
        # Post-Verawood: entries for both the unit and the component
        assert len(entries) >= 1
        item_types = {e["item_type"] for e in entries}
        assert "unit" in item_types
        assert "problem" in item_types

    def test_post_verawood_multiple_publishes_stay_separate(self):
        """
        Post-Verawood: two separate publish events produce two separate groups,
        ordered most-recent-first.
        """
        lib = self._create_library(slug="pv-multi", title="PV Multi")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])

        first_publish = datetime(2026, 1, 1, tzinfo=UTC)
        with freeze_time(first_publish):
            self._publish_container(unit_key)

        self._set_library_block_olx(block_key, "<problem><p>v2</p></problem>")

        second_publish = datetime(2026, 2, 1, tzinfo=UTC)
        with freeze_time(second_publish):
            self._publish_container(unit_key)

        unit_history = self._get_container_publish_history(unit_key)
        assert len(unit_history) == 2
        assert unit_history[0]["published_at"] == second_publish.isoformat().replace("+00:00", "Z")
        assert unit_history[1]["published_at"] == first_publish.isoformat().replace("+00:00", "Z")

    # --- Pre-Verawood publish history tests ---
    # Pre-Verawood records have direct=None. We simulate them by publishing and
    # then backfilling direct=None on the resulting PublishLogRecords, mirroring
    # what the 0007_publishlogrecord_direct migration does for historical data.

    def test_pre_verawood_component_history_uses_component_as_entity(self):
        """
        Pre-Verawood (direct=None): component history has one group per publish event.
        direct_published_entities has a single approximated entry for the component itself.
        """
        from openedx_content.models_api import PublishLogRecord

        lib = self._create_library(slug="prev-comp", title="PreV Comp")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._publish_library_block(block_key)

        # Simulate Pre-Verawood by backfilling direct=None
        PublishLogRecord.objects.all().update(direct=None)

        history = self._get_block_publish_history(block_key)
        assert len(history) == 1
        entities = history[0]["direct_published_entities"]
        assert len(entities) == 1
        assert entities[0]["entity_key"] == block_key
        assert entities[0]["entity_type"] == "problem"
        assert history[0]["scope_entity_key"] == block_key

    def test_pre_verawood_container_history_produces_separate_groups(self):
        """
        Pre-Verawood (direct=None): when a Unit and Component are published in the
        same PublishLog, the container history produces SEPARATE groups — one per
        entity (unlike Post-Verawood which merges into one group).
        """
        from openedx_content.models_api import PublishLogRecord

        lib = self._create_library(slug="prev-separate", title="PreV Separate")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])
        self._publish_container(unit_key)

        # Simulate Pre-Verawood by backfilling direct=None
        PublishLogRecord.objects.all().update(direct=None)

        unit_history = self._get_container_publish_history(unit_key)
        # Pre-Verawood: one group per entity (unit + component = 2 groups)
        assert len(unit_history) == 2
        # Each group's scope_entity_key matches its own entity_key
        for group in unit_history:
            assert group["scope_entity_key"] == group["direct_published_entities"][0]["entity_key"]

    def test_pre_verawood_container_history_entries_only_container_itself(self):
        """
        Pre-Verawood (direct=None): the entries endpoint returns entries only for
        the container itself, not for descendant components (old behavior preserved).
        """
        from openedx_content.models_api import PublishLogRecord

        lib = self._create_library(slug="prev-entries", title="PreV Entries")
        unit = self._create_container(lib["id"], "unit", "u1", "Unit 1")
        unit_key = unit["id"]
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]
        self._add_container_children(unit_key, [block_key])
        self._publish_container(unit_key)

        # Simulate Pre-Verawood by backfilling direct=None
        PublishLogRecord.objects.all().update(direct=None)

        unit_history = self._get_container_publish_history(unit_key)
        # Find the group whose approximated entity_key is the unit itself
        unit_group = next(
            g for g in unit_history
            if g["direct_published_entities"][0]["entity_key"] == unit_key
        )
        publish_log_uuid = unit_group["publish_log_uuid"]

        entries = self._get_container_publish_history_entries(unit_key, publish_log_uuid)
        # Pre-Verawood: entries only for the container itself
        assert len(entries) >= 1
        # All entries should be for the unit, not the component
        for entry in entries:
            assert entry["item_type"] == "unit"

    def test_creation_entry_returns_first_version(self):
        """
        The creation entry corresponds to the first time the block was saved,
        with action='created' and the correct fields populated.
        """
        lib = self._create_library(slug="creation-entry-basic", title="Creation Entry Basic")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        entry = self._get_block_creation_entry(block_key)

        assert entry is not None
        assert entry["action"] == "created"
        assert entry["item_type"] == "problem"
        assert entry["old_version"] == 0
        assert entry["new_version"] == 1
        assert "changed_at" in entry
        assert "title" in entry
        assert "contributor" in entry

    def test_creation_entry_unchanged_after_edits(self):
        """
        Subsequent edits and publishes do not affect the creation entry — it
        always reflects the first saved version.
        """
        lib = self._create_library(slug="creation-entry-stable", title="Creation Entry Stable")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        # Record the creation entry before any edits
        entry_before = self._get_block_creation_entry(block_key)

        self._set_library_block_olx(block_key, "<problem><p>edited</p></problem>")
        self._publish_library_block(block_key)

        entry_after = self._get_block_creation_entry(block_key)

        assert entry_after["changed_at"] == entry_before["changed_at"]
        assert entry_after["action"] == "created"

    def test_creation_entry_nonexistent_block(self):
        """
        Requesting the creation entry for a non-existent block returns 404.
        """
        self._get_block_creation_entry("lb:CL-TEST:creation-404:problem:nope", expect_response=404)

    def test_creation_entry_permissions(self):
        """
        A user without library access receives 403.
        """
        lib = self._create_library(slug="creation-entry-auth", title="Creation Entry Auth")
        block = self._add_block_to_library(lib["id"], "problem", "prob1")
        block_key = block["id"]

        unauthorized = UserFactory.create(username="noauth-creation", password="edx")
        with self.as_user(unauthorized):
            self._get_block_creation_entry(block_key, expect_response=403)

    def test_container_creation_entry_returns_first_version(self):
        """
        The container creation entry corresponds to the first time the container was
        saved, with action='created' and item_type matching the container type.
        """
        lib = self._create_library(slug="ct-creation-basic", title="Container Creation Basic")
        unit = self._create_container(lib["id"], "unit", slug="unit1", display_name="My Unit")
        unit_key = unit["id"]

        entry = self._get_container_creation_entry(unit_key)

        assert entry is not None
        assert entry["action"] == "created"
        assert entry["item_type"] == "unit"
        assert entry["old_version"] == 0
        assert entry["new_version"] == 1
        assert entry["title"] == "My Unit"
        assert "changed_at" in entry
        assert "contributor" in entry

    def test_container_creation_entry_unchanged_after_edits(self):
        """
        Subsequent edits and publishes do not affect the creation entry — it always
        reflects the first saved version of the container.
        """
        lib = self._create_library(slug="ct-creation-stable", title="Container Creation Stable")
        unit = self._create_container(lib["id"], "unit", slug="unit1", display_name="Original Title")
        unit_key = unit["id"]

        entry_before = self._get_container_creation_entry(unit_key)

        self._update_container(unit_key, display_name="Updated Title")
        self._publish_container(unit_key)

        entry_after = self._get_container_creation_entry(unit_key)

        assert entry_after["changed_at"] == entry_before["changed_at"]
        assert entry_after["action"] == "created"
        assert entry_after["title"] == "Original Title"

    def test_container_creation_entry_permissions(self):
        """
        A user without library access receives 403 for the container creation entry.
        """
        lib = self._create_library(slug="ct-creation-auth", title="Container Creation Auth")
        unit = self._create_container(lib["id"], "unit", slug="unit1", display_name="Auth Unit")
        unit_key = unit["id"]

        unauthorized = UserFactory.create(username="noauth-ct-creation", password="edx")
        with self.as_user(unauthorized):
            self._get_container_creation_entry(unit_key, expect_response=403)


class LibraryRestoreViewTestCase(ContentLibrariesRestApiTest):
    """
    Tests for LibraryRestoreView endpoints.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.package_author_data = {
            "username": "test_author",
            "email": "author@example.com",
            "first_name": "Test",
            "last_name": "Author",
        }
        cls.org_short_name = "CL-TEST"
        cls.library_slug = "LIB_C001"
        cls.learning_package_key = f"lib:{cls.org_short_name}:{cls.library_slug}"

        cls.learning_package_data = {
            "key": cls.learning_package_key,
            "title": "Demo Learning Package",
            "description": "A demo learning package for testing.",
            "created": "2025-10-05T18:23:45.180535Z",
            "updated": "2025-10-05T18:23:45.180535Z",
        }

        cls.learning_package_metadata = {
            "format_version": 1,
            "created_at": "2025-10-05T18:23:45.180535Z",
            "created_by": cls.package_author_data["username"],
            "created_by_email": cls.package_author_data["email"],
            "origin_server": "cms.test",
        }

        toml_data = {
            "learning_package": cls.learning_package_data,
            "meta": cls.learning_package_metadata,
        }

        toml_content = tomlkit.dumps(toml_data)

        cls.tmp_file = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        zip_path = cls.tmp_file.name

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("package.toml", toml_content)

    @classmethod
    def tearDownClass(cls):
        cls.tmp_file.close()
        os.remove(cls.tmp_file.name)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        # The parent class provides a staff self.user ("Bob") and self.organization ("CL-TEST")

        # Create additional users
        self.admin_user = UserFactory.create(username="Admin", email="admin@example.com", is_staff=True)
        self.non_admin_user = UserFactory.create(username="NonAdmin", email="non_admin@example.com")
        self.learning_package_author = UserFactory.create(**self.package_author_data)

        # Prepare the ZIP file for upload
        with open(self.tmp_file.name, "rb") as f:
            self.uploaded_zip_file = SimpleUploadedFile("test.zip", f.read(), content_type="application/zip")

    def _create_user_task_status(
        self,
        user=None,
        task_id='',
        state=UserTaskStatus.SUCCEEDED,
        total_steps=5,
        task_class='test_rest_api.sample_task',
        name='SampleTask',
    ):
        """
        Helper method to create a UserTaskStatus instance.
        """
        user = user or self.user
        return UserTaskStatus.objects.create(
            user=user,
            task_id=task_id or str(uuid.uuid4()),
            state=state,
            total_steps=total_steps,
            task_class=task_class,
            name=name,
        )

    def test_restore_library_success(self):
        """
        Test successful task creation for library restore by admin user.
        """
        ## POST the zip file to start restore task
        with self.as_user(self.admin_user):
            response_data = self._start_library_restore_task(self.uploaded_zip_file)

        self.assertIn('task_id', response_data)  # noqa: PT009
        self.assertIsNotNone(response_data['task_id'])  # noqa: PT009

        ## GET the task status and result (task is run synchronously in tests)
        with self.as_user(self.admin_user):
            response_data = self._get_library_restore_task(response_data['task_id'])

        self.assertIn('state', response_data)  # noqa: PT009
        self.assertEqual(response_data['state'], 'Succeeded')  # noqa: PT009

        self.assertIn('result', response_data)  # noqa: PT009
        task_result = response_data.get('result', {})

        # Validate the learning package data in the result
        expected = {
            "learning_package_id": ANY,
            "key": ANY,
            "title": self.learning_package_data["title"],
            "org": self.org_short_name,
            "slug": self.library_slug,
            "archive_key": self.learning_package_key,
            "collections": 0,
            "components": 0,
            "containers": 0,
            "sections": 0,
            "subsections": 0,
            "units": 0,
            "created_on_server": self.learning_package_metadata["origin_server"],
            "created_at": ANY,
            "created_by": {
                "username": self.learning_package_author.username,
                "email": self.learning_package_author.email,
            },
        }

        self.assertIn('learning_package_id', task_result)  # noqa: PT009
        self.assertTrue(LearningPackage.objects.filter(pk=task_result['learning_package_id']).exists())  # noqa: PT009

        for key, value in expected.items():
            self.assertEqual(task_result[key], value)  # noqa: PT009

    def test_create_content_library_from_restore(self):
        """
        Test that a content library is created as part of the library restore process.
        """
        with self.as_user(self.admin_user):
            response_data = self._start_library_restore_task(self.uploaded_zip_file)

        self.assertIn('task_id', response_data)  # noqa: PT009
        self.assertIsNotNone(response_data['task_id'])  # noqa: PT009

        with self.as_user(self.admin_user):
            response_data = self._get_library_restore_task(response_data['task_id'])

        self.assertIn('state', response_data)  # noqa: PT009
        self.assertEqual(response_data['state'], 'Succeeded')  # noqa: PT009

        task_result = response_data.get('result', {})
        self.assertIn('learning_package_id', task_result)  # noqa: PT009
        learning_package_id = task_result['learning_package_id']
        self.assertTrue(LearningPackage.objects.filter(pk=learning_package_id).exists())  # noqa: PT009

        library_title = "Restored Library"
        library_description = "A library restored from a learning package"

        with self.as_user(self.admin_user):
            create_response_data = self._create_library(
                org=self.org_short_name,
                slug=self.library_slug,
                title=library_title,
                description=library_description,
                learning_package=learning_package_id,
            )

        self.assertIn('id', create_response_data)  # noqa: PT009
        library_locator = LibraryLocatorV2.from_string(create_response_data['id'])
        content_library = ContentLibrary.objects.get_by_key(library_locator)

        self.assertIsNotNone(content_library)  # noqa: PT009
        self.assertEqual(content_library.learning_package.id, learning_package_id)  # noqa: PT009
        self.assertEqual(content_library.learning_package.title, library_title)  # noqa: PT009
        self.assertEqual(content_library.learning_package.description, library_description)  # noqa: PT009
        self.assertIn(self.org_short_name, content_library.library_key.org)  # noqa: PT009
        self.assertIn(self.library_slug, content_library.library_key.slug)  # noqa: PT009

    def test_restore_library_unauthorized(self):
        """
        Test that non-admin users cannot start a library restore task.
        """
        with self.as_user(self.non_admin_user):
            self._start_library_restore_task(self.uploaded_zip_file, expect_response=403)

    def test_restore_library_invalid_file(self):
        """
        Test that uploading a non-ZIP file returns a 400 error.
        """
        non_zip_file = SimpleUploadedFile(
            "test.txt",
            b'This is not a ZIP file',
            content_type='text/plain'
        )

        with self.as_user(self.admin_user):
            self._start_library_restore_task(non_zip_file, expect_response=400)

    def test_get_restore_task_unfinished(self):
        """
        Test that attempting to get the status of an unfinished task returns an appropriate response.
        """
        # Create a UserTaskStatus in PENDING state
        pending_task_status = self._create_user_task_status(state=UserTaskStatus.PENDING)

        with patch(
            'openedx.core.djangoapps.content_libraries.rest_api.libraries.get_object_or_404',
            return_value=pending_task_status
        ):
            response_data = self._get_library_restore_task(pending_task_status.task_id)

        expected = {
            "state": UserTaskStatus.PENDING,
            "result": None,
            "error": None,
            "error_log": None,
        }

        self.assertEqual(response_data, expected)  # noqa: PT009

        in_progress_task_status = self._create_user_task_status(state=UserTaskStatus.IN_PROGRESS)

        with patch(
            'openedx.core.djangoapps.content_libraries.rest_api.libraries.get_object_or_404',
            return_value=in_progress_task_status
        ):
            response_data = self._get_library_restore_task(in_progress_task_status.task_id)

        expected["state"] = UserTaskStatus.IN_PROGRESS
        self.assertEqual(response_data, expected)  # noqa: PT009

    def test_task_user_mismatch(self):
        """
        A user should not be able to access another user's library restore task.
        """
        with self.as_user(self.admin_user):
            post_response = self._start_library_restore_task(self.uploaded_zip_file)

        other_user = UserFactory.create(username="OtherUser", email="other@example.com", is_staff=True)

        with self.as_user(other_user):
            self._get_library_restore_task(post_response['task_id'], expect_response=404)

    def test_task_artifact_text_not_json(self):
        """
        Test that a task artifact that is not JSON returns an appropriate response.
        """
        task_status = self._create_user_task_status(state=UserTaskStatus.SUCCEEDED)

        # Manually create a UserTaskArtifact with non-JSON text content
        artifact_text = 'Some unexpected text content that is not JSON.'
        UserTaskArtifact.objects.create(
            status=task_status,
            text=artifact_text,
            name=LibraryRestoreTask.ARTIFACT_NAMES[task_status.state],
        )

        with patch(
            'openedx.core.djangoapps.content_libraries.rest_api.libraries.get_object_or_404',
            return_value=task_status
        ):
            response_data = self._get_library_restore_task(task_status.task_id)

        expected = {
            "state": UserTaskStatus.SUCCEEDED,
            "result": None,
            "error": ANY,
            "error_log": None,
        }

        self.assertEqual(response_data, expected)  # noqa: PT009

    def test_failed_task_with_error_log(self):
        """
        If a task fails with an error log, include the url to the log
        """
        error_result = {
            'status': 'error',
            'log_file_error': StringIO("Library restore failed: An unexpected error occurred during processing."),
            'lp_restore_data': None,
            'backup_metadata': None,
        }

        with self.as_user(self.admin_user):
            with patch(
                "openedx.core.djangoapps.content_libraries.tasks.content_api.load_learning_package",
                return_value=error_result
            ):
                response = self._start_library_restore_task(self.uploaded_zip_file)

        with self.as_user(self.admin_user):
            task_data = self._get_library_restore_task(response['task_id'])

        expected = {
            'state': 'Failed',
            'error': ANY,
            'error_log': ANY,
            'result': None,
        }

        self.assertEqual(task_data, expected)  # noqa: PT009

    def test_uncaught_error_creates_error_log(self):
        """
        If an uncaught error occurs during task execution, an error log should be created
        """
        with self.as_user(self.admin_user):
            with patch(
                "openedx.core.djangoapps.content_libraries.tasks.content_api.load_learning_package",
                side_effect=Exception("Uncaught exception during processing.")
            ):
                response = self._start_library_restore_task(self.uploaded_zip_file)

        with self.as_user(self.admin_user):
            task_data = self._get_library_restore_task(response['task_id'])

        expected = {
            'state': 'Failed',
            'error': ANY,
            'error_log': ANY,
            'result': None,
        }

        self.assertEqual(task_data, expected)  # noqa: PT009


@skip_unless_cms
class ContentLibrariesAuthZTestCase(ContentLibrariesRestApiTest):
    """
    Tests for Content Libraries AuthZ integration via openedx-authz.

    These tests verify the HasPermissionInContentLibraryScope Bridgekeeper rule
    integrates correctly with the openedx-authz authorization system (Casbin).
    See: https://github.com/openedx/openedx-authz/

    IMPORTANT: These tests explicitly remove legacy ContentLibraryPermission grants
    to ensure ONLY the AuthZ system is being tested, not the legacy fallback.
    """

    def setUp(self):
        super().setUp()
        # The parent class provides self.user (a staff user) and self.organization
        # Set up admin_user as an alias to self.user for test readability
        self.admin_user = self.user
        # Set up org_short_name for convenience
        self.org_short_name = self.organization.short_name

    def test_authz_scope_filters_by_authorized_libraries(self):
        """
        Test that HasPermissionInContentLibraryScope rule filters libraries
        based on authorized org/slug combinations.

        Given:
        - 3 libraries: lib1 (org1), lib2 (org2), lib3 (org1)
        - User authorized for lib1 and lib2 only via AuthZ (NO legacy permissions)

        Expected:
        - Filter returns exactly 2 libraries (lib1 and lib2)
        - lib3 is excluded (same org as lib1, but different slug)
        - Correct org/slug combinations are matched
        """
        user = UserFactory.create(username="scope_user", is_staff=False)

        Organization.objects.get_or_create(short_name="org1", defaults={"name": "Org 1"})
        Organization.objects.get_or_create(short_name="org2", defaults={"name": "Org 2"})

        with self.as_user(self.admin_user):
            lib1 = self._create_library(slug="lib1", org="org1", title="Library 1")
            lib2 = self._create_library(slug="lib2", org="org2", title="Library 2")
            self._create_library(slug="lib3", org="org1", title="Library 3")

        # CRITICAL: Ensure user has NO legacy permissions (test ONLY AuthZ filtering)
        ContentLibraryPermission.objects.filter(user=user).delete()

        with patch(
            'openedx_authz.api.get_scopes_for_user_and_permission'
        ) as mock_get_scopes:
            # Mock: User authorized for lib1 (org1:lib1) and lib2 (org2:lib2) only, NOT lib3
            mock_scope1 = type('Scope', (), {'library_key': LibraryLocatorV2.from_string(lib1['id'])})()
            mock_scope2 = type('Scope', (), {'library_key': LibraryLocatorV2.from_string(lib2['id'])})()
            mock_get_scopes.return_value = [mock_scope1, mock_scope2]

            all_libs = ContentLibrary.objects.filter(slug__in=['lib1', 'lib2', 'lib3'])
            filtered = perms[CAN_VIEW_THIS_CONTENT_LIBRARY].filter(user, all_libs).distinct()

            # TEST: Verify exactly 2 libraries returned (lib1 and lib2, not lib3)
            self.assertEqual(filtered.count(), 2, "Should return exactly 2 authorized libraries")  # noqa: PT009

            # TEST: Verify correct libraries are included/excluded
            slugs = set(filtered.values_list('slug', flat=True))
            self.assertIn('lib1', slugs, "lib1 (org1:lib1) should be included")  # noqa: PT009
            self.assertIn('lib2', slugs, "lib2 (org2:lib2) should be included")  # noqa: PT009
            self.assertNotIn('lib3', slugs, "lib3 (org1:lib3) should be excluded")  # noqa: PT009

            # TEST: Verify the org/slug combinations match
            lib1_result = filtered.get(slug='lib1')
            lib2_result = filtered.get(slug='lib2')
            self.assertEqual(lib1_result.org.short_name, 'org1')  # noqa: PT009
            self.assertEqual(lib2_result.org.short_name, 'org2')  # noqa: PT009

    def test_authz_scope_individual_check_with_permission(self):
        """
        Test that HasPermissionInContentLibraryScope.check() returns True
        when authorization is granted.

        Given:
        - Non-staff user
        - Library exists
        - Authorization system grants permission (mocked)
        - NO legacy permissions

        Expected:
        - check() returns True
        """
        user = UserFactory.create(username="check_user", is_staff=False)

        with self.as_user(self.admin_user):
            lib = self._create_library(slug="check-lib", org=self.org_short_name, title="Check Library")

        library_obj = ContentLibrary.objects.get_by_key(LibraryLocatorV2.from_string(lib["id"]))

        # CRITICAL: Ensure user has NO legacy permissions (test ONLY AuthZ)
        ContentLibraryPermission.objects.filter(user=user).delete()

        with patch("openedx_authz.api.is_user_allowed", return_value=True):
            result = perms[CAN_VIEW_THIS_CONTENT_LIBRARY].check(user, library_obj)

            self.assertTrue(result, "Should return True when user is authorized")  # noqa: PT009

    def test_authz_scope_individual_check_without_permission(self):
        """
        Test that HasPermissionInContentLibraryScope.check() returns False
        when authorization is denied.

        Given:
        - Non-staff user
        - Non-public library
        - Authorization system denies permission (mocked)
        - NO legacy permissions

        Expected:
        - check() returns False
        """
        user = UserFactory.create(username="no_perm_user", is_staff=False)

        with self.as_user(self.admin_user):
            lib = self._create_library(slug="no-perm-lib", org=self.org_short_name, title="No Permission Library")

        library_obj = ContentLibrary.objects.get_by_key(LibraryLocatorV2.from_string(lib['id']))

        # CRITICAL: Ensure user has NO legacy permissions (test ONLY AuthZ)
        ContentLibraryPermission.objects.filter(user=user).delete()

        with patch('openedx_authz.api.is_user_allowed', return_value=False):
            result = perms[CAN_VIEW_THIS_CONTENT_LIBRARY].check(user, library_obj)

            self.assertFalse(result, "Should return False when user is not authorized")  # noqa: PT009

            self.assertFalse(library_obj.allow_public_read)  # noqa: PT009
            self.assertFalse(user.is_staff)  # noqa: PT009

    def test_authz_scope_handles_empty_scopes(self):
        """
        Test that HasPermissionInContentLibraryScope.query() returns empty
        result when user has no authorized scopes.

        Given:
        - Non-staff user
        - Library exists in database
        - Authorization system returns empty scope list (mocked)
        - NO legacy permissions

        Expected:
        - Filter returns 0 libraries
        - Library exists in database but is not accessible
        """
        user = UserFactory.create(username="empty_user", is_staff=False)

        with self.as_user(self.admin_user):
            self._create_library(slug="empty-lib", title="Empty Scopes Test")

        # CRITICAL: Ensure user has NO legacy permissions (test ONLY AuthZ)
        ContentLibraryPermission.objects.filter(user=user).delete()

        with patch(
            'openedx_authz.api.get_scopes_for_user_and_permission',
            return_value=[]
        ):
            filtered = perms[CAN_VIEW_THIS_CONTENT_LIBRARY].filter(
                user,
                ContentLibrary.objects.filter(slug="empty-lib")
            ).distinct()

            self.assertEqual(  # noqa: PT009
                filtered.count(),
                0,
                "Should return 0 libraries when user has no authorized scopes",
            )

            self.assertTrue(  # noqa: PT009
                ContentLibrary.objects.filter(slug="empty-lib").exists(),
                "Library should exist in database",
            )

    def test_authz_scope_q_object_has_correct_structure(self):
        """
        Test that HasPermissionInContentLibraryScope.query() generates Q object
        with structure: Q(org__short_name='X') & Q(slug='Y') for each scope.

        Multiple scopes should be OR'd:
        (Q(org__short_name='org1') & Q(slug='lib1')) | (Q(org__short_name='org2') & Q(slug='lib2'))

        Note: This test focuses on Q object structure, not filtering behavior,
        so legacy permissions don't affect the outcome.
        """
        user = UserFactory.create(username="q_user")
        rule = HasPermissionInContentLibraryScope(VIEW_LIBRARY, filter_keys=['org', 'slug'])

        with patch(
            "openedx_authz.api.get_scopes_for_user_and_permission"
        ) as mock_get_scopes:
            # Create scopes with specific org/slug values we can verify
            mock_scope1 = type("Scope", (), {
                "library_key": type("Key", (), {"org": "specific-org1", "slug": "specific-slug1"})()
            })()
            mock_scope2 = type("Scope", (), {
                "library_key": type("Key", (), {"org": "specific-org2", "slug": "specific-slug2"})()
            })()
            mock_get_scopes.return_value = [mock_scope1, mock_scope2]

            q_obj = rule.query(user)

            # Test 1: Verify it returns a Q object
            self.assertIsInstance(q_obj, Q)  # noqa: PT009

            # Test 2: Verify Q object uses OR connector (for multiple scopes)
            self.assertEqual(  # noqa: PT009
                q_obj.connector,
                'OR',
                "Should use OR to combine different library scopes",
            )

            # Test 3: Verify the Q object string contains the exact fields and values
            q_str = str(q_obj)

            # Should filter by org__short_name field
            self.assertIn(  # noqa: PT009
                "org__short_name",
                q_str,
                "Q object must filter by org__short_name field",
            )

            # Should filter by slug field
            self.assertIn(  # noqa: PT009
                "slug",
                q_str,
                "Q object must filter by slug field",
            )

            # Should contain exact org values
            self.assertIn(  # noqa: PT009
                "specific-org1",
                q_str,
                "Q object must include 'specific-org1'",
            )
            self.assertIn(  # noqa: PT009
                "specific-org2",
                q_str,
                "Q object must include 'specific-org2'",
            )

            # Should contain exact slug values
            self.assertIn(  # noqa: PT009
                "specific-slug1",
                q_str,
                "Q object must include 'specific-slug1'",
            )
            self.assertIn(  # noqa: PT009
                'specific-slug2',
                q_str,
                "Q object must include 'specific-slug2'",
            )

    def test_authz_scope_q_object_matches_exact_org_slug_pairs(self):
        """
        Test that the Q object filters by EXACT (org, slug) pairs, not just org OR slug.

        Critical test: Verifies the rule generates:
            Q(org__short_name='org1' AND slug='lib1') OR Q(org__short_name='org2' AND slug='lib2')

        NOT just:
            Q(org__short_name IN ['org1', 'org2']) OR Q(slug IN ['lib1', 'lib2'])

        Creates scenario:
        - lib1: org1 + lib1 (authorized)
        - lib2: org2 + lib2 (authorized)
        - lib3: org1 + lib3 (NOT authorized - same org, different slug)
        - lib4: org3 + lib1 (NOT authorized - same slug, different org)
        """
        user = UserFactory.create(username="exact_pair_user")
        rule = HasPermissionInContentLibraryScope(VIEW_LIBRARY, filter_keys=['org', 'slug'])

        Organization.objects.get_or_create(short_name="pair-org1", defaults={"name": "Pair Org 1"})
        Organization.objects.get_or_create(short_name="pair-org2", defaults={"name": "Pair Org 2"})
        Organization.objects.get_or_create(short_name="pair-org3", defaults={"name": "Pair Org 3"})

        with self.as_user(self.admin_user):
            lib1 = self._create_library(slug="pair-lib1", org="pair-org1", title="Pair Lib 1")
            lib2 = self._create_library(slug="pair-lib2", org="pair-org2", title="Pair Lib 2")
            self._create_library(slug="pair-lib3", org="pair-org1", title="Pair Lib 3")  # Same org as lib1
            self._create_library(slug="pair-lib1", org="pair-org3", title="Pair Lib 4")  # Same slug as lib1

        # CRITICAL: Ensure user has NO legacy permissions (test ONLY AuthZ filtering)
        ContentLibraryPermission.objects.filter(user=user).delete()

        with patch(
            'openedx_authz.api.get_scopes_for_user_and_permission'
        ) as mock_get_scopes:
            # Authorize ONLY (pair-org1, pair-lib1) and (pair-org2, pair-lib2)
            lib1_key = LibraryLocatorV2.from_string(lib1['id'])
            lib2_key = LibraryLocatorV2.from_string(lib2['id'])

            mock_get_scopes.return_value = [
                type('Scope', (), {'library_key': lib1_key})(),
                type('Scope', (), {'library_key': lib2_key})(),
            ]

            q_obj = rule.query(user)
            filtered = ContentLibrary.objects.filter(q_obj)

            # TEST: Verify EXACTLY 2 libraries match (lib1 and lib2 only)
            self.assertEqual(  # noqa: PT009
                filtered.count(),
                2,
                "Must match EXACTLY 2 libraries - only those with authorized (org, slug) pairs",
            )

            # TEST: Verify lib1 matches (pair-org1, pair-lib1)
            lib1_result = filtered.filter(slug='pair-lib1', org__short_name='pair-org1')
            self.assertEqual(  # noqa: PT009
                lib1_result.count(),
                1,
                "Must match lib1: (pair-org1, pair-lib1) - this exact pair is authorized",
            )

            # TEST: Verify lib2 matches (pair-org2, pair-lib2)
            lib2_result = filtered.filter(slug='pair-lib2', org__short_name='pair-org2')
            self.assertEqual(  # noqa: PT009
                lib2_result.count(),
                1,
                "Must match lib2: (pair-org2, pair-lib2) - this exact pair is authorized",
            )

            # TEST: Verify lib3 does NOT match (pair-org1, pair-lib3)
            lib3_result = filtered.filter(slug='pair-lib3', org__short_name='pair-org1')
            self.assertEqual(  # noqa: PT009
                lib3_result.count(),
                0,
                "Must NOT match lib3: (pair-org1, pair-lib3) - only pair-lib1 is authorized for pair-org1",
            )

            # TEST: Verify lib4 does NOT match (pair-org3, pair-lib1)
            lib4_result = filtered.filter(slug='pair-lib1', org__short_name='pair-org3')
            self.assertEqual(  # noqa: PT009
                lib4_result.count(),
                0,
                "Must NOT match lib4: (pair-org3, pair-lib1) - only pair-org1 is authorized for pair-lib1",
            )

            # TEST: Verify the result set contains exactly the right libraries
            result_pairs = set(filtered.values_list('org__short_name', 'slug'))
            expected_pairs = {('pair-org1', 'pair-lib1'), ('pair-org2', 'pair-lib2')}
            self.assertEqual(  # noqa: PT009
                result_pairs,
                expected_pairs,
                f"Result must contain exactly {expected_pairs}, got {result_pairs}",
            )

    def test_authz_scope_with_combined_authz_and_legacy_permissions(self):
        """
        Test that the filter returns libraries when user has BOTH AuthZ AND legacy permissions.

        The CAN_VIEW_THIS_CONTENT_LIBRARY permission uses OR logic:
            is_user_active & (
                is_global_staff |
                (allow_public_read & is_course_creator) |
                HasPermissionInContentLibraryScope(VIEW_LIBRARY) |  # AuthZ
                has_explicit_read_permission_for_library  # Legacy
            )

        This means a user with BOTH types of permissions should get access through EITHER system.

        Test scenario:
        - lib1: User has AuthZ permission only
        - lib2: User has legacy permission only
        - lib3: User has BOTH AuthZ AND legacy permissions
        - lib4: User has NO permissions

        Expected behavior:
        - Filter returns lib1, lib2, and lib3 (NOT lib4)
        - Having both permission types doesn't break filtering
        - Each permission system contributes its authorized libraries
        """
        user = UserFactory.create(username="combined_perm_user", is_staff=False)

        Organization.objects.get_or_create(short_name="comb-org", defaults={"name": "Combined Org"})

        with self.as_user(self.admin_user):
            lib1 = self._create_library(slug="comb-lib1", org="comb-org", title="AuthZ Only Library")
            lib2 = self._create_library(slug="comb-lib2", org="comb-org", title="Legacy Only Library")
            lib3 = self._create_library(slug="comb-lib3", org="comb-org", title="Both AuthZ and Legacy Library")
            lib4 = self._create_library(slug="comb-lib4", org="comb-org", title="No Permissions Library")  # noqa: F841

        # Retrieve library objects for permission assignment
        lib1_obj = ContentLibrary.objects.get_by_key(LibraryLocatorV2.from_string(lib1['id']))  # noqa: F841
        lib2_obj = ContentLibrary.objects.get_by_key(LibraryLocatorV2.from_string(lib2['id']))
        lib3_obj = ContentLibrary.objects.get_by_key(LibraryLocatorV2.from_string(lib3['id']))

        # Set up legacy permissions: lib2 (legacy only), lib3 (both)
        ContentLibraryPermission.objects.create(
            library=lib2_obj,
            user=user,
            access_level=ContentLibraryPermission.READ_LEVEL,
        )
        ContentLibraryPermission.objects.create(
            library=lib3_obj,
            user=user,
            access_level=ContentLibraryPermission.READ_LEVEL,
        )

        with patch(
            'openedx_authz.api.get_scopes_for_user_and_permission'
        ) as mock_get_scopes:
            # Set up AuthZ permissions: lib1 (AuthZ only), lib3 (both)
            lib1_key = LibraryLocatorV2.from_string(lib1['id'])
            lib3_key = LibraryLocatorV2.from_string(lib3['id'])

            mock_get_scopes.return_value = [
                type('Scope', (), {'library_key': lib1_key})(),
                type('Scope', (), {'library_key': lib3_key})(),
            ]

            all_libs = ContentLibrary.objects.filter(slug__in=['comb-lib1', 'comb-lib2', 'comb-lib3', 'comb-lib4'])
            filtered = perms[CAN_VIEW_THIS_CONTENT_LIBRARY].filter(user, all_libs).distinct()

            # TEST: Verify exactly 3 libraries returned (lib1, lib2, lib3 - NOT lib4)
            self.assertEqual(  # noqa: PT009
                filtered.count(),
                3,
                "Should return exactly 3 libraries: AuthZ-only, legacy-only, and both",
            )

            # TEST: Verify correct libraries are included
            slugs = set(filtered.values_list('slug', flat=True))
            self.assertIn('comb-lib1', slugs, "lib1 should be accessible via AuthZ permission")  # noqa: PT009
            self.assertIn('comb-lib2', slugs, "lib2 should be accessible via legacy permission")  # noqa: PT009
            self.assertIn('comb-lib3', slugs, "lib3 should be accessible via BOTH AuthZ and legacy permissions")  # noqa: PT009  # pylint: disable=line-too-long
            self.assertNotIn('comb-lib4', slugs, "lib4 should NOT be accessible (no permissions)")  # noqa: PT009

            # TEST: Verify lib3 doesn't get duplicated despite having both permission types
            lib3_results = filtered.filter(slug='comb-lib3')
            self.assertEqual(  # noqa: PT009
                lib3_results.count(),
                1,
                "lib3 should appear exactly once despite having both AuthZ and legacy permissions",
            )

            # TEST: Verify the permission sources work independently
            # This demonstrates the OR logic: user gets access if EITHER permission type grants it
            result_pairs = set(filtered.values_list('org__short_name', 'slug'))
            expected_pairs = {
                ('comb-org', 'comb-lib1'),  # AuthZ only
                ('comb-org', 'comb-lib2'),  # Legacy only
                ('comb-org', 'comb-lib3'),  # Both
            }
            self.assertEqual(  # noqa: PT009
                result_pairs,
                expected_pairs,
                f"Should get exactly the 3 authorized libraries via OR logic, got {result_pairs}",
            )


@ddt.ddt
class ContentLibraryXBlockValidationTest(APITestCase):
    """Tests only focused on service validation, no openedx_content interactions here."""

    @ddt.data(
        (URL_BLOCK_METADATA_URL, dict(block_key='totally_invalid_key')),
        (URL_BLOCK_RENDER_VIEW, dict(block_key='totally_invalid_key', view_name='random')),
        (URL_BLOCK_GET_HANDLER_URL, dict(block_key='totally_invalid_key', handler_name='random')),
    )
    @ddt.unpack
    def test_invalid_key(self, endpoint, endpoint_parameters):
        """Test all xblock related endpoints, when the key is invalid, return 404."""
        response = self.client.get(
            endpoint.format(**endpoint_parameters),
        )
        self.assertEqual(response.status_code, 404)  # noqa: PT009

    def test_xblock_handler_invalid_key(self):
        """This endpoint is tested separately from the previous ones as it's not a DRF endpoint."""
        client = Client()
        response = client.get(URL_BLOCK_XBLOCK_HANDLER.format(**dict(
            block_key='totally_invalid_key',
            handler_name='random',
            user_id='random',
            secure_token='random',
        )))
        self.assertEqual(response.status_code, 404)  # noqa: PT009


@skip_unless_cms
class ContentLibrariesRestAPIAuthzIntegrationTestCase(ContentLibrariesRestApiTest):
    """
    Test that Content Libraries REST API endpoints respect AuthZ roles and permissions.

    Roles tested:
    1. Library Admin: Full access to all library operations.
    2. Library Author: Can view and edit library content, but cannot delete the library.
    3. Library Contributor: Can view and edit library content, but cannot delete or publish the library.
    4. Library User: Can only view library content.
    """

    def setUp(self):
        super().setUp()
        self._seed_database_with_policies()

        self.library_admin = UserFactory.create(
            username="library_admin",
            email="libadmin@example.com")
        self.library_author = UserFactory.create(
            username="library_author",
            email="libauthor@example.com")
        self.library_contributor = UserFactory.create(
            username="library_contributor",
            email="libcontributor@example.com")
        self.library_user = UserFactory.create(
            username="library_user",
            email="libuser@example.com")
        self.random_user = UserFactory.create(
            username="random_user",
            email="random@example.com")

        # Define user groups by permission level
        self.list_of_all_users = [
            self.library_admin,
            self.library_author,
            self.library_contributor,
            self.library_user,
            self.random_user,
        ]
        self.library_viewers = [self.library_admin, self.library_author, self.library_contributor, self.library_user]
        self.library_editors = [self.library_admin, self.library_author, self.library_contributor]
        self.library_publishers = [self.library_admin, self.library_author]
        self.library_collection_editors = [self.library_admin, self.library_author, self.library_contributor]
        self.library_deleters = [self.library_admin]

        # Create library and assign roles
        library = self._create_library(
            slug="authzlib",
            title="AuthZ Test Library",
            description="Testing AuthZ",
        )
        self.lib_id = library["id"]

        authz_api.assign_role_to_user_in_scope(
            self.library_admin.username,
            roles.LIBRARY_ADMIN.external_key, self.lib_id)
        authz_api.assign_role_to_user_in_scope(
            self.library_author.username,
            roles.LIBRARY_AUTHOR.external_key, self.lib_id)
        authz_api.assign_role_to_user_in_scope(
            self.library_contributor.username,
            roles.LIBRARY_CONTRIBUTOR.external_key, self.lib_id)
        authz_api.assign_role_to_user_in_scope(
            self.library_user.username,
            roles.LIBRARY_USER.external_key, self.lib_id)
        AuthzEnforcer.get_enforcer().load_policy()  # Load policies to simulate fresh start

    def tearDown(self):
        """Clean up after each test to ensure isolation."""
        super().tearDown()
        AuthzEnforcer.get_enforcer().clear_policy()  # Clear policies after each test to ensure isolation

    @classmethod
    def _seed_database_with_policies(cls):
        """Seed the database with policies from the policy file.

        This simulates the one-time database seeding that would happen
        during application deployment, separate from the runtime policy loading.
        """
        import casbin
        import pkg_resources
        from openedx_authz.engine.utils import migrate_policy_between_enforcers

        global_enforcer = AuthzEnforcer.get_enforcer()
        global_enforcer.load_policy()
        model_path = pkg_resources.resource_filename("openedx_authz.engine", "config/model.conf")
        policy_path = pkg_resources.resource_filename("openedx_authz.engine", "config/authz.policy")

        migrate_policy_between_enforcers(
            source_enforcer=casbin.Enforcer(model_path, policy_path),
            target_enforcer=global_enforcer,
        )
        global_enforcer.clear_policy()  # Clear to simulate fresh start for each test

    def _all_users_excluding(self, excluded_users):
        return set(self.list_of_all_users) - set(excluded_users)

    def test_view_permissions(self):
        """
        Verify that only users with view permissions can view.
        """
        # Test library view access
        for user in self.library_viewers:
            with self.as_user(user):
                self._get_library(self.lib_id, expect_response=status.HTTP_200_OK)
        for user in self._all_users_excluding(self.library_viewers):
            with self.as_user(user):
                self._get_library(self.lib_id, expect_response=status.HTTP_403_FORBIDDEN)

    def test_edit_permissions(self):
        """
        Verify that only users with edit permissions can edit.
        """
        # Test library edit access
        for user in self.library_editors:
            with self.as_user(user):
                self._update_library(
                    self.lib_id,
                    description=f"Description by {user.username}",
                    expect_response=status.HTTP_200_OK,
                )
                #Verify the permitted changes were made
                data = self._get_library(self.lib_id)
                assert data['description'] == f"Description by {user.username}"

        for user in self._all_users_excluding(self.library_editors):
            with self.as_user(user):
                self._update_library(
                    self.lib_id,
                    description="I can't edit this.", expect_response=status.HTTP_403_FORBIDDEN)

        # Verify the no permitted changes weren't made:
        data = self._get_library(self.lib_id)
        assert data['description'] != "I can't edit this."

        # Library XBlock editing
        for user in self.library_editors:
            with self.as_user(user):
                # They can create blocks
                block_data = self._add_block_to_library(self.lib_id, "problem", f"problem_{user.username}")
                # They can modify blocks
                self._set_library_block_olx(
                    block_data["id"],
                    "<problem/>",
                    expect_response=status.HTTP_200_OK)
                self._set_library_block_fields(
                    block_data["id"],
                    {"data": "<problem />", "metadata": {}},
                    expect_response=status.HTTP_200_OK)
                self._set_library_block_asset(
                    block_data["id"],
                    "static/test.txt",
                    b"data",
                    expect_response=status.HTTP_200_OK)
                # They can remove blocks
                self._delete_library_block(block_data["id"], expect_response=status.HTTP_200_OK)
                # Verify deletion
                self._get_library_block(block_data["id"], expect_response=404)

        # Recreate blocks for further tests
        block_data = self._add_block_to_library(self.lib_id, "problem", "new_problem")

        for user in self._all_users_excluding(self.library_editors):
            with self.as_user(user):
                self._add_block_to_library(
                    self.lib_id,
                    "problem",
                    "problem1",
                    expect_response=status.HTTP_403_FORBIDDEN)
                # They can't modify blocks
                self._set_library_block_olx(
                    block_data["id"],
                    "<problem/>",
                    expect_response=status.HTTP_403_FORBIDDEN)
                self._set_library_block_fields(
                    block_data["id"],
                    {"data": "<problem />", "metadata": {}},
                    expect_response=status.HTTP_403_FORBIDDEN)
                self._set_library_block_asset(
                    block_data["id"],
                    "static/test.txt",
                    b"data",
                    expect_response=status.HTTP_403_FORBIDDEN)
                # They can't remove blocks
                self._delete_library_block(block_data["id"], expect_response=status.HTTP_403_FORBIDDEN)

    def test_publish_permissions(self):
        """
        Verify that only users with publish permissions can publish.
        """
        # Test publish access
        for user in self.library_publishers:
            with self.as_user(user):
                block_data = self._add_block_to_library(self.lib_id, "problem", f"problem_{user.username}_1")
                self._publish_library_block(block_data["id"], expect_response=status.HTTP_200_OK)
                block_data = self._add_block_to_library(self.lib_id, "problem", f"problem_{user.username}_2")
                assert self._get_library(self.lib_id)['has_unpublished_changes'] is True
                self._commit_library_changes(self.lib_id, expect_response=status.HTTP_200_OK)
                assert self._get_library(self.lib_id)['has_unpublished_changes'] is False

        block_data = self._add_block_to_library(self.lib_id, "problem", "draft_problem")
        assert self._get_library(self.lib_id)['has_unpublished_changes'] is True

        for user in self._all_users_excluding(self.library_publishers):
            with self.as_user(user):
                self._publish_library_block(block_data["id"], expect_response=status.HTTP_403_FORBIDDEN)
                self._commit_library_changes(self.lib_id, expect_response=status.HTTP_403_FORBIDDEN)
        # Verify that no changes were published
        assert self._get_library(self.lib_id)['has_unpublished_changes'] is True

    def test_collection_permissions(self):
        """
        Verify that only users with collection permissions can perform collection actions.
        """
        library_key = LibraryLocatorV2.from_string(self.lib_id)
        block_data = self._add_block_to_library(self.lib_id, "problem", "collection_problem")
        # Test library collection access
        for user in self.library_collection_editors:
            with self.as_user(user):
                # Create collection
                collection_data = self._create_collection(
                    self.lib_id,
                    title=f"Temp Collection {user.username}",
                    expect_response=status.HTTP_200_OK)
                collection_id = collection_data["key"]
                collection_key = LibraryCollectionLocator(lib_key=library_key, collection_id=collection_id)
                # Update collection
                self._update_collection(collection_key, title="Updated Collection", expect_response=status.HTTP_200_OK)
                self._add_items_to_collection(
                    collection_key,
                    item_keys=[block_data["id"]],
                    expect_response=status.HTTP_200_OK)
                # Delete collection
                self._soft_delete_collection(collection_key, expect_response=status.HTTP_204_NO_CONTENT)

        collection_data = self._create_collection(
            self.lib_id,
            title="New Temp Collection",
            expect_response=status.HTTP_200_OK)
        collection_id = collection_data["key"]
        collection_key = LibraryCollectionLocator(lib_key=library_key, collection_id=collection_id)

        for user in self._all_users_excluding(self.library_collection_editors):
            with self.as_user(user):
                # Attempt to create collection
                self._create_collection(
                    self.lib_id,
                    title="Unauthorized Collection",
                    expect_response=status.HTTP_403_FORBIDDEN)
                # Attempt to update collection
                self._update_collection(
                    collection_key,
                    title="Unauthorized Change",
                    expect_response=status.HTTP_403_FORBIDDEN)
                self._add_items_to_collection(
                    collection_key,
                    item_keys=[block_data["id"]],
                    expect_response=status.HTTP_403_FORBIDDEN)
                # Attempt to delete collection
                self._soft_delete_collection(collection_key, expect_response=status.HTTP_403_FORBIDDEN)

    def test_delete_library_permissions(self):
        """
        Verify that only users with delete permissions can delete a library.
        """
        # Test library delete access
        for user in self._all_users_excluding(self.library_deleters):
            with self.as_user(user):
                result = self._delete_library(self.lib_id, expect_response=status.HTTP_403_FORBIDDEN)
                assert 'detail' in result  # Error message
                assert 'permission' in result['detail'].lower()

        for user in self.library_deleters:
            with self.as_user(user):
                result = self._delete_library(self.lib_id, expect_response=status.HTTP_200_OK)
                assert result == {}

    def test_learn_from_library_permissions(self):
        """
        Verify that users with view permissions can learn from the library.
        Learning from a library means being able to render/interact with blocks.
        """
        # Create and publish a block
        block_data = self._add_block_to_library(self.lib_id, "problem", "test_problem")
        block_id = block_data["id"]
        self._commit_library_changes(self.lib_id)

        # Users with view permissions should be able to learn from the library
        for user in self.library_viewers:
            with self.as_user(user):
                # Rendering a block view requires CAN_LEARN permission
                self._render_block_view(block_id, "student_view", expect_response=status.HTTP_200_OK)

        # Users without view permissions should NOT be able to learn from the library
        for user in self._all_users_excluding(self.library_viewers):
            with self.as_user(user):
                self._render_block_view(block_id, "student_view", expect_response=status.HTTP_403_FORBIDDEN)
