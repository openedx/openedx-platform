# pylint: disable=missing-module-docstring

import logging
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from opaque_keys.edx.locator import AssetLocator

from xmodule.contentstore.content import StaticContent

log = logging.getLogger(__name__)
XBLOCK_STATIC_RESOURCE_PREFIX = '/static/xblock/'


def _url_replace_regex(prefix):
    """
    Match static urls in quotes that don't end in '?raw'.

    To anyone contemplating making this more complicated:
    http://xkcd.com/1171/
    """
    return """(?x)                # flags=re.VERBOSE
        (?P<quote>\\\\?['"])      # the opening quotes
        (?P<prefix>{prefix})      # the prefix
        (?P<rest>.*?)             # everything else in the url
        (?P=quote)                # the first matching closing quote
        """.format(prefix=prefix)  # noqa: UP032


def try_staticfiles_lookup(path):
    """
    Try to lookup a path in staticfiles_storage.  If it fails, return
    a dead link instead of raising an exception.
    """
    try:
        url = staticfiles_storage.url(path)
    except Exception as err:  # pylint: disable=broad-except
        log.warning("staticfiles_storage couldn't find path {}: {}".format(  # noqa: UP032
            path, str(err)))
        # Just return the original path; don't kill everything.
        url = path
    return url


def replace_jump_to_id_urls(text, course_id, jump_to_id_base_url):  # pylint: disable=unused-argument
    """
    This will replace a link to another piece of courseware to a 'jump_to'
    URL that will redirect to the right place in the courseware

    NOTE: This is similar to replace_course_urls in terms of functionality
    but it is intended to be used when we only have a 'id' that the
    course author provides. This is much more helpful when using
    Studio authored courses since they don't need to know the path. This
    is also durable with respect to item moves.

    text: The content over which to perform the subtitutions
    course_id: The course_id in which this rewrite happens
    jump_to_id_base_url:
        A app-tier (e.g. LMS) absolute path to the base of the handler that will perform the
        redirect. e.g. /courses/<org>/<course>/<run>/jump_to_id. NOTE the <id> will be appended to
        the end of this URL at re-write time

    output: <text> after the link rewriting rules are applied
    """

    def replace_jump_to_id_url(match):
        quote = match.group('quote')
        rest = match.group('rest')
        return "".join([quote, jump_to_id_base_url + rest, quote])

    return re.sub(_url_replace_regex('/jump_to_id/'), replace_jump_to_id_url, text)


def replace_course_urls(text, course_key):
    """
    Replace /course/$stuff urls with /courses/$course_id/$stuff urls

    text: The text to replace
    course_key: A CourseBlock

    returns: text with the links replaced
    """

    course_id = str(course_key)

    def replace_course_url(match):
        quote = match.group('quote')
        rest = match.group('rest')
        return "".join([quote, '/courses/' + course_id + '/', rest, quote])

    return re.sub(_url_replace_regex('/course/'), replace_course_url, text)


def process_static_urls(text, replacement_function, data_dir=None):
    """
    Run an arbitrary replacement function on any urls matching the static file
    directory
    """
    def wrap_part_extraction(match):
        """
        Unwraps a match group for the captures specified in _url_replace_regex
        and forward them on as function arguments
        """
        original = match.group(0)
        prefix = match.group('prefix')
        quote = match.group('quote')
        rest = match.group('rest')

        # Don't rewrite XBlock resource links.  Probably wasn't a good idea that /static
        # works for actual static assets and for magical course asset URLs....
        full_url = prefix + rest

        starts_with_static_url = full_url.startswith(str(settings.STATIC_URL))
        starts_with_prefix = full_url.startswith(XBLOCK_STATIC_RESOURCE_PREFIX)
        contains_prefix = XBLOCK_STATIC_RESOURCE_PREFIX in full_url
        if starts_with_prefix or (starts_with_static_url and contains_prefix):
            return original

        return replacement_function(original, prefix, quote, rest)

    return re.sub(
        _url_replace_regex('(?:{static_url}|/static/)(?!{data_dir})'.format(  # noqa: UP032
            static_url=settings.STATIC_URL,
            data_dir=data_dir
        )),
        wrap_part_extraction,
        text
    )


