"""
Tests that several XBlock APIs support versioning
"""
from django.test.utils import override_settings
from xblock.core import XBlock

from openedx.core.djangoapps.content_libraries.tests.base import ContentLibrariesRestApiTest
from openedx.core.djangolib.testing.utils import skip_unless_cms

from .fields_test_block import FieldsTestBlock


@skip_unless_cms
@override_settings(CORS_ORIGIN_WHITELIST=[])  # For some reason, this setting isn't defined in our test environment?
class VersionedXBlockApisTestCase(ContentLibrariesRestApiTest):
    """
    Tests for three APIs implemented by djangoapps.xblock, and used by content
    libraries. These tests focus on versioning.

    Note the metadata endpoint is different than the similar "metadata" endpoint
    within the content libraries API, which returns a lot more information. This
    endpoint pretty much only returns the display name of a block, but it does
    allow retrieving past versions.
    """

    @XBlock.register_temp_plugin(FieldsTestBlock, FieldsTestBlock.BLOCK_TYPE)
    def test_versioned_metadata(self):
        """
        Test that metadata endpoint can get different versions of the block's metadata
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

        def check_results(version, display_name, settings_field, content_field):
            meta = self._get_basic_xblock_metadata(block_id, version=version)
            assert meta["block_id"] == block_id
            assert meta["block_type"] == FieldsTestBlock.BLOCK_TYPE
            assert meta["display_name"] == display_name
            fields = self._get_library_block_fields(block_id, version=version)
            assert fields["display_name"] == display_name
            assert fields["metadata"]["setting_field"] == settings_field
            rendered = self._render_block_view(block_id, "student_view", version=version)
            assert rendered["block_id"] == block_id
            assert f"SF: {settings_field}" in rendered["content"]
            assert f"CF: {content_field}" in rendered["content"]

        # Now get the metadata. If we don't specify a version, it should be the latest draft (in Studio):
        check_results(
            version=None,
            display_name="Field Test Block (Draft, v4)",
            settings_field="Draft setting value 4.",
            content_field="Draft content value 4.",
        )

        # Get the published version:
        check_results(
            version="published",
            display_name="Field Test Block (Published, v3)",
            settings_field="Published setting value 3.",
            content_field="Published content value 3.",
        )

        # Get a specific version:
        check_results(
            version="2",
            display_name="Field Test Block (Old, v2)",
            settings_field="Old setting value 2.",
            content_field="Old content value 2.",
        )
