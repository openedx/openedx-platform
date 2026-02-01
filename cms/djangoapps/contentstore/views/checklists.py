# lint-amnesty, pylint: disable=missing-module-docstring
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http.response import Http404
from django.shortcuts import redirect
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.auth import has_course_author_access

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

__all__ = ['checklists_handler']


@login_required
def checklists_handler(request, course_key_string=None):
    '''
    The restful handler for course checklists.
    It allows retrieval of the checklists (as an HTML page).
    '''
    course_key = CourseKey.from_string(course_key_string)
    if not has_course_author_access(request.user, course_key):
        raise PermissionDenied()
    mfe_base_url = configuration_helpers.get_value(
        'COURSE_AUTHORING_MICROFRONTEND_URL',
        settings.COURSE_AUTHORING_MICROFRONTEND_URL,
    )
    if mfe_base_url:
        studio_checklist_url = f'{mfe_base_url}/course/{course_key_string}/checklists'
        return redirect(studio_checklist_url)
    raise Http404
