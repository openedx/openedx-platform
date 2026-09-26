"""
REST API views for course cohorts, v2.

Consolidates the three v1 function-and-APIView surfaces (cohort list/detail,
cohort membership and course cohort settings) into DRF ViewSets registered
through a router.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, extend_schema_view
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import (
    SessionAuthenticationAllowInactiveUser,
)
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
    CourseCohortsSettings,
    CourseUserGroup,
    CourseUserGroupPartitionGroup,
)
from openedx.core.djangoapps.course_groups.rest_api.cohort_permissions import CanManageCohorts
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


class CohortAPIAccessMixin:
    """
    Authentication and authorization shared by every v2 cohort endpoint.

    JWT is the standard scheme for user-authenticated requests. Session
    authentication keeps the Studio and instructor dashboard front ends
    working, and the inactive-user variant is retained deliberately: the v1
    surface accepted inactive users over session, and narrowing that here
    would lock out callers that work today.

    Authorization lives entirely in the permission class. The handlers do not
    repeat it, which is what the v1 views did by calling get_course_with_access
    again inside each method.
    """

    # Session auth keeps the Studio and instructor dashboard front ends working,
    # and the inactive-user variant is retained because the superseded surface
    # accepts inactive users over session; narrowing it would lock out callers
    # that work today.
    authentication_classes = (
        JwtAuthentication,
        SessionAuthenticationAllowInactiveUser,
    )
    permission_classes = (permissions.IsAuthenticated, CanManageCohorts)

    def initial(self, request, *args, **kwargs):
        """
        Reject a malformed course key before the permission check runs.

        The key is validated for syntax only. Whether the course exists is
        checked inside the handlers, after authorization, so that a 404 cannot
        be used to probe for courses.
        """
        course_key_string = kwargs.get("course_key_string")
        if course_key_string is not None:
            try:
                CourseKey.from_string(course_key_string)
            except InvalidKeyError as exc:
                raise NotFound(f"{course_key_string} is not a valid course key.") from exc
        super().initial(request, *args, **kwargs)


class CourseScopedMixin:
    """
    Resolves the course key that every cohort resource is addressed under.
    """

    @property
    def course_key(self):
        """Return the course key taken from the request path."""
        return CourseKey.from_string(self.kwargs["course_key_string"])

    def ensure_course_exists(self):
        """
        Raise a 404 when the addressed course does not exist.

        Listing cohorts by course key alone would otherwise return an empty
        collection for a course that is not there.
        """
        get_course_by_id(self.course_key)

    def get_cohort_or_404(self):
        """Return the cohort named in the path, or raise a 404."""
        try:
            return cohorts.get_cohort_by_id(self.course_key, self.kwargs["cohort_id"])
        except CourseUserGroup.DoesNotExist as exc:
            raise NotFound(
                f"No cohort {self.kwargs['cohort_id']} in {self.kwargs['course_key_string']}."
            ) from exc


COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key_string",
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
FORBIDDEN_RESPONSE = OpenApiResponse(description="Caller may not manage cohorts in this course.")
COHORT_NOT_FOUND_RESPONSE = OpenApiResponse(description="No such course or cohort.")

COHORT_ORDERING_FIELDS = ("name", "id")
DEFAULT_COHORT_ORDERING = "name"


@extend_schema_view(
    list=extend_schema(
        summary="List the cohorts of a course",
        parameters=[
            COURSE_KEY_PARAMETER,
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Field to order by; prefix with '-' to reverse. One of: name, id.",
            ),
        ],
        responses={
            200: CohortSerializer(many=True),
            400: OpenApiResponse(description="Unsupported ordering field."),
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    retrieve=extend_schema(
        summary="Read one cohort",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        responses={200: CohortSerializer, 403: FORBIDDEN_RESPONSE, 404: COHORT_NOT_FOUND_RESPONSE},
    ),
    create=extend_schema(
        summary="Create a cohort",
        parameters=[COURSE_KEY_PARAMETER],
        request=CohortSerializer,
        responses={
            201: CohortSerializer,
            400: OpenApiResponse(description="Invalid payload, or the cohort name is taken."),
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    partial_update=extend_schema(
        summary="Update a cohort",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        request=CohortUpdateSerializer,
        responses={
            200: CohortSerializer,
            400: OpenApiResponse(description="Invalid payload, or the cohort name is taken."),
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
# permission_classes is declared on CohortAPIAccessMixin.
class CohortViewSet(
    CohortAPIAccessMixin,
    CourseScopedMixin,
    StandardizedErrorMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    List, create, read and update the cohorts of a course.
    """

    serializer_class = CohortSerializer
    pagination_class = DefaultPagination
    lookup_url_kwarg = "cohort_id"

    def list(self, request, course_key_string):
        """Return the cohorts of this course."""
        self.ensure_course_exists()
        course_key = self.course_key
        records = cohorts.get_course_cohorts(
            course_id=course_key,
            ordering=self._ordering_direction(request),
        )
        return self.paginate_iterable(
            request,
            records,
            serialize=lambda page: [represent_cohort(c, course_key) for c in page],
        )

    def _ordering_direction(self, request):
        """
        Translate the standard ordering parameter into a sort direction.

        The parameter names a field, optionally prefixed with '-' to reverse
        it, which is the convention the rest of the platform's list endpoints
        follow. Only the cohort name is orderable, so the field is validated
        and the direction handed to the cohort helper.
        """
        requested = request.query_params.get("ordering", DEFAULT_COHORT_ORDERING).strip()
        descending = requested.startswith("-")
        field = requested.lstrip("-") or DEFAULT_COHORT_ORDERING
        if field not in COHORT_ORDERING_FIELDS:
            raise ValidationError(
                {"ordering": f"Cannot order by '{field}'. Valid fields: {', '.join(COHORT_ORDERING_FIELDS)}."}
            )
        return "desc" if descending else "asc"

    def retrieve(self, request, course_key_string, cohort_id):
        """Return a single cohort."""
        cohort = self.get_cohort_or_404()
        return Response(represent_cohort(cohort, self.course_key))

    def create(self, request, course_key_string):
        """Create a cohort in this course."""
        serializer = CohortSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        course_key = self.course_key

        if cohorts.is_cohort_exists(course_key, data["name"]):
            raise ValidationError(
                    {"name": "A cohort with that name already exists in this course."}
                )

        cohort = cohorts.add_cohort(course_key, data["name"], data["assignment_type"])
        if data.get("group_id") is not None or data.get("user_partition_id") is not None:
            self._apply_content_group(cohort, data)
        return Response(represent_cohort(cohort, course_key), status=status.HTTP_201_CREATED)

    def partial_update(self, request, course_key_string, cohort_id):
        """Update a cohort's name, assignment type or content group."""
        serializer = CohortUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        course_key = self.course_key
        cohort = self.get_cohort_or_404()

        name = data.get("name")
        if name is not None and name != cohort.name:
            if cohorts.is_cohort_exists(course_key, name):
                raise ValidationError(
                    {"name": "A cohort with that name already exists in this course."}
                )
            cohort.name = name
            cohort.save()

        if data.get("assignment_type") is not None:
            try:
                cohorts.set_assignment_type(cohort, data["assignment_type"])
            except ValueError as exc:
                raise ValidationError({"assignment_type": str(exc)}) from exc

        if "group_id" in request.data:
            self._apply_content_group(cohort, data)

        return Response(represent_cohort(cohort, course_key))

    def _apply_content_group(self, cohort, data):
        """
        Link or unlink the cohort's content group association.

        A ``group_id`` of None unlinks any existing association, which is
        distinct from the caller omitting the field entirely.
        """
        group_id = data.get("group_id")
        partition_id = data.get("user_partition_id")
        existing_group_id, existing_partition_id = cohorts.get_group_info_for_cohort(cohort)

        if group_id is None:
            if existing_group_id is not None:
                CourseUserGroupPartitionGroup.objects.filter(course_user_group=cohort).delete()
            return

        if partition_id is None:
            raise ValidationError(
                {"user_partition_id": "Required when group_id is supplied."}
            )
        if group_id != existing_group_id or partition_id != existing_partition_id:
            CourseUserGroupPartitionGroup.objects.filter(course_user_group=cohort).delete()
            CourseUserGroupPartitionGroup(
                course_user_group=cohort,
                partition_id=partition_id,
                group_id=group_id,
            ).save()


