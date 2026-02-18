"""
Helper functions for XBlock serialization
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager

from fs.memoryfs import MemoryFS
from fs.wrapfs import WrapFS
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import AssetKey, CourseKey

from common.djangoapps.static_replace import replace_static_urls
from xmodule.assetstore.assetmgr import AssetManager
from xmodule.contentstore.content import StaticContent
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.util.sandboxing import course_code_library_asset_name
from xmodule.xml_block import XmlMixin

from .data import StaticFile

log = logging.getLogger(__name__)


def get_asset_content_from_path(course_key, asset_path):
    """
    Locate the given asset content, load it into memory, and return it.
    Returns None if the asset is not found.
    """
    try:
        asset_key = StaticContent.get_asset_key_from_path(course_key, asset_path)
        return AssetManager.find(asset_key)
    except (ItemNotFoundError, NotFoundError):
        return None


def rewrite_absolute_static_urls(text, course_id):
    """
    Convert absolute URLs like
        https://studio-site.opencraft.hosting/asset-v1:LabXchange+101+2019+type@asset+block@SCI_1.2_Image_.png
    to the proper
        /static/SCI_1.2_Image_.png
    format for consistency and portability.
    """
    if not course_id.is_course:
        return text  # We can't rewrite URLs for libraries, which don't have "Files & Uploads".
    asset_full_url_re = r'https?://[^/]+/(?P<maybe_asset_key>[^\s\'"&]+)'

    def check_asset_key(match_obj):
        """
        If this URL's path part is an AssetKey from the same course, rewrite it.
        """
        try:
            asset_key = AssetKey.from_string(match_obj.group('maybe_asset_key'))
        except InvalidKeyError:
            return match_obj.group(0)  # Not an asset key; do not rewrite
        if asset_key.course_key == course_id:
            return '/static/' + asset_key.path  # Rewrite this to portable form
        else:
            return match_obj.group(0)  # From a different course; do not rewrite

    return re.sub(asset_full_url_re, check_asset_key, text)


def collect_assets_from_text(text, course_id, include_content=False):
    """
    Yield dicts of asset content and path from static asset paths found in the given text.
    Make sure to have replaced the URLs with rewrite_absolute_static_urls first.
    If include_content is True, the result will include a contentstore
    StaticContent file object which wraps the actual binary content of the file.
    """
    # Replace static urls like '/static/foo.png'
    static_paths = []
    # Drag-and-drop-v2 has
    #     &quot;/static/blah.png&quot;
    # which must be changed to "/static/blah.png" for replace_static_urls to work:
    text2 = text.replace("&quot;", '"')
    replace_static_urls(text=text2, course_id=course_id, static_paths_out=static_paths)
    for (path, uri) in static_paths:
        if path.startswith('/static/'):
            path = path[8:]
        info = {
            'path': path,
            'url': '/' + str(StaticContent.compute_location(course_id, path)),
        }
        if include_content:
            content = get_asset_content_from_path(course_id, path)
            if content is None:
                log.error("Static asset not found: (%s, %s)", path, uri)
            else:
                info['content'] = content
        yield info


def get_python_lib_zip_if_using(olx: str, course_id: CourseKey) -> StaticFile | None:
    """
    When python_lib is in use, capa problems that contain python code should be assumed to depend on it.

    Note: for any given problem that uses python, there is no way to tell if it
    actually uses any imports from python_lib.zip because the imports could be
    named anything. So we just have to assume that any python problems may be
    using python_lib.zip
    """
    if _has_python_script(olx):
        python_lib_filename = course_code_library_asset_name()
        asset_key = StaticContent.get_asset_key_from_path(course_id, python_lib_filename)
        # Now, it seems like this capa problem uses python_lib.zip - but does it exist in the course?
        if AssetManager.find(asset_key, throw_on_not_found=False):
            url = '/' + str(StaticContent.compute_location(course_id, python_lib_filename))
            return StaticFile(name=python_lib_filename, url=url, data=None)
    return None


def _has_python_script(olx: str) -> bool:
    """
    Check if the given OLX <problem> block string seems to contain any python
    code. (If it does, we know that it may be using python_lib.zip.)
    """
    match_strings = (
        '<script type="text/python">',
        "<script type='text/python'>",
        '<script type="loncapa/python">',
        "<script type='loncapa/python'>",
    )
    for check in match_strings:
        if check in olx:
            return True
    return False


def get_js_input_files_if_using(olx: str, course_id: CourseKey) -> [StaticFile]:
    """
    When a problem uses JSInput and references an html file uploaded to the course (i.e. uses /static/),
    all the other related static asset files that it depends on should also be included.
    """
    static_files = []
    html_file_fullpath = _extract_local_html_path(olx)
    if html_file_fullpath:
        html_filename = html_file_fullpath.split('/')[-1]
        asset_key = StaticContent.get_asset_key_from_path(course_id, html_filename)
        html_file_content = AssetManager.find(asset_key, throw_on_not_found=False)
        if html_file_content:
            static_assets = _extract_static_assets(str(html_file_content.data))
            for static_asset in static_assets:
                url = '/' + str(StaticContent.compute_location(course_id, static_asset))
                static_files.append(StaticFile(name=static_asset, url=url, data=None))

    return static_files


def _extract_static_assets(html_file_content_data: str) -> [str]:
    """
    Extracts all the static assets with relative paths that are present in the html content
    """
    # Regular expression that looks for URLs that are inside HTML tag
    # attributes (src or href) with relative paths.
    # The pattern looks for either src or href, followed by an equals sign
    # and then captures everything until it finds the closing quote (single or double)
    assets_re = r'\b(?:src|href)\s*=\s*(?![\'"]?(?:https?://))["\']([^\'"]*?\.[^\'"]*?)["\']'

    # Find all matches in the HTML code
    matches = re.findall(assets_re, html_file_content_data)

    return matches


def _extract_local_html_path(olx: str) -> str | None:
    """
    Check if the given OlX <problem> block string contains a `jsinput` tag and the `html_file` attribute
    is referencing a file in `/static/`. If so, extract the relative path of the html file in the OLX
    """
    if "<jsinput" in olx:
        # Regular expression to match html_file="/static/[anything].html" in both single and double quotes and
        # extract the "/static/[anything].html" part from the input strings.
        local_html_file_re = r'html_file=([\"\'])(?P<url>\/static\/[^\"\']*\.html)\1'
        matches = re.search(local_html_file_re, olx)
        if matches:
            return matches.group('url')  # Output example: /static/question.html

    return None


@contextmanager
def override_export_fs(block):
    """
    Hack that makes some legacy XBlocks which inherit `XmlMixin.add_xml_to_node`
    instead of the usual `XmlSerialization.add_xml_to_node` serializable to a string.
    This is needed for the OLX export API.

    Originally, `add_xml_to_node` was `XModuleDescriptor`'s method and was migrated to `XmlMixin`
    as part of the content core platform refactoring. It differs from `XmlSerialization.add_xml_to_node`
    in that it relies on `XmlMixin.export_to_file` (or `CustomTagBlock.export_to_file`) method to control
    whether a block has to be exported as two files (one .olx pointing to one .xml) file, or a single XML node.

    For the legacy blocks (`AnnotatableBlock` for instance) `export_to_file` returns `True` by default.
    The only exception is `CustomTagBlock`, for which this method was originally developed, as customtags don't
    have to be exported as separate files.

    This method temporarily replaces a block's runtime's `export_fs` system with an in-memory filesystem.
    Also, it abuses the `XmlMixin.export_to_file` API to prevent the XBlock export code from exporting
    each block as two files (one .olx pointing to one .xml file).

    Although `XModuleDescriptor` has been removed a long time ago, we have to keep this hack untill the legacy
    `add_xml_to_node` implementation is removed in favor of `XmlSerialization.add_xml_to_node`, which itself
    is a hard task involving refactoring of `CourseExportManager`.
    """
    fs = WrapFS(MemoryFS())
    fs.makedir('course')
    fs.makedir('course/static')  # Video XBlock requires this directory to exists, to put srt files etc.

    old_export_fs = block.runtime.export_fs
    block.runtime.export_fs = fs
    if hasattr(block, 'export_to_file'):
        old_export_to_file = block.export_to_file
        block.export_to_file = lambda: False
    old_global_export_to_file = XmlMixin.export_to_file
    XmlMixin.export_to_file = lambda _: False  # So this applies to child blocks that get loaded during export
    yield fs
    block.runtime.export_fs = old_export_fs
    if hasattr(block, 'export_to_file'):
        block.export_to_file = old_export_to_file
    XmlMixin.export_to_file = old_global_export_to_file


def openedx_core_def_key_from_modulestore_usage_key(usage_key):
    """
    In modulestore, the "definition key" is a MongoDB ObjectID kept in split's
    definitions table, which theoretically allows the same block to be used in
    many places (each with a unique usage key). However, that functionality is
    not exposed in Studio (other than via content libraries). So when we import
    into the OeX core content API, we assume that each usage is unique, don't
    generate a usage key, and create a new "definition key" from the original
    usage key.
    So modulestore usage key
        block-v1:A+B+C+type@html+block@introduction
    will become openedx_content definition key
        html/introduction
    """
    block_type = usage_key.block_type
    if block_type == 'vertical':
        # We transform <vertical> to <unit>
        block_type = "unit"
    return block_type + "/" + usage_key.block_id
