"""
Views for the v2 cohorts REST API.
"""
import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import Http404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.paginators import DefaultPagination, IterablePaginationMixin
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.course_groups import api as cohort_api
from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.cohorts import get_legacy_discussion_settings
from openedx.core.djangoapps.course_groups.constants import USERNAME_LOOKUP_REGEX
from openedx.core.djangoapps.course_groups.models import (
    CohortMembership,
    CourseCohort,
    CourseCohortsSettings,
    CourseUserGroup,
    CourseUserGroupPartitionGroup,
)
from openedx.core.djangoapps.course_groups.rest_api.cohort_permissions import (
    CanReadCohortsAsDiscussionModerator,
    IsCohortCourseAdmin,
)
from openedx.core.djangoapps.course_groups.rest_api.cohort_serializers import (
    CohortMemberSerializer,
    CohortMembershipRequestSerializer,
    CohortMembershipResultSerializer,
    CohortSerializer,
    CohortSettingsSerializer,
    CohortUpdateSerializer,
    represent_cohort,
)
from openedx.core.lib.courses import get_course_by_id

User = get_user_model()
log = logging.getLogger(__name__)

DUPLICATE_NAME_MESSAGE = "A cohort with that name already exists in this course."
LAST_RANDOM_COHORT_MESSAGE = "There must be one cohort to which students can automatically be assigned."


class CohortAPIAccessMixin:
    """
    Authentication and path handling shared by every v2 cohort endpoint.

    The course key in the path is parsed once, before the permission check,
    and is available to the handlers as ``self.course_key``.
    """

    # Session auth keeps the Studio and instructor dashboard front ends working,
    # and the inactive-user variant is retained because the superseded surface
    # accepts inactive users over session; narrowing it would lock out callers
    # that work today.
    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )

    def initial(self, request, *args, **kwargs):
        """
        Reject a malformed course key before the permission check runs.

        The key is validated for syntax only. Whether the course exists is
        checked inside the handlers, after authorization, so that a 404 cannot
        be used to probe for courses.
        """
        try:
            self.course_key = CourseKey.from_string(kwargs.get("course_key", ""))
        except InvalidKeyError as exc:
            raise NotFound("The course key in the path is not valid.") from exc
        super().initial(request, *args, **kwargs)

    def ensure_course_exists(self):
        """Raise a 404 when the addressed course does not exist."""
        try:
            get_course_by_id(self.course_key)
        except Http404 as exc:
            raise NotFound("No such course.") from exc

    def get_cohort_or_404(self):
        """Return the cohort named in the path, or raise a 404."""
        try:
            return cohorts.get_cohort_by_id(self.course_key, self.kwargs["cohort_id"])
        except CourseUserGroup.DoesNotExist as exc:
            raise NotFound("No such cohort in this course.") from exc


def _error(description):
    """Document an error response as the standard error envelope."""
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="Course key, for example course-v1:edX+DemoX+Demo_Course.",
)
COHORT_ID_PARAMETER = OpenApiParameter(
    name="cohort_id",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    description="Identifier of the cohort.",
)
USERNAME_PARAMETER = OpenApiParameter(
    name="username",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.PATH,
    description="Username of the learner to remove.",
)
UNAUTHENTICATED_RESPONSE = _error("Authentication credentials were not provided or are not valid.")
FORBIDDEN_RESPONSE = _error("The caller may not perform this action on the cohorts of this course.")
COURSE_NOT_FOUND_RESPONSE = _error("No such course.")
COHORT_NOT_FOUND_RESPONSE = _error("No such course, or no such cohort in it.")

COHORT_ORDERING_FIELDS = ("name", "id")
DEFAULT_COHORT_ORDERING = "name"


