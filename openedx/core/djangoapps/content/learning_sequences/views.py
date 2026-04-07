"""
The views.py for this app is intentionally thin, and only exists to translate
user input/output to and from the business logic in the `api` package.
"""
import logging
from datetime import datetime, timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers
from rest_framework.exceptions import NotAuthenticated, NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.masquerade import setup_masquerade
from openedx.core import types
from openedx.core.lib.api.view_utils import validate_course_key

from .api import get_user_course_outline_details
from .data import CourseOutlineData

User = get_user_model()
log = logging.getLogger(__name__)


class CourseOutlineView(APIView):
    """
    Display all CourseOutline information for a given user.
    """
    # We want to eventually allow unauthenticated users to use this as well...
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)

    class UserCourseOutlineDataSerializer(serializers.BaseSerializer):  # lint-amnesty, pylint: disable=abstract-method
        """
        Read-only serializer for CourseOutlineData for this endpoint.

        This serializer was purposefully declared inline with the
        CourseOutlineView to discourage reuse/magic. Our goal is to make it
        extremely obvious how things are being serialized, and not have surprise
        regressions because a shared serializer in another module was modified
        to fix an issue in one of its three use cases.

        The data structures in api/data.py send back try to separate the data by
        lifecycle (e.g. CourseOutlineData vs UserCourseOutlineData) and by
        logical system (e.g. ScheduleData) to promote performance and
        pluggability. But for the REST API, we're just trying to collapse those
        into the simplest, most convenient output possible.

        We also remove any references to "usage_keys" at this layer. UsageKeys
        are a critical part of the internals of edx-platform, so the in-process
        API uses them, but we translate them to "ids" for REST API clients.
        """
        def to_representation(self, user_course_outline_details):  # lint-amnesty, pylint: disable=arguments-differ
            """
            Convert to something DRF knows how to serialize (so no custom types)

            This is intentionally dumb and lists out every field to make API
            additions/changes more obvious.
            """
            user_course_outline = user_course_outline_details.outline
            schedule = user_course_outline_details.schedule
            exam_information = user_course_outline_details.special_exam_attempts
            return {
                # Top level course information
                "course_key": str(user_course_outline.course_key),
                "course_start": schedule.course_start,
                "course_end": schedule.course_end,
                "title": user_course_outline.title,
                "published_at": user_course_outline.published_at,
                "published_version": user_course_outline.published_version,
                "entrance_exam_id": user_course_outline.entrance_exam_id,
                "days_early_for_beta": user_course_outline.days_early_for_beta,
                "self_paced": user_course_outline.self_paced,

                # Who and when this request was generated for (we can eventually
                # support arbitrary times).
                "username": user_course_outline.user.username,  # "" if anonymous
                "user_id": user_course_outline.user.id,  # null if anonymous
                "at_time": user_course_outline.at_time,

                # The actual course structure information...
                "outline": {
                    "sections": [
                        self._section_repr(section, schedule.sections.get(section.usage_key))
                        for section in user_course_outline.sections
                    ],
                    "sequences": {
                        str(seq_usage_key): self._sequence_repr(
                            sequence,
                            schedule.sequences.get(seq_usage_key),
                            exam_information.sequences.get(seq_usage_key, {}),
                            user_course_outline.accessible_sequences,
                        )
                        for seq_usage_key, sequence in user_course_outline.sequences.items()
                    },
                },
            }

        def _sequence_repr(self, sequence, sequence_schedule, sequence_exam, accessible_sequences):
            """Representation of a Sequence."""
            if sequence_schedule is None:
                schedule_item_dict = {'start': None, 'effective_start': None, 'due': None}
            else:
                schedule_item_dict = {
                    # Any of these values could be `None`
                    'start': sequence_schedule.start,
                    'effective_start': sequence_schedule.effective_start,
                    'due': sequence_schedule.due,
                }

            sequence_representation = {
                "id": str(sequence.usage_key),
                "title": sequence.title,
                "accessible": sequence.usage_key in accessible_sequences,
                "inaccessible_after_due": sequence.inaccessible_after_due,
                **schedule_item_dict,
            }

            # Only include this data if special exams are on
            if settings.FEATURES.get('ENABLE_SPECIAL_EXAMS', False):
                sequence_representation["exam"] = sequence_exam

            return sequence_representation

        def _section_repr(self, section, section_schedule):
            """Representation of a Section."""
            if section_schedule is None:
                schedule_item_dict = {'start': None, 'effective_start': None}
            else:
                # Scheduling data is very similiar to Sequences, but there are
                # no due dates for Sections. It's in the data model because OLX
                # lets you put it there, but that's a quirk that API clients
                # shouldn't have to care about.
                schedule_item_dict = {
                    # Any of these values could be `None`
                    'start': section_schedule.start,
                    'effective_start': section_schedule.effective_start,
                }

            return {
                "id": str(section.usage_key),
                "title": section.title,
                "sequence_ids": [
                    str(seq.usage_key) for seq in section.sequences
                ],
                **schedule_item_dict,
            }

    def get(self, request, course_key_str, format=None):  # lint-amnesty, pylint: disable=redefined-builtin, unused-argument
        """
        The CourseOutline, customized for a given user.

        TODO: Swagger docs of API. For an exemplar to imitate, see:
        https://github.com/openedx/edx-platform/blob/master/lms/djangoapps/program_enrollments/rest_api/v1/views.py#L792-L820
        """
        # Translate input params and do course key validation (will cause HTTP
        # 400 error if an invalid CourseKey was entered, instead of 404).
        course_key = validate_course_key(course_key_str)
        at_time = datetime.now(timezone.utc)

        # Get target user (and override request user for the benefit of any waffle checks)
        request.user = self._determine_user(request, course_key)

        try:
            # Grab the user's outline and send our response...
            user_course_outline_details = get_user_course_outline_details(course_key, request.user, at_time)
        except CourseOutlineData.DoesNotExist as does_not_exist_err:
            if not request.user.id:
                # Outline is private or doesn't exist. But don't leak whether a course exists or not to anonymous
                # users with a 404 - give a 401 instead. This mostly prevents drive-by crawlers from creating a bunch
                # of 404 errors in your error report dashboard.
                raise NotAuthenticated() from does_not_exist_err
            raise NotFound() from does_not_exist_err

        serializer = self.UserCourseOutlineDataSerializer(user_course_outline_details)
        return Response(serializer.data)

    def _determine_user(self, request, course_key: CourseKey) -> types.User:
        """
        For which user should we get an outline?

        Uses a combination of the user on the request object, session masquerading
        data, and a manually passed in "user" parameter. Ensures that the requesting
        user has permission to view course outline of target user. Raise request-level
        exceptions otherwise.

        The "user" querystring param is expected to be a username, with a blank
        value being interpreted as the anonymous user. It will take priority over
        session masquerading, if provided.
        """
        has_staff_access = has_access(request.user, 'staff', course_key).has_access

        target_username = request.GET.get("user")
        if target_username is not None:
            target_user = self._get_target_user(request, course_key, has_staff_access, target_username)
            # Just like in masquerading, set real_user so that the
            # SafeSessions middleware can see that the user didn't
            # change unexpectedly.
            target_user.real_user = request.user  # type: ignore
            return target_user

        _course_masquerade, user = setup_masquerade(request, course_key, has_staff_access)
        return user

    @staticmethod
    def _get_target_user(request, course_key: CourseKey, has_staff_access: bool, target_username: str) -> types.User:
        """
        Load and return the requested user with permission checking.

        A blank username will return an anonymous user.

        This was designed for manual API testing and kept in, as it may be useful for future development.
        """
        # Users can always see the outline as themselves.
        if target_username == request.user.username:
            return request.user

        # Otherwise, do a permission check to see if they're allowed to view as
        # other users.
        if not has_staff_access:
            display_username = "the anonymous user" if target_username == "" else target_username
            raise PermissionDenied(
                f"User {request.user.username} does not have permission to "
                f"view course outline for {course_key} as {display_username}."
            )

        # If we've gotten this far, their permissions are fine. Now we handle
        # the different-user use case...

        # Having a "user" querystring that is a blank string is interpreted as
        # "show me this outline as an anonymous user".
        if target_username == "":
            return AnonymousUser()

        # Finally, the actual work of looking up a user to target.
        try:
            target_user = User.objects.get(username=target_username)
        except User.DoesNotExist as err:
            # We throw this only after we've passed the permission check, to
            # make it harder for crawlers to get a list of our usernames.
            raise NotFound(f"User {target_username} does not exist.") from err

        return target_user
