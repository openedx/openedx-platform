"""
Views for serving course assets.

For historical reasons, this is just one view that matches to three different URL patterns, and then has to
re-parse the URL to determine which pattern is in effect. We should probably
have 3 views as entry points.
"""
import datetime
import logging

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotFound,
    HttpResponseNotModified,
    HttpResponsePermanentRedirect,
)
from django.views.decorators.http import require_safe
from edx_django_utils.monitoring import set_custom_attribute
from opaque_keys import InvalidKeyError
from opaque_keys.edx.locator import AssetLocator

from common.djangoapps.student.auth import has_studio_read_access
from common.djangoapps.student.models import CourseEnrollment
from openedx.core.djangoapps.header_control import force_header_for_response
from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag
from xmodule.assetstore.assetmgr import AssetManager
from xmodule.contentstore.content import XASSET_LOCATION_TAG, StaticContent
from xmodule.exceptions import NotFoundError
from xmodule.modulestore import InvalidLocationError
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.util.sandboxing import course_code_library_asset_name

from .caching import get_cached_content, set_cached_content
from .models import CdnUserAgentsConfig, CourseAssetCacheTtlConfig


@require_safe
def course_assets_view(request):
    """
    Serve course assets to end users. Colloquially referred to as "contentserver."
    """
    return process_request(request)


log = logging.getLogger(__name__)

# TODO: Soon as we have a reasonable way to serialize/deserialize AssetKeys, we need
# to change this file so instead of using course_id_partial, we're just using asset keys

HTTP_DATE_FORMAT = "%a, %d %b %Y %H:%M:%S GMT"


def is_asset_request(request):
    """Determines whether the given request is an asset request"""
    # Don't change this without updating urls.py! See docstring of views.py.
    return (
        request.path.startswith('/' + XASSET_LOCATION_TAG + '/')
        or
        request.path.startswith('/' + AssetLocator.CANONICAL_NAMESPACE)
        or
        StaticContent.is_versioned_asset_path(request.path)
    )


