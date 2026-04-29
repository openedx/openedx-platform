# pylint: disable=missing-module-docstring
import functools

from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError

from common.djangoapps.edxmako.shortcuts import render_to_response, render_to_string
from common.djangoapps.util.views import fix_crum_request
from openedx.core.djangolib.js_utils import dump_js_escaped_json

__all__ = ['not_found', 'server_error', 'render_404', 'render_500']


def jsonable_error(status=500, message="The Studio servers encountered an error"):
    """
    A decorator to make an error view return an JSON-formatted message if
    it was requested via AJAX.
    """
    def outer(func):
        @functools.wraps(func)
        def inner(request, *args, **kwargs):
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                content = dump_js_escaped_json({"error": message})
                return HttpResponse(content, content_type="application/json",  # pylint: disable=http-response-with-content-type-json
                                    status=status)
            else:
                return func(request, *args, **kwargs)
        return inner
    return outer


@jsonable_error(404, "Resource not found")
def not_found(request, exception):  # pylint: disable=unused-argument
    return render_to_response('error.html', {'error': '404'})


@jsonable_error(500, "The Studio servers encountered an error")
def server_error(request):
    return render_to_response('error.html', {'error': '500'})


@fix_crum_request
@jsonable_error(404, "Resource not found")
def render_404(request, exception):  # pylint: disable=unused-argument
    return HttpResponseNotFound(render_to_string('404.html', {}, request=request))


@fix_crum_request
@jsonable_error(500, "The Studio servers encountered an error")
def render_500(request):
    return HttpResponseServerError(render_to_string('500.html', {}, request=request))
