"""
Image encoding helpers for the verification app.
"""


import base64
import logging

log = logging.getLogger(__name__)


class InvalidImageData(Exception):
    """
    The provided image data could not be decoded.
    """
    pass  # pylint: disable=unnecessary-pass


def decode_image_data(data):
    """
    Decode base64-encoded image data.

    Arguments:
        data (str): The raw image data, base64-encoded.

    Returns:
        str

    Raises:
        InvalidImageData: The image data could not be decoded.

    """
    try:
        return base64.b64decode(data.split(",")[1])
    except (IndexError, UnicodeEncodeError):
        log.exception("Could not decode image data")
        raise InvalidImageData  # pylint: disable=raise-missing-from  # noqa: B904
