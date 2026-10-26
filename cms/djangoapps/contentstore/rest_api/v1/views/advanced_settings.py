"""API views for a course's advanced settings."""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from edx_rest_framework_extensions.mixins import StandardizedErrorMixin
from edx_rest_framework_extensions.shaping import project
from edx_rest_framework_extensions.url_converters import CourseKeyConverter
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from cms.djangoapps.contentstore.rest_api.v1.advanced_settings_service import (
    INVALID_SETTING_MESSAGE,
    get_advanced_settings,
    settings_authors_do_not_edit,
    update_advanced_settings,
)
from cms.djangoapps.contentstore.rest_api.v1.serializers.advanced_settings import (
    FULL_VIEW,
    MINIMAL_VIEW,
    AdvancedSettingsQuerySerializer,
    CourseAdvancedSettingsSerializer,
    CourseAdvancedSettingsUpdateSerializer,
)
from cms.djangoapps.contentstore.views.permissions import (
    CanManageAdvancedSettings,
    CanViewAdvancedSettings,
    IsExistingCourse,
)

_COURSE_KEY_PARAMETER = OpenApiParameter(
    name="course_key",
    description="Key of the course, such as course-v1:edX+DemoX+2026.",
    location=OpenApiParameter.PATH,
    type=str,
    pattern=f"^{CourseKeyConverter.regex}$",
)


def _query_parameter(name, **kwargs):
    return OpenApiParameter(
        name=name,
        description=AdvancedSettingsQuerySerializer().fields[name].help_text,
        required=False,
        location=OpenApiParameter.QUERY,
        **kwargs,
    )


def _error(description):
    return OpenApiResponse(response=ErrorResponseSerializer, description=description)


_SETTINGS_EXAMPLE = OpenApiExample(
    "Advanced settings",
    summary="Two of a course's settings",
    response_only=True,
    status_codes=["200"],
    value={
        "display_name": {
            "value": "Demonstration Course",
            "display_name": "Course Display Name",
            "help": "Enter the name of the course as it should appear in the course list.",
            "deprecated": False,
            "hide_on_enabled_publisher": False,
        },
        "days_early_for_beta": {
            "value": None,
            "display_name": "Days Early for Beta Users",
            "help": "Enter the number of days before the start date that beta users can access the course.",
            "deprecated": False,
            "hide_on_enabled_publisher": False,
        },
    },
)

_UPDATE_EXAMPLE = OpenApiExample(
    "New values",
    summary="Turn on the calculator and set the advanced modules",
    request_only=True,
    value={
        "show_calculator": {"value": True},
        "advanced_modules": {"value": ["poll", "survey"]},
    },
)


@extend_schema(tags=["openedx-platform-sdk"])
class CourseAdvancedSettingsViewSet(StandardizedErrorMixin, viewsets.ViewSet):
    """
    The advanced settings of one course: its policy settings keyed by name.

    By default (``view=minimal``) the settings course authors edit in Studio
    are returned; with ``view=full`` every setting of the course is. A partial
    update changes the values of the settings it names, all of them or none.

    Anyone who may view the course's advanced settings may read them; changing
    them needs the permission to manage them. The course is looked up first,
    so a signed-in caller learns that a course does not exist (404) before
    being refused (403).

    A plain ``ViewSet``: the settings are fields of the course block in the
    modulestore, not database rows.
    """

    # An inactive account's browser session stays authenticated, so the course permission decides: staff status
    # admits it, and its roles count only on courses where policy-based permissions are on.
    authentication_classes = (JwtAuthentication, SessionAuthenticationAllowInactiveUser)
    permission_classes = (IsAuthenticated, IsExistingCourse)
    action_permission_classes = {
        "retrieve": (CanViewAdvancedSettings,),
        "partial_update": (CanManageAdvancedSettings,),
    }
    # A method with no route (answered 405) and OPTIONS serve no settings, so
    # only the course is checked for them.
    unrouted_actions = (None, "metadata")
    serializer_class = CourseAdvancedSettingsSerializer

    def get_permissions(self):
        """
        Check the course, then the permission the requested action needs.

        An action with no permission declared in ``action_permission_classes``
        is refused with a server error instead of being served unchecked.
        """
        if self.action in self.unrouted_actions:
            return super().get_permissions()
        if self.action not in self.action_permission_classes:
            raise ImproperlyConfigured(f"No permission is declared for the {self.action!r} action.")
        specific = self.action_permission_classes[self.action]
        return [*super().get_permissions(), *(permission() for permission in specific)]

    def get_serializer(self, *args, **kwargs):
        """Instantiate the settings serializer declared on this viewset."""
        return self.serializer_class(*args, **kwargs)

    @extend_schema(
        summary="Get a course's advanced settings",
        description=(
            "Returns the course's advanced settings keyed by setting name, each with its value, its name "
            "as Studio shows it, its help text and whether it is deprecated. By default (view=minimal) the "
            "settings course authors edit in Studio are returned; view=full returns every setting of the "
            "course. fields narrows the response to the named settings."
        ),
        parameters=[
            _COURSE_KEY_PARAMETER,
            _query_parameter("view", type=str, enum=[MINIMAL_VIEW, FULL_VIEW], default=MINIMAL_VIEW),
            _query_parameter("fields", type=str),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseAdvancedSettingsSerializer,
                description="The course's advanced settings.",
            ),
            400: _error("A query parameter has an invalid value."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not view this course's advanced settings."),
            404: _error("The course does not exist."),
        },
        examples=[_SETTINGS_EXAMPLE],
    )
    def retrieve(self, request, course_key):
        """Return the course's advanced settings, narrowed to ``fields`` when given."""
        query = AdvancedSettingsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        current = get_advanced_settings(course_key, full=params["view"] == FULL_VIEW)
        return Response(project(self.get_serializer(current).data, params.get("fields")))

    @extend_schema(
        summary="Change some of a course's advanced settings",
        description=(
            "Sets the value of each setting named in the body. Every value is validated before any is "
            "stored: when one is refused nothing is changed, and the error's errors member maps the name "
            f"of each refused setting to the message '{INVALID_SETTING_MESSAGE}'. Settings that authors do "
            "not edit are ignored, and so are settings the course does not have. Returns the settings "
            "course authors edit in Studio, as stored."
        ),
        parameters=[_COURSE_KEY_PARAMETER],
        request={"application/json": CourseAdvancedSettingsUpdateSerializer},
        responses={
            200: OpenApiResponse(
                response=CourseAdvancedSettingsSerializer,
                description="The settings course authors edit in Studio, after the change.",
            ),
            400: _error("The body is not an object of settings, or a value was refused."),
            401: _error("The requester is not authenticated."),
            403: _error("The requester may not change this course's advanced settings."),
            404: _error("The course does not exist."),
        },
        examples=[_SETTINGS_EXAMPLE, _UPDATE_EXAMPLE],
    )
    def partial_update(self, request, course_key):
        """Store the new values and return the editable settings as stored."""
        changes = CourseAdvancedSettingsUpdateSerializer(
            data=request.data, context={"ignored_settings": settings_authors_do_not_edit(course_key)},
        )
        changes.is_valid(raise_exception=True)
        stored = update_advanced_settings(course_key, changes.validated_data, request.user)
        return Response(self.get_serializer(stored).data)
