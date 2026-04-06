"""
Views to toggle Calendar Sync settings for a user on a course
"""


import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View
from opaque_keys.edx.keys import CourseKey
from rest_framework import status

from common.djangoapps.util.views import ensure_valid_course_key
from openedx.features.calendar_sync.api import (
    SUBSCRIBE,
    UNSUBSCRIBE,
    subscribe_user_to_calendar,
    unsubscribe_user_to_calendar,
)
from openedx.features.course_experience import course_home_url


class CalendarSyncView(View):
    """
    View for Calendar Sync
    """
    @method_decorator(login_required)
    @method_decorator(ensure_csrf_cookie)
    @method_decorator(ensure_valid_course_key)
    def post(self, request, course_id):
        """
        Updates the request user's calendar sync subscription status

        Arguments:
            request: HTTP request
            course_id (str): string of a course key
        """
        course_key = CourseKey.from_string(course_id)
        tool_data = request.POST.get('tool_data')
        if not tool_data:
            return HttpResponse('Tool data was not provided.', status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        json_acceptable_string = tool_data.replace("'", "\"")
        data = json.loads(json_acceptable_string)
        toggle_data = data.get('toggle_data')
        if toggle_data == SUBSCRIBE:
            subscribe_user_to_calendar(request.user, course_key)
        elif toggle_data == UNSUBSCRIBE:
            unsubscribe_user_to_calendar(request.user, course_key)
        else:
            return HttpResponse('Toggle data was not provided or had unknown value.',
                                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return redirect(course_home_url(course_key))
