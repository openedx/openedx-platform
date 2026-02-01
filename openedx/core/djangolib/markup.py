"""
Utilities for use in Mako markup.
"""

import markupsafe
import nh3
from lxml.html.clean import Cleaner
from mako.filters import decode
from django.conf import settings


# Text() can be used to declare a string as plain text, as HTML() is used
# for HTML.  It simply wraps markupsafe's escape, which will HTML-escape if
# it isn't already escaped.
Text = markupsafe.escape  # pylint: disable=invalid-name


def HTML(html):  # pylint: disable=invalid-name
    """
    Mark a string as already HTML, so that it won't be escaped before output.

    Use this function when formatting HTML into other strings.  It must be
    used in conjunction with ``Text()``, and both ``HTML()`` and ``Text()``
    must be closed before any calls to ``format()``::

        <%page expression_filter="h"/>
        <%!
        from django.utils.translation import gettext as _

        from openedx.core.djangolib.markup import HTML, Text
        %>
        ${Text(_("Write & send {start}email{end}")).format(
            start=HTML("<a href='mailto:{}'>").format(user.email),
            end=HTML("</a>"),
        )}

    """
    return markupsafe.Markup(html)


def strip_all_tags_but_br(string_to_strip):
    """
    Strips all tags from a string except <br/> and marks as HTML.

    Usage:
        <%page expression_filter="h"/>
        <%!
        from openedx.core.djangolib.markup import strip_all_tags_but_br
        %>
        ${accomplishment_course_title | n, strip_all_tags_but_br}
    """

    if string_to_strip is None:
        string_to_strip = ""

    string_to_strip = decode.utf8(string_to_strip)
    string_to_strip = nh3.clean(string_to_strip, tags={"br"})

    return HTML(string_to_strip)


def clean_dangerous_html(html):
    """
    Mark a string as already HTML and remove unsafe tags, so that it won't be escaped before output.

    Allows embedded content (iframes) only from domains configured in the
    ALLOWED_EMBED_HOSTS setting. This provides security while enabling
    legitimate video embeds in course about pages.

    Configuration:
        Set ALLOWED_EMBED_HOSTS in your settings to control which domains
        can embed content:

        ALLOWED_EMBED_HOSTS = [
            'youtube.com',
            'www.youtube.com',
            'vimeo.com',
            'custom-video-service.com',  # Add your own
        ]

    Usage:
        <%page expression_filter="h"/>
        <%!
        from openedx.core.djangolib.markup import clean_dangerous_html
        %>
        ${course_details.overview | n, clean_dangerous_html}
    """
    if not html:
        return html

    # Get allowed hosts from settings, with sensible defaults
    allowed_hosts = getattr(settings, 'ALLOWED_EMBED_HOSTS', [])

    cleaner = Cleaner(
        style=True,
        inline_style=False,
        safe_attrs_only=False,
        host_whitelist=allowed_hosts,
    )
    html = cleaner.clean_html(html)
    return HTML(html)
