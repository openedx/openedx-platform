"""
ConfigurationModel for the mobile_api djangoapp.
"""


from config_models.models import ConfigurationModel
from django.db import models
from model_utils.models import TimeStampedModel

from . import utils
from .mobile_platform import PLATFORM_CLASSES


class MobileApiConfig(ConfigurationModel):
    """
    Configuration for the video upload feature.

    The order in which the comma-separated list of names of profiles are given
    is in priority order.

    .. no_pii:
    """
    video_profiles = models.TextField(
        blank=True,
        help_text="A comma-separated list of names of profiles to include for videos returned from the mobile API."
    )

    class Meta:
        app_label = "mobile_api"

    @classmethod
    def get_video_profiles(cls):
        """
        Get the list of profiles in priority order when requesting from VAL
        """
        return [profile.strip() for profile in cls.current().video_profiles.split(",") if profile]


class AppVersionConfig(models.Model):
    """
    Configuration for mobile app versions available.

    .. no_pii:
    """
    PLATFORM_CHOICES = tuple((platform, platform) for platform in sorted(PLATFORM_CLASSES.keys()))

    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, blank=False)
    version = models.CharField(
        max_length=50,
        blank=False,
        help_text="Version should be in the format X.X.X.Y where X is a number and Y is alphanumeric"
    )
    major_version = models.IntegerField()
    minor_version = models.IntegerField()
    patch_version = models.IntegerField()
    expire_at = models.DateTimeField(null=True, blank=True, verbose_name="Expiry date for platform version")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "mobile_api"
        unique_together = ('platform', 'version',)
        ordering = ['-major_version', '-minor_version', '-patch_version']

    def __str__(self):
        return f"{self.platform}_{self.version}"

    @classmethod
    def latest_version(cls, platform):
        """ Returns latest supported app version for a platform. """
        latest_version_config = cls.objects.filter(platform=platform, enabled=True).first()
        if latest_version_config:
            return latest_version_config.version

    @classmethod
    def last_supported_date(cls, platform, version):
        """ Returns date when app version will get expired for a platform """
        parsed_version = utils.parsed_version(version)
        active_configs = cls.objects.filter(platform=platform, enabled=True, expire_at__isnull=False).reverse()
        for config in active_configs:
            if utils.parsed_version(config.version) >= parsed_version:
                return config.expire_at

    def save(self, *args, **kwargs):  # pylint: disable=signature-differs  # noqa: DJ012
        """ parses version into major, minor and patch versions before saving """
        self.major_version, self.minor_version, self.patch_version = utils.parsed_version(self.version)
        super().save(*args, **kwargs)


class IgnoreMobileAvailableFlagConfig(ConfigurationModel):
    """
    Configuration for the mobile_available flag. Default is false.

    Enabling this configuration will cause the mobile_available flag check in
    access.py._is_block_mobile_available to ignore the mobile_available
    flag.

    .. no_pii:
    """

    class Meta:
        app_label = "mobile_api"


class MobileConfig(TimeStampedModel):
    """
    Mobile configs to add through admin panel. Config values can be added dynamically.

    .. no_pii:
    """
    name = models.CharField(max_length=255)
    value = models.CharField(max_length=1000)

    class Meta:
        app_label = "mobile_api"

    def __str__(self):
        return self.name

    @classmethod
    def get_structured_configs(cls):
        """
        Add config values in the following manner:
            - If flag name starts with `iap_`, add value to configs['iap_configs']
            - Else add values to configs{}
        """
        configs = MobileConfig.objects.all().values('name', 'value')
        structured_configs = {"iap_configs": {}}
        for config in configs:
            name = config.get('name')
            value = config.get('value')

            if name.startswith('iap_'):
                structured_configs['iap_configs'][name] = value
            else:
                structured_configs[name] = value

        return structured_configs