def make_static_urls_absolute(request, html):
    """
    Converts relative URLs referencing static assets to absolute URLs
    """
    def replace(__, prefix, quote, rest):
        """
        Function to actually do a single relative -> absolute url replacement
        """
        processed = request.build_absolute_uri(prefix + rest)
        return quote + processed + quote

    return process_static_urls(
        html,
        replace
    )


def replace_static_urls(
    text,
    data_directory=None,
    course_id=None,
    static_asset_path='',
    static_paths_out=None,
    xblock=None,
    lookup_asset_url=None
):
    """
    Replace /static/$stuff urls either with their correct url as generated by collectstatic,
    (/static/$md5_hashed_stuff) or by the course-specific content static url
    /static/$course_data_dir/$stuff, or, if course_namespace is not None, by the
    correct url in the contentstore (/c4x/.. or /asset-loc:..) or by lookup_asset_url

    text: The source text to do the substitution in
    data_directory: The directory in which course data is stored
    course_id: The course identifier used to distinguish static content for this course in studio
    static_asset_path: Path for static assets, which overrides data_directory and course_namespace, if nonempty
    static_paths_out: (optional) pass an array to collect tuples for each static URI found:
      * the original unmodified static URI
      * the updated static URI (will match the original if unchanged)
    xblock: xblock where the static assets are stored
    lookup_url_func: Lookup function which returns the correct path of the asset
    """

    if static_paths_out is None:
        static_paths_out = []

    def replace_static_url(original, prefix, quote, rest):
        """
        Replace a single matched url.
        """
        original_uri = "".join([prefix, rest])
        # Don't mess with things that end in '?raw'
        if rest.endswith('?raw'):
            static_paths_out.append((original_uri, original_uri))
            return original

        if lookup_asset_url:
            new_url = lookup_asset_url(xblock, rest) or original_uri
            return "".join([quote, new_url, quote])

        # In debug mode, if we can find the url as is,
        if settings.DEBUG and finders.find(rest, True):
            static_paths_out.append((original_uri, original_uri))
            return original

        # if we're running with a MongoBacked store course_namespace is not None, then use studio style urls
        elif (not static_asset_path) and course_id:
            # first look in the static file pipeline and see if we are trying to reference
            # a piece of static content which is in the edx-platform repo (e.g. JS associated with an xmodule)

            exists_in_staticfiles_storage = False
            try:
                exists_in_staticfiles_storage = staticfiles_storage.exists(rest)
            except Exception as err:  # pylint: disable=broad-except
                log.warning("staticfiles_storage couldn't find path {}: {}".format(  # noqa: UP032
                    rest, str(err)))

            if exists_in_staticfiles_storage:
                url = staticfiles_storage.url(rest)
            else:
                # if not, then assume it's courseware specific content and then look in the
                # Mongo-backed database
                # Import is placed here to avoid model import at project startup.
                from common.djangoapps.static_replace.models import AssetBaseUrlConfig, AssetExcludedExtensionsConfig
                base_url = AssetBaseUrlConfig.get_base_url()
                excluded_exts = AssetExcludedExtensionsConfig.get_excluded_extensions()
                url = StaticContent.get_canonicalized_asset_path(course_id, rest, base_url, excluded_exts)

                if AssetLocator.CANONICAL_NAMESPACE in url:
                    url = url.replace('block@', 'block/', 1)

        # Otherwise, look the file up in staticfiles_storage, and append the data directory if needed
        else:
            course_path = "/".join((static_asset_path or data_directory, rest))

            try:
                if staticfiles_storage.exists(rest):
                    url = staticfiles_storage.url(rest)
                else:
                    url = staticfiles_storage.url(course_path)
            # And if that fails, assume that it's course content, and add manually data directory
            except Exception as err:  # pylint: disable=broad-except
                log.warning("staticfiles_storage couldn't find path {}: {}".format(  # noqa: UP032
                    rest, str(err)))
                url = "".join([prefix, course_path])

        static_paths_out.append((original_uri, url))
        return "".join([quote, url, quote])

    return process_static_urls(text, replace_static_url, data_dir=static_asset_path or data_directory)