# pylint: disable=too-many-statements
def process_request(request):
    """Process the given request"""
    asset_path = request.path

    if is_asset_request(request):
        # Make sure we can convert this request into a location.
        if AssetLocator.CANONICAL_NAMESPACE in asset_path:
            asset_path = asset_path.replace('block/', 'block@', 1)

        # If this is a versioned request, pull out the digest and chop off the prefix.
        requested_digest = None
        if StaticContent.is_versioned_asset_path(asset_path):
            requested_digest, asset_path = StaticContent.parse_versioned_asset_path(asset_path)

        # Make sure we have a valid location value for this asset.
        try:
            loc = StaticContent.get_location_from_path(asset_path)
        except (InvalidLocationError, InvalidKeyError):
            return HttpResponseBadRequest()

        # Attempt to load the asset to make sure it exists, and grab the asset digest
        # if we're able to load it.
        actual_digest = None
        try:
            content = load_asset_from_location(loc)
            actual_digest = getattr(content, "content_digest", None)
        except (ItemNotFoundError, NotFoundError):
            return HttpResponseNotFound()

        # If this was a versioned asset, and the digest doesn't match, redirect
        # them to the actual version.
        if requested_digest is not None and actual_digest is not None and (actual_digest != requested_digest):
            actual_asset_path = StaticContent.add_version_to_asset_path(asset_path, actual_digest)
            return HttpResponsePermanentRedirect(actual_asset_path)

        # Set the basics for this request. Make sure that the course key for this
        # asset has a run, which old-style courses do not.  Otherwise, this will
        # explode when the key is serialized to be sent to NR.
        safe_course_key = loc.course_key
        if safe_course_key.run is None:
            safe_course_key = safe_course_key.replace(run='only')

        set_custom_attribute('course_id', safe_course_key)
        set_custom_attribute('org', loc.org)
        set_custom_attribute('contentserver.path', loc.path)

        # Figure out if this is a CDN using us as the origin.
        is_from_cdn = is_cdn_request(request)
        set_custom_attribute('contentserver.from_cdn', is_from_cdn)

        # Check if this content is locked or not.
        locked = is_content_locked(content)
        set_custom_attribute('contentserver.locked', locked)

        # Check that user has access to the content.
        if not is_user_authorized(request, content, loc):
            return HttpResponseForbidden('Unauthorized')

        # Figure out if the client sent us a conditional request, and let them know
        # if this asset has changed since then.
        last_modified_at_str = content.last_modified_at.strftime(HTTP_DATE_FORMAT)
        if 'HTTP_IF_MODIFIED_SINCE' in request.META:
            if_modified_since = request.META['HTTP_IF_MODIFIED_SINCE']
            if if_modified_since == last_modified_at_str:
                return HttpResponseNotModified()

        # *** File streaming within a byte range ***
        # If a Range is provided, parse Range attribute of the request
        # Add Content-Range in the response if Range is structurally correct
        # Request -> Range attribute structure: "Range: bytes=first-[last]"
        # Response -> Content-Range attribute structure: "Content-Range: bytes first-last/totalLength"
        # http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.35
        response = None
        if request.META.get('HTTP_RANGE'):
            # If we have a StaticContent, get a StaticContentStream.  Can't manipulate the bytes otherwise.
            if isinstance(content, StaticContent):
                content = AssetManager.find(loc, as_stream=True)

            header_value = request.META['HTTP_RANGE']
            try:
                unit, ranges = parse_range_header(header_value, content.length)
            except ValueError as exception:
                # If the header field is syntactically invalid it should be ignored.
                log.exception(
                    "%s in Range header: %s for content: %s",
                    str(exception), header_value, str(loc)
                )
            else:
                if unit != 'bytes':
                    # Only accept ranges in bytes
                    log.warning("Unknown unit in Range header: %s for content: %s", header_value, str(loc))
                elif len(ranges) > 1:
                    # According to Http/1.1 spec content for multiple ranges should be sent as a multipart message.
                    # http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.16
                    # But we send back the full content.
                    log.warning(
                        "More than 1 ranges in Range header: %s for content: %s", header_value, str(loc)
                    )
                else:
                    first, last = ranges[0]

                    if 0 <= first <= last < content.length:
                        # If the byte range is satisfiable
                        response = HttpResponse(content.stream_data_in_range(first, last))
                        response['Content-Range'] = 'bytes {first}-{last}/{length}'.format(  # noqa: UP032
                            first=first, last=last, length=content.length
                        )
                        response['Content-Length'] = str(last - first + 1)
                        response.status_code = 206  # Partial Content

                        set_custom_attribute('contentserver.ranged', True)
                    else:
                        log.warning(
                            "Cannot satisfy ranges in Range header: %s for content: %s",
                            header_value, str(loc)
                        )
                        return HttpResponse(status=416)  # Requested Range Not Satisfiable

        # If Range header is absent or syntactically invalid return a full content response.
        if response is None:
            response = HttpResponse(content.stream_data())
            response['Content-Length'] = content.length

        set_custom_attribute('contentserver.content_len', content.length)
        set_custom_attribute('contentserver.content_type', content.content_type)

        # "Accept-Ranges: bytes" tells the user that only "bytes" ranges are allowed
        response['Accept-Ranges'] = 'bytes'
        response['Content-Type'] = content.content_type
        response['X-Frame-Options'] = 'ALLOW'

        # Set any caching headers, and do any response cleanup needed.  Based on how much
        # middleware we have in place, there's no easy way to use the built-in Django
        # utilities and properly sanitize and modify a response to ensure that it is as
        # cacheable as possible, which is why we do it ourselves.
        set_caching_headers(content, loc, response)

        return response


def set_caching_headers(content, location, response):
    """
    Sets caching headers based on whether or not the asset is restricted.
    """
    is_locked = getattr(content, "locked", False)
    is_pylib = location.path == course_code_library_asset_name()

    # All classes of asset that have any kind of access control should be marked
    # as non-cacheable.
    is_restricted = is_locked or is_pylib

    # We want to signal to the end user's browser, and to any intermediate proxies/caches,
    # whether or not this asset is cacheable.  If we have a TTL configured, we inform the
    # caller, for unlocked assets, how long they are allowed to cache it.  Since locked
    # assets should be restricted to enrolled students, we simply send headers that
    # indicate there should be no caching whatsoever.
    cache_ttl = CourseAssetCacheTtlConfig.get_cache_ttl()
    if cache_ttl > 0 and not is_restricted:
        set_custom_attribute('contentserver.cacheable', True)

        response['Expires'] = get_expiration_value(datetime.datetime.utcnow(), cache_ttl)
        response['Cache-Control'] = "public, max-age={ttl}, s-maxage={ttl}".format(ttl=cache_ttl)  # noqa: UP032
    elif is_restricted:
        set_custom_attribute('contentserver.cacheable', False)

        response['Cache-Control'] = "private, no-cache, no-store"

    response['Last-Modified'] = content.last_modified_at.strftime(HTTP_DATE_FORMAT)

    # Force the Vary header to only vary responses on Origin, so that XHR and browser requests get cached
    # separately and don't screw over one another. i.e. a browser request that doesn't send Origin, and
    # caches a version of the response without CORS headers, in turn breaking XHR requests.
    force_header_for_response(response, 'Vary', 'Origin')


