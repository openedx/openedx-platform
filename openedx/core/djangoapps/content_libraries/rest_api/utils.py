"""
REST API utilities for content libraries
"""
import logging
from functools import wraps

from opaque_keys import InvalidKeyError
from rest_framework.exceptions import NotFound, ValidationError

from .. import api

log = logging.getLogger(__name__)


def convert_exceptions(fn):
    """
    Catch any Content Library API exceptions that occur and convert them to
    DRF exceptions so DRF will return an appropriate HTTP response
    """

    @wraps(fn)
    def wrapped_fn(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except InvalidKeyError as exc:
            log.exception(str(exc))
            raise NotFound  # pylint: disable=raise-missing-from  # noqa: B904
        except api.ContentLibraryNotFound:
            log.exception("Content library not found")
            raise NotFound  # pylint: disable=raise-missing-from  # noqa: B904
        except api.ContentLibraryBlockNotFound:
            log.exception("XBlock not found in content library")
            raise NotFound  # pylint: disable=raise-missing-from  # noqa: B904
        except api.ContentLibraryCollectionNotFound:
            log.exception("Collection not found in content library")
            raise NotFound  # pylint: disable=raise-missing-from  # noqa: B904
        except api.ContentLibraryContainerNotFound:
            log.exception("Container not found in content library")
            raise NotFound  # pylint: disable=raise-missing-from  # noqa: B904
        except api.LibraryCollectionAlreadyExists as exc:
            log.exception(str(exc))
            raise ValidationError(str(exc))  # pylint: disable=raise-missing-from  # noqa: B904
        except api.LibraryBlockAlreadyExists as exc:
            log.exception(str(exc))
            raise ValidationError(str(exc))  # pylint: disable=raise-missing-from  # noqa: B904
        except api.InvalidNameError as exc:
            log.exception(str(exc))
            raise ValidationError(str(exc))  # pylint: disable=raise-missing-from  # noqa: B904
        except api.BlockLimitReachedError as exc:
            log.exception(str(exc))
            raise ValidationError(str(exc))  # pylint: disable=raise-missing-from  # noqa: B904
    return wrapped_fn
