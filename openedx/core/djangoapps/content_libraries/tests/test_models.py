"""
Unit tests for Content Libraries models.
"""


from django.test import TestCase
from organizations.models import Organization

from ..models import ALL_RIGHTS_RESERVED, ContentLibrary


class ContentLibraryTest(TestCase):
    """
    Tests for ContentLibrary model.
    """

    def _create_library(self, **kwds):
        """
        Create a library model, without a LearningPackage attached to it.
        """
        org = Organization.objects.create(name='foo', short_name='foo')
        return ContentLibrary.objects.create(
            org=org,
            slug='foobar',
            allow_public_learning=False,
            allow_public_read=False,
            license=ALL_RIGHTS_RESERVED,
            **kwds,
        )