@staticmethod
def is_cdn_request(request):
    """
    Attempts to determine whether or not the given request is coming from a CDN.

    Currently, this is a static check because edx.org only uses CloudFront, but may
    be expanded in the future.
    """
    cdn_user_agents = CdnUserAgentsConfig.get_cdn_user_agents()
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    if user_agent in cdn_user_agents:
        # This is a CDN request.
        return True

    return False


@staticmethod
def get_expiration_value(now, cache_ttl):
    """Generates an RFC1123 datetime string based on a future offset."""
    expire_dt = now + datetime.timedelta(seconds=cache_ttl)
    return expire_dt.strftime(HTTP_DATE_FORMAT)


def is_content_locked(content):
    """
    Determines whether or not the given content is locked.
    """
    return bool(getattr(content, "locked", False))


# .. toggle_name: course_assets.allow_download_code_library
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Whether to allow learners to download the course code library
#   that is used for custom Python-graded problem blocks. (This is conventionally
#   ``python_lib.zip``, but configurable with Django setting ``PYTHON_LIB_FILENAME``).
#   This file may contain custom grading code or problem answers that should not be
#   revealed to learners.
# .. toggle_warning: This flag is only intended as a temporary override for use
#   in rollout, to be removed before Ulmo. Courses that rely on learners being able
#   to download the code library should find an alternative workflow, or the toggle
#   should be re-documented as permanent.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2025-05-01
# .. toggle_target_removal_date: 2025-10-01
COURSE_CODE_LIBRARY_DOWNLOAD_ALLOWED = CourseWaffleFlag(
    'course_assets.allow_download_code_library', module_name=__name__,
)


def is_user_authorized(request, content, location):
    """
    Determines whether or not the user for this request is authorized to view the given asset.

    Any asset classes that have restrictions placed on them should also
    be marked as no-cache in `set_caching_headers`.
    """
    # Special-case python_lib.zip, since it often contains grading code that
    # shouldn't be revealed to learners.
    if location.path == course_code_library_asset_name():
        if has_studio_read_access(request.user, location.course_key) or \
           COURSE_CODE_LIBRARY_DOWNLOAD_ALLOWED.is_enabled(location.course_key):
            # Fall through to other access checks
            pass
        else:
            return False

    if not is_content_locked(content):
        return True

    if not hasattr(request, "user") or not request.user.is_authenticated:
        return False

    if not request.user.is_staff:
        deprecated = getattr(location, 'deprecated', False)
        if deprecated and not CourseEnrollment.is_enrolled_by_partial(request.user, location.course_key):
            return False
        if not deprecated and not CourseEnrollment.is_enrolled(request.user, location.course_key):
            return False

    return True


def load_asset_from_location(location):
    """
    Loads an asset based on its location, either retrieving it from a cache
    or loading it directly from the contentstore.
    """

    # See if we can load this item from cache.
    content = get_cached_content(location)
    if content is None:
        # Not in cache, so just try and load it from the asset manager.
        content = AssetManager.find(location, as_stream=True)

        # Now that we fetched it, let's go ahead and try to cache it. We cap this at 1MB
        # because it's the default for memcached and also we don't want to do too much
        # buffering in memory when we're serving an actual request.
        if content.length is not None and content.length < 1048576:
            content = content.copy_to_in_mem()
            set_cached_content(content)

    return content


def parse_range_header(header_value, content_length):
    """
    Returns the unit and a list of (start, end) tuples of ranges.

    Raises ValueError if header is syntactically invalid or does not contain a range.

    See spec for details: http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.35
    """

    unit = None
    ranges = []

    if '=' in header_value:
        unit, byte_ranges_string = header_value.split('=')

        # Parse the byte ranges.
        for byte_range_string in byte_ranges_string.split(','):
            byte_range_string = byte_range_string.strip()
            # Case 0:
            if '-' not in byte_range_string:  # Invalid syntax of header value.  # pylint: disable=no-else-raise
                raise ValueError('Invalid syntax.')
            # Case 1: -500
            elif byte_range_string.startswith('-'):
                first = max(0, (content_length + int(byte_range_string)))
                last = content_length - 1
            # Case 2: 500-
            elif byte_range_string.endswith('-'):
                first = int(byte_range_string[0:-1])
                last = content_length - 1
            # Case 3: 500-999
            else:
                first, last = byte_range_string.split('-')
                first = int(first)
                last = min(int(last), content_length - 1)

            ranges.append((first, last))

    if len(ranges) == 0:
        raise ValueError('Invalid syntax')

    return unit, ranges
