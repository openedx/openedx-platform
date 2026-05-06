"""Tests for static_replace"""
import re
from functools import partial
from io import BytesIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import ddt
import pytest
from django.test import override_settings
from opaque_keys.edx.keys import CourseKey
from PIL import Image
from web_fragments.fragment import Fragment

from common.djangoapps.static_replace import (
    _url_replace_regex,
    make_static_urls_absolute,
    process_static_urls,
    replace_course_urls,
    replace_jump_to_id_urls,
    replace_static_urls,
)
from common.djangoapps.static_replace.services import ReplaceURLService
from common.djangoapps.static_replace.wrapper import replace_urls_wrapper
from xmodule.assetstore.assetmgr import AssetManager  # pylint: disable=wrong-import-order
from xmodule.contentstore.content import StaticContent  # pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.exceptions import NotFoundError  # pylint: disable=wrong-import-order
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.exceptions import ItemNotFoundError  # pylint: disable=wrong-import-order
from xmodule.modulestore.mongo import MongoModuleStore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    CourseFactory,
    check_mongo_calls,
)
from xmodule.modulestore.xml import XMLModuleStore  # pylint: disable=wrong-import-order

DATA_DIRECTORY = 'data_dir'
COURSE_KEY = CourseKey.from_string('org/course/run')
STATIC_SOURCE = '"/static/file.png"'


def encode_unicode_characters_in_url(url):
    """
    Encodes all Unicode characters to their percent-encoding representation
    in both the path portion and query parameter portion of the given URL.
    """
    scheme, netloc, path, params, query, fragment = urlparse(url)
    query_params = parse_qsl(query)
    updated_query_params = []
    for query_name, query_val in query_params:
        updated_query_params.append((query_name, quote(query_val)))

    return urlunparse((scheme, netloc, quote(path, '/:+@'), params, urlencode(query_params), fragment))


def test_multi_replace():
    course_source = '"/course/file.png"'

    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == \
        replace_static_urls(replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY), DATA_DIRECTORY)
    assert replace_course_urls(course_source, COURSE_KEY) == \
        replace_course_urls(replace_course_urls(course_source, COURSE_KEY), COURSE_KEY)


def test_process_url():
    def processor(__, prefix, quote, rest):  # pylint: disable=redefined-outer-name
        return quote + 'test' + prefix + rest + quote

    assert process_static_urls(STATIC_SOURCE, processor) == '"test/static/file.png"'


def test_process_url_data_dir_exists():
    base = f'"/static/{DATA_DIRECTORY}/file.png"'

    def processor(original, prefix, quote, rest):  # pylint: disable=unused-argument, redefined-outer-name
        return quote + 'test' + rest + quote

    assert process_static_urls(base, processor, data_dir=DATA_DIRECTORY) == base


def test_process_url_no_match():

    def processor(__, prefix, quote, rest):  # pylint: disable=redefined-outer-name
        return quote + 'test' + prefix + rest + quote

    assert process_static_urls(STATIC_SOURCE, processor) == '"test/static/file.png"'


def test_process_url_no_match_starts_with_xblock():
    def processor(original, prefix, quote, rest):  # pylint: disable=unused-argument, redefined-outer-name
        return quote + 'test' + prefix + rest + quote
    assert process_static_urls(
        '"/static/xblock-file.png"',
        processor,
        data_dir=DATA_DIRECTORY
    ) == '"test/static/xblock-file.png"'


@patch('django.http.HttpRequest', autospec=True)
def test_static_urls(mock_request):
    mock_request.build_absolute_uri = lambda url: 'http://' + url
    result = make_static_urls_absolute(mock_request, STATIC_SOURCE)
    assert result == '\"http:///static/file.png\"'


@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
def test_storage_url_exists(mock_storage):
    mock_storage.exists.return_value = True
    mock_storage.url.return_value = '/static/file.png'

    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == '"/static/file.png"'
    mock_storage.exists.assert_called_once_with('file.png')
    mock_storage.url.assert_called_once_with('file.png')


@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
def test_storage_url_not_exists(mock_storage):
    mock_storage.exists.return_value = False
    mock_storage.url.return_value = '/static/data_dir/file.png'

    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == '"/static/data_dir/file.png"'
    mock_storage.exists.assert_called_once_with('file.png')
    mock_storage.url.assert_called_once_with('data_dir/file.png')


