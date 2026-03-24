"""
Tests for LTI views.
"""

from django.conf import settings
from django.test import TestCase, override_settings

from .base import (
    ContentLibrariesRestApiTest,
    URL_LIB_LTI_JWKS,
    skip_unless_cms,
)


def override_features(**kwargs):
    """
    Wrapps ``override_settings`` to override ``settings.FEATURES``.
    """
    return override_settings(FEATURES={**settings.FEATURES, **kwargs})


@skip_unless_cms
class LtiToolJwksViewTest(TestCase):
    """
    Test JWKS view.
    """

    def test_when_lti_disabled_return_404(self):
        """
        Given LTI toggle is disabled
        When JWKS requested
        Then return 404
        """
        response = self.client.get(URL_LIB_LTI_JWKS)
        self.assertEqual(response.status_code, 404)

    @override_features(ENABLE_CONTENT_LIBRARIES=True,
                       ENABLE_CONTENT_LIBRARIES_LTI_TOOL=True)
    def test_when_no_keys_then_return_empty(self):
        """
        Given no LTI tool in the database.
        When JWKS requested.
        Then return empty
        """
        response = self.client.get(URL_LIB_LTI_JWKS)
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, '{"keys": []}')


class LibraryBlockLtiUrlViewTestMixin:
    """
    Test generating LTI URL for a block in a library.
    """

    def test_lti_url_generation(self):
        """
        Test the LTI URL generated from the block ID.
        """

        library = self._create_library(
            slug="libgg", title="A Test Library", description="Testing library",
        )

        block = self._add_block_to_library(library['id'], 'problem', 'problem')
        usage_key = str(block['id'])

        url = f'/api/libraries/v2/blocks/{usage_key}/lti/'
        expected_lti_url = f"/api/libraries/v2/lti/1.3/launch/?id={usage_key}"

        response = self._api("get", url, None, expect_response=200)

        self.assertDictEqual(response, {"lti_url": expected_lti_url})

    def test_block_not_found(self):
        """
        Test the LTI URL cannot be generated as the block not found.
        """
        self._api("get", '/api/libraries/v2/blocks/lb:CL-TEST:libgg:problem:bad-block/lti/', None, expect_response=404)


@override_features(ENABLE_CONTENT_LIBRARIES=True,
                   ENABLE_CONTENT_LIBRARIES_LTI_TOOL=True)
class LibraryBlockLtiUrlViewTest(
    LibraryBlockLtiUrlViewTestMixin,
    ContentLibrariesRestApiTest,
):
    """
    Test generating LTI URL for a block in a library, using the installed openedx_content app.
    """