@extend_schema_view(
    list=extend_schema(
        summary="List the cohorts of a course",
        description=(
            "Course staff and instructors may call this, and so may the course's discussion "
            "administrators, moderators and community TAs."
        ),
        parameters=[
            COURSE_KEY_PARAMETER,
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Field to order by; prefix with '-' to reverse. One of: name, id. Defaults to name.",
            ),
        ],
        responses={
            200: CohortSerializer(many=True),
            400: _error("Unsupported ordering field."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COURSE_NOT_FOUND_RESPONSE,
        },
    ),
    retrieve=extend_schema(
        summary="Read one cohort",
        description=(
            "Course staff and instructors may call this, and so may the course's discussion "
            "administrators, moderators and community TAs."
        ),
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        responses={
            200: CohortSerializer,
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    create=extend_schema(
        summary="Create a cohort",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER],
        request=CohortSerializer,
        responses={
            201: CohortSerializer,
            400: _error("Invalid payload, or the cohort name is taken. Nothing is created."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COURSE_NOT_FOUND_RESPONSE,
        },
    ),
    partial_update=extend_schema(
        summary="Update a cohort",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        request=CohortUpdateSerializer,
        responses={
            200: CohortSerializer,
            400: _error("Invalid payload, the cohort name is taken, or the change would leave the course "
                        "without a random cohort. Nothing is changed."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
class CohortViewSet(
    CohortAPIAccessMixin,
    StandardizedErrorMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    List, create, read and update the cohorts of a course.

    Discussion administrators, moderators and community TAs may list and read
    cohorts; every write needs course staff access.
    """

    permission_classes = (
        permissions.IsAuthenticated,
        IsCohortCourseAdmin | CanReadCohortsAsDiscussionModerator,
    )
    serializer_class = CohortSerializer
    pagination_class = DefaultPagination
    lookup_url_kwarg = "cohort_id"

    def list(self, request, **kwargs):
        """Return the cohorts of this course."""
        self.ensure_course_exists()
        records = CourseUserGroup.objects.filter(
            course_id=self.course_key,
            group_type=CourseUserGroup.COHORT,
        ).select_related("cohort").order_by(*self._ordering(request))
        return self.paginate_iterable(
            request,
            records,
            serialize=lambda page: [represent_cohort(c, self.course_key) for c in page],
        )

    @staticmethod
    def _ordering(request):
        """
        Translate the standard ordering parameter into ``order_by`` arguments.

        The parameter names a field, optionally prefixed with '-' to reverse
        it. The cohort id breaks ties so that pages are stable.
        """
        requested = request.query_params.get("ordering", "").strip() or DEFAULT_COHORT_ORDERING
        field = requested[1:] if requested.startswith("-") else requested
        if field not in COHORT_ORDERING_FIELDS:
            raise ValidationError(
                {"ordering": [f"Unsupported ordering field. Valid fields: {', '.join(COHORT_ORDERING_FIELDS)}."]}
            )
        return (requested,) if field == "id" else (requested, "id")

    def retrieve(self, request, **kwargs):
        """Return a single cohort."""
        cohort = self.get_cohort_or_404()
        return Response(represent_cohort(cohort, self.course_key))

    def create(self, request, **kwargs):
        """
        Create a cohort in this course.

        The payload is validated in full, content-group pairing included, before
        anything is written, and the cohort and its content-group link are
        created together or not at all.
        """
        self.ensure_course_exists()
        serializer = CohortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if cohorts.is_cohort_exists(self.course_key, data["name"]):
            raise ValidationError({"name": [DUPLICATE_NAME_MESSAGE]})

        with transaction.atomic():
            cohort = cohorts.add_cohort(self.course_key, data["name"], data["assignment_type"])
            if data.get("group_id") is not None:
                self._apply_content_group(cohort, data)
        return Response(represent_cohort(cohort, self.course_key), status=status.HTTP_201_CREATED)

    def partial_update(self, request, **kwargs):
        """
        Update a cohort's name, assignment type or content group.

        The changes are applied together or not at all, so a refused assignment
        type leaves a rename in the same request unsaved.
        """
        serializer = CohortUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cohort = self.get_cohort_or_404()

        with transaction.atomic():
            name = data.get("name")
            if name is not None and name != cohort.name:
                if cohorts.is_cohort_exists(self.course_key, name):
                    raise ValidationError({"name": [DUPLICATE_NAME_MESSAGE]})
                cohort.name = name
                cohort.save()

            if data.get("assignment_type") is not None:
                if not hasattr(cohort, "cohort"):
                    # A cohort from before CourseCohort existed, which has read as manual until now.
                    CourseCohort.objects.create(course_user_group=cohort, assignment_type=CourseCohort.MANUAL)
                try:
                    cohorts.set_assignment_type(cohort, data["assignment_type"])
                except ValueError as exc:
                    log.info("Refused assignment type change for cohort %s: %s", cohort.id, exc)
                    raise ValidationError({"assignment_type": [LAST_RANDOM_COHORT_MESSAGE]}) from exc

            if "group_id" in data:
                self._apply_content_group(cohort, data)

        return Response(represent_cohort(cohort, self.course_key))

    @staticmethod
    def _apply_content_group(cohort, data):
        """
        Link or unlink the cohort's content group.

        A ``group_id`` of None unlinks any existing link, which is distinct from
        the caller omitting the field. The serializer has already required
        ``user_partition_id`` alongside a non-null ``group_id``.
        """
        group_id = data.get("group_id")
        partition_id = data.get("user_partition_id")
        existing_group_id, existing_partition_id = cohorts.get_group_info_for_cohort(cohort)

        if group_id is None:
            if existing_group_id is not None:
                CourseUserGroupPartitionGroup.objects.filter(course_user_group=cohort).delete()
            return

        if (group_id, partition_id) != (existing_group_id, existing_partition_id):
            CourseUserGroupPartitionGroup.objects.filter(course_user_group=cohort).delete()
            CourseUserGroupPartitionGroup(
                course_user_group=cohort,
                partition_id=partition_id,
                group_id=group_id,
            ).save()


@extend_schema_view(
    list=extend_schema(
        summary="List the learners in a cohort",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        responses={
            200: CohortMemberSerializer(many=True),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    create=extend_schema(
        summary="Add learners to a cohort",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        request=CohortMembershipRequestSerializer,
        responses={
            200: CohortMembershipResultSerializer,
            400: _error("No usernames supplied."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    destroy=extend_schema(
        summary="Remove a learner from a cohort",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER, USERNAME_PARAMETER],
        responses={
            204: OpenApiResponse(description="Learner removed."),
            400: _error("Learner is not a member of this cohort."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: _error("No such course, cohort or learner."),
        },
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
class CohortMemberViewSet(
    CohortAPIAccessMixin,
    StandardizedErrorMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    List the learners in a cohort, add learners to it and remove one from it.

    Restricted to course staff in every method: the member list carries
    learners' usernames, email addresses and full names.
    """

    permission_classes = (permissions.IsAuthenticated, IsCohortCourseAdmin)
    serializer_class = CohortMemberSerializer
    pagination_class = DefaultPagination
    lookup_url_kwarg = "username"
    lookup_value_regex = USERNAME_LOOKUP_REGEX

    def list(self, request, **kwargs):
        """Return the learners in this cohort, in a stable order."""
        cohort = self.get_cohort_or_404()
        return self.paginate_iterable(
            request,
            cohort.users.order_by("id"),
            serialize=lambda page: CohortMemberSerializer(page, many=True).data,
        )

    def create(self, request, **kwargs):
        """Add the named learners to this cohort."""
        cohort = self.get_cohort_or_404()
        serializer = CohortMembershipRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = {key: [] for key in
                  ("added", "changed", "present", "unknown", "preassigned", "invalid")}
        for identifier in serializer.validated_data["users"]:
            try:
                user, previous_cohort, preassigned = cohorts.add_user_to_cohort(cohort, identifier)
            except User.DoesNotExist:
                result["unknown"].append(identifier)
            except DjangoValidationError:
                result["invalid"].append(identifier)
            except ValueError:
                result["present"].append(identifier)
            else:
                if preassigned:
                    result["preassigned"].append(identifier)
                elif previous_cohort:
                    result["changed"].append({
                        "username": user.username,
                        "email": user.email,
                        "previous_cohort": previous_cohort,
                    })
                else:
                    result["added"].append({"username": user.username, "email": user.email})
        return Response(result)

    def destroy(self, request, username, **kwargs):
        """Remove one learner from this cohort."""
        cohort = self.get_cohort_or_404()
        try:
            cohort_api.remove_user_from_cohort(self.course_key, username, cohort.id)
        except User.DoesNotExist as exc:
            raise NotFound("No such learner.") from exc
        except CohortMembership.DoesNotExist as exc:
            raise ValidationError({"username": ["The learner is not a member of this cohort."]}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        summary="Read the cohort configuration of a course",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER],
        responses={
            200: CohortSettingsSerializer,
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COURSE_NOT_FOUND_RESPONSE,
        },
    ),
    put=extend_schema(
        summary="Enable or disable cohorts for a course",
        description="Course staff and instructors only.",
        parameters=[COURSE_KEY_PARAMETER],
        request=CohortSettingsSerializer,
        responses={
            200: CohortSettingsSerializer,
            400: _error("Invalid payload."),
            401: UNAUTHENTICATED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: COURSE_NOT_FOUND_RESPONSE,
        },
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
class CohortSettingsView(CohortAPIAccessMixin, StandardizedErrorMixin, APIView):
    """
    Read and update whether a course uses cohorts.

    Restricted to course staff in both methods.
    """

    permission_classes = (permissions.IsAuthenticated, IsCohortCourseAdmin)
    serializer_class = CohortSettingsSerializer

    def get(self, request, **kwargs):
        """Return the course's cohort configuration."""
        self.ensure_course_exists()
        return Response(self._representation(self.course_key))

    def put(self, request, **kwargs):
        """Enable or disable cohorts for the course."""
        self.ensure_course_exists()
        serializer = CohortSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cohorts.set_course_cohorted(self.course_key, serializer.validated_data["is_cohorted"])
        return Response(self._representation(self.course_key))

    @staticmethod
    def _representation(course_key):
        """
        Build the cohort settings payload for a course without writing.

        cohorts.is_course_cohorted() lazily migrates a course's settings out of
        the modulestore and persists them, so calling it would make this read
        create rows. The stored row is used when it exists; otherwise the
        authored value is reported and persisting it is left to a write.
        """
        stored = CourseCohortsSettings.objects.filter(course_id=course_key).first()
        if stored is not None:
            return {"id": stored.id, "is_cohorted": stored.is_cohorted}
        return {
            "id": None,
            "is_cohorted": bool(get_legacy_discussion_settings(course_key)["is_cohorted"]),
        }
