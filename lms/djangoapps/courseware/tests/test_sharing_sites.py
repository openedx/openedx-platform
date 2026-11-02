"""
tests for the sharing sites
"""

import ddt
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qsl
from openedx.core.djangoapps.video_config.sharing_sites import (
    sharing_url,
    sharing_sites_info_for_video,
    SharingSiteConfig,
)

TEST_SHARING_SITE_NAME = "test_site_name"
TEST_SHARING_SITE_ICON = "test-icon-name"
TEST_URL_PARAM_NAME = "this-url"
TEST_BASE_SHARE_URL = "www.mysite.org/videos"

TEST_SHARING_SITE_CONFIG = SharingSiteConfig(
    name=TEST_SHARING_SITE_NAME,
    fa_icon_name=TEST_SHARING_SITE_ICON,
    url_param_name=TEST_URL_PARAM_NAME,
    base_share_url=TEST_BASE_SHARE_URL,
)

TEST_SHARING_SITE_CONFIG_WITH_ADDITIONAL_PARAMS = SharingSiteConfig(
    name=TEST_SHARING_SITE_NAME,
    fa_icon_name=TEST_SHARING_SITE_ICON,
    url_param_name=TEST_URL_PARAM_NAME,
    base_share_url=TEST_BASE_SHARE_URL,
    additional_site_params={'state': 'NY', 'color': 'red'}
)

TEST_PUBLIC_URL = "http://www.openedx.org/videos/some_video_or_other"


@ddt.ddt
class TestSharingSites(TestCase):
    """
    Tests for the sharing sites
    """
    @ddt.data(
        TEST_SHARING_SITE_CONFIG,
        TEST_SHARING_SITE_CONFIG_WITH_ADDITIONAL_PARAMS
    )
    def test_sharing_url(self, config):
        """
        Test that the sharing url is built correctly
        """
        base_url, params = sharing_url(TEST_PUBLIC_URL, config).split("?")
        self.assertEqual(base_url, config.base_share_url)
        decoded_params = dict(parse_qsl(params))
        self.assertEqual(decoded_params[config.url_param_name], TEST_PUBLIC_URL)
        if getattr(config, 'additional_site_params', False):
            # additional_site_params will be the subset of decoded_params
            for key, expected_value in config.additional_site_params.items():
                assert decoded_params[key] == expected_value
            self.assertNotIn('additional_site_params', decoded_params)

    def test_sharing_sites_info_for_video(self):
        """
        Test that the sharing sites info is built correctly
        """
        sharing_site_configs = [
            TEST_SHARING_SITE_CONFIG,
            TEST_SHARING_SITE_CONFIG_WITH_ADDITIONAL_PARAMS,
        ]
        with patch('openedx.core.djangoapps.video_config.sharing_sites.ALL_SHARING_SITES', new=sharing_site_configs):
            sharing_sites_info = sharing_sites_info_for_video(TEST_PUBLIC_URL, organization=None)
            for expected_config, actual_info in zip(sharing_site_configs, sharing_sites_info):
                self.assertDictEqual(
                    actual_info,
                    {
                        'name': expected_config.name,
                        'fa_icon_name': expected_config.fa_icon_name,
                        'sharing_url': sharing_url(
                            TEST_PUBLIC_URL,
                            expected_config
                        )
                    }
                )
