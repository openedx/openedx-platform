# pylint: disable=missing-module-docstring

# View for semi-static templatized content.
#
# List of valid templates is explicitly managed for (short-term)
# security reasons.

import logging
import mimetypes

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseNotFound, HttpResponseServerError
from django.shortcuts import redirect
from django.template import TemplateDoesNotExist
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.defaults import permission_denied
from django_ratelimit.exceptions import Ratelimited
from mako.exceptions import TopLevelLookupException

from common.djangoapps.edxmako.shortcuts import render_to_response, render_to_string
from common.djangoapps.util.cache import cache_if_anonymous
from common.djangoapps.util.views import fix_crum_request
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

log = logging.getLogger(__name__)

valid_templates = []

if settings.STATIC_GRAB:
    valid_templates = valid_templates + [
        'server-down.html',
        'server-error.html'
        'server-overloaded.html',
    ]


def index(request, template):
    if template in valid_templates:
        return render_to_response('static_templates/' + template, {})
    else:
        return redirect('/')


@ensure_csrf_cookie
@cache_if_anonymous()
def render(request, template):
    """
    This view function renders the template sent without checking that it
    exists. Do not expose template as a regex part of the url. The user should
    not be able to ender any arbitray template name. The correct usage would be:

    url(r'^jobs$', 'static_template_view.views.render', {'template': 'jobs.html'}, name="jobs")
    """

    # Guess content type from file extension
    content_type, __ = mimetypes.guess_type(template)

    try:
        context = {}
        # This is necessary for the dialog presented with the TOS in /register
        if template == 'honor.html':
            context['allow_iframing'] = True
        # Format Examples: static_template_about_header
        configuration_base = 'static_template_' + template.replace('.html', '').replace('-', '_')
        page_header = configuration_helpers.get_value(configuration_base + '_header')
        page_content = configuration_helpers.get_value(configuration_base + '_content')
        if page_header:
            context['page_header'] = mark_safe(page_header)
        if page_content:
            context['page_content'] = mark_safe(page_content)
        result = render_to_response('static_templates/' + template, context, content_type=content_type)
        return result
    except TopLevelLookupException:
        raise Http404  # pylint: disable=raise-missing-from  # noqa: B904
    except TemplateDoesNotExist:
        raise Http404  # pylint: disable=raise-missing-from  # noqa: B904


@ensure_csrf_cookie
@cache_if_anonymous()
def render_press_release(request, slug):
    """
    Render a press release given a slug.  Similar to the "render" function above,
    but takes a slug and does a basic conversion to convert it to a template file.
    a) all lower case,
    b) convert dashes to underscores, and
    c) appending ".html"
    """
    template = slug.lower().replace('-', '_') + ".html"
    try:
        resp = render_to_response('static_templates/press_releases/' + template, {})
    except TemplateDoesNotExist:
        raise Http404  # pylint: disable=raise-missing-from  # noqa: B904
    return resp


@fix_crum_request
def render_403(request, exception=None):
    """
    Render the permission_denied template unless it's a ratelimit exception in which case use the rate limit template.
    """
    if isinstance(exception, Ratelimited):
        return render_429(request, exception)

    return permission_denied(request, exception)


@fix_crum_request
def render_404(request, exception=None):  # pylint: disable=unused-argument
    request.view_name = '404'
    return HttpResponseNotFound(render_to_string('static_templates/404.html', {}, request=request))


@fix_crum_request
def render_429(request, exception=None):  # pylint: disable=unused-argument
    """
    Render the rate limit template as an HttpResponse.
    """
    request.view_name = '429'
    return HttpResponse(render_to_string('static_templates/429.html', {}, request=request), status=429)


@fix_crum_request
def render_500(request):
    """
    Render the generic error page when we have an uncaught error.
    """
    try:
        return HttpResponseServerError(render_to_string('static_templates/server-error.html', {}, request=request))
    except BaseException as e:  # noqa: F841
        # If we can't render the error page, ensure we don't raise another
        # exception -- because if we do, we'll probably just end up back
        # at the same rendering error.
        #
        # This is an attempt at working around the recursive error handling issues
        # observed in <https://github.com/openedx/edx-platform/issues/35151>, which
        # were triggered by Mako and translation errors.

        log.error("Encountered error while rendering error page.", exc_info=True)
        # This message is intentionally hardcoded and does not involve
        # any translation, templating, etc. Do not translate.
        return HttpResponseServerError("Encountered error while rendering error page.")
