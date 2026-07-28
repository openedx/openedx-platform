"""
HTTP endpoints for the Teams API.
"""


import logging
from collections import Counter

from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import PermissionDenied
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop
from django_countries import countries
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.paginators import DefaultPagination, paginate_search_results
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from common.djangoapps.student.models import CourseAccessRole, CourseEnrollment
from common.djangoapps.util.model_utils import truncate_fields
from lms.djangoapps.courseware.courses import get_course_with_access, has_access
from lms.djangoapps.discussion.django_comment_client.utils import has_discussion_privileges
from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership
from openedx.core.lib.api.authentication import BearerAuthentication, BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.parsers import MergePatchParser
from openedx.core.lib.api.permissions import IsCourseStaffInstructor, IsStaffOrReadOnly
from openedx.core.lib.api.view_utils import (
    ExpandableFieldViewMixin,
    RetrievePatchAPIView,
    add_serializer_errors,
    build_api_error,
)
from openedx.core.lib.teams_config import TeamsetType
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from . import is_feature_enabled
from .api import (
    OrganizationProtectionStatus,
    add_team_count,
    can_user_create_team_in_topic,
    can_user_modify_team,
    get_assignments_for_team,
    has_course_staff_privileges,
    has_specific_team_access,
    has_specific_teamset_access,
    has_team_api_access,
    user_organization_protection_status,
)
from .csv import TeamMembershipImportManager, load_team_membership_csv
from .errors import AlreadyOnTeamInTeamset, ElasticSearchConnectionError, NotEnrolledInCourseForTeam
from .search_indexes import CourseTeamIndexer
from .serializers import (
    BulkTeamCountTopicSerializer,
    CourseTeamCreationSerializer,
    CourseTeamSerializer,
    MembershipSerializer,
    TopicSerializer,
)
from .toggles import are_team_submissions_enabled
from .utils import emit_team_event

TEAM_MEMBERSHIPS_PER_PAGE = 5
TOPICS_PER_PAGE = 12
MAXIMUM_SEARCH_SIZE = 10000

log = logging.getLogger(__name__)


@receiver(post_save, sender=CourseTeam)
def team_post_save_callback(sender, instance, **kwargs):  # pylint: disable=unused-argument
    """ Emits signal after the team is saved. """
    changed_fields = instance.field_tracker.changed()
    # Don't emit events when we are first creating the team.
    if not kwargs['created']:
        for field in changed_fields:
            if field not in instance.FIELD_BLACKLIST:
                truncated_fields = truncate_fields(
                    str(changed_fields[field]),
                    str(getattr(instance, field))
                )
                truncated_fields['team_id'] = instance.team_id
                truncated_fields['team_id'] = instance.team_id
                truncated_fields['field'] = field

                emit_team_event(
                    'edx.team.changed',
                    instance.course_id,
                    truncated_fields
                )


class TopicsPagination(DefaultPagination):
    """Paginate topics. """
    page_size = TOPICS_PER_PAGE


class MyTeamsPagination(DefaultPagination):
    """Paginate the user's teams. """
    page_size = TEAM_MEMBERSHIPS_PER_PAGE


class TeamsDashboardView(GenericAPIView):
    """
    View methods related to the teams dashboard.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, course_id):
        """
        Renders the teams dashboard, which is shown on the "Teams" tab.

        Raises a 404 if the course specified by course_id does not exist, the
        user is not registered for the course, or the teams feature is not enabled.
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, "load", course_key)

        if not is_feature_enabled(course):
            raise Http404

        if not CourseEnrollment.is_enrolled(request.user, course.id) and \
                not has_access(request.user, 'staff', course, course.id):
            raise Http404

        user = request.user

        # Even though sorting is done outside of the serializer, sort_order needs to be passed
        # to the serializer so that the paginated results indicate how they were sorted.
        sort_order = 'name'
        topics = get_alphabetical_topics(course)
        topics = _filter_hidden_private_teamsets(user, topics, course)
        organization_protection_status = user_organization_protection_status(request.user, course_key)

        # We have some frontend logic that needs to know if we have any open, public, or managed teamsets,
        # and it's easier to just figure that out here when we have them all already
        teamset_counts_by_type = Counter([topic['type'] for topic in topics])

        # Paginate and serialize topic data
        # BulkTeamCountPaginatedTopicSerializer will add team counts to the topics in a single
        # bulk operation per page.
        topics_data = self._serialize_and_paginate(
            TopicsPagination,
            topics,
            request,
            BulkTeamCountTopicSerializer,
            {
                'course_id': course.id,
                'organization_protection_status': organization_protection_status
            },
        )
        topics_data["sort_order"] = sort_order  # pylint: disable=unsupported-assignment-operation

        filter_query = {
            'membership__user': user,
            'course_id': course.id,
        }
        if organization_protection_status != OrganizationProtectionStatus.protection_exempt:
            is_user_org_protected = organization_protection_status == OrganizationProtectionStatus.protected
            filter_query['organization_protected'] = is_user_org_protected

        user_teams = CourseTeam.objects.filter(**filter_query).order_by('-last_activity_at', 'team_size')
        user_teams_data = self._serialize_and_paginate(
            MyTeamsPagination,
            user_teams,
            request,
            CourseTeamSerializer,
            {'expand': ('user',)}
        )

        context = {
            "course": course,
            "topics": topics_data,
            # It is necessary to pass both privileged and staff because only privileged users can
            # administer discussion threads, but both privileged and staff users are allowed to create
            # multiple teams (since they are not automatically added to teams upon creation).
            "user_info": {
                "username": user.username,
                "privileged": has_discussion_privileges(user, course_key),
                "staff": bool(has_access(user, 'staff', course_key)),
                "teams": user_teams_data
            },
            "has_open_teamset": bool(teamset_counts_by_type[TeamsetType.open.value]),
            "has_public_managed_teamset": bool(
                teamset_counts_by_type[TeamsetType.public_managed.value] +
                teamset_counts_by_type[TeamsetType.open_managed.value]
            ),
            "has_managed_teamset": bool(
                teamset_counts_by_type[TeamsetType.public_managed.value] +
                teamset_counts_by_type[TeamsetType.private_managed.value]
            ),
            "topic_url": reverse(
                'topics_detail', kwargs={'topic_id': 'topic_id', 'course_id': str(course_id)}, request=request
            ),
            "topics_url": reverse('topics_list', request=request),
            "teams_url": reverse('teams_list', request=request),
            "teams_detail_url": reverse('teams_detail', args=['team_id']),
            "team_memberships_url": reverse('team_membership_list', request=request),
            "my_teams_url": reverse('teams_list', request=request),
            "team_membership_detail_url": reverse('team_membership_detail', args=['team_id', user.username]),
            "team_membership_management_url": reverse(
                'team_membership_bulk_management', request=request, kwargs={'course_id': course_id}
            ),
            "languages": [[lang[0], _(lang[1])] for lang in settings.ALL_LANGUAGES],  # pylint: disable=translation-of-non-string
            "countries": list(countries),
            "disable_courseware_js": True,
            "teams_base_url": reverse('teams_dashboard', request=request, kwargs={'course_id': course_id}),
        }

        # Assignments are feature-flagged
        if are_team_submissions_enabled(course_key):
            context["teams_assignments_url"] = reverse('teams_assignments_list', args=['team_id'])

        return render(request, "teams/teams.html", context)

    def _serialize_and_paginate(self, pagination_cls, queryset, request, serializer_cls, serializer_ctx):
        """
        Serialize and paginate objects in a queryset.

        Arguments:
            pagination_cls (pagination.Paginator class): Django Rest Framework Paginator subclass.
            queryset (QuerySet): Django queryset to serialize/paginate.
            serializer_cls (serializers.Serializer class): Django Rest Framework Serializer subclass.
            serializer_ctx (dict): Context dictionary to pass to the serializer

        Returns: dict

        """
        # Django Rest Framework v3 requires that we pass the request
        # into the serializer's context if the serialize contains
        # hyperlink fields.
        serializer_ctx["request"] = request

        # Instantiate the paginator and use it to paginate the queryset
        paginator = pagination_cls()
        page = paginator.paginate_queryset(queryset, request)

        # Serialize the page
        serializer = serializer_cls(page, context=serializer_ctx, many=True)

        # Use the paginator to construct the response data
        # This will use the pagination subclass for the view to add additional
        # fields to the response.
        # For example, if the input data is a list, the output data would
        # be a dictionary with keys "count", "next", "previous", and "results"
        # (where "results" is set to the value of the original list)
        return paginator.get_paginated_response(serializer.data).data


