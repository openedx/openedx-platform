"""
Exceptions that can be thrown by the Content Libraries API.
"""
from django.db import IntegrityError

from openedx_content.models_api import Collection, Container
from xblock.exceptions import XBlockNotFoundError

from ..models import ContentLibrary


# The public API is only the following symbols:
__all__ = [
    "ContentLibraryNotFound",
    "ContentLibraryCollectionNotFound",
    "ContentLibraryContainerNotFound",
    "ContentLibraryBlockNotFound",
    "LibraryAlreadyExists",
    "LibraryCollectionAlreadyExists",
    "LibraryBlockAlreadyExists",
    "BlockLimitReachedError",
    "IncompatibleTypesError",
    "InvalidNameError",
    "LibraryPermissionIntegrityError",
]


ContentLibraryNotFound = ContentLibrary.DoesNotExist

ContentLibraryCollectionNotFound = Collection.DoesNotExist

ContentLibraryContainerNotFound = Container.DoesNotExist


class ContentLibraryBlockNotFound(XBlockNotFoundError):
    """ XBlock not found in the content library """


class LibraryAlreadyExists(KeyError):
    """ A library with the specified slug already exists """


class LibraryCollectionAlreadyExists(IntegrityError):
    """ A Collection with that key already exists in the library """


class LibraryBlockAlreadyExists(KeyError):
    """ An XBlock with that ID already exists in the library """


class BlockLimitReachedError(Exception):
    """ Maximum number of allowed XBlocks in the library reached """


class IncompatibleTypesError(Exception):
    """ Library type constraint violated """


class InvalidNameError(ValueError):
    """ The specified name/identifier is not valid """


class LibraryPermissionIntegrityError(IntegrityError):
    """ Thrown when an operation would cause insane permissions. """
