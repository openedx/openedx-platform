"""
Views to allow modification of the current coverage context during test runs.
"""

import coverage
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


@require_POST
@csrf_exempt
def update_context(request):
    """
    Set the current coverage context.

    POST data:
        context: The current context
    """
    context = request.POST.get('context')
    current = coverage.Coverage.current()
    if current is not None and context:
        current.switch_context(context)
    return HttpResponse(status=204)
