"""
XBlock runtime implementations for edX Studio
"""

import logging  # noqa: I001

from django.urls import reverse

log = logging.getLogger(__name__)


def handler_url(block, handler_name, suffix='', query='', thirdparty=False):
    """
    Handler URL function for Studio
    """

    if thirdparty:
        log.warning("edX Studio doesn't support third-party handler urls for XBlock %s", type(block))

    url = reverse('component_handler', kwargs={
        'usage_key_string': str(block.scope_ids.usage_id),
        'handler': handler_name,
        'suffix': suffix,
    }).rstrip('/')

    if query:
        url += '?' + query

    return url
