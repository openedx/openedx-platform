"""Views for debugging and diagnostics"""


from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.html import escape

from openedx.core.djangolib.markup import HTML


@login_required
def show_parameters(request):
    """A page that shows what parameters were on the URL and post."""
    html_list = []
    for name, value in sorted(request.GET.items()):
        html_list.append(escape(f"GET {name}: {value!r}"))
    for name, value in sorted(request.POST.items()):
        html_list.append(escape(f"POST {name}: {value!r}"))
    return HttpResponse("\n".join(HTML("<p>{}</p>").format(h) for h in html_list))