@extend_schema_view(
    list=extend_schema(
        summary="List the learners in a cohort",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        responses={200: CohortMemberSerializer(many=True), 403: FORBIDDEN_RESPONSE,
                   404: COHORT_NOT_FOUND_RESPONSE},
    ),
    create=extend_schema(
        summary="Add learners to a cohort",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER],
        request=CohortMembershipRequestSerializer,
        responses={
            200: CohortMembershipResultSerializer,
            400: OpenApiResponse(description="No usernames supplied."),
            403: FORBIDDEN_RESPONSE,
            404: COHORT_NOT_FOUND_RESPONSE,
        },
    ),
    destroy=extend_schema(
        summary="Remove a learner from a cohort",
        parameters=[COURSE_KEY_PARAMETER, COHORT_ID_PARAMETER, USERNAME_PARAMETER],
        responses={
            204: OpenApiResponse(description="Learner removed."),
            400: OpenApiResponse(description="Learner is not a member of this cohort."),
            403: FORBIDDEN_RESPONSE,
            404: OpenApiResponse(description="No such course, cohort or learner."),
        },
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
# permission_classes is declared on CohortAPIAccessMixin.
class CohortMemberViewSet(
    CohortAPIAccessMixin,
    CourseScopedMixin,
    StandardizedErrorMixin,
    IterablePaginationMixin,
    viewsets.ViewSet,
):
    """
    List the learners in a cohort, add learners to it and remove one from it.
    """

    serializer_class = CohortMemberSerializer
    pagination_class = DefaultPagination
    lookup_url_kwarg = "username"
    lookup_value_regex = USERNAME_LOOKUP_REGEX

    def list(self, request, course_key_string, cohort_id):
        """Return the learners in this cohort."""
        cohort = self.get_cohort_or_404()
        return self.paginate_iterable(
            request,
            cohort.users.all(),
            serialize=lambda page: CohortMemberSerializer(page, many=True).data,
        )

    def create(self, request, course_key_string, cohort_id):
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

    def destroy(self, request, course_key_string, cohort_id, username):
        """Remove one learner from this cohort."""
        cohort = self.get_cohort_or_404()
        try:
            cohort_api.remove_user_from_cohort(self.course_key, username, cohort.id)
        except User.DoesNotExist as exc:
            raise NotFound(f"No user named {username}.") from exc
        except CohortMembership.DoesNotExist as exc:
            raise ValidationError(
                {"username": f"{username} is not a member of this cohort."}
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        summary="Read the cohort configuration of a course",
        parameters=[COURSE_KEY_PARAMETER],
        responses={200: CohortSettingsSerializer, 403: FORBIDDEN_RESPONSE,
                   404: OpenApiResponse(description="No such course.")},
    ),
    put=extend_schema(
        summary="Enable or disable cohorts for a course",
        parameters=[COURSE_KEY_PARAMETER],
        request=CohortSettingsSerializer,
        responses={200: CohortSettingsSerializer,
                   400: OpenApiResponse(description="Invalid payload."),
                   403: FORBIDDEN_RESPONSE,
                   404: OpenApiResponse(description="No such course.")},
    ),
)
@extend_schema(tags=["openedx-platform-sdk"])
# permission_classes is declared on CohortAPIAccessMixin.
class CohortSettingsView(CohortAPIAccessMixin, StandardizedErrorMixin, APIView):
    """
    Read and update whether a course uses cohorts.
    """

    serializer_class = CohortSettingsSerializer

    def get(self, request, course_key_string):
        """Return the course's cohort configuration."""
        course_key = CourseKey.from_string(course_key_string)
        return Response(self._representation(course_key))

    def put(self, request, course_key_string):
        """Enable or disable cohorts for the course."""
        course_key = CourseKey.from_string(course_key_string)
        serializer = CohortSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            cohorts.set_course_cohorted(course_key, serializer.validated_data["is_cohorted"])
        except ValueError as exc:
            raise ValidationError({"is_cohorted": str(exc)}) from exc
        return Response(self._representation(course_key))

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
