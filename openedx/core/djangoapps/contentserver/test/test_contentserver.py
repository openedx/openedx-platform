"""
Tests for content server.
"""


import copy
import datetime
import logging
import unittest
from unittest.mock import patch
from uuid import uuid4

import ddt
from django.conf import settings
from django.test import RequestFactory
from django.test.client import Client
from django.test.utils import override_settings
from opaque_keys import InvalidKeyError

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from xmodule.assetstore.assetmgr import AssetManager
from xmodule.contentstore.content import VERSIONED_ASSETS_PREFIX, StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, SharedModuleStoreTestCase
from xmodule.modulestore.xml_importer import import_course_from_xml

from .. import views

log = logging.getLogger(__name__)

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex  # noqa: UP031
TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT

FAKE_MD5_HASH = 'ffffffffffffffffffffffffffffffff'


def get_versioned_asset_url(asset_path):
    """
    Creates a versioned asset URL.
    """
    try:
        locator = StaticContent.get_location_from_path(asset_path)
        content = AssetManager.find(locator, as_stream=True)
        return StaticContent.add_version_to_asset_path(asset_path, content.content_digest)
    except (InvalidKeyError, ItemNotFoundError):
        pass

    return asset_path


def get_old_style_versioned_asset_url(asset_path):
    """
    Creates an old-style versioned asset URL.
    """
    try:
        locator = StaticContent.get_location_from_path(asset_path)
        content = AssetManager.find(locator, as_stream=True)
        return f'{VERSIONED_ASSETS_PREFIX}/{content.content_digest}{asset_path}'
    except (InvalidKeyError, ItemNotFoundError):
        pass

    return asset_path


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ContentStoreToyCourseTest(SharedModuleStoreTestCase):
    """
    Tests that use the toy course.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.contentstore = contentstore()
        cls.modulestore = modulestore()

        course_items = import_course_from_xml(
            cls.modulestore, 1, TEST_DATA_DIR, ['toy'],
            static_content_store=cls.contentstore, verbose=True, create_if_not_present=True
        )
        cls.course = course_items[0]
        cls.course_key = cls.course.id

        # A locked asset
        cls.locked_asset = cls.course_key.make_asset_key('asset', 'sample_static.html')
        cls.url_locked = '/' + str(cls.locked_asset)
        cls.url_locked_versioned = get_versioned_asset_url(cls.url_locked)
        cls.url_locked_versioned_old_style = get_old_style_versioned_asset_url(cls.url_locked)
        cls.contentstore.set_attr(cls.locked_asset, 'locked', True)

        # An unlocked asset
        cls.unlocked_asset = cls.course_key.make_asset_key('asset', 'another_static.txt')
        cls.url_unlocked = '/' + str(cls.unlocked_asset)
        cls.url_unlocked_versioned = get_versioned_asset_url(cls.url_unlocked)
        cls.url_unlocked_versioned_old_style = get_old_style_versioned_asset_url(cls.url_unlocked)
        cls.length_unlocked = cls.contentstore.get_attr(cls.unlocked_asset, 'length')

        # Special case: python_lib.zip
        cls.pylib_asset = cls.course_key.make_asset_key('asset', 'python_lib.zip')
        cls.url_pylib = '/' + str(cls.pylib_asset)
        cls.contentstore.set_attr(cls.pylib_asset, 'locked', False)

    def setUp(self):
        """
        Create user and login.
        """
        super().setUp()
        self.staff_usr = AdminFactory.create()
        self.non_staff_usr = UserFactory.create()

        self.client = Client()

    def test_unlocked_asset(self):
        """
        Test that unlocked assets are being served.
        """
        self.client.logout()
        resp = self.client.get(self.url_unlocked)
        assert resp.status_code == 200

    def test_unlocked_versioned_asset(self):
        """
        Test that unlocked assets that are versioned are being served.
        """
        self.client.logout()
        resp = self.client.get(self.url_unlocked_versioned)
        assert resp.status_code == 200

    def test_unlocked_versioned_asset_old_style(self):
        """
        Test that unlocked assets that are versioned (old-style) are being served.
        """
        self.client.logout()
        resp = self.client.get(self.url_unlocked_versioned_old_style, follow=True)
        assert resp.status_code == 200

    def test_unlocked_versioned_asset_with_nonexistent_version(self):
        """
        Test that unlocked assets that are versioned, but have a nonexistent version,
        are sent back as a 301 redirect which tells the caller the correct URL.
        """
        url_unlocked_versioned_old = StaticContent.add_version_to_asset_path(self.url_unlocked, FAKE_MD5_HASH)

        self.client.logout()
        resp = self.client.get(url_unlocked_versioned_old)
        assert resp.status_code == 301
        assert resp.url.endswith(self.url_unlocked_versioned)

    def test_locked_versioned_asset(self):
        """
        Test that locked assets that are versioned are being served.
        """
        CourseEnrollment.enroll(self.non_staff_usr, self.course_key)
        assert CourseEnrollment.is_enrolled(self.non_staff_usr, self.course_key)

        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked_versioned)
        assert resp.status_code == 200

    def test_locked_versioned_old_styleasset(self):
        """
        Test that locked assets that are versioned (old-style) are being served.
        """
        CourseEnrollment.enroll(self.non_staff_usr, self.course_key)
        assert CourseEnrollment.is_enrolled(self.non_staff_usr, self.course_key)

        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked_versioned_old_style, follow=True)
        assert resp.status_code == 200

    def test_locked_asset_not_logged_in(self):
        """
        Test that locked assets behave appropriately in case the user is not
        logged in.
        """
        self.client.logout()
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 403

    def test_locked_asset_not_registered(self):
        """
        Test that locked assets behave appropriately in case user is logged in
        in but not registered for the course.
        """
        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 403

    def test_locked_asset_registered(self):
        """
        Test that locked assets behave appropriately in case user is logged in
        and registered for the course.
        """
        CourseEnrollment.enroll(self.non_staff_usr, self.course_key)
        assert CourseEnrollment.is_enrolled(self.non_staff_usr, self.course_key)

        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 200

    def test_locked_asset_staff(self):
        """
        Test that locked assets behave appropriately in case user is staff.
        """
        self.client.login(username=self.staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 200

    def test_python_lib_zip_staff(self):
        """
        Test that staff can download python_lib.zip.
        """
        self.client.login(username=self.staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_pylib)
        assert resp.status_code == 200
        assert resp['Cache-Control'] == 'private, no-cache, no-store'

    def test_python_lib_zip_not_staff(self):
        """
        Test that python_lib.zip cannot be downloaded by non-staff by default.
        """
        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_pylib)
        assert resp.status_code == 403
        # We should be sending a no-cache header, but the contentserver
        # currently doesn't set caching headers for "unauthorized" responses. So
        # this test allows either in order to make the transition easier if we
        # fix that.
        assert 'Cache-Control' not in resp or resp['Cache-Control'] == 'private, no-cache, no-store'

    @patch(
        'openedx.core.djangoapps.contentserver.views.COURSE_CODE_LIBRARY_DOWNLOAD_ALLOWED.is_enabled',
        return_value=True,
    )
    def test_python_lib_zip_not_staff_but_course_allows_it(self, mock_download_allowed_flag):
        """
        Test that python_lib.zip can be downloaded by non-staff when flag enabled.
        """
        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_pylib)
        assert resp.status_code == 200
        assert resp['Cache-Control'] == 'private, no-cache, no-store'

        mock_download_allowed_flag.assert_called_once_with(self.course_key)

    @patch(
        'openedx.core.djangoapps.contentserver.views.COURSE_CODE_LIBRARY_DOWNLOAD_ALLOWED.is_enabled',
        return_value=True,
    )
    @patch(
        'openedx.core.djangoapps.contentserver.views.is_content_locked',
        return_value=True,
    )
    def test_python_lib_zip_can_be_locked(self, mock_is_locked, mock_download_allowed_flag):
        """
        Even when python_lib.zip download is broadly allowed, it can be locked.
        """
        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_pylib)
        assert resp.status_code == 403
        assert 'Cache-Control' not in resp or resp['Cache-Control'] == 'private, no-cache, no-store'

        assert mock_is_locked.call_count == 2  # for auth check, then caching check
        mock_download_allowed_flag.assert_called_once_with(self.course_key)

    @ddt.data(True, False)
    def test_python_lib_zip_uses_studio_read_check(self, allow):
        """
        Specifically check that python_lib.zip is gated on studio read access.

        Ideally this test would actually check access for a course team member
        who is *not* site staff/superuser, but that would require more
        complicated setup.
        """
        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        with patch('openedx.core.djangoapps.contentserver.views.has_studio_read_access', return_value=allow):
            resp = self.client.get(self.url_pylib)

        if allow:
            assert resp.status_code == 200
            assert resp['Cache-Control'] == 'private, no-cache, no-store'
        else:
            assert resp.status_code == 403
            assert 'Cache-Control' not in resp or resp['Cache-Control'] == 'private, no-cache, no-store'

    def test_range_request_full_file(self):
        """
        Test that a range request from byte 0 to last,
        outputs partial content status code and valid Content-Range and Content-Length.
        """
        resp = self.client.get(self.url_unlocked, HTTP_RANGE='bytes=0-')

        assert resp.status_code == 206
        # HTTP_206_PARTIAL_CONTENT
        assert resp['Content-Range'] ==\
               'bytes {first}-{last}/{length}'.format(first=0, last=(self.length_unlocked - 1),  # noqa: UP032
                                                      length=self.length_unlocked)
        assert resp['Content-Length'] == str(self.length_unlocked)

    def test_range_request_partial_file(self):
        """
        Test that a range request for a partial file,
        outputs partial content status code and valid Content-Range and Content-Length.
        first_byte and last_byte are chosen to be simple but non trivial values.
        """
        first_byte = self.length_unlocked // 4
        last_byte = self.length_unlocked // 2
        resp = self.client.get(self.url_unlocked, HTTP_RANGE='bytes={first}-{last}'.format(  # noqa: UP032
            first=first_byte, last=last_byte))

        assert resp.status_code == 206
        # HTTP_206_PARTIAL_CONTENT
        assert resp['Content-Range'] == 'bytes {first}-{last}/{length}'.format(first=first_byte,  # noqa: UP032
                                                                               last=last_byte,
                                                                               length=self.length_unlocked)
        assert resp['Content-Length'] == str((last_byte - first_byte) + 1)

    def test_range_request_multiple_ranges(self):
        """
        Test that multiple ranges in request outputs the full content.
        """
        first_byte = self.length_unlocked / 4
        last_byte = self.length_unlocked / 2
        resp = self.client.get(self.url_unlocked, HTTP_RANGE='bytes={first}-{last}, -100'.format(  # noqa: UP032
            first=first_byte, last=last_byte))

        assert resp.status_code == 200
        assert 'Content-Range' not in resp
        assert resp['Content-Length'] == str(self.length_unlocked)

    @ddt.data(
        'bytes 0-',
        'bits=0-',
        'bytes=0',
        'bytes=one-',
    )
    def test_syntax_errors_in_range(self, header_value):
        """
        Test that syntactically invalid Range values result in a 200 OK full content response.
        """
        resp = self.client.get(self.url_unlocked, HTTP_RANGE=header_value)
        assert resp.status_code == 200
        assert 'Content-Range' not in resp

    def test_range_request_malformed_invalid_range(self):
        """
        Test that a range request with malformed Range (first_byte > last_byte) outputs
        416 Requested Range Not Satisfiable.
        """
        resp = self.client.get(self.url_unlocked, HTTP_RANGE='bytes={first}-{last}'.format(  # noqa: UP032
            first=(self.length_unlocked // 2), last=(self.length_unlocked // 4)))
        assert resp.status_code == 416

    def test_range_request_malformed_out_of_bounds(self):
        """
        Test that a range request with malformed Range (first_byte, last_byte == totalLength, offset by 1 error)
        outputs 416 Requested Range Not Satisfiable.
        """
        resp = self.client.get(self.url_unlocked, HTTP_RANGE='bytes={first}-{last}'.format(  # noqa: UP032
            first=(self.length_unlocked), last=(self.length_unlocked)))
        assert resp.status_code == 416

    def test_vary_header_sent(self):
        """
        Tests that we're properly setting the Vary header to ensure browser requests don't get
        cached in a way that breaks XHR requests to the same asset.
        """
        resp = self.client.get(self.url_unlocked)
        assert resp.status_code == 200
        assert 'Origin' == resp['Vary']

    @patch('openedx.core.djangoapps.contentserver.models.CourseAssetCacheTtlConfig.get_cache_ttl')
    def test_cache_headers_with_ttl_unlocked(self, mock_get_cache_ttl):
        """
        Tests that when a cache TTL is set, an unlocked asset will be sent back with
        the correct cache control/expires headers.
        """
        mock_get_cache_ttl.return_value = 10

        resp = self.client.get(self.url_unlocked)
        assert resp.status_code == 200
        assert 'Expires' in resp
        assert 'public, max-age=10, s-maxage=10' == resp['Cache-Control']

    @patch('openedx.core.djangoapps.contentserver.models.CourseAssetCacheTtlConfig.get_cache_ttl')
    def test_cache_headers_with_ttl_locked(self, mock_get_cache_ttl):
        """
        Tests that when a cache TTL is set, a locked asset will be sent back without
        any cache control/expires headers.
        """
        mock_get_cache_ttl.return_value = 10

        CourseEnrollment.enroll(self.non_staff_usr, self.course_key)
        assert CourseEnrollment.is_enrolled(self.non_staff_usr, self.course_key)

        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 200
        assert 'Expires' not in resp
        assert 'private, no-cache, no-store' == resp['Cache-Control']

    @patch('openedx.core.djangoapps.contentserver.models.CourseAssetCacheTtlConfig.get_cache_ttl')
    def test_cache_headers_without_ttl_unlocked(self, mock_get_cache_ttl):
        """
        Tests that when a cache TTL is not set, an unlocked asset will be sent back without
        any cache control/expires headers.
        """
        mock_get_cache_ttl.return_value = 0

        resp = self.client.get(self.url_unlocked)
        assert resp.status_code == 200
        assert 'Expires' not in resp
        assert 'Cache-Control' not in resp

    @patch('openedx.core.djangoapps.contentserver.models.CourseAssetCacheTtlConfig.get_cache_ttl')
    def test_cache_headers_without_ttl_locked(self, mock_get_cache_ttl):
        """
        Tests that when a cache TTL is not set, a locked asset will be sent back with a
        cache-control header that indicates this asset should not be cached.
        """
        mock_get_cache_ttl.return_value = 0

        CourseEnrollment.enroll(self.non_staff_usr, self.course_key)
        assert CourseEnrollment.is_enrolled(self.non_staff_usr, self.course_key)

        self.client.login(username=self.non_staff_usr, password=self.TEST_PASSWORD)
        resp = self.client.get(self.url_locked)
        assert resp.status_code == 200
        assert 'Expires' not in resp
        assert 'private, no-cache, no-store' == resp['Cache-Control']

    def test_get_expiration_value(self):
        start_dt = datetime.datetime.strptime("Thu, 01 Dec 1983 20:00:00 GMT", views.HTTP_DATE_FORMAT)
        near_expire_dt = views.get_expiration_value(start_dt, 55)
        assert 'Thu, 01 Dec 1983 20:00:55 GMT' == near_expire_dt

    @patch('openedx.core.djangoapps.contentserver.models.CdnUserAgentsConfig.get_cdn_user_agents')
    def test_cache_is_cdn_with_normal_request(self, mock_get_cdn_user_agents):
        """
        Tests that when a normal request is made -- i.e. from an end user with their
        browser -- that we don't classify the request as coming from a CDN.
        """
        mock_get_cdn_user_agents.return_value = 'Amazon CloudFront'

        request_factory = RequestFactory()
        browser_request = request_factory.get('/fake', HTTP_USER_AGENT='Chrome 1234')

        is_from_cdn = views.is_cdn_request(browser_request)
        assert is_from_cdn is False

    @patch('openedx.core.djangoapps.contentserver.models.CdnUserAgentsConfig.get_cdn_user_agents')
    def test_cache_is_cdn_with_cdn_request(self, mock_get_cdn_user_agents):
        """
        Tests that when a CDN request is made -- i.e. from an edge node back to the
        origin -- that we classify the request as coming from a CDN.
        """
        mock_get_cdn_user_agents.return_value = 'Amazon CloudFront'

        request_factory = RequestFactory()
        browser_request = request_factory.get('/fake', HTTP_USER_AGENT='Amazon CloudFront')

        is_from_cdn = views.is_cdn_request(browser_request)
        assert is_from_cdn is True

    @patch('openedx.core.djangoapps.contentserver.models.CdnUserAgentsConfig.get_cdn_user_agents')
    def test_cache_is_cdn_with_cdn_request_multiple_user_agents(self, mock_get_cdn_user_agents):
        """
        Tests that when a CDN request is made -- i.e. from an edge node back to the
        origin -- that we classify the request as coming from a CDN when multiple UAs
        are configured.
        """
        mock_get_cdn_user_agents.return_value = 'Amazon CloudFront\nAkamai GHost'

        request_factory = RequestFactory()
        browser_request = request_factory.get('/fake', HTTP_USER_AGENT='Amazon CloudFront')

        is_from_cdn = views.is_cdn_request(browser_request)
        assert is_from_cdn is True


@ddt.ddt
class ParseRangeHeaderTestCase(unittest.TestCase):
    """
    Tests for the parse_range_header function.
    """

    def setUp(self):
        super().setUp()
        self.content_length = 10000

    def test_bytes_unit(self):
        unit, __ = views.parse_range_header('bytes=100-', self.content_length)
        assert unit == 'bytes'

    @ddt.data(
        ('bytes=100-', 1, [(100, 9999)]),
        ('bytes=1000-', 1, [(1000, 9999)]),
        ('bytes=100-199, 200-', 2, [(100, 199), (200, 9999)]),
        ('bytes=100-199, 200-499', 2, [(100, 199), (200, 499)]),
        ('bytes=-100', 1, [(9900, 9999)]),
        ('bytes=-100, -200', 2, [(9900, 9999), (9800, 9999)])
    )
    @ddt.unpack
    def test_valid_syntax(self, header_value, excepted_ranges_length, expected_ranges):
        __, ranges = views.parse_range_header(header_value, self.content_length)
        assert len(ranges) == excepted_ranges_length
        assert ranges == expected_ranges

    @ddt.data(
        ('bytes=one-20', ValueError, 'invalid literal for int()'),
        ('bytes=-one', ValueError, 'invalid literal for int()'),
        ('bytes=-', ValueError, 'invalid literal for int()'),
        ('bytes=--', ValueError, 'invalid literal for int()'),
        ('bytes', ValueError, 'Invalid syntax'),
        ('bytes=', ValueError, 'Invalid syntax'),
        ('bytes=0', ValueError, 'Invalid syntax'),
        ('bytes=0-10,0', ValueError, 'Invalid syntax'),
        ('bytes=0=', ValueError, 'too many values to unpack'),
    )
    @ddt.unpack
    def test_invalid_syntax(self, header_value, exception_class, exception_message_regex):
        self.assertRaisesRegex(  # noqa: PT027
            exception_class, exception_message_regex, views.parse_range_header, header_value, self.content_length
        )