class TeamsListView(ExpandableFieldViewMixin, GenericAPIView):
    """
        **Use Cases**

            Get or create a course team.

        **Example Requests**:

            GET /api/team/v0/teams

            POST /api/team/v0/teams

        **Query Parameters for GET**

            * course_id: Filters the result to teams belonging to the given
              course. Required.

            * topic_id: Filters the result to teams associated with the given
              topic.

            * text_search: Searches for full word matches on the name, description,
              country, and language fields. NOTES: Search is on full names for countries
              and languages, not the ISO codes. Text_search cannot be requested along with
              with order_by.

            * order_by: Cannot be called along with with text_search. Must be one of the following:

                * name: Orders results by case insensitive team name (default).

                * open_slots: Orders results by most open slots (for tie-breaking,
                  last_activity_at is used, with most recent first).

                * last_activity_at: Orders result by team activity, with most active first
                  (for tie-breaking, open_slots is used, with most open slots first).

            * username: Return teams whose membership contains the given user.

            * page_size: Number of results to return per page.

            * page: Page number to retrieve.

            * expand: Comma separated list of types for which to return
              expanded representations. Supports "user" and "team".

        **Response Values for GET**

            If the user is logged in and enrolled, the response contains:

            * count: The total number of teams matching the request.

            * next: The URL to the next page of results, or null if this is the
              last page.

            * previous: The URL to the previous page of results, or null if this
              is the first page.

            * num_pages: The total number of pages in the result.

            * results: A list of the teams matching the request.

                * id: The team's unique identifier.

                * discussion_topic_id: The unique id of the comments service
                  discussion topic associated with this team.

                * name: The name of the team.

                * course_id: The identifier for the course this team belongs to.

                * topic_id: Optionally specifies which topic the team is associated
                  with.

                * date_created: Date and time when the team was created.

                * description: A description of the team.

                * country: Optionally specifies which country the team is
                  associated with.

                * language: Optionally specifies which language the team is
                  associated with.

                * last_activity_at: The date of the last activity of any team member
                  within the team.

                * membership: A list of the users that are members of the team.
                  See membership endpoint for more detail.

                * organization_protected: Whether the team consists of organization-protected
                  learners

            For all text fields, clients rendering the values should take care
            to HTML escape them to avoid script injections, as the data is
            stored exactly as specified. The intention is that plain text is
            supported, not HTML.

            If the user is not logged in, a 401 error is returned.

            If the user is not enrolled in the course specified by course_id or
            is not course or global staff, a 403 error is returned.

            If the specified course_id is not valid or the user attempts to
            use an unsupported query parameter, a 400 error is returned.

            If the response does not exist, a 404 error is returned. For
            example, the course_id may not reference a real course or the page
            number may be beyond the last page.

            If the server is unable to connect to Elasticsearch, and
            the text_search parameter is supplied, a 503 error is returned.

            If the requesting user is a learner, the learner would only see organization
            protected set of teams if the learner is enrolled in a degree bearing institution.
            Otherwise, the learner will only see organization unprotected set of teams.

        **Response Values for POST**

            Any logged in user who has verified their email address can create
            a team in an open teamset. The format mirrors that of a GET for an individual team,
            but does not include the id, date_created, or membership fields.
            id is automatically computed based on name.

            If the user is not logged in, a 401 error is returned.

            If the user is not enrolled in the course, is not course or
            global staff, or does not have discussion privileges a 403 error
            is returned.

            If the course_id is not valid, or the topic_id is missing, or extra fields
            are included in the request, a 400 error is returned.

            If the specified course does not exist, a 404 error is returned.
            If the specified teamset does not exist, a 404 error is returned.
            If the specified teamset does exist, but the requesting user shouldn't be
            able to see it, a 404 is returned.
    """

    # BearerAuthentication must come first to return a 401 for unauthenticated users
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = CourseTeamSerializer

    def get(self, request):
        """GET /api/team/v0/teams/"""
        result_filter = {}

        if 'course_id' not in request.query_params:
            return Response(
                build_api_error(gettext_noop("course_id must be provided")),
                status=status.HTTP_400_BAD_REQUEST
            )

        course_id_string = request.query_params['course_id']
        try:
            course_key = CourseKey.from_string(course_id_string)
            course_block = modulestore().get_course(course_key)
        except InvalidKeyError:
            error = build_api_error(
                gettext_noop("The supplied course id {course_id} is not valid."),
                course_id=course_id_string,
            )
            return Response(error, status=status.HTTP_400_BAD_REQUEST)

        # Ensure the course exists
        if course_block is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        result_filter.update({'course_id': course_key})

        if not has_team_api_access(request.user, course_key):
            return Response(status=status.HTTP_403_FORBIDDEN)

        text_search = request.query_params.get('text_search', None)
        if text_search and request.query_params.get('order_by', None):
            return Response(
                build_api_error(gettext_noop("text_search and order_by cannot be provided together")),
                status=status.HTTP_400_BAD_REQUEST
            )

        username = request.query_params.get('username', None)
        if username is not None:
            result_filter.update({'membership__user__username': username})
        topic_id = request.query_params.get('topic_id', None)
        if topic_id is not None:
            if topic_id not in course_block.teamsets_by_id:
                error = build_api_error(
                    gettext_noop('The supplied topic id {topic_id} is not valid'),
                    topic_id=topic_id
                )
                return Response(error, status=status.HTTP_400_BAD_REQUEST)

            result_filter.update({'topic_id': topic_id})

        organization_protection_status = user_organization_protection_status(
            request.user, course_key
        )
        if not organization_protection_status.is_exempt:
            result_filter.update({
                'organization_protected': organization_protection_status.is_protected
            })

        if text_search and CourseTeamIndexer.search_is_enabled():
            try:
                search_engine = CourseTeamIndexer.engine()
            except ElasticSearchConnectionError:
                return Response(
                    build_api_error(gettext_noop('Error connecting to elasticsearch')),
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )

            result_filter.update({'course_id': course_id_string})

            search_results = search_engine.search(
                query_string=text_search,
                field_dictionary=result_filter,
                size=MAXIMUM_SEARCH_SIZE,
            )

            # We need to manually exclude some potential private_managed teams from results, because
            # it doesn't appear that the search supports "field__in" style lookups

            # Non-staff users should not be able to see private_managed teams that they are not on.
            # Staff shouldn't have any excluded teams.
            excluded_private_team_ids = self._get_private_team_ids_to_exclude(course_block)

            search_results['results'] = [
                result for result in search_results['results']
                if result['data']['id'] not in excluded_private_team_ids
            ]
            search_results['total'] = len(search_results['results'])

            paginated_results = paginate_search_results(
                CourseTeam,
                search_results,
                self.paginator.get_page_size(request),
                self.get_page()
            )
            emit_team_event('edx.team.searched', course_key, {
                "number_of_results": search_results['total'],
                "search_text": text_search,
                "topic_id": topic_id,
            })

            page = self.paginate_queryset(paginated_results)
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        ordering_schemes = {
            'name': ('name',),  # MySQL does case-insensitive order_by
            'open_slots': ('team_size', '-last_activity_at'),
            'last_activity_at': ('-last_activity_at', 'team_size'),
        }

        # hide private_managed courses from non-staff users that aren't members of those teams
        excluded_private_team_ids = self._get_private_team_ids_to_exclude(course_block)

        queryset = CourseTeam.objects.filter(**result_filter).exclude(team_id__in=excluded_private_team_ids)
        order_by_input = request.query_params.get('order_by', 'name')
        if order_by_input not in ordering_schemes:
            return Response(
                {
                    'developer_message': "unsupported order_by value {ordering}".format(  # noqa: UP032
                        ordering=order_by_input,
                    ),
                    # Translators: 'ordering' is a string describing a way
                    # of ordering a list. For example, {ordering} may be
                    # 'name', indicating that the user wants to sort the
                    # list by lower case name.
                    'user_message': _("The ordering {ordering} is not supported").format(
                        ordering=order_by_input,
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        queryset = queryset.order_by(*ordering_schemes[order_by_input])
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        response = self.get_paginated_response(serializer.data)
        response.data['sort_order'] = order_by_input
        return response

    def post(self, request):
        """POST /api/team/v0/teams/"""
        field_errors = {}
        course_key = None
        course_id = request.data.get('course_id')
        #Handle field errors and check that the course exists
        try:
            course_key = CourseKey.from_string(course_id)
            # Ensure the course exists
            course_block = modulestore().get_course(course_key)
            if not course_block:
                return Response(status=status.HTTP_404_NOT_FOUND)
        except InvalidKeyError:
            field_errors['course_id'] = build_api_error(
                gettext_noop('The supplied course_id {course_id} is not valid.'),
                course_id=course_id
            )
            return Response({
                'field_errors': field_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        topic_id = request.data.get('topic_id')
        if not topic_id:
            field_errors['topic_id'] = build_api_error(
                gettext_noop('topic_id is required'),
                course_id=course_id
            )
            return Response({
                'field_errors': field_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        if course_key and not has_team_api_access(request.user, course_key):
            return Response(status=status.HTTP_403_FORBIDDEN)

        if topic_id not in course_block.teams_configuration.teamsets_by_id or (
            not has_specific_teamset_access(request.user, course_block, topic_id)
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)

        # The user has to have access to this teamset at this point, so we can return 403
        # and not leak the existance of a private teamset
        if not can_user_create_team_in_topic(request.user, course_key, topic_id):
            return Response(
                build_api_error(gettext_noop("You can't create a team in an instructor managed topic.")),
                status=status.HTTP_403_FORBIDDEN
            )

        # Course and global staff, as well as discussion "privileged" users, will not automatically
        # be added to a team when they create it. They are allowed to create multiple teams.
        is_team_administrator = (has_access(request.user, 'staff', course_key)
                                 or has_discussion_privileges(request.user, course_key))
        if not is_team_administrator and (
            CourseTeamMembership.user_in_team_for_teamset(request.user, course_key, topic_id=topic_id)
        ):
            error_message = build_api_error(
                gettext_noop('You are already in a team in this teamset.'),
                course_id=course_id,
                teamset_id=topic_id,
            )
            return Response(error_message, status=status.HTTP_400_BAD_REQUEST)

        data = request.data.copy()
        data['course_id'] = str(course_key)

        organization_protection_status = user_organization_protection_status(request.user, course_key)
        if organization_protection_status != OrganizationProtectionStatus.protection_exempt:
            data['organization_protected'] = organization_protection_status == OrganizationProtectionStatus.protected

        serializer = CourseTeamCreationSerializer(data=data)
        add_serializer_errors(serializer, data, field_errors)

        if field_errors:
            return Response({
                'field_errors': field_errors,
            }, status=status.HTTP_400_BAD_REQUEST)
        else:
            team = serializer.save()
            emit_team_event('edx.team.created', course_key, {
                'team_id': team.team_id
            })
            if not is_team_administrator:
                # Add the creating user to the team.
                team.add_user(request.user)
                emit_team_event(
                    'edx.team.learner_added',
                    course_key,
                    {
                        'team_id': team.team_id,
                        'user_id': request.user.id,
                        'add_method': 'added_on_create'
                    }
                )

            data = CourseTeamSerializer(team, context={"request": request}).data
            return Response(data)

    def get_page(self):
        """ Returns page number specified in args, params, or defaults to 1. """
        # This code is taken from within the GenericAPIView#paginate_queryset method.
        # We need need access to the page outside of that method for our paginate_search_results method
        page_kwarg = self.kwargs.get(self.paginator.page_query_param)
        page_query_param = self.request.query_params.get(self.paginator.page_query_param)
        return page_kwarg or page_query_param or 1

    def _get_private_team_ids_to_exclude(self, course_block):
        """
        Get the list of team ids that should be excluded from the response.
        Staff can see all private teams.
        Users should not be able to see teams in private teamsets that they are not a member of.
        """
        if has_access(self.request.user, 'staff', course_block.id):
            return set()

        private_teamset_ids = [ts.teamset_id for ts in course_block.teamsets if ts.is_private_managed]
        excluded_team_ids = CourseTeam.objects.filter(
            course_id=course_block.id,
            topic_id__in=private_teamset_ids
        ).exclude(
            membership__user=self.request.user
        ).values_list('team_id', flat=True)
        return set(excluded_team_ids)


class IsEnrolledOrIsStaff(permissions.BasePermission):
    """Permission that checks to see if the user is enrolled in the course or is staff."""

    def has_object_permission(self, request, view, obj):
        """Returns true if the user is enrolled or is staff."""
        return has_team_api_access(request.user, obj.course_id)


class IsStaffOrPrivilegedOrReadOnly(IsStaffOrReadOnly):
    """
    Permission that checks to see if the user is global staff, course
    staff, course admin, or has discussion privileges. If none of those conditions are
    met, only read access will be granted.
    """

    def has_object_permission(self, request, view, obj):
        return (
            has_discussion_privileges(request.user, obj.course_id) or
            IsCourseStaffInstructor.has_object_permission(self, request, view, obj) or
            super().has_object_permission(request, view, obj)
        )


class HasSpecificTeamAccess(permissions.BasePermission):
    """
    Permission that checks if the user has access to a specific team.
    If the user doesn't have access to the team, the endpoint should behave as if
    the team does not exist,
    """

    def has_object_permission(self, request, view, obj):
        if not has_specific_team_access(request.user, obj):
            raise Http404
        return True


class TeamsDetailView(ExpandableFieldViewMixin, RetrievePatchAPIView):
    """
        **Use Cases**

            Get, update, or delete a course team's information. Updates are supported
            only through merge patch.

        **Example Requests**:

            GET /api/team/v0/teams/{team_id}}

            PATCH /api/team/v0/teams/{team_id} "application/merge-patch+json"

            DELETE /api/team/v0/teams/{team_id}

        **Query Parameters for GET**

            * expand: Comma separated list of types for which to return
              expanded representations. Supports "user" and "team".

        **Response Values for GET**

            If the user is logged in, the response contains the following fields:

                * id: The team's unique identifier.

                * discussion_topic_id: The unique id of the comments service
                  discussion topic associated with this team.

                * name: The name of the team.

                * course_id: The identifier for the course this team belongs to.

                * topic_id: Optionally specifies which topic the team is
                  associated with.

                * date_created: Date and time when the team was created.

                * description: A description of the team.

                * country: Optionally specifies which country the team is
                  associated with.

                * language: Optionally specifies which language the team is
                  associated with.

                * membership: A list of the users that are members of the team. See
                  membership endpoint for more detail.

                * last_activity_at: The date of the last activity of any team member
                  within the team.

                * organization_protected: Whether the team consists of organization-protected
                  learners

            For all text fields, clients rendering the values should take care
            to HTML escape them to avoid script injections, as the data is
            stored exactly as specified. The intention is that plain text is
            supported, not HTML.

            If the user is not logged in, a 401 error is returned.

            If the user is not course or global staff, a 403 error is returned.

            If the specified team does not exist, a 404 error is returned.

        **Response Values for PATCH**

            Only staff can patch teams.

            If the user is anonymous or inactive, a 401 is returned.

            If the user is logged in and the team does not exist, a 404 is returned.
            If the user is not course or global staff, does not have discussion
            privileges, and the team does exist, a 403 is returned.

            If "application/merge-patch+json" is not the specified content type,
            a 415 error is returned.

            If the update could not be completed due to validation errors, this
            method returns a 400 error with all error messages in the
            "field_errors" field of the returned JSON.

        **Response Values for DELETE**

            Only staff can delete teams. When a team is deleted, all
            team memberships associated with that team are also
            deleted. Returns 204 on successful deletion.

            If the user is anonymous or inactive, a 401 is returned.

            If the user is not course or global staff and does not
            have discussion privileges, a 403 is returned.

            If the user is logged in and the team does not exist, a 404 is returned.

    """
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (
        permissions.IsAuthenticated,
        IsEnrolledOrIsStaff,
        HasSpecificTeamAccess,
        IsStaffOrPrivilegedOrReadOnly,
    )
    lookup_field = 'team_id'
    serializer_class = CourseTeamSerializer
    parser_classes = (MergePatchParser,)

    def get_queryset(self):
        """Returns the queryset used to access the given team."""
        return CourseTeam.objects.all()

    def delete(self, request, team_id):
        """DELETE /api/team/v0/teams/{team_id}"""
        team = get_object_or_404(CourseTeam, team_id=team_id)
        self.check_object_permissions(request, team)
        # Note: [] forces the queryset to be evualuated before delete()
        memberships = list(CourseTeamMembership.get_memberships(team_ids=[team_id]))

        # Note: also deletes all team memberships associated with this team
        team.delete()
        log.info('user %d deleted team %s', request.user.id, team_id)
        emit_team_event('edx.team.deleted', team.course_id, {
            'team_id': team_id,
        })
        for member in memberships:
            emit_team_event('edx.team.learner_removed', team.course_id, {
                'team_id': team_id,
                'remove_method': 'team_deleted',
                'user_id': member.user_id
            })
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeamsAssignmentsView(GenericAPIView):
    """
        **Use Cases**

            Get a team's assignments

        **Example Requests**:

            GET /api/team/v0/teams/{team_id}/assignments

        **Response Values for GET**

            If the user is logged in, the response is an array of the following data strcuture:

                * display_name: The name of the assignment to display (currently the Unit title)

                * location: The jump link to a specific assignments

            For all text fields, clients rendering the values should take care
            to HTML escape them to avoid script injections, as the data is
            stored exactly as specified. The intention is that plain text is
            supported, not HTML.

            If team assignments are not enabled for course, a 503 is returned.

            If the user is not logged in, a 401 error is returned.

            If the user is unenrolled or does not have API access, a 403 error is returned.

            If the supplied course/team is bad or the user is not permitted to
            search in a protected team, a 404 error is returned as if the team does not exist.

    """
    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (
        permissions.IsAuthenticated,
        IsEnrolledOrIsStaff,
        HasSpecificTeamAccess,
        IsStaffOrPrivilegedOrReadOnly,
    )

    def get(self, request, team_id):
        """GET v0/teams/{team_id_pattern}/assignments"""
        course_team = get_object_or_404(CourseTeam, team_id=team_id)
        user = request.user
        course_id = course_team.course_id

        if not are_team_submissions_enabled(course_id):
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if not has_team_api_access(request.user, course_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        if not has_specific_team_access(user, course_team):
            return Response(status=status.HTTP_404_NOT_FOUND)

        teamset_ora_blocks = get_assignments_for_team(user, course_team)

        # Serialize info for display
        assignments = [{
            'display_name': self._display_name_for_ora_block(block),
            'location': self._jump_location_for_block(course_id, block.location)
        } for block in teamset_ora_blocks]

        return Response(assignments)

    def _display_name_for_ora_block(self, block):
        """ Get the unit name where the ORA is located for better display naming """
        unit = modulestore().get_item(block.parent)
        section = modulestore().get_item(unit.parent)

        return "{section}: {unit}".format(  # noqa: UP032
            section=section.display_name,
            unit=unit.display_name
        )

    def _jump_location_for_block(self, course_id, location):
        """ Get the URL for jumping to a designated XBlock in a course """
        return reverse('jump_to', kwargs={'course_id': str(course_id), 'location': str(location)})


class TopicListView(GenericAPIView):
    """
        **Use Cases**

            Retrieve a list of topics associated with a single course.

        **Example Requests**

            GET /api/team/v0/topics/?course_id={course_id}

        **Query Parameters for GET**

            * course_id: Filters the result to topics belonging to the given
              course (required).

            * order_by: Orders the results. Currently only 'name' and 'team_count' are supported;
              the default value is 'name'. If 'team_count' is specified, topics are returned first sorted
              by number of teams per topic (descending), with a secondary sort of 'name'.

            * page_size: Number of results to return per page.

            * page: Page number to retrieve.

        **Response Values for GET**

            If the user is not logged in, a 401 error is returned.

            If the course_id is not given or an unsupported value is passed for
            order_by, returns a 400 error.

            If the user is not logged in, is not enrolled in the course, or is
            not course or global staff, returns a 403 error.

            If the course does not exist, returns a 404 error.

            Otherwise, a 200 response is returned containing the following
            fields:

            * count: The total number of topics matching the request.

            * next: The URL to the next page of results, or null if this is the
              last page.

            * previous: The URL to the previous page of results, or null if this
              is the first page.

            * num_pages: The total number of pages in the result.

            * results: A list of the topics matching the request.

                * id: The topic's unique identifier.

                * name: The name of the topic.

                * description: A description of the topic.

                * team_count: Number of teams created under the topic. If the requesting user
                  is enrolled into a degree bearing institution, the count only include the teams
                  with organization_protected attribute true. If the requesting user is not
                  affiliated with any institutions, the teams included in the count would only be
                  those teams whose members are outside of institutions affliation.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)
    pagination_class = TopicsPagination
    queryset = []

    def get(self, request):
        """GET /api/team/v0/topics/?course_id={course_id}"""
        course_id_string = request.query_params.get('course_id', None)
        if course_id_string is None:
            return Response({
                'field_errors': {
                    'course_id': build_api_error(
                        gettext_noop("The supplied course id {course_id} is not valid."),
                        course_id=course_id_string
                    )
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            course_id = CourseKey.from_string(course_id_string)
        except InvalidKeyError:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Ensure the course exists
        course_block = modulestore().get_course(course_id)
        if course_block is None:  # course is None if not found
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_team_api_access(request.user, course_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        ordering = request.query_params.get('order_by', 'name')
        if ordering not in ['name', 'team_count']:
            return Response({
                'developer_message': f"unsupported order_by value {ordering}",
                # Translators: 'ordering' is a string describing a way
                # of ordering a list. For example, {ordering} may be
                # 'name', indicating that the user wants to sort the
                # list by lower case name.
                'user_message': _("The ordering {ordering} is not supported").format(ordering=ordering),
            }, status=status.HTTP_400_BAD_REQUEST)

        # Always sort alphabetically, as it will be used as secondary sort
        # in the case of "team_count".
        organization_protection_status = user_organization_protection_status(request.user, course_id)
        topics = get_alphabetical_topics(course_block)
        topics = _filter_hidden_private_teamsets(request.user, topics, course_block)

        if ordering == 'team_count':
            add_team_count(request.user, topics, course_id, organization_protection_status)
            topics.sort(key=lambda t: t['team_count'], reverse=True)
            page = self.paginate_queryset(topics)
            serializer = TopicSerializer(
                page,
                context={'course_id': course_id, 'user': request.user},
                many=True,
            )
        else:
            page = self.paginate_queryset(topics)
            # Use the serializer that adds team_count in a bulk operation per page.
            serializer = BulkTeamCountTopicSerializer(
                page,
                context={
                    'request': request,
                    'course_id': course_id,
                    'organization_protection_status': organization_protection_status
                },
                many=True
            )

        response = self.get_paginated_response(serializer.data)
        response.data['sort_order'] = ordering

        return response


def _filter_hidden_private_teamsets(user, teamsets, course_block):
    """
    Return a filtered list of teamsets, removing any private teamsets that a user doesn't have access to.
    Follows the same logic as `has_specific_teamset_access` but in bulk rather than for one teamset at a time
    """
    if has_course_staff_privileges(user, course_block.id):
        return teamsets
    private_teamset_ids = [teamset.teamset_id for teamset in course_block.teamsets if teamset.is_private_managed]
    teamset_ids_user_has_access_to = set(
        CourseTeam.objects.filter(
            course_id=course_block.id,
            topic_id__in=private_teamset_ids,
            membership__user=user
        ).values_list('topic_id', flat=True)
    )
    return [
        teamset for teamset in teamsets
        if teamset['type'] != TeamsetType.private_managed.value or teamset['id'] in teamset_ids_user_has_access_to
    ]


def get_alphabetical_topics(course_block):
    """Return a list of team topics sorted alphabetically.

    Arguments:
        course_block (xblock): the course which owns the team topics

    Returns:
        list: a list of sorted team topics
    """
    return sorted(
        course_block.teams_configuration.cleaned_data['team_sets'],
        key=lambda t: t['name'].lower(),
    )


class TopicDetailView(APIView):
    """
        **Use Cases**

            Retrieve a single topic from a course.

        **Example Requests**

            GET /api/team/v0/topics/{topic_id},{course_id}

        **Query Parameters for GET**

            * topic_id: The ID of the topic to retrieve (required).

            * course_id: The ID of the course to retrieve the topic from
              (required).

        **Response Values for GET**

            If the user is not logged in, a 401 error is returned.

            If the topic_id course_id are not given or an unsupported value is
            passed for order_by, returns a 400 error.

            If the user is not enrolled in the course, or is not course or
            global staff, returns a 403 error.

            If the course does not exist, returns a 404 error.

            Otherwise, a 200 response is returned containing the following fields:

            * id: The topic's unique identifier.

            * name: The name of the topic.

            * description: A description of the topic.

            * team_count: Number of teams created under the topic. If the requesting user
                  is enrolled into a degree bearing institution, the count only include the teams
                  with organization_protected attribute true. If the requesting user is not
                  affiliated with any institutions, the teams included in the count would only be
                  those teams whose members are outside of institutions affliation.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, topic_id, course_id):
        """GET /api/team/v0/topics/{topic_id},{course_id}/"""
        try:
            course_id = CourseKey.from_string(course_id)
        except InvalidKeyError:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Ensure the course exists
        course_block = modulestore().get_course(course_id)
        if course_block is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_team_api_access(request.user, course_id):
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            topic = course_block.teamsets_by_id[topic_id]
        except KeyError:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_specific_teamset_access(request.user, course_block, topic_id):
            return Response(status=status.HTTP_404_NOT_FOUND)

        organization_protection_status = user_organization_protection_status(request.user, course_id)
        serializer = TopicSerializer(
            topic.cleaned_data,
            context={
                'course_id': course_id,
                'organization_protection_status': organization_protection_status,
                'user': request.user
            }
        )
        return Response(serializer.data)


class MembershipListView(ExpandableFieldViewMixin, GenericAPIView):
    """
        **Use Cases**

            List teamset team memberships or add a user to a teamset.

        **Example Requests**:

            GET /api/team/v0/team_membership

            POST /api/team/v0/team_membership

        **Query Parameters for GET**

            At least one of username and team_id must be provided.

            * username: Returns membership records only for the specified user.
              If the requesting user is not staff then only memberships for
              teams associated with courses in which the requesting user is
              enrolled are returned.

            * team_id: Returns only membership records associated with the
              specified team. The requesting user must be staff or enrolled in
              the course associated with the team.

            * teamset_id: Returns membership records only for the specified teamset.
              if teamset_id is specified, course_id must also be specified.
              teamset_id and team_id are mutually exclusive. For open and public_managed
              teamsets, the user must be staff or enrolled in the course. For
              private_managed teamsets, the user must be course staff, or a member of the
              specified teamset.

            * course_id: Returns membership records only for the specified
              course. Username must have access to this course, or else team_id
              must be in this course.

            * page_size: Number of results to return per page.

            * page: Page number to retrieve.

            * expand: Comma separated list of types for which to return
              expanded representations. Supports "user" and "team".

        **Response Values for GET**

            If the user is logged in and enrolled, the response contains:

            * count: The total number of memberships matching the request.

            * next: The URL to the next page of results, or null if this is the
              last page.

            * previous: The URL to the previous page of results, or null if this
              is the first page.

            * num_pages: The total number of pages in the result.

            * results: A list of the memberships matching the request.

                * user: The user associated with the membership. This field may
                  contain an expanded or collapsed representation.

                * team: The team associated with the membership. This field may
                  contain an expanded or collapsed representation.

                * date_joined: The date and time the membership was created.

                * last_activity_at: The date of the last activity of the user
                  within the team.

            For all text fields, clients rendering the values should take care
            to HTML escape them to avoid script injections, as the data is
            stored exactly as specified. The intention is that plain text is
            supported, not HTML.

            If the user is not logged in and active, a 401 error is returned.

            If neither team_id nor username are provided, a 400 error is
            returned.

            If team_id is provided but the team does not exist, a 404 error is
            returned.

            If the specified course_id is invalid, a 404 error is returned.

            This endpoint uses 404 error codes to avoid leaking information
            about team or user existence. Specifically, a 404 error will be
            returned if a logged in user specifies a team_id for a course
            they are not enrolled in.

            Additionally, when username is specified the list of returned
            memberships will be filtered to memberships in teams associated
            with courses that the requesting user is enrolled in.

            If the course specified by course_id does not contain the team
            specified by team_id, a 400 error is returned.

            If the user is not enrolled in the course specified by course_id,
            and does not have staff access to the course, a 400 error is
            returned.

        **Response Values for POST**

            Any logged in user enrolled in a course can enroll themselves in a
            team in the course. Course staff, global staff, and discussion
            privileged users can enroll any user in a team, with a few
            exceptions noted below.

            If the user is not logged in and active, a 401 error is returned.

            If username and team are not provided in the posted JSON, a 400
            error is returned describing the missing fields.

            If the specified team does not exist, a 404 error is returned.

            If the user is not staff, does not have discussion privileges,
            and is not enrolled in the course associated with the team they
            are trying to join, or if they are trying to add a user other
            than themselves to a team, a 404 error is returned. This is to
            prevent leaking information about the existence of teams and users.

            If the specified user does not exist, a 404 error is returned.

            If the user is already a member of a team in the course associated
            with the team they are trying to join, a 400 error is returned.
            This applies to both staff and students.

            If the user is not enrolled in the course associated with the team
            they are trying to join, a 400 error is returned. This can occur
            when a staff or discussion privileged user posts a request adding
            another user to a team.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = MembershipSerializer

    def get(self, request):  # pylint: disable=too-many-statements
        """GET /api/team/v0/team_membership"""
        specified_username_or_team = False
        username = None
        team_ids = None
        requested_course_id = None
        requested_course_key = None
        accessible_course_ids = None

        if 'course_id' in request.query_params:
            requested_course_id = request.query_params['course_id']
            try:
                requested_course_key = CourseKey.from_string(requested_course_id)
            except InvalidKeyError:
                return Response(status=status.HTTP_404_NOT_FOUND)

        if 'team_id' in request.query_params and 'teamset_id' in request.query_params:
            return Response(
                build_api_error(gettext_noop("teamset_id and team_id are mutually exclusive options.")),
                status=status.HTTP_400_BAD_REQUEST
            )
        elif 'team_id' in request.query_params:
            specified_username_or_team = True
            team_id = request.query_params['team_id']
            try:
                team = CourseTeam.objects.get(team_id=team_id)
                team_ids = [team.team_id]
            except CourseTeam.DoesNotExist:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if requested_course_key is not None and requested_course_key != team.course_id:
                return Response(status=status.HTTP_400_BAD_REQUEST)
            if not has_team_api_access(request.user, team.course_id):
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not has_specific_team_access(request.user, team):
                return Response(status=status.HTTP_403_FORBIDDEN)
        elif 'teamset_id' in request.query_params:
            if 'course_id' not in request.query_params:
                return Response(
                    build_api_error(gettext_noop("teamset_id requires course_id to also be provided.")),
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not has_team_api_access(request.user, requested_course_key):
                return Response(status=status.HTTP_404_NOT_FOUND)

            course_block = modulestore().get_course(requested_course_key)
            if not course_block:
                return Response(status=status.HTTP_404_NOT_FOUND)
            specified_username_or_team = True
            teamsets = course_block.teams_configuration.teamsets_by_id
            teamset_id = request.query_params['teamset_id']
            teamset = teamsets.get(teamset_id, None)
            if not teamset:
                return Response(
                    build_api_error(gettext_noop("No teamset found in given course with given id")),
                    status=status.HTTP_404_NOT_FOUND
                )
            teamset_teams = CourseTeam.objects.filter(course_id=requested_course_key, topic_id=teamset_id)
            if has_course_staff_privileges(request.user, requested_course_key):
                teams_with_access = list(teamset_teams)
            else:
                teams_with_access = [
                    team for team in teamset_teams
                    if has_specific_team_access(request.user, team)
                ]
                if teamset.is_private_managed and not teams_with_access:
                    return Response(
                        build_api_error(gettext_noop("No teamset found in given course with given id")),
                        status=status.HTTP_404_NOT_FOUND
                    )
            team_ids = [team.team_id for team in teams_with_access]

        if 'username' in request.query_params:
            specified_username_or_team = True
            username = request.query_params['username']
            if not request.user.is_staff:
                enrolled_courses = (
                    CourseEnrollment.enrollments_for_user(request.user).values_list('course_id', flat=True)
                )
                staff_courses = (
                    CourseAccessRole.objects.filter(user=request.user, role='staff').values_list('course_id', flat=True)
                )
                accessible_course_ids = [item for sublist in (enrolled_courses, staff_courses) for item in sublist]
                if requested_course_id is not None and requested_course_key not in accessible_course_ids:
                    return Response(status=status.HTTP_400_BAD_REQUEST)

        if not specified_username_or_team:
            return Response(
                build_api_error(gettext_noop("username or (team_id or teamset_id) must be specified.")),
                status=status.HTTP_400_BAD_REQUEST
            )

        course_keys = None
        if requested_course_key is not None:
            course_keys = [requested_course_key]
        elif accessible_course_ids is not None:
            course_keys = accessible_course_ids

        queryset = CourseTeamMembership.get_memberships(username, course_keys, team_ids)

        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def post(self, request):
        """POST /api/team/v0/team_membership"""
        field_errors = {}

        if 'username' not in request.data:
            field_errors['username'] = build_api_error(gettext_noop("Username is required."))

        if 'team_id' not in request.data:
            field_errors['team_id'] = build_api_error(gettext_noop("Team id is required."))

        if field_errors:
            return Response({
                'field_errors': field_errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            team = CourseTeam.objects.get(team_id=request.data['team_id'])
        except CourseTeam.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        username = request.data['username']
        if not has_team_api_access(request.user, team.course_id, access_username=username):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_specific_team_access(request.user, team):
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        course_block = modulestore().get_course(team.course_id)
        # This should use `calc_max_team_size` instead of `default_max_team_size` (TODO MST-32).
        max_team_size = course_block.teams_configuration.default_max_team_size
        if max_team_size is not None and team.users.count() >= max_team_size:
            return Response(
                build_api_error(gettext_noop("This team is already full.")),
                status=status.HTTP_400_BAD_REQUEST
            )

        if not can_user_modify_team(request.user, team):
            return Response(
                build_api_error(gettext_noop("You can't join an instructor managed team.")),
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            membership = team.add_user(user)
            emit_team_event(
                'edx.team.learner_added',
                team.course_id,
                {
                    'team_id': team.team_id,
                    'user_id': user.id,
                    'add_method': 'joined_from_team_view' if user == request.user else 'added_by_another_user'
                }
            )
        except AlreadyOnTeamInTeamset:
            return Response(
                build_api_error(
                    gettext_noop("The user {username} is already a member of a team in this teamset."),
                    username=username
                ),
                status=status.HTTP_400_BAD_REQUEST
            )
        except NotEnrolledInCourseForTeam:
            return Response(
                build_api_error(
                    gettext_noop("The user {username} is not enrolled in the course associated with this team."),
                    username=username
                ),
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(instance=membership)
        return Response(serializer.data)


class MembershipDetailView(ExpandableFieldViewMixin, GenericAPIView):
    """
        **Use Cases**

            Gets individual course team memberships or removes a user from a course team.

        **Example Requests**:

            GET /api/team/v0/team_membership/{team_id},{username}

            DELETE /api/team/v0/team_membership/{team_id},{username}

        **Query Parameters for GET**

            * expand: Comma separated list of types for which to return
              expanded representations. Supports "user" and "team".

        **Response Values for GET**

            If the user is logged in and enrolled, or is course or global staff
            the response contains:

            * user: The user associated with the membership. This field may
              contain an expanded or collapsed representation.

            * team: The team associated with the membership. This field may
              contain an expanded or collapsed representation.

            * date_joined: The date and time the membership was created.

            * last_activity_at: The date of the last activity of any team member
                within the team.

            For all text fields, clients rendering the values should take care
            to HTML escape them to avoid script injections, as the data is
            stored exactly as specified. The intention is that plain text is
            supported, not HTML.

            If the user is not logged in and active, a 401 error is returned.

            If specified team does not exist, a 404 error is returned.

            If the user is logged in but is not enrolled in the course
            associated with the specified team, or is not staff, a 404 error is
            returned. This avoids leaking information about course or team
            existence.

            If the membership does not exist, a 404 error is returned.

        **Response Values for DELETE**

            Any logged in user enrolled in a course can remove themselves from
            a team in the course. Course staff, global staff, and discussion
            privileged users can remove any user from a team. Successfully
            deleting a membership will return a 204 response with no content.

            If the user is not logged in and active, a 401 error is returned.

            If the specified team or username does not exist, a 404 error is
            returned.

            If the user is not staff or a discussion privileged user and is
            attempting to remove another user from a team, a 404 error is
            returned. This prevents leaking information about team and user
            existence.

            If the membership does not exist, a 404 error is returned.
    """

    authentication_classes = (
        BearerAuthenticationAllowInactiveUser,
        SessionAuthenticationAllowInactiveUser
    )
    permission_classes = (permissions.IsAuthenticated,)

    serializer_class = MembershipSerializer

    def get_team(self, team_id):
        """Returns the team with team_id, or throws Http404 if it does not exist."""
        try:
            return CourseTeam.objects.get(team_id=team_id)
        except CourseTeam.DoesNotExist:
            raise Http404  # pylint: disable=raise-missing-from  # noqa: B904

    def get_membership(self, username, team):
        """Returns the membership for the given user and team, or throws Http404 if it does not exist."""
        try:
            return CourseTeamMembership.objects.get(user__username=username, team=team)
        except CourseTeamMembership.DoesNotExist:
            raise Http404  # pylint: disable=raise-missing-from  # noqa: B904

    def get(self, request, team_id, username):
        """GET /api/team/v0/team_membership/{team_id},{username}"""
        team = self.get_team(team_id)
        if not has_team_api_access(request.user, team.course_id):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_specific_team_access(request.user, team):
            return Response(status=status.HTTP_404_NOT_FOUND)

        membership = self.get_membership(username, team)

        serializer = self.get_serializer(instance=membership)
        return Response(serializer.data)

    def delete(self, request, team_id, username):
        """DELETE /api/team/v0/team_membership/{team_id},{username}"""
        team = self.get_team(team_id)
        if not has_team_api_access(request.user, team.course_id, access_username=username):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not has_specific_team_access(request.user, team):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not can_user_modify_team(request.user, team):
            return Response(
                build_api_error(gettext_noop("You can't leave an instructor managed team.")),
                status=status.HTTP_403_FORBIDDEN
            )

        membership = self.get_membership(username, team)
        removal_method = 'self_removal'
        if 'admin' in request.query_params:
            removal_method = 'removed_by_admin'
        membership.delete()
        emit_team_event(
            'edx.team.learner_removed',
            team.course_id,
            {
                'team_id': team.team_id,
                'user_id': membership.user.id,
                'remove_method': removal_method
            }
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipBulkManagementView(GenericAPIView):
    """
    View for uploading and downloading team membership CSVs.
    """

    authentication_classes = (BearerAuthentication, SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, **_kwargs):
        """
        Download CSV with team membership data for given course run.
        """
        self.check_access()
        response = HttpResponse(content_type='text/csv')
        filename = "team-membership_{}_{}_{}.csv".format(  # noqa: UP032
            self.course.id.org, self.course.id.course, self.course.id.run
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        load_team_membership_csv(self.course, response)
        return response

    def post(self, request, **_kwargs):
        """
        Process uploaded CSV to modify team memberships for given course run.
        """
        self.check_access()

        inputfile_handle = request.FILES['csv']
        team_import_manager = TeamMembershipImportManager(self.course)
        team_import_manager.set_team_membership_from_csv(inputfile_handle)

        if team_import_manager.import_succeeded:
            msg = f"{team_import_manager.number_of_learners_assigned} learners were affected."
            return JsonResponse({'message': msg}, status=status.HTTP_201_CREATED)
        else:
            return JsonResponse({
                'errors': team_import_manager.validation_errors
            }, status=status.HTTP_400_BAD_REQUEST)

    def check_access(self):
        """
        Raises 403 if user does not have access to this endpoint.
        """
        if not has_course_staff_privileges(self.request.user, self.course.id):
            raise PermissionDenied(
                "To manage team membership of {}, you must be course staff.".format(  # noqa: UP032
                    self.course.id
                )
            )

    @cached_property
    def course(self):
        """
        Return a CourseBlock based on the `course_id` kwarg.
        If invalid or not found, raise 404.
        """
        course_id_string = self.kwargs.get('course_id')
        if not course_id_string:
            raise Http404('No course key provided.')
        try:
            course_id = CourseKey.from_string(course_id_string)
        except InvalidKeyError:
            raise Http404(f'Invalid course key: {course_id_string}')  # pylint: disable=raise-missing-from  # noqa: B904
        course_block = modulestore().get_course(course_id)
        if not course_block:
            raise Http404(f'Course not found: {course_id}')
        return course_block
