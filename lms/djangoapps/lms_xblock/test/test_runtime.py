"""
Tests of the LMS XBlock Runtime and associated utilities
"""


from unittest.mock import Mock
from urllib.parse import urlparse

from django.conf import settings
from django.test import TestCase
from opaque_keys.edx.locations import BlockUsageLocator, CourseLocator
from xblock.fields import ScopeIds

from xmodule.x_module import ModuleStoreRuntime
from lms.djangoapps.lms_xblock.runtime import handler_url


class BlockMock(Mock):
    """Mock class that we fill with our "handler" methods."""
    scope_ids = ScopeIds(
        None,
        None,
        None,
        BlockUsageLocator(
            CourseLocator(org="mockx", course="100", run="2015"), block_type='mock_type', block_id="mock_id"
        ),
    )

    def handler(self, _context):
        """
        A test handler method.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def handler1(self, _context):
        """
        A test handler method.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    def handler_a(self, _context):
        """
        A test handler method.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass


class TestHandlerUrl(TestCase):
    """Test the LMS handler_url"""

    def setUp(self):
        super().setUp()
        self.block = BlockMock(name='block')
        self.runtime = ModuleStoreRuntime(
            load_item=Mock(name='get_test_descriptor_system.load_item'),
            resources_fs=Mock(name='get_test_descriptor_system.resources_fs'),
            error_tracker=Mock(name='get_test_descriptor_system.error_tracker')
        )
        self.runtime.get_block_for_descriptor = Mock()
        self.runtime.handler_url_override = handler_url

    def test_trailing_characters(self):
        assert not self.runtime.handler_url(self.block, 'handler').endswith('?')
        assert not self.runtime.handler_url(self.block, 'handler').endswith('/')

        assert not self.runtime.handler_url(self.block, 'handler', 'suffix').endswith('?')
        assert not self.runtime.handler_url(self.block, 'handler', 'suffix').endswith('/')

        assert not self.runtime.handler_url(self.block, 'handler', 'suffix', 'query').endswith('?')
        assert not self.runtime.handler_url(self.block, 'handler', 'suffix', 'query').endswith('/')

        assert not self.runtime.handler_url(self.block, 'handler', query='query').endswith('?')
        assert not self.runtime.handler_url(self.block, 'handler', query='query').endswith('/')

    def _parsed_query(self, query_string):
        """Return the parsed query string from a handler_url generated with the supplied query_string"""
        return urlparse(self.runtime.handler_url(self.block, 'handler', query=query_string)).query

    def test_query_string(self):
        assert 'foo=bar' in self._parsed_query('foo=bar')
        assert 'foo=bar&baz=true' in self._parsed_query('foo=bar&baz=true')
        assert 'foo&bar&baz' in self._parsed_query('foo&bar&baz')

    def _parsed_path(self, handler_name='handler', suffix=''):
        """Return the parsed path from a handler_url with the supplied handler_name and suffix"""
        return urlparse(self.runtime.handler_url(self.block, handler_name, suffix=suffix)).path

    def test_suffix(self):
        assert self._parsed_path(suffix='foo').endswith('foo')
        assert self._parsed_path(suffix='foo/bar').endswith('foo/bar')
        assert self._parsed_path(suffix='/foo/bar').endswith('/foo/bar')

    def test_handler_name(self):
        assert 'handler1' in self._parsed_path('handler1')
        assert 'handler_a' in self._parsed_path('handler_a')

    def test_thirdparty_fq(self):
        """Testing the Fully-Qualified URL returned by thirdparty=True"""
        parsed_fq_url = urlparse(self.runtime.handler_url(self.block, 'handler', thirdparty=True))
        assert parsed_fq_url.scheme == 'https'
        assert parsed_fq_url.hostname == settings.SITE_NAME

    def test_not_thirdparty_rel(self):
        """Testing the Fully-Qualified URL returned by thirdparty=False"""
        parsed_fq_url = urlparse(self.runtime.handler_url(self.block, 'handler', thirdparty=False))
        assert parsed_fq_url.scheme == ''
        assert parsed_fq_url.hostname is None