@patch('common.djangoapps.static_replace.StaticContent', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
@patch('common.djangoapps.static_replace.models.AssetBaseUrlConfig.get_base_url')
@patch('common.djangoapps.static_replace.models.AssetExcludedExtensionsConfig.get_excluded_extensions')
def test_mongo_filestore(mock_get_excluded_extensions, mock_get_base_url, mock_modulestore, mock_static_content):

    mock_modulestore.return_value = Mock(MongoModuleStore)
    mock_static_content.get_canonicalized_asset_path.return_value = "c4x://mock_url"
    mock_get_base_url.return_value = ''
    mock_get_excluded_extensions.return_value = ['foobar']

    # No namespace => no change to path
    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == '"/static/data_dir/file.png"'

    # Namespace => content url
    assert '"' + mock_static_content.get_canonicalized_asset_path.return_value + '"' == \
        replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY, course_id=COURSE_KEY)

    mock_static_content.get_canonicalized_asset_path.assert_called_once_with(COURSE_KEY, 'file.png', '', ['foobar'])


@patch('common.djangoapps.static_replace.settings', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
def test_data_dir_fallback(mock_storage, mock_modulestore, mock_settings):  # pylint: disable=unused-argument
    mock_modulestore.return_value = Mock(XMLModuleStore)
    mock_storage.url.side_effect = Exception

    mock_storage.exists.return_value = True
    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == '"/static/data_dir/file.png"'

    mock_storage.exists.return_value = False
    assert replace_static_urls(STATIC_SOURCE, DATA_DIRECTORY) == '"/static/data_dir/file.png"'


def test_raw_static_check():
    """
    Make sure replace_static_urls leaves alone things that end in '.raw'
    """
    path = '"/static/foo.png?raw"'
    assert replace_static_urls(path, DATA_DIRECTORY) == path

    text = 'text <tag a="/static/js/capa/protex/protex.nocache.js?raw"/><div class="'
    assert replace_static_urls(path, text) == path


@pytest.mark.django_db
@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
def test_static_url_with_query(mock_modulestore, mock_storage):
    """
    Make sure that for urls with query params:
     query params that contain "^/static/" are converted to full location urls
     query params that do not contain "^/static/" are left unchanged
    """
    mock_storage.exists.return_value = False
    mock_modulestore.return_value = Mock(MongoModuleStore)

    pre_text = 'EMBED src ="/static/LAlec04_controller.swf?csConfigFile=/static/LAlec04_config.xml&name1=value1&name2=value2"'  # pylint: disable=line-too-long
    post_text = 'EMBED src ="/c4x/org/course/asset/LAlec04_controller.swf?csConfigFile=%2Fc4x%2Forg%2Fcourse%2Fasset%2FLAlec04_config.xml&name1=value1&name2=value2"'  # pylint: disable=line-too-long
    assert replace_static_urls(pre_text, DATA_DIRECTORY, COURSE_KEY) == post_text


@pytest.mark.django_db
@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
def test_static_paths_out(mock_modulestore, mock_storage):
    """
    Tests the side-effect of passing an array to collect static_paths_out.

    * if a static URL is changed, then its changed URL is returned.
    * if a static URL is unchanged, then the unchanged URL is returned.
    * xblock paths are not included in the static_paths_out array.
    """
    mock_storage.exists.return_value = False
    mock_modulestore.return_value = Mock(MongoModuleStore)

    static_url = '/static/LAlec04_controller.swf?csConfigFile=/static/LAlec04_config.xml&name1=value1&name2=value2'
    static_course_url = '/c4x/org/course/asset/LAlec04_controller.swf?csConfigFile=%2Fc4x%2Forg%2Fcourse%2Fasset%2FLAlec04_config.xml&name1=value1&name2=value2'  # pylint: disable=line-too-long
    raw_url = '/static/js/capa/protex/protex.nocache.js?raw'
    xblock_url = '/static/xblock/resources/babys_first.lil_xblock/public/images/pacifier.png'
    # xss-lint: disable=python-wrap-html
    pre_text = f'EMBED src ="{static_url}" xblock={xblock_url} text <tag a="{raw_url}"/><div class="'
    # xss-lint: disable=python-wrap-html
    post_text = f'EMBED src ="{static_course_url}" xblock={xblock_url} text <tag a="{raw_url}"/><div class="'  # pylint: disable=line-too-long
    static_paths = []
    assert replace_static_urls(pre_text, DATA_DIRECTORY, COURSE_KEY, static_paths_out=static_paths) == post_text
    assert static_paths == [(static_url, static_course_url), (raw_url, raw_url)]


def test_regex():
    yes = ('"/static/foo.png"',
           '"/static/foo.png"',
           "'/static/foo.png'")

    no = ('"/not-static/foo.png"',
          '"/static/foo',  # no matching quote
          )

    regex = _url_replace_regex('/static/')

    for s in yes:
        print(f'Should match: {s!r}')
        assert re.match(regex, s)

    for s in no:
        print(f'Should not match: {s!r}')
        assert not re.match(regex, s)


@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
def test_static_url_with_xblock_resource(mock_modulestore, mock_storage):
    """
    Make sure that for URLs with XBlock resource URL, which start with /static/,
    we don't rewrite them.
    """
    mock_storage.exists.return_value = False
    mock_modulestore.return_value = Mock(MongoModuleStore)

    pre_text = 'EMBED src ="/static/xblock/resources/babys_first.lil_xblock/public/images/pacifier.png"'
    post_text = pre_text
    assert replace_static_urls(pre_text, DATA_DIRECTORY, COURSE_KEY) == post_text


@patch('common.djangoapps.static_replace.staticfiles_storage', autospec=True)
@patch('xmodule.modulestore.django.modulestore', autospec=True)
@override_settings(STATIC_URL='https://example.com/static/')
def test_static_url_with_xblock_resource_on_cdn(mock_modulestore, mock_storage):
    """
    Make sure that for URLs with XBlock resource URL, which start with /static/,
    we don't rewrite them, even if these are served from an absolute URL like a CDN.
    """
    mock_storage.exists.return_value = False
    mock_modulestore.return_value = Mock(MongoModuleStore)

    pre_text = 'EMBED src ="https://example.com/static/xblock/resources/tehehe.xblock/public/images/woo.png"'
    post_text = pre_text
    assert replace_static_urls(pre_text, DATA_DIRECTORY, COURSE_KEY) == post_text


@ddt.ddt
class CanonicalContentTest(SharedModuleStoreTestCase):
    """
    Tests the generation of canonical asset URLs for different types
    of assets: c4x-style, opaque key style, locked, unlocked, CDN
    set, CDN not set, etc.
    """

    @classmethod
    def setUpClass(cls):
        cls.courses = {}

        super().setUpClass()

        names_and_prefixes = [(ModuleStoreEnum.Type.split, 'split')]
        for store, prefix in names_and_prefixes:
            with cls.store.default_store(store):
                cls.courses[prefix] = CourseFactory.create(org='a', course='b', run=prefix)

                # Create an unlocked image.
                unlock_content = cls.create_image(prefix, (32, 32), 'blue', '{}_ünlöck.png')

                # Create a locked image.
                lock_content = cls.create_image(prefix, (32, 32), 'green', '{}_lock.png', locked=True)

                # Create a thumbnail of the images.
                contentstore().generate_thumbnail(unlock_content, dimensions=(16, 16))
                contentstore().generate_thumbnail(lock_content, dimensions=(16, 16))

                # Create an unlocked image in a subdirectory.
                cls.create_image(prefix, (1, 1), 'red', 'special/{}_ünlöck.png')

                # Create a locked image in a subdirectory.
                cls.create_image(prefix, (1, 1), 'yellow', 'special/{}_lock.png', locked=True)

                # Create an unlocked image with funky characters in the name.
                cls.create_image(prefix, (1, 1), 'black', 'weird {}_ünlöck.png')
                cls.create_image(prefix, (1, 1), 'black', 'special/weird {}_ünlöck.png')

                # Create an HTML file to test extension exclusion, and create a control file.
                cls.create_arbitrary_content(prefix, '{}_not_excluded.htm')
                cls.create_arbitrary_content(prefix, '{}_excluded.html')
                cls.create_arbitrary_content(prefix, 'special/{}_not_excluded.htm')
                cls.create_arbitrary_content(prefix, 'special/{}_excluded.html')

    @classmethod
    def get_content_digest_for_asset_path(cls, prefix, path):
        """
        Takes an unprocessed asset path, parses it just enough to try and find the
        asset it refers to, and returns the content digest of that asset if it exists.
        """

        # Parse the path as if it was potentially a relative URL with query parameters,
        # or an absolute URL, etc.  Only keep the path because that's all we need.
        _, _, relative_path, _, _, _ = urlparse(path)
        asset_key = StaticContent.get_asset_key_from_path(cls.courses[prefix].id, relative_path)

        try:
            content = AssetManager.find(asset_key, as_stream=True)
            return content.content_digest
        except (ItemNotFoundError, NotFoundError):
            return None

    @classmethod
    def create_image(cls, prefix, dimensions, color, name, locked=False):
        """
        Creates an image.

        Args:
            prefix: the prefix to use e.g. split vs mongo
            dimensions: tuple of (width, height)
            color: the background color of the image
            name: the name of the image; can be a format string
            locked: whether or not the asset should be locked

        Returns:
            StaticContent: the StaticContent object for the created image
        """
        new_image = Image.new('RGB', dimensions, color)
        new_buf = BytesIO()
        new_image.save(new_buf, format='png')
        new_buf.seek(0)
        new_name = name.format(prefix)
        new_key = StaticContent.compute_location(cls.courses[prefix].id, new_name)
        new_content = StaticContent(new_key, new_name, 'image/png', new_buf.getvalue(), locked=locked)
        contentstore().save(new_content)

        return new_content

    @classmethod
    def create_arbitrary_content(cls, prefix, name, locked=False):
        """
        Creates an arbitrary piece of content with a fixed body, for when content doesn't matter.

        Args:
            prefix: the prefix to use e.g. split vs mongo
            name: the name of the content; can be a format string
            locked: whether or not the asset should be locked

        Returns:
            StaticContent: the StaticContent object for the created content

        """
        new_buf = BytesIO(b'testingggggggggggg')
        new_name = name.format(prefix)
        new_key = StaticContent.compute_location(cls.courses[prefix].id, new_name)
        new_content = StaticContent(new_key, new_name, 'application/octet-stream', new_buf.getvalue(), locked=locked)
        contentstore().save(new_content)

        return new_content

    @ddt.data(
        # No leading slash.
        ('', '{prfx}_ünlöck.png', '/{asset}@{prfx}_ünlöck.png', 1),
        ('', '{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('', 'weird {prfx}_ünlöck.png', '/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('', '{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('', '{prfx}_not_excluded.htm', '/{asset}@{prfx}_not_excluded.htm', 1),
        ('dev', '{prfx}_ünlöck.png', '//dev/{asset}@{prfx}_ünlöck.png', 1),
        ('dev', '{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('dev', 'weird {prfx}_ünlöck.png', '//dev/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('dev', '{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('dev', '{prfx}_not_excluded.htm', '//dev/{asset}@{prfx}_not_excluded.htm', 1),
        # No leading slash with subdirectory.  This ensures we properly substitute slashes.
        ('', 'special/{prfx}_ünlöck.png', '/{asset}@special_{prfx}_ünlöck.png', 1),
        ('', 'special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('', 'special/weird {prfx}_ünlöck.png', '/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('', 'special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('', 'special/{prfx}_not_excluded.htm', '/{asset}@special_{prfx}_not_excluded.htm', 1),
        ('dev', 'special/{prfx}_ünlöck.png', '//dev/{asset}@special_{prfx}_ünlöck.png', 1),
        ('dev', 'special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('dev', 'special/weird {prfx}_ünlöck.png', '//dev/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('dev', 'special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('dev', 'special/{prfx}_not_excluded.htm', '//dev/{asset}@special_{prfx}_not_excluded.htm', 1),
        # Leading slash.
        ('', '/{prfx}_ünlöck.png', '/{asset}@{prfx}_ünlöck.png', 1),
        ('', '/{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('', '/weird {prfx}_ünlöck.png', '/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('', '/{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('', '/{prfx}_not_excluded.htm', '/{asset}@{prfx}_not_excluded.htm', 1),
        ('dev', '/{prfx}_ünlöck.png', '//dev/{asset}@{prfx}_ünlöck.png', 1),
        ('dev', '/{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('dev', '/weird {prfx}_ünlöck.png', '//dev/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('dev', '/{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('dev', '/{prfx}_not_excluded.htm', '//dev/{asset}@{prfx}_not_excluded.htm', 1),
        # Leading slash with subdirectory.  This ensures we properly substitute slashes.
        ('', '/special/{prfx}_ünlöck.png', '/{asset}@special_{prfx}_ünlöck.png', 1),
        ('', '/special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('', '/special/weird {prfx}_ünlöck.png', '/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('', '/special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('', '/special/{prfx}_not_excluded.htm', '/{asset}@special_{prfx}_not_excluded.htm', 1),
        ('dev', '/special/{prfx}_ünlöck.png', '//dev/{asset}@special_{prfx}_ünlöck.png', 1),
        ('dev', '/special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('dev', '/special/weird {prfx}_ünlöck.png', '//dev/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('dev', '/special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('dev', '/special/{prfx}_not_excluded.htm', '//dev/{asset}@special_{prfx}_not_excluded.htm', 1),
        # Static path.
        ('', '/static/{prfx}_ünlöck.png', '/{asset}@{prfx}_ünlöck.png', 1),
        ('', '/static/{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('', '/static/weird {prfx}_ünlöck.png', '/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('', '/static/{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('', '/static/{prfx}_not_excluded.htm', '/{asset}@{prfx}_not_excluded.htm', 1),
        ('dev', '/static/{prfx}_ünlöck.png', '//dev/{asset}@{prfx}_ünlöck.png', 1),
        ('dev', '/static/{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('dev', '/static/weird {prfx}_ünlöck.png', '//dev/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('dev', '/static/{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('dev', '/static/{prfx}_not_excluded.htm', '//dev/{asset}@{prfx}_not_excluded.htm', 1),
        # Static path with subdirectory.  This ensures we properly substitute slashes.
        ('', '/static/special/{prfx}_ünlöck.png', '/{asset}@special_{prfx}_ünlöck.png', 1),
        ('', '/static/special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('', '/static/special/weird {prfx}_ünlöck.png', '/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('', '/static/special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('', '/static/special/{prfx}_not_excluded.htm', '/{asset}@special_{prfx}_not_excluded.htm', 1),
        ('dev', '/static/special/{prfx}_ünlöck.png', '//dev/{asset}@special_{prfx}_ünlöck.png', 1),
        ('dev', '/static/special/{prfx}_lock.png', '/{asset}@special_{prfx}_lock.png', 1),
        ('dev', '/static/special/weird {prfx}_ünlöck.png', '//dev/{asset}@special_weird_{prfx}_ünlöck.png', 1),
        ('dev', '/static/special/{prfx}_excluded.html', '/{base_asset}@special_{prfx}_excluded.html', 1),
        ('dev', '/static/special/{prfx}_not_excluded.htm', '//dev/{asset}@special_{prfx}_not_excluded.htm', 1),
        # Static path with query parameter.
        (
            '',
            '/static/{prfx}_ünlöck.png?foo=/static/{prfx}_lock.png',
            '/{asset}@{prfx}_ünlöck.png?foo={encoded_asset}{prfx}_lock.png',
            2
        ),
        (
            '',
            '/static/{prfx}_lock.png?foo=/static/{prfx}_ünlöck.png',
            '/{asset}@{prfx}_lock.png?foo={encoded_asset}{prfx}_ünlöck.png',
            2
        ),
        (
            '',
            '/static/{prfx}_excluded.html?foo=/static/{prfx}_excluded.html',
            '/{base_asset}@{prfx}_excluded.html?foo={encoded_base_asset}{prfx}_excluded.html',
            2
        ),
        (
            '',
            '/static/{prfx}_excluded.html?foo=/static/{prfx}_not_excluded.htm',
            '/{base_asset}@{prfx}_excluded.html?foo={encoded_asset}{prfx}_not_excluded.htm',
            2
        ),
        (
            '',
            '/static/{prfx}_not_excluded.htm?foo=/static/{prfx}_excluded.html',
            '/{asset}@{prfx}_not_excluded.htm?foo={encoded_base_asset}{prfx}_excluded.html',
            2
        ),
        (
            '',
            '/static/{prfx}_not_excluded.htm?foo=/static/{prfx}_not_excluded.htm',
            '/{asset}@{prfx}_not_excluded.htm?foo={encoded_asset}{prfx}_not_excluded.htm',
            2
        ),
        (
            'dev',
            '/static/{prfx}_ünlöck.png?foo=/static/{prfx}_lock.png',
            '//dev/{asset}@{prfx}_ünlöck.png?foo={encoded_asset}{prfx}_lock.png',
            2
        ),
        (
            'dev',
            '/static/{prfx}_lock.png?foo=/static/{prfx}_ünlöck.png',
            '/{asset}@{prfx}_lock.png?foo={encoded_base_url}{encoded_asset}{prfx}_ünlöck.png',
            2
        ),
        (
            'dev',
            '/static/{prfx}_excluded.html?foo=/static/{prfx}_excluded.html',
            '/{base_asset}@{prfx}_excluded.html?foo={encoded_base_asset}{prfx}_excluded.html',
            2
        ),
        (
            'dev',
            '/static/{prfx}_excluded.html?foo=/static/{prfx}_not_excluded.htm',
            '/{base_asset}@{prfx}_excluded.html?foo={encoded_base_url}{encoded_asset}{prfx}_not_excluded.htm',
            2
        ),
        (
            'dev',
            '/static/{prfx}_not_excluded.htm?foo=/static/{prfx}_excluded.html',
            '//dev/{asset}@{prfx}_not_excluded.htm?foo={encoded_base_asset}{prfx}_excluded.html',
            2
        ),
        (
            'dev',
            '/static/{prfx}_not_excluded.htm?foo=/static/{prfx}_not_excluded.htm',
            '//dev/{asset}@{prfx}_not_excluded.htm?foo={encoded_base_url}{encoded_asset}{prfx}_not_excluded.htm',
            2
        ),
        # Already asset key.
        ('', '/{base_asset}@{prfx}_ünlöck.png', '/{asset}@{prfx}_ünlöck.png', 1),
        ('', '/{base_asset}@{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('', '/{base_asset}@weird_{prfx}_ünlöck.png', '/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('', '/{base_asset}@{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('', '/{base_asset}@{prfx}_not_excluded.htm', '/{asset}@{prfx}_not_excluded.htm', 1),
        ('dev', '/{base_asset}@{prfx}_ünlöck.png', '//dev/{asset}@{prfx}_ünlöck.png', 1),
        ('dev', '/{base_asset}@{prfx}_lock.png', '/{asset}@{prfx}_lock.png', 1),
        ('dev', '/{base_asset}@weird_{prfx}_ünlöck.png', '//dev/{asset}@weird_{prfx}_ünlöck.png', 1),
        ('dev', '/{base_asset}@{prfx}_excluded.html', '/{base_asset}@{prfx}_excluded.html', 1),
        ('dev', '/{base_asset}@{prfx}_not_excluded.htm', '//dev/{asset}@{prfx}_not_excluded.htm', 1),
        # Old, c4x-style path.
        ('', '/{c4x}/{prfx}_ünlöck.png', '/{c4x}/{prfx}_ünlöck.png', 1),
        ('', '/{c4x}/{prfx}_lock.png', '/{c4x}/{prfx}_lock.png', 1),
        ('', '/{c4x}/weird_{prfx}_lock.png', '/{c4x}/weird_{prfx}_lock.png', 1),
        ('', '/{c4x}/{prfx}_excluded.html', '/{c4x}/{prfx}_excluded.html', 1),
        ('', '/{c4x}/{prfx}_not_excluded.htm', '/{c4x}/{prfx}_not_excluded.htm', 1),
        ('dev', '/{c4x}/{prfx}_ünlöck.png', '/{c4x}/{prfx}_ünlöck.png', 1),
        ('dev', '/{c4x}/{prfx}_lock.png', '/{c4x}/{prfx}_lock.png', 1),
        ('dev', '/{c4x}/weird_{prfx}_ünlöck.png', '/{c4x}/weird_{prfx}_ünlöck.png', 1),
        ('dev', '/{c4x}/{prfx}_excluded.html', '/{c4x}/{prfx}_excluded.html', 1),
        ('dev', '/{c4x}/{prfx}_not_excluded.htm', '/{c4x}/{prfx}_not_excluded.htm', 1),
        # Thumbnails.
        ('', '/{base_th_key}@{prfx}_ünlöck-{th_ext}', '/{th_key}@{prfx}_ünlöck-{th_ext}', 1),
        ('', '/{base_th_key}@{prfx}_lock-{th_ext}', '/{th_key}@{prfx}_lock-{th_ext}', 1),
        ('dev', '/{base_th_key}@{prfx}_ünlöck-{th_ext}', '//dev/{th_key}@{prfx}_ünlöck-{th_ext}', 1),
        ('dev', '/{base_th_key}@{prfx}_lock-{th_ext}', '//dev/{th_key}@{prfx}_lock-{th_ext}', 1),
    )
    @ddt.unpack
    def test_canonical_asset_path_with_new_style_assets(self, base_url, start, expected, mongo_calls):
        exts = ['.html', '.tm']
        prefix = 'split'
        encoded_base_url = quote('//' + base_url)
        c4x = 'c4x/a/b/asset'
        base_asset_key = f'asset-v1:a+b+{prefix}+type@asset+block'
        adjusted_asset_key = base_asset_key
        encoded_asset_key = quote(f'/asset-v1:a+b+{prefix}+type@asset+block@')
        encoded_base_asset_key = encoded_asset_key
        base_th_key = f'asset-v1:a+b+{prefix}+type@thumbnail+block'
        adjusted_th_key = base_th_key
        th_ext = 'png-16x16.jpg'

        start = start.format(
            prfx=prefix,
            c4x=c4x,
            base_asset=base_asset_key,
            asset=adjusted_asset_key,
            encoded_base_url=encoded_base_url,
            encoded_asset=encoded_asset_key,
            base_th_key=base_th_key,
            th_key=adjusted_th_key,
            th_ext=th_ext
        )

        # Adjust for content digest.  This gets dicey quickly and we have to order our steps:
        # - replace format markets because they have curly braces
        # - encode Unicode characters to percent-encoded
        # - finally shove back in our regex patterns
        digest = CanonicalContentTest.get_content_digest_for_asset_path(prefix, start)
        if digest:
            adjusted_asset_key = f'assets/courseware/VMARK/HMARK/asset-v1:a+b+{prefix}+type@asset+block'
            adjusted_th_key = f'assets/courseware/VMARK/HMARK/asset-v1:a+b+{prefix}+type@thumbnail+block'
            encoded_asset_key = f'/assets/courseware/VMARK/HMARK/asset-v1:a+b+{prefix}+type@asset+block@'
            encoded_asset_key = quote(encoded_asset_key)

        expected = expected.format(
            prfx=prefix,
            c4x=c4x,
            base_asset=base_asset_key,
            asset=adjusted_asset_key,
            encoded_base_url=encoded_base_url,
            encoded_asset=encoded_asset_key,
            base_th_key=base_th_key,
            th_key=adjusted_th_key,
            th_ext=th_ext,
            encoded_base_asset=encoded_base_asset_key,
        )

        expected = encode_unicode_characters_in_url(expected)
        expected = expected.replace('VMARK', r'v[\d]')
        expected = expected.replace('HMARK', '[a-f0-9]{32}')
        expected = expected.replace('+', r'\+').replace('?', r'\?')

        with check_mongo_calls(mongo_calls):
            asset_path = StaticContent.get_canonicalized_asset_path(self.courses[prefix].id, start, base_url, exts)
            assert re.match(expected, asset_path) is not None


class ReplaceURLServiceTest(SharedModuleStoreTestCase):
    """
    Test ReplaceURLService methods
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org='TestX', number='TS02', run='2015')

    def setUp(self):
        super().setUp()
        self.mock_replace_static_urls = self.create_patch(
            'common.djangoapps.static_replace.services.replace_static_urls'
        )
        self.mock_replace_course_urls = self.create_patch(
            'common.djangoapps.static_replace.services.replace_course_urls'
        )
        self.mock_replace_jump_to_id_urls = self.create_patch(
            'common.djangoapps.static_replace.services.replace_jump_to_id_urls'
        )

    def create_patch(self, name):
        patcher = patch(name)
        mock_method = patcher.start()
        self.addCleanup(patcher.stop)
        return mock_method

    def test_replace_static_url_only(self):
        """
        Test only replace_static_urls method called when static_replace_only is passed as True.
        """
        replace_url_service = ReplaceURLService(xblock=self.course)
        replace_url_service.replace_urls("text", static_replace_only=True)
        assert self.mock_replace_static_urls.called
        assert not self.mock_replace_course_urls.called
        assert not self.mock_replace_jump_to_id_urls.called

    def test_service_block_argument(self):
        """This service accepts either `block` or `xblock` keyword argument."""
        replace_url_service = ReplaceURLService(block=self.course)
        replace_url_service.replace_urls("text", static_replace_only=True)
        assert self.mock_replace_static_urls.called
        assert not self.mock_replace_course_urls.called
        assert not self.mock_replace_jump_to_id_urls.called

    def test_replace_course_urls_called(self):
        """
        Test replace_course_urls method called static_replace_only is passed as False.
        """
        replace_url_service = ReplaceURLService(xblock=self.course)
        replace_url_service.replace_urls("text")
        assert self.mock_replace_course_urls.called

    def test_replace_jump_to_id_urls_called(self):
        """
        Test replace_jump_to_id_urls method called jump_to_id_base_url is provided.
        """
        replace_url_service = ReplaceURLService(xblock=self.course, jump_to_id_base_url="/course/course_id")
        replace_url_service.replace_urls("text")
        assert self.mock_replace_jump_to_id_urls.called

    def test_replace_jump_to_id_urls_not_called(self):
        """
        Test replace_jump_to_id_urls method called jump_to_id_base_url is not provided.
        """
        replace_url_service = ReplaceURLService(xblock=self.course)
        replace_url_service.replace_urls("text")
        assert not self.mock_replace_jump_to_id_urls.called


@ddt.ddt
class TestReplaceURLWrapper(SharedModuleStoreTestCase):
    """
    Tests for replace_url_wrapper utility function.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(org='TestX', number='TS02', run='2015')

    def test_replace_jump_to_id_urls(self):
        """
        Verify that the jump-to URL has been replaced.
        """
        fragment = Fragment('<a href="/jump_to_id/id">')
        test_replace = replace_urls_wrapper(
            block=self.course,
            view='baseview',
            frag=fragment,
            context=None,
            replace_url_service=partial(ReplaceURLService, jump_to_id_base_url='/base_url/'),
        )
        assert isinstance(test_replace, Fragment)
        assert test_replace.content == replace_jump_to_id_urls(fragment.content, self.course.id, '/base_url/')
        assert test_replace.content == '<a href="/base_url/id">'

    def test_replace_course_urls(self):
        """
        Verify that the course URL has been replaced.
        """
        fragment = Fragment('<a href="/course/id">')
        test_replace = replace_urls_wrapper(
            block=self.course,
            view='baseview',
            frag=fragment,
            context=None,
            replace_url_service=ReplaceURLService,
        )
        assert isinstance(test_replace, Fragment)
        assert test_replace.content == replace_course_urls(fragment.content, course_key=self.course.id)
        assert test_replace.content == '<a href="/courses/course-v1:TestX+TS02+2015/id">'

    def test_replace_static_urls(self):
        """
        Verify that the static URL has been replaced.
        """
        fragment = Fragment('<a href="/static/id">')
        test_replace = replace_urls_wrapper(
            block=self.course,
            view='baseview',
            frag=fragment,
            context=None,
            replace_url_service=ReplaceURLService,
            static_replace_only=True,
        )
        assert isinstance(test_replace, Fragment)
        assert test_replace.content == replace_static_urls(fragment.content, course_id=self.course.id)
        assert test_replace.content == '<a href="/asset-v1:TestX+TS02+2015+type@asset+block/id">'
