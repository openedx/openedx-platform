"""
API Views for course team management in support app.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from opaque_keys.edx.keys import CourseKey
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.models.user import CourseAccessRole
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

from ..serializers import CourseTeamManageSerializer

User = get_user_model()


class CourseTeamManageAPIView(GenericAPIView):
    """
    Use case:
        - APIs for viewing and managing a user's course team roles.
        - Lists courses where the authenticated user has permission to manage roles.
        - Displays the given user's role in each accessible course.
        - Allows assigning or revoking roles via PUT, limited to courses the user can manage.
    """

    permission_classes = (IsAuthenticated,)
    serializer_class = CourseTeamManageSerializer
    pagination_class = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._course_role_map = {}
        self._access_roles = None

    def get_serializer_context(self):
        """Provide extra context to the serializer."""
        context = super().get_serializer_context()
        context["course_role_map"] = getattr(self, "_course_role_map", {})
        return context

    def get_course_role_map_for_user(self, user):
        """Return a mapping of course_id to role for staff/instructor roles of given user."""
        access_roles = CourseAccessRole.objects.filter(
            user=user, role__in=["staff", "instructor"]
        )
        course_role_map = {}
        for access_role in access_roles:
            course_id = str(access_role.course_id)
            # Prioritize instructor role if present
            if access_role.role == "instructor" or course_id not in course_role_map:
                course_role_map[course_id] = access_role.role
        return course_role_map

    def get_accessible_courses_for_user(self, auth_user):
        """Return queryset of courses accessible by the authenticated user."""
        if auth_user.is_superuser or auth_user.is_staff:
            return CourseOverview.objects.all()

        access_roles = CourseAccessRole.objects.filter(
            user=auth_user, role="instructor"
        ).only("org", "course_id")

        # Collect org-level and course-level permissions
        orgs = set()
        course_keys = set()

        for access_role in access_roles:
            if access_role.course_id:
                course_keys.add(str(access_role.course_id))
            elif access_role.org:
                orgs.add(access_role.org)

        # Build a filter to fetch all courses:
        # - Courses in the orgs where the user has org-level access
        # - Courses explicitly assigned to the user
        course_filter = Q()
        if orgs:
            course_filter |= Q(org__in=orgs)
        if course_keys:
            course_filter |= Q(id__in=course_keys)

        return (
            CourseOverview.objects.filter(course_filter)
            if course_filter
            else CourseOverview.objects.none()
        )

    def get_queryset(self):
        """Main entry to return courses filtered by authenticated user access and requested user roles."""
        email = self.request.query_params.get("email")
        username = self.request.query_params.get("username")
        user_id = self.request.query_params.get("user_id")

        if not any([email, username, user_id]):
            raise ValidationError(
                {
                    "detail": "Missing required query parameters. "
                    "At least one of 'email', 'username', or 'user_id' is required."
                }
            )

        # Build user lookup query
        user_query = Q(is_active=True)
        if email:
            user_query &= Q(email=email)
        elif username:
            user_query &= Q(username=username)
        elif user_id:
            user_query &= Q(id=user_id)

        try:
            user = User.objects.get(user_query)
        except ObjectDoesNotExist as exc:
            identifier = email or username or user_id
            raise NotFound(
                f"User with identifier '{identifier}' not found or not active."
            ) from exc

        self._access_roles = CourseAccessRole.objects.filter(
            user=user, role__in=["staff", "instructor"]
        )
        self._course_role_map = self.get_course_role_map_for_user(user)

        auth_user = self.request.user
        return self.get_accessible_courses_for_user(auth_user)

    def list(self, request, *args, **kwargs):
        """list of courses for the organization and user."""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    from drf_yasg import openapi
    from drf_yasg.utils import swagger_auto_schema

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "email",
                openapi.IN_QUERY,
                description="User's email address",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "username",
                openapi.IN_QUERY,
                description="User's username",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "user_id",
                openapi.IN_QUERY,
                description="User's ID",
                type=openapi.TYPE_INTEGER,
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        """
        Retrieve a list of courses accessible by the authenticated user

        **Use Case**

            GET API to retrieve a list of courses accessible by the authenticated user,
            along with the specified user's role ("instructor", "staff", or null) in each course.

        **Endpoint**

            GET /api/support/v1/manage_course_team/

        **Query Parameters**

            At least one of the following parameters is required:
            - email: User's email address
            - username: User's username
            - user_id: User's ID

        **Returns**

            List of courses accessible to the authenticated user, each annotated with the
            specified user's role in that course. Each course includes organizational
            information and identifiers.

        **Example Response**

        ```json
            {
                "results": [
                    {
                        "course_id": "course-v1:edX+DemoX+2025_T1",
                        "course_name": "edX Demonstration Course",
                        "course_url": "https://studio.example.com/course/course-v1:edX+DemoX+2025_T1",
                        "role": "instructor",
                        "status": "active",
                        "org": "edX",
                        "run": "2025_T1",
                        "number": "DemoX"
                    },
                    {
                        "course_id": "course-v1:MITx+6.00x+2024_Fall",
                        "course_name": "Introduction to Computer Science",
                        "course_url": "https://studio.example.com/course/course-v1:MITx+6.00x+2024_Fall",
                        "role": "staff",
                        "status": "archived",
                        "org": "MITx",
                        "run": "2024_Fall",
                        "number": "6.00x"
                    }
                ]
            }
        ```
        """
        return self.list(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        """
        Bulk assign or revoke course team roles for a user across multiple courses.

        **Endpoint**

            PUT /api/support/v1/manage_course_team/

        **Permissions**

            - Admin/Staff users: Can manage roles for any course
            - Instructor users: Can only manage roles for courses/orgs they have instructor access to
            - Other users: Access denied

        **Request Data**

            A JSON object containing:
                - email: User's email address
                - bulk_role_operations: List of role operations, each containing:
                    - course_id: Course key string
                    - role: Role to assign or revoke ("instructor" or "staff")
                    - action: "assign" or "revoke"

        **Example Request**

        ```json
        {
            "email": "user1@example.com",
            "bulk_role_operations": [
                {
                    "course_id": "course-v1:edX+DemoX+2025_T1",
                    "role": "instructor",
                    "action": "assign"
                },
                {
                    "course_id": "course-v1:edX+DemoX+2025_T2",
                    "role": "staff",
                    "action": "revoke"
                }
            ]
        }
        ```

        **Returns**

            - HTTP 200 with results for each operation (success or error details)
            - HTTP 400 if the request data is invalid

        **Example Response**

        ```json
        {
            "email": "user1@example.com",
            "results": [
                {
                    "course_id": "course-v1:edX+DemoX+2025_T1",
                    "role": "instructor",
                    "action": "assign",
                    "status": "success"
                },
                {
                    "course_id": "course-v1:edX+DemoX+2025_T2",
                    "role": "staff",
                    "action": "revoke",
                    "status": "failed",
                    "error": "error_message"
                }
            ]
        }
        ```
        """
        results = []

        # Validate request data structure and extract fields
        if not isinstance(request.data, dict):
            return self._error_response(
                "",
                "Request data must be a JSON object with 'email' and 'bulk_role_operations' fields.",
            )

        email = request.data.get("email")
        bulk_role_operations = request.data.get("bulk_role_operations", [])

        # Combined validation for email and bulk_role_operations
        if not email:
            return self._error_response(
                "", "Missing required field: 'email' is required."
            )

        if not isinstance(bulk_role_operations, list) or not bulk_role_operations:
            return self._error_response(
                email,
                "Missing or empty 'bulk_role_operations' field. Must be a non-empty list.",
            )

        auth_user = request.user

        # Get accessible orgs/courses and user validation
        accessible_orgs, accessible_courses, user, validation_error = (
            self._validate_permissions_and_user(auth_user, email)
        )
        if validation_error:
            return validation_error

        course_key_cache = {}

        for operation_data in bulk_role_operations:
            result = self._handle_role_assignment_entry(
                operation_data,
                auth_user,
                accessible_orgs,
                accessible_courses,
                user,
                course_key_cache,
            )
            results.append(result)

        return Response({"email": email, "results": results}, status=status.HTTP_200_OK)

    def _fetch_user_accessible_orgs_and_courses(self, auth_user):
        """Return (orgs, courses, error_response) based on user permissions."""
        accessible_orgs = set()
        accessible_courses = set()
        permission_error = None

        # Admins and staff have full access by default
        if not (auth_user.is_superuser or auth_user.is_staff):
            roles = CourseAccessRole.objects.filter(
                user=auth_user, role="instructor"
            ).only("org", "course_id")

            if roles.exists():
                for role in roles:
                    if role.course_id:
                        accessible_courses.add(str(role.course_id))
                    elif role.org:
                        # Lowercase course org for case-insensitive compare
                        accessible_orgs.add(role.org.lower())
            else:
                permission_error = Response(
                    {
                        "results": [
                            {
                                "status": "failed",
                                "error": "You do not have permission to perform bulk role operations.",
                            }
                        ]
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        return accessible_orgs, accessible_courses, permission_error

    def _error_response(
        self, email, error_message, status_code=status.HTTP_400_BAD_REQUEST
    ):
        """Helper method to create standardized error responses."""
        return Response(
            {
                "email": email,
                "results": [
                    {
                        "status": "failed",
                        "error": error_message,
                    }
                ],
            },
            status=status_code,
        )

    def _validate_permissions_and_user(self, auth_user, email):
        """Combined validation for user permissions and user lookup."""
        error_response = None
        user = None

        # Get accessible orgs/courses
        accessible_orgs, accessible_courses, permission_error = (
            self._fetch_user_accessible_orgs_and_courses(auth_user)
        )

        if permission_error:
            error_response = self._error_response(
                email,
                "You do not have permission to perform bulk role operations.",
                status.HTTP_403_FORBIDDEN,
            )
            return accessible_orgs, accessible_courses, user, error_response

        # Get and validate user
        user = self._get_user(email, {})
        if not user:
            error_response = self._error_response(
                email, "User not found.", status.HTTP_404_NOT_FOUND
            )
        elif not user.is_active:
            error_response = self._error_response(email, "User is not active.")

        return accessible_orgs, accessible_courses, user, error_response

    def _handle_role_assignment_entry(
        self,
        data,
        auth_user,
        accessible_orgs,
        accessible_courses,
        user,
        course_key_cache,
    ):
        """Validate and perform the action for a single operation entry."""

        course_id = data.get("course_id")
        role = data.get("role")
        action = data.get("action")

        # Validate required fields
        if not all([course_id, role, action]):
            return self._make_result(
                data,
                outcome="failed",
                error="Missing required fields: 'course_id', 'role', and 'action' are all required.",
            )

        # Validate role field
        if role not in {"instructor", "staff"}:
            return self._make_result(
                data, "failed", "Invalid role. Must be 'instructor' or 'staff'."
            )

        # Validate action field
        if action not in {"assign", "revoke"}:
            return self._make_result(
                data, "failed", "Invalid action. Must be 'assign' or 'revoke'."
            )

        # Validate course
        course_key = self._validate_course(course_id, course_key_cache)
        if not course_key:
            return self._make_result(
                data, "failed", "Invalid course_id or course not found."
            )

        # Ensure only admin/staff or authorized instructors can manage roles for this course/org
        if not (auth_user.is_staff or auth_user.is_superuser) and (
            course_id not in accessible_courses
            # Lowercase course org for case-insensitive compare
            and course_key.org.lower() not in accessible_orgs
        ):
            return self._make_result(
                data,
                "failed",
                f"You do not have instructor access to course '{course_id}' or org '{course_key.org}'.",
            )

        # Perform role update based on action
        return self._perform_role_action(data, role, action, user, course_key)

    def _validate_course(self, course_id, cache):
        """
        Validate that course_id is well-formed and course exists.
        Caches results to minimize DB queries.
        """
        if course_id in cache:
            return cache[course_id]

        try:
            course_key = CourseKey.from_string(course_id)
            if not CourseOverview.course_exists(course_key):
                course_key = None
        except Exception as exc:  # pylint: disable=broad-except  # noqa: F841
            course_key = None

        cache[course_id] = course_key
        return course_key

    def _get_user(self, email, cache):
        """Fetch or get cached user by email."""
        if email in cache:
            return cache[email]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = None

        cache[email] = user
        return user

    def _perform_role_action(self, data, role, action, user, course_key):
        """Assign or revoke role for the user on the course."""
        try:
            if action == "assign":
                self._assign_role(user, role, course_key)
            elif action == "revoke":
                self._revoke_role(user, role, course_key)
            else:
                return self._make_result(
                    data, "failed", "Invalid action. Use 'assign' or 'revoke'."
                )
            # Clear user roles cache
            if hasattr(user, '_roles'):
                del user._roles
            return self._make_result(data, "success")
        except Exception as exc:  # pylint: disable=broad-except
            return self._make_result(data, "failed", str(exc))

    def _assign_role(self, user, role, course_key):
        """
        Assign role with single entry.
        """
        existing_role = CourseAccessRole.objects.filter(
            user=user,
            course_id=course_key,
            org=course_key.org,
            role__in=["staff", "instructor"]
        ).first()

        if existing_role:
            existing_role.role = role
            existing_role.save()
        else:
            CourseAccessRole.objects.create(
                user=user,
                role=role,
                course_id=course_key,
                org=course_key.org,
            )
        CourseEnrollment.enroll(user, course_key)

    def _revoke_role(self, user, role, course_key):
        """
        Revoke role with cleanup - instructor revoke removes all access.
        """
        existing_roles = CourseAccessRole.objects.filter(
            user=user, course_id=course_key, role__in=["staff", "instructor"]
        )
        existing_roles.delete()

    def _make_result(self, data, outcome, error=None):
        """
        Formats the response for each role assignment entry.
        """
        result = {
            "course_id": data.get("course_id"),
            "role": data.get("role"),
            "action": data.get("action"),
            "status": outcome,
        }
        if error:
            result["error"] = error
        return result
