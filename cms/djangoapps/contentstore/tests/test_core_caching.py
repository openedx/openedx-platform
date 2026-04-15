"""
Tests core caching facilities.
"""


from django.test import TestCase
from opaque_keys.edx.locator import AssetLocator, CourseLocator

from openedx.core.djangoapps.contentserver.caching import del_cached_content, get_cached_content, set_cached_content


class Content:
    """
    Mock cached content
    """
    def __init__(self, location, content):
        self.location = location
        self.content = content

    def get_id(self):
        return self.location.to_deprecated_son()


class CachingTestCase(TestCase):
    """
    Tests for https://edx.lighthouseapp.com/projects/102637/tickets/112-updating-asset-does-not-refresh-the-cached-copy
    """
    unicodeLocation = AssetLocator(CourseLocator('c4x', 'mitX', '800'), 'thumbnail', 'monsters.jpg')
    # Note that some of the parts are strings instead of unicode strings
    nonUnicodeLocation = AssetLocator(CourseLocator('c4x', 'mitX', '800'), 'thumbnail', 'monsters.jpg')
    mockAsset = Content(unicodeLocation, 'my content')

    def test_put_and_get(self):
        set_cached_content(self.mockAsset)
        self.assertEqual(self.mockAsset.content, get_cached_content(self.unicodeLocation).content,  # noqa: PT009
                         'should be stored in cache with unicodeLocation')
        self.assertEqual(self.mockAsset.content, get_cached_content(self.nonUnicodeLocation).content,  # noqa: PT009
                         'should be stored in cache with nonUnicodeLocation')

    def test_delete(self):
        set_cached_content(self.mockAsset)
        del_cached_content(self.nonUnicodeLocation)
        self.assertEqual(None, get_cached_content(self.unicodeLocation),  # noqa: PT009
                         'should not be stored in cache with unicodeLocation')
        self.assertEqual(None, get_cached_content(self.nonUnicodeLocation),  # noqa: PT009
                         'should not be stored in cache with nonUnicodeLocation')
