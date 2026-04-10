"""Views for debugging and diagnostics"""


import pprint
import traceback

from codejail.safe_exec import safe_exec
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.utils.html import escape
from django.views.decorators.csrf import ensure_csrf_cookie

from common.djangoapps.edxmako.shortcuts import render_to_response
from openedx.core.djangolib.markup import HTML


@login_required
@ensure_csrf_cookie
def run_python(request):
    """
    A page to allow testing the Python sandbox on a production server.

    Runs in the override context "debug_run_python", so resource limits with come first from:
        CODE_JAIL['limit_overrides']['debug_run_python']
    and then from:
        CODE_JAIL['limits']
    """
    if not request.user.is_staff:
        raise Http404
    c = {}
    c['code'] = ''
    c['results'] = None
    if request.method == 'POST':
        py_code = c['code'] = request.POST.get('code')
        g = {}
        try:
            safe_exec(
                code=py_code,
                globals_dict=g,
                slug="debug_run_python",
                limit_overrides_context="debug_run_python",
            )
        except Exception:   # pylint: disable=broad-except
            c['results'] = traceback.format_exc()
        else:
            c['results'] = pprint.pformat(g)
    return render_to_response("debug/run_python_form.html", c)


@login_required
def show_parameters(request):
    """A page that shows what parameters were on the URL and post.

    Debug-only aid. The URL is additionally gated by ``settings.DEBUG`` in
    ``lms/urls.py`` so it is not reachable in production, but we enforce
    the staff check here as a defence-in-depth guard in case the URL is
    ever re-exposed by misconfiguration.
    """
    if not request.user.is_staff:
        raise Http404
    html_list = []
    for name, value in sorted(request.GET.items()):
        html_list.append(escape(f"GET {name}: {value!r}"))
    for name, value in sorted(request.POST.items()):
        html_list.append(escape(f"POST {name}: {value!r}"))
    return HttpResponse("\n".join(HTML("<p>{}</p>").format(h) for h in html_list))
