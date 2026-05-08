"""
Common views dedicated to rendering xblocks.
"""


import logging
import mimetypes

from django.http import Http404, HttpResponse
from xblock.core import XBlock

log = logging.getLogger(__name__)


def xblock_resource(request, block_type, uri):  # pylint: disable=unused-argument
    """
    Return a package resource for the specified XBlock.
    """
    try:
        # Figure out what the XBlock class is from the block type, and
        # then open whatever resource has been requested.
        xblock_class = XBlock.load_class(block_type)
        content = xblock_class.open_local_resource(uri)
    except OSError:
        log.info('Failed to load xblock resource', exc_info=True)
        raise Http404  # pylint: disable=raise-missing-from  # noqa: B904
    except Exception:
        log.error('Failed to load xblock resource', exc_info=True)
        raise Http404  # pylint: disable=raise-missing-from  # noqa: B904

    mimetype, _ = mimetypes.guess_type(uri)
    return HttpResponse(content, content_type=mimetype)
