"""
Unit tests for the asset upload endpoint.
"""
from datetime import datetime
from urllib.parse import urljoin

from django.test import override_settings
from opaque_keys.edx.locator import CourseLocator
from openedx_filters import PipelineStep
from pytz import UTC

from cms.djangoapps.contentstore import asset_storage_handlers
from xmodule.contentstore.content import StaticContent
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


class TestPageURLRequestedPipelineStep(PipelineStep):
    """
    Utility class used when getting steps for pipeline.
    """

    def run_filter(self, url, org):  # pylint: disable=arguments-differ
        """Pipeline step that modifies lms url creation."""
        url = "https://lms-url-creation"
        org = "org"
        return {
            "url": url,
            "org": org,
        }


class LMSPageURLRequestedFiltersTest(ModuleStoreTestCase):
    """
    Tests for the Open edX Filters associated with the lms url requested process.
    This class guarantees that the following filters are triggered during the microsite render:
    - LMSPageURLRequested
    """

    def setUp(self):  # pylint: disable=arguments-differ
        super().setUp()
        self.upload_date = datetime(2013, 6, 1, 10, 30, tzinfo=UTC)
        self.content_type = 'image/jpg'
        self.course_key = CourseLocator('org', 'class', 'run')
        self.location = self.course_key.make_asset_key('asset', 'my_file_name.jpg')
        self.thumbnail_location = self.course_key.make_asset_key('thumbnail', 'my_file_name_thumb.jpg')

        self.asset_url = StaticContent.serialize_asset_key_with_slash(self.location)

    @override_settings(
        OPEN_EDX_FILTERS_CONFIG={
            "org.openedx.content_authoring.lms.page.url.requested.v1": {
                "pipeline": [
                    "common.djangoapps.util.tests.test_filters.TestPageURLRequestedPipelineStep",
                ],
                "fail_silently": False,
            },
        },
    )
    def test_lms_url_requested_filter_executed(self):
        """
        Test that filter get new LMS URL for asset URL generation
        based on the course organization settings for org.
        Expected result:
            - LMSPageURLRequested is triggered and executes TestPageURLRequestedPipelineStep.
            - The arguments that the receiver gets are the arguments used by the filter.
        """
        output = asset_storage_handlers.get_asset_json(
            "my_file",
            self.content_type,
            self.upload_date,
            self.location,
            self.thumbnail_location,
            True,
            self.course_key
        )

        self.assertEqual(output.get('external_url'), urljoin('https://lms-url-creation', self.asset_url))

    @override_settings(OPEN_EDX_FILTERS_CONFIG={}, LMS_ROOT_URL="https://lms-base")
    def test_lms_url_requested_without_filter_configuration(self):
        """
        Test that filter get new LMS URL for asset URL generation
        based on LMS_ROOT_URL settings because OPEN_EDX_FILTERS_CONFIG is not set.
        Expected result:
            - Returns the asset URL with domain base LMS_ROOT_URL.
            - The get process ends successfully.
        """
        output = asset_storage_handlers.get_asset_json(
            "my_file",
            self.content_type,
            self.upload_date,
            self.location,
            self.thumbnail_location,
            True,
            self.course_key
        )

        self.assertEqual(output.get('external_url'), urljoin('https://lms-base', self.asset_url))
