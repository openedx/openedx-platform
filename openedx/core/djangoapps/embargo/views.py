"""Views served by the embargo app. """


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.http import Http404
from django.views.generic.base import View
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.edxmako.shortcuts import render_to_response

from . import messages
from .api import check_course_access


class CheckCourseAccessView(APIView):  # pylint: disable=missing-class-docstring
    permission_classes = (permissions.IsAuthenticated, permissions.IsAdminUser)

    def get(self, request):
        """
        GET /api/embargo/v1/course_access/

        Arguments:
            request (HttpRequest)

        Return:
            Response: True or False depending on the check.

        """
        course_ids = request.GET.getlist('course_ids', [])
        username = request.GET.get('user')
        user_ip_address = request.GET.get('ip_address')

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = None

        response = {'access': True}

        if course_ids and user and user_ip_address:
            for course_id in course_ids:
                try:
                    course_key = CourseKey.from_string(course_id)
                except InvalidKeyError as exc:
                    raise ValidationError('Invalid course_ids') from exc
                if not check_course_access(course_key, user=user, ip_addresses=[user_ip_address]):
                    response['access'] = False
                    break
        else:
            raise ValidationError('Missing parameters')

        return Response(response)


class CourseAccessMessageView(View):
    """Show a message explaining that the user was blocked from a course. """

    ENROLLMENT_ACCESS_POINT = 'enrollment'
    COURSEWARE_ACCESS_POINT = 'courseware'

    def get(self, request, access_point=None, message_key=None):
        """Show a message explaining that the user was blocked.

        Arguments:
            request (HttpRequest)

        Keyword Arguments:
            access_point (str): Either 'enrollment' or 'courseware',
                indicating how the user is trying to access the restricted
                content.

            message_key (str): An identifier for which message to show.
                See `embargo.messages` for more information.

        Returns:
            HttpResponse

        Raises:
            Http404: If no message is configured for the specified message key.

        """
        blocked_message = self._message(access_point, message_key)

        if blocked_message is None:
            raise Http404

        return render_to_response(blocked_message.template, {})

    def _message(self, access_point, message_key):
        """Retrieve message information.

        Arguments:
            access_point (str): Either 'enrollment' or 'courseware'
            message_key (str): The identifier for which message to show.

        Returns:
            embargo.messages.BlockedMessage or None

        """
        message_dict = {}

        # The access point determines which set of messages to use.
        # This allows us to show different messages to students who
        # are enrolling in a course than we show to students
        # who are enrolled and accessing courseware.
        if access_point == self.ENROLLMENT_ACCESS_POINT:
            message_dict = messages.ENROLL_MESSAGES
        elif access_point == self.COURSEWARE_ACCESS_POINT:
            message_dict = messages.COURSEWARE_MESSAGES

        return message_dict.get(message_key)
