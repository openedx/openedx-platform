"""Models for the util app. """


import gzip
import logging
from io import BytesIO

from config_models.models import ConfigurationModel
from django.db import models
from django.utils.text import compress_string
from opaque_keys.edx.django.models import CreatorMixin

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class RateLimitConfiguration(ConfigurationModel):
    """
    Configuration flag to enable/disable rate limiting.

    Applies to Django Rest Framework views.

    This is useful for disabling rate limiting for performance tests.
    When enabled, it will disable rate limiting on any view decorated
    with the `can_disable_rate_limit` class decorator.

    .. no_pii:
    """
    class Meta(ConfigurationModel.Meta):
        app_label = "util"


def decompress_string(value):
    """
    Helper function to reverse CompressedTextField.get_prep_value.
    """

    try:
        val = value.encode('utf').decode('base64')
        zbuf = BytesIO(val)
        zfile = gzip.GzipFile(fileobj=zbuf)
        ret = zfile.read()
        zfile.close()
    except Exception as e:  # pylint: disable=broad-except
        logger.error('String decompression failed. There may be corrupted data in the database: %s', e)
        ret = value
    return ret


class CompressedTextField(CreatorMixin, models.TextField):
    """
    TextField that transparently compresses data when saving to the database, and decompresses the data
    when retrieving it from the database.
    """

    def get_prep_value(self, value):
        """
        Compress the text data.
        """
        if value is not None:
            if isinstance(value, str):
                value = value.encode('utf8')
            value = compress_string(value)
            value = value.encode('base64').decode('utf8')
        return value

    def to_python(self, value):
        """
        Decompresses the value from the database.
        """
        if isinstance(value, str):
            value = decompress_string(value)

        return value
