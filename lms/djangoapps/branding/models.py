"""
Model used by Video block for Branding configuration.

Includes:
    BrandingInfoConfig: A ConfigurationModel for managing how Video Block will
        use Branding.
"""


import json

from config_models.models import ConfigurationModel
from django.core.exceptions import ValidationError
from django.db.models import TextField


class BrandingInfoConfig(ConfigurationModel):
    """
    Configuration for Branding.

    Example of configuration that must be stored:
        {
            "CN": {
                    "url": "http://www.xuetangx.com",
                    "logo_src": "http://www.xuetangx.com/static/images/logo.png",
                    "logo_tag": "Video hosted by XuetangX.com"
            }
        }

    .. no_pii:
    """
    class Meta(ConfigurationModel.Meta):
        app_label = "branding"

    configuration = TextField(
        help_text="JSON data of Configuration for Video Branding."
    )

    def clean(self):
        """
        Validates configuration text field.
        """
        try:
            json.loads(self.configuration)
        except ValueError:
            raise ValidationError('Must be valid JSON string.')  # pylint: disable=raise-missing-from  # noqa: B904

    @classmethod
    def get_config(cls):
        """
        Get the Video Branding Configuration.
        """
        info = cls.current()
        return json.loads(info.configuration) if info.enabled else {}


class BrandingApiConfig(ConfigurationModel):
    """Configure Branding api's

    Enable or disable api's functionality.
    When this flag is disabled, the api will return 404.

    When the flag is enabled, the api will returns the valid reponse.

    .. no_pii:
    """
    class Meta(ConfigurationModel.Meta):
        app_label = "branding"
