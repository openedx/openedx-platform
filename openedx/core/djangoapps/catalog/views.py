# pylint: disable=missing-module-docstring
from django.conf import settings
from django.core.management import call_command
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def cache_programs(request):
    """
    Call the cache_programs management command.

    This view is intended for use in testing contexts where the LMS can only be
    reached over HTTP (e.g., Selenium-based browser tests). The discovery service
    API the management command attempts to call should be stubbed out first.
    """

    # checks that does site has configuration if not then
    # add a configuration with COURSE_CATALOG_API_URL parameter.

    if settings.FEATURES.get('EXPOSE_CACHE_PROGRAMS_ENDPOINT'):
        call_command('cache_programs')

        return HttpResponse('Programs cached.')

    raise Http404
