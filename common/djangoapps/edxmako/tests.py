# lint-amnesty, pylint: disable=cyclic-import, missing-module-docstring

from unittest.mock import Mock, patch

import ddt
from django.conf import settings
from django.http import HttpResponse
from django.test import TestCase
from django.test.client import RequestFactory
from django.test.utils import override_settings
from django.urls import reverse
from edx_django_utils.cache import RequestCache

from common.djangoapps.edxmako import LOOKUP, add_lookup
from common.djangoapps.edxmako.request_context import get_template_request_context
from common.djangoapps.edxmako.services import MakoService
from common.djangoapps.edxmako.shortcuts import (
    is_any_marketing_link_set,
    is_marketing_link_set,
    marketing_link,
    render_to_string
)
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util.testing import UrlResetMixin
from openedx.core.djangolib.testing.utils import skip_unless_cms, skip_unless_lms


@ddt.ddt
class ShortcutsTests(UrlResetMixin, TestCase):
    """
    Test the edxmako shortcuts file
    """

    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'ABOUT': '/about-us'})
    def test_marketing_link(self):
        with override_settings(MKTG_URL_LINK_MAP={'ABOUT': self._get_test_url_name()}):
            # test marketing site on - relative paths are returned directly to avoid redirect loops
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
                expected_link = '/about-us'
                link = marketing_link('ABOUT')
                assert link == expected_link
            # test marketing site off
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
                expected_link = reverse(self._get_test_url_name())
                link = marketing_link('ABOUT')
                assert link == expected_link

    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'ABOUT': '/about-us'})
    def test_is_marketing_link_set(self):
        with override_settings(MKTG_URL_LINK_MAP={'ABOUT': self._get_test_url_name()}):
            # test marketing site on
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
                assert is_marketing_link_set('ABOUT')
                assert not is_marketing_link_set('NOT_CONFIGURED')
            # test marketing site off
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
                assert is_marketing_link_set('ABOUT')
                assert not is_marketing_link_set('NOT_CONFIGURED')

    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'ABOUT': '/about-us'})
    def test_is_any_marketing_link_set(self):
        with override_settings(MKTG_URL_LINK_MAP={'ABOUT': self._get_test_url_name()}):
            # test marketing site on
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
                assert is_any_marketing_link_set(['ABOUT'])
                assert is_any_marketing_link_set(['ABOUT', 'NOT_CONFIGURED'])
                assert not is_any_marketing_link_set(['NOT_CONFIGURED'])
            # test marketing site off
            with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
                assert is_any_marketing_link_set(['ABOUT'])
                assert is_any_marketing_link_set(['ABOUT', 'NOT_CONFIGURED'])
                assert not is_any_marketing_link_set(['NOT_CONFIGURED'])

    def _get_test_url_name(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        if settings.ROOT_URLCONF == 'lms.urls':
            # return any lms url name
            return 'dashboard'
        else:
            # return any cms url name
            return 'organizations'

    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'TOS': '/tos'})
    @override_settings(MKTG_URL_OVERRIDES={'TOS': 'https://edx.org'})
    def test_override_marketing_link_valid(self):
        expected_link = 'https://edx.org'
        # test marketing site on
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
            link = marketing_link('TOS')
            assert link == expected_link
        # test marketing site off
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
            link = marketing_link('TOS')
            assert link == expected_link

    @override_settings(MKTG_URLS={'ROOT': 'https://dummy-root', 'TOS': '/tos'})
    @override_settings(MKTG_URL_OVERRIDES={'TOS': '123456'})
    def test_override_marketing_link_invalid(self):
        expected_link = '#'
        # test marketing site on
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': True}):
            link = marketing_link('TOS')
            assert link == expected_link
        # test marketing site off
        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
            link = marketing_link('TOS')
            assert link == expected_link

    @skip_unless_lms
    def test_link_map_url_reverse(self):
        url_link_map = {
            'ABOUT': 'dashboard',
            'BAD_URL': 'foobarbaz',
        }

        with patch.dict('django.conf.settings.FEATURES', {'ENABLE_MKTG_SITE': False}):
            with override_settings(MKTG_URL_LINK_MAP=url_link_map):
                link = marketing_link('ABOUT')
                assert link == '/dashboard'

                link = marketing_link('BAD_URL')
                assert link == '#'


class AddLookupTests(TestCase):
    """
    Test the `add_lookup` function.
    """

    @patch('common.djangoapps.edxmako.LOOKUP', {})
    def test_with_package(self):
        add_lookup('test', 'management', __name__)
        dirs = LOOKUP['test'].directories
        assert len(dirs) == 1
        assert dirs[0].endswith('management')


class MakoRequestContextTest(TestCase):
    """
    Test MakoMiddleware.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.url = "/"
        self.request = RequestFactory().get(self.url)
        self.request.user = self.user
        self.response = Mock(spec=HttpResponse)

        self.addCleanup(RequestCache.clear_all_namespaces)

    def test_with_current_request(self):
        """
        Test that if get_current_request returns a request, then get_template_request_context
        returns a RequestContext.
        """

        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=self.request):
            # requestcontext should not be None.
            assert get_template_request_context() is not None

    def test_without_current_request(self):
        """
        Test that if get_current_request returns None, then get_template_request_context
        returns None.
        """
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=None):
            # requestcontext should be None.
            assert get_template_request_context() is None

    def test_request_context_caching(self):
        """
        Test that the RequestContext is cached in the RequestCache.
        """
        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=None):
            # requestcontext should be None, because the cache isn't filled
            assert get_template_request_context() is None

        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=self.request):
            # requestcontext should not be None, and should fill the cache
            assert get_template_request_context() is not None

        mock_get_current_request = Mock()
        with patch('common.djangoapps.edxmako.request_context.get_current_request'):
            with patch('common.djangoapps.edxmako.request_context.RequestContext.__init__') as mock_context_init:
                # requestcontext should not be None, because the cache is filled
                assert get_template_request_context() is not None
                mock_context_init.assert_not_called()
        mock_get_current_request.assert_not_called()

        RequestCache.clear_all_namespaces()

        with patch('common.djangoapps.edxmako.request_context.get_current_request', return_value=None):
            # requestcontext should be None, because the cache isn't filled
            assert get_template_request_context() is None

    @skip_unless_lms
    def test_render_to_string_when_no_global_context_lms(self):
        """
        Test render_to_string() when makomiddleware has not initialized
        the threadlocal REQUEST_CONTEXT.context. This is meant to run in LMS.
        """
        assert 'this module is temporarily unavailable' in render_to_string('courseware/error-message.html', None)

    @skip_unless_cms
    def test_render_to_string_when_no_global_context_cms(self):
        """
        Test render_to_string() when makomiddleware has not initialized
        the threadlocal REQUEST_CONTEXT.context. This is meant to run in CMS.
        """
        assert "We're having trouble rendering your component" in render_to_string('html_error.html', None)


@ddt.ddt
class MakoServiceTestCase(TestCase):
    """
    Tests for the MakoService
    """
    @ddt.data(
        (MakoService(),
         '<div id="mako_id" ns="main">Testing the MakoService</div>\n'),
        (MakoService(namespace_prefix='lms.'),
         '<div id="mako_id" ns="main">Testing the MakoService</div>\n'),
    )
    @ddt.unpack
    def test_render_template(self, service, expected_html):
        """
        Tests MakoService.render_template returns the expected rendered content.
        """
        html = service.render_template('templates/edxmako.html', {'element_id': 'mako_id'})
        assert html == expected_html
