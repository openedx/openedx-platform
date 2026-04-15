"""
Tests for Mobile API Configuration Models
"""


from datetime import datetime

import ddt
from django.test import TestCase
from pytz import UTC

from lms.djangoapps.mobile_api.models import AppVersionConfig, MobileApiConfig, MobileConfig


@ddt.ddt
class TestAppVersionConfigModel(TestCase):
    """
    Tests for app version configuration model
    """

    def set_app_version_config(self):
        """ Creates configuration data for platform versions """
        AppVersionConfig(platform="ios", version="1.1.1", expire_at=None, enabled=True).save()
        AppVersionConfig(
            platform="ios",
            version="2.2.2",
            expire_at=datetime(2014, 1, 1, tzinfo=UTC),
            enabled=True
        ).save()
        AppVersionConfig(
            platform="ios",
            version="4.1.1",
            expire_at=datetime(5000, 1, 1, tzinfo=UTC),
            enabled=False
        ).save()
        AppVersionConfig(
            platform="ios",
            version="4.4.4",
            expire_at=datetime(9000, 1, 1, tzinfo=UTC),
            enabled=True
        ).save()
        AppVersionConfig(platform="ios", version="6.6.6", expire_at=None, enabled=True).save()
        AppVersionConfig(platform="ios", version="8.8.8", expire_at=None, enabled=False).save()

        AppVersionConfig(platform="android", version="1.1.1", expire_at=None, enabled=True).save()
        AppVersionConfig(
            platform="android",
            version="2.2.2",
            expire_at=datetime(2014, 1, 1, tzinfo=UTC),
            enabled=True
        ).save()
        AppVersionConfig(
            platform="android",
            version="4.4.4",
            expire_at=datetime(9000, 1, 1, tzinfo=UTC),
            enabled=True
        ).save()
        AppVersionConfig(platform="android", version="8.8.8", expire_at=None, enabled=True).save()

    @ddt.data(
        ('ios', '4.4.4'),
        ('ios', '6.6.6'),
        ("android", '4.4.4'),
        ('android', '8.8.8')
    )
    @ddt.unpack
    def test_no_configs_available(self, platform, version):
        assert AppVersionConfig.latest_version(platform) is None
        assert AppVersionConfig.last_supported_date(platform, version) is None

    @ddt.data(('ios', '6.6.6'), ('android', '8.8.8'))
    @ddt.unpack
    def test_latest_version(self, platform, latest_version):
        self.set_app_version_config()
        assert latest_version == AppVersionConfig.latest_version(platform)

    @ddt.data(
        ('ios', '3.3.3', datetime(9000, 1, 1, tzinfo=UTC)),
        ('ios', '4.4.4', datetime(9000, 1, 1, tzinfo=UTC)),
        ('ios', '6.6.6', None),
        ("android", '4.4.4', datetime(9000, 1, 1, tzinfo=UTC)),
        ('android', '8.8.8', None)
    )
    @ddt.unpack
    def test_last_supported_date(self, platform, version, last_supported_date):
        self.set_app_version_config()
        assert last_supported_date == AppVersionConfig.last_supported_date(platform, version)


class TestMobileApiConfig(TestCase):
    """
    Tests MobileAPIConfig
    """

    def test_video_profile_list(self):
        """Check that video_profiles config is returned in order as a list"""
        MobileApiConfig(video_profiles="mobile_low,mobile_high,youtube").save()
        video_profile_list = MobileApiConfig.get_video_profiles()
        assert video_profile_list == ['mobile_low', 'mobile_high', 'youtube']

    def test_video_profile_list_with_whitespace(self):
        """Check video_profiles config with leading and trailing whitespace"""
        MobileApiConfig(video_profiles=" mobile_low , mobile_high,youtube ").save()
        video_profile_list = MobileApiConfig.get_video_profiles()
        assert video_profile_list == ['mobile_low', 'mobile_high', 'youtube']

    def test_empty_video_profile(self):
        """Test an empty video_profile"""
        MobileApiConfig(video_profiles="").save()
        video_profile_list = MobileApiConfig.get_video_profiles()
        assert video_profile_list == []


class TestMobileConfig(TestCase):
    """
    Tests MobileConfig
    """

    def test_structured_configs(self):
        """Check that configs are structured properly"""
        MobileConfig(name="simple config", value="simple").save()
        MobileConfig(name="iap config", value="false iap").save()
        MobileConfig(name="iap_config", value="true").save()
        MobileConfig(name="", value="empty").save()
        configs = MobileConfig.get_structured_configs()
        expected_result = {
            'iap_configs': {'iap_config': 'true'},
            'simple config': 'simple',
            'iap config': 'false iap',
            '': 'empty'}

        self.assertDictEqual(configs, expected_result)  # noqa: PT009

    def test_structured_configs_without_iap_configs(self):
        """Check that configs are structured properly without iap configs"""
        MobileConfig(name="simple config", value="simple").save()
        MobileConfig(name="iap config", value="false iap").save()
        MobileConfig(name="", value="empty").save()
        configs = MobileConfig.get_structured_configs()
        expected_result = {
            'iap_configs': {},
            'simple config': 'simple',
            'iap config': 'false iap',
            '': 'empty'}

        self.assertDictEqual(configs, expected_result)  # noqa: PT009
