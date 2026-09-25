"""
Tests for the XBlock v2 runtime's "embed" view, using Content Libraries

This view is used in the MFE to preview XBlocks that are in the library.
"""
import re

import ddt
import pytest
from django.core.exceptions import ValidationError
from openedx_authz.constants.roles import COURSE_AUDITOR
from xblock.core import XBlock

from openedx.core.djangoapps.authz.tests.mixins import CourseAuthoringAuthzTestMixin
from openedx.core.djangoapps.content_libraries.tests.base import URL_BLOCK_EMBED_VIEW, ContentLibrariesRestApiTest
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .fields_test_block import FieldsTestBlock


@skip_unless_cms
@ddt.ddt
class LibrariesEmbedViewTestCase(ContentLibrariesRestApiTest):
    """
    Tests for embed_view and interacting with draft/published/past versions of
    openedx_content-based XBlocks (in Content Libraries).

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

    @XBlock.register_temp_plugin(FieldsTestBlock, FieldsTestBlock.BLOCK_TYPE)
    def test_embed_view_versions(self):
        """
        Test that the embed_view renders a block and can render different versions of it.
        """
        # Create a library:
        lib = self._create_library(slug="test-eb-1", title="Test Library", description="")
        lib_id = lib["id"]
        # Create an XBlock. This will be the empty version 1:
        create_response = self._add_block_to_library(lib_id, FieldsTestBlock.BLOCK_TYPE, "block1")
        block_id = create_response["id"]
        # Create version 2 of the block by setting its OLX:
        olx_response = self._set_library_block_olx(block_id, """
            <fields-test
                display_name="Field Test Block (Old, v2)"
                setting_field="Old setting value 2."
                content_field="Old content value 2."
            />
        """)
        assert olx_response["version_num"] == 2
        # Create version 3 of the block by setting its OLX again:
        olx_response = self._set_library_block_olx(block_id, """
            <fields-test
                display_name="Field Test Block (Published, v3)"
                setting_field="Published setting value 3."
                content_field="Published content value 3."
            />
        """)
        assert olx_response["version_num"] == 3
        # Publish the library:
        self._commit_library_changes(lib_id)

        # Create the draft (version 4) of the block:
        olx_response = self._set_library_block_olx(block_id, """
            <fields-test
                display_name="Field Test Block (Draft, v4)"
                setting_field="Draft setting value 4."
                content_field="Draft content value 4."
            />
        """)

        # Now render the "embed block" view. This test only runs in CMS so it should default to the draft:
        html = self._embed_block(block_id)

        def check_fields(display_name, setting_value, content_value):
            assert f'<h1>{display_name}</h1>' in html
            assert f'<p>SF: {setting_value}</p>' in html
            assert f'<p>CF: {content_value}</p>' in html
            handler_url = re.search(r'<p>handler URL: ([^<]+)</p>', html).group(1)
            assert handler_url.startswith('http')
            handler_result = self.client.get(handler_url).json()
            assert handler_result == {
                "display_name": display_name,
                "setting_field": setting_value,
                "content_field": content_value,
            }
        check_fields('Field Test Block (Draft, v4)', 'Draft setting value 4.', 'Draft content value 4.')

        # But if we request the published version, we get that:
        html = self._embed_block(block_id, version="published")
        check_fields('Field Test Block (Published, v3)', 'Published setting value 3.', 'Published content value 3.')

        # And if we request a specific version, we get that:
        html = self._embed_block(block_id, version=3)
        check_fields('Field Test Block (Published, v3)', 'Published setting value 3.', 'Published content value 3.')

        # And if we request a specific version, we get that:
        html = self._embed_block(block_id, version=2)
        check_fields('Field Test Block (Old, v2)', 'Old setting value 2.', 'Old content value 2.')

        html = self._embed_block(block_id, version=4)
        check_fields('Field Test Block (Draft, v4)', 'Draft setting value 4.', 'Draft content value 4.')

    @XBlock.register_temp_plugin(FieldsTestBlock, FieldsTestBlock.BLOCK_TYPE)
    def test_handlers_modifying_published_data(self):
        """
        Test that if we requested any version other than "draft", the handlers should not allow _writing_ to authored
        field data (because you'd be overwriting the latest draft version with changes based on an old version).

        We may decide to relax this restriction in the future. Not sure how important it is.

        Writing to student state is OK.
        """
        # Create a library:
        lib = self._create_library(slug="test-eb-2", title="Test Library", description="")
        lib_id = lib["id"]
        # Create an XBlock. This will be the empty version 1:
        create_response = self._add_block_to_library(lib_id, FieldsTestBlock.BLOCK_TYPE, "block1")
        block_id = create_response["id"]

        # Now render the "embed block" view. This test only runs in CMS so it should default to the draft:
        html = self._embed_block(block_id)

        def call_update_handler(**kwargs):
            handler_url = re.search(r'<p>handler URL: ([^<]+)</p>', html).group(1)
            assert handler_url.startswith('http')
            handler_url = handler_url.replace('get_fields', 'update_fields')
            response = self.client.post(handler_url, kwargs, format='json')
            assert response.status_code == 200

        def check_fields(display_name, setting_field, content_field):
            assert f'<h1>{display_name}</h1>' in html
            assert f'<p>SF: {setting_field}</p>' in html
            assert f'<p>CF: {content_field}</p>' in html

        # Call the update handler to change the fields on the draft:
        call_update_handler(display_name="DN-01", setting_field="SV-01", content_field="CV-01")

        # Render the block again and check that the handler was able to update the fields:
        html = self._embed_block(block_id)
        check_fields(display_name="DN-01", setting_field="SV-01", content_field="CV-01")

        # Publish the library:
        self._commit_library_changes(lib_id)

        # Now try changing the authored fields of the published version using a handler:
        html = self._embed_block(block_id, version="published")
        expected_msg = "Do not make changes to a component starting from the published or past versions."
        with pytest.raises(ValidationError, match=expected_msg) as err:
            call_update_handler(display_name="DN-X", setting_field="SV-X", content_field="CV-X")

        # Now try changing the authored fields of a specific past version using a handler:
        html = self._embed_block(block_id, version=2)
        with pytest.raises(ValidationError, match=expected_msg) as err:  # noqa: F841
            call_update_handler(display_name="DN-X", setting_field="SV-X", content_field="CV-X")

        # Make sure the fields were not updated:
        html = self._embed_block(block_id)
        check_fields(display_name="DN-01", setting_field="SV-01", content_field="CV-01")

    def test_embed_view_versions_static_assets(self):
        """
        Test asset substitution and version-awareness.
        """
        # Create a library:
        lib = self._create_library(
            slug="test-eb-asset-1", title="Asset Test Library", description="",
        )
        lib_id = lib["id"]

        # Create an HTMLBlock. This will be the empty version 1:
        create_response = self._add_block_to_library(lib_id, "html", "asset_block")
        block_id = create_response["id"]

        # Create version 2 of the block by setting its OLX. This has a reference
        # to an image, but not the image itself–so it won't get auto-replaced.
        olx_response = self._set_library_block_olx(block_id, """
            <html display_name="Asset Test Component"><![CDATA[
                <p>This is the enemy of our garden:</p>
                <p><img src="/static/deer.jpg"/></p>
            ]]></html>
        """)
        assert olx_response["version_num"] == 2

        # Create version 3 with some bogus file data
        self._set_library_block_asset(block_id, "static/deer.jpg", b"This is not a valid JPEG file")

        # Publish the library (making version 3 the published state):
        self._commit_library_changes(lib_id)

        # Create version 4 by deleting the asset
        self._delete_library_block_asset(block_id, "static/deer.jpg")

        # Grab version 2, which has the asset reference but not the asset. No
        # substitution should happen.
        html = self._embed_block(block_id, version=2)
        assert 'src="/static/deer.jpg"' in html

        # Grab the published version 3. This has the asset, so the link should
        # show up.
        html = self._embed_block(block_id, version='published')
        # This is the pattern we're looking for:
        #   <img src="https://{host}/library_assets/component_versions/.../static/deer.jpg"/>
        assert re.search(r'/library_assets/component_versions/[0-9a-f-]*/static/deer.jpg', html)

        # Now grab the draft version (4), which is going to once again not have
        # the asset (because we deleted it).
        html = self._embed_block(block_id, version='draft')
        assert 'src="/static/deer.jpg"' in html

    # TODO: if we are ever able to run these tests in the LMS, test that the LMS only allows accessing the published
    # version.


@skip_unless_cms
class EmbedViewAuthzBypassTest(CourseAuthoringAuthzTestMixin, ContentLibrariesRestApiTest, ModuleStoreTestCase):
    """
    A course auditor has no direct permissions on the library backing a block they're
    reviewing, but does hold `courses.view_library_updates` in the course. Passing the
    linked downstream block's usage key as `course_id` should let them view the upstream
    block's embed anyway, but only because that specific downstream is actually linked to
    it: holding the permission in the course alone isn't enough.

    See openedx-authz#441.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create()
        self.add_user_to_role_in_course(self.authorized_user, COURSE_AUDITOR.external_key, str(self.course.id))

        lib = self._create_library(slug="embed-authz-bypass-lib", title="Embed AuthZ Bypass Test Library")
        create_response = self._add_block_to_library(lib["id"], "html", "block1")
        self.block_id = create_response["id"]
        self._set_library_block_olx(self.block_id, "<html>Hello world</html>")
        self._commit_library_changes(lib["id"])

        # Linked to self.block_id: this is the downstream that should grant access.
        self.linked_downstream_id = str(
            BlockFactory.create(category="html", parent=self.course, upstream=self.block_id).location
        )
        # In the same (accessible) course, but not linked to self.block_id at all: this is
        # what openedx-authz#441 was actually about, holding the course permission must not
        # be enough on its own, the resource has to be linked to that specific downstream.
        self.unrelated_downstream_id = str(
            BlockFactory.create(category="html", parent=self.course).location
        )

    def test_embed_denied_without_course_id(self):
        """A course auditor with no library access is denied when course_id isn't given."""
        with self.as_user(self.authorized_user), self.allow_transaction_exception():
            response = self.client.get(URL_BLOCK_EMBED_VIEW.format(block_key=self.block_id, view_name="student_view"))
        assert response.status_code == 403

    def test_embed_allowed_with_linked_downstream(self):
        """The same course auditor is allowed once course_id points at the linked downstream."""
        with self.as_user(self.authorized_user):
            response = self.client.get(
                URL_BLOCK_EMBED_VIEW.format(block_key=self.block_id, view_name="student_view"),
                {"course_id": self.linked_downstream_id},
            )
        assert response.status_code == 200

    def test_unrelated_course_id_is_denied(self):
        """A course_id where the user holds no role at all must not grant access."""
        with self.as_user(self.authorized_user), self.allow_transaction_exception():
            response = self.client.get(
                URL_BLOCK_EMBED_VIEW.format(block_key=self.block_id, view_name="student_view"),
                {"course_id": "course-v1:CL-TEST+OTHER101+2025"},
            )
        assert response.status_code == 403

    def test_downstream_not_linked_to_this_block_is_denied(self):
        """
        A downstream in a course the user can review, but that isn't linked to this block,
        must not grant access. Regression test for openedx-authz#441: the original
        implementation trusted the course_id alone, so holding the permission in any course
        was enough to read any library resource, whether or not that course actually used it.
        """
        with self.as_user(self.authorized_user), self.allow_transaction_exception():
            response = self.client.get(
                URL_BLOCK_EMBED_VIEW.format(block_key=self.block_id, view_name="student_view"),
                {"course_id": self.unrelated_downstream_id},
            )
        assert response.status_code == 403

    def test_library_permission_alone_still_works_without_course_id(self):
        """
        Backward compatibility: a user with direct library access (self.user, who created
        the library) keeps working exactly as before, without ever passing course_id.
        """
        response = self.client.get(URL_BLOCK_EMBED_VIEW.format(block_key=self.block_id, view_name="student_view"))
        assert response.status_code == 200
