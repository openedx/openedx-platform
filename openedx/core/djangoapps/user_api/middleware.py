"""
Middleware for user api.
Adds user's tags to tracking event context.
"""

from django.utils.deprecation import MiddlewareMixin
from eventtracking import tracker
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.track.contexts import COURSE_REGEX

from .models import UserCourseTag


class UserTagsEventContextMiddleware(MiddlewareMixin):
    """Middleware that adds a user's tags to tracking event context."""
    CONTEXT_NAME = 'user_tags_context'

    def process_request(self, request):
        """
        Add a user's tags to the tracking event context.
        """
        match = COURSE_REGEX.match(request.build_absolute_uri())
        course_id = None
        if match:
            course_id = match.group('course_id')
            try:
                course_key = CourseKey.from_string(course_id)
            except InvalidKeyError:
                course_id = None
                course_key = None

        context = {}

        if course_id:
            context['course_id'] = course_id

            if request.user.is_authenticated:
                context['course_user_tags'] = dict(
                    UserCourseTag.objects.filter(
                        user=request.user.pk,
                        course_id=course_key,
                    ).values_list('key', 'value')
                )
            else:
                context['course_user_tags'] = {}

        tracker.get_tracker().enter_context(
            self.CONTEXT_NAME,
            context
        )

    def process_response(self, request, response):  # pylint: disable=unused-argument
        """Exit the context if it exists."""
        try:
            tracker.get_tracker().exit_context(self.CONTEXT_NAME)
        except:  # pylint: disable=bare-except
            pass

        return response
