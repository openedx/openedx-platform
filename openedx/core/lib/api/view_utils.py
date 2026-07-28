"""
Utilities related to API views
"""

from collections.abc import Sequence
from functools import wraps

from django.core.exceptions import NON_FIELD_ERRORS, ObjectDoesNotExist, ValidationError
from django.http import Http404, HttpResponseBadRequest
from django.utils.translation import gettext as _
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from edx_rest_framework_extensions.auth.session.authentication import SessionAuthenticationAllowInactiveUser
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.generics import GenericAPIView
from rest_framework.mixins import RetrieveModelMixin, UpdateModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import clone_request
from rest_framework.response import Response
from rest_framework.views import APIView

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.user_api.accounts import BIO_MAX_LENGTH
from openedx.core.lib.api.authentication import BearerAuthenticationAllowInactiveUser
from openedx.core.lib.api.permissions import IsUserInUrl


class DeveloperErrorResponseException(Exception):
    """
    An exception class that wraps a DRF Response object so that
    it does not need to be recreated when returning a response.
    Intended to be used with and by DeveloperErrorViewMixin.
    """

    def __init__(self, response):
        super().__init__()
        self.response = response


class DeveloperErrorViewMixin:
    """
    A view mixin to handle common error cases other than validation failure
    (auth failure, method not allowed, etc.) by generating an error response
    conforming to our API conventions with a developer message.
    """
    @classmethod
    def api_error(cls, status_code, developer_message, error_code=None):
        response = cls._make_error_response(status_code, developer_message, error_code)
        return DeveloperErrorResponseException(response)

    @classmethod
    def _make_error_response(cls, status_code, developer_message, error_code=None):
        """
        Build an error response with the given status code and developer_message
        """
        error_data = {"developer_message": developer_message}
        if error_code is not None:
            error_data['error_code'] = error_code
        return Response(error_data, status=status_code)

    @classmethod
    def _make_validation_error_response(cls, validation_error):
        """
        Build a 400 error response from the given ValidationError
        """
        if hasattr(validation_error, "message_dict"):
            response_obj = {}
            message_dict = dict(validation_error.message_dict)
            # Extract both Django form and DRF serializer non-field errors
            non_field_error_list = (
                message_dict.pop(NON_FIELD_ERRORS, []) +
                message_dict.pop("non_field_errors", [])
            )
            if non_field_error_list:
                response_obj["developer_message"] = non_field_error_list[0]
            if message_dict:
                response_obj["field_errors"] = {
                    field: {"developer_message": message_dict[field][0]}
                    for field in message_dict
                }
            return Response(response_obj, status=400)
        else:
            return cls._make_error_response(400, validation_error.messages[0])

    def handle_exception(self, exc):
        """
        Generalized helper method for managing specific API exception workflows
        """
        if isinstance(exc, DeveloperErrorResponseException):
            return exc.response
        elif isinstance(exc, APIException):
            return self._make_error_response(exc.status_code, exc.detail)
        elif isinstance(exc, (Http404, ObjectDoesNotExist)):
            return self._make_error_response(404, str(exc) or "Not found.")
        elif isinstance(exc, ValidationError):
            return self._make_validation_error_response(exc)
        else:
            raise  # pylint: disable=misplaced-bare-raise


class ExpandableFieldViewMixin:
    """A view mixin to add expansion information to the serializer context for later use by an ExpandableField."""

    def get_serializer_context(self):
        """Adds expand information from query parameters to the serializer context to support expandable fields."""
        result = super().get_serializer_context()
        result['expand'] = [x for x in self.request.query_params.get('expand', '').split(',') if x]
        return result


def view_auth_classes(is_user=False, is_authenticated=True):
    """
    Function and class decorator that abstracts the authentication and permission checks for api views.
    """
    def _decorator(func_or_class):
        """
        Requires either OAuth2 or Session-based authentication.
        If is_user is True, also requires username in URL matches the request user.
        """
        func_or_class.authentication_classes = (
            JwtAuthentication,
            BearerAuthenticationAllowInactiveUser,
            SessionAuthenticationAllowInactiveUser
        )
        func_or_class.permission_classes = ()
        if is_authenticated:
            func_or_class.permission_classes += (IsAuthenticated,)
        if is_user:
            func_or_class.permission_classes += (IsUserInUrl,)
        return func_or_class
    return _decorator


def clean_errors(error):
    """
    DRF error messages are of type ErrorDetail and serialize out as such.
    We want to coerce the strings into the message only.

    This cursively handles the nesting of errors.
    """
    if isinstance(error, ErrorDetail):
        return str(error)
    if isinstance(error, list):
        return [clean_errors(el) for el in error]
    else:
        # We assume that it's a nested dictionary if it's not a list.
        return {key: clean_errors(value) for key, value in error.items()}


def add_serializer_errors(serializer, data, field_errors):
    """Adds errors from serializer validation to field_errors. data is the original data to deserialize."""
    if not serializer.is_valid():
        errors = serializer.errors
        for key, error in errors.items():
            error = clean_errors(error)
            if key == 'bio':
                user_message = _(f"The about me field must be at most {BIO_MAX_LENGTH} characters long.")  # pylint: disable=translation-of-non-string
            else:
                user_message = _("This value is invalid.")

            field_errors[key] = {
                'developer_message': "Value '{field_value}' is not valid for field '{field_name}': {error}".format(
                    field_value=data.get(key, ''), field_name=key, error=error
                ),
                'user_message': user_message,
            }
    return field_errors


def build_api_error(message, **kwargs):
    """Build an error dict corresponding to edX API conventions.

    Args:
        message (string): The string to use for developer and user messages.
            The user message will be translated, but for this to work message
            must have already been scraped. gettext_noop is useful for this.
        **kwargs: format parameters for message
    """
    return {
        'developer_message': message.format(**kwargs),
        'user_message': _(message).format(**kwargs),  # pylint: disable=translation-of-non-string
    }


class RetrievePatchAPIView(RetrieveModelMixin, UpdateModelMixin, GenericAPIView):
    """Concrete view for retrieving and updating a model instance.

    Like DRF's RetrieveUpdateAPIView, but without PUT and with automatic validation errors in the edX format.
    """

    def get(self, request, *args, **kwargs):
        """Retrieves the specified resource using the RetrieveModelMixin."""
        return self.retrieve(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        """Checks for validation errors, then updates the model using the UpdateModelMixin."""
        field_errors = self._validate_patch(request.data)
        if field_errors:
            return Response({'field_errors': field_errors}, status=status.HTTP_400_BAD_REQUEST)
        return self.partial_update(request, *args, **kwargs)

    def _validate_patch(self, patch):
        """Validates a JSON merge patch. Captures DRF serializer errors and converts them to edX's standard format."""
        field_errors = {}
        serializer = self.get_serializer(self.get_object_or_none(), data=patch, partial=True)
        fields = self.get_serializer().get_fields()

        for key in patch:
            if key in fields and fields[key].read_only:
                field_errors[key] = {
                    'developer_message': "This field is not editable",
                    'user_message': _("This field is not editable"),
                }

        add_serializer_errors(serializer, patch, field_errors)

        return field_errors

    def get_object_or_none(self):
        """
        Retrieve an object or return None if the object can't be found.

        NOTE: This replaces functionality that was removed in Django Rest Framework v3.1.
        """
        try:
            return self.get_object()
        except Http404:
            if self.request.method == 'PUT':
                # For PUT-as-create operation, we need to ensure that we have
                # relevant permissions, as if this was a POST request.  This
                # will either raise a PermissionDenied exception, or simply
                # return None.
                self.check_permissions(clone_request(self.request, 'POST'))
            else:
                # PATCH requests where the object does not exist should still
                # return a 404 response.
                raise


class LazySequence(Sequence):
    """
    This class provides an immutable Sequence interface on top of an existing
    iterable.

    It is immutable, and accepts an estimated length in order to support __len__
    without exhausting the underlying sequence
    """

    def __init__(self, iterable, est_len=None):
        self.iterable = iterable
        self.est_len = est_len
        self._data = []
        self._exhausted = False

    def __len__(self):
        # Return the actual data length if we know it exactly (because
        # the underlying sequence is exhausted), or it's greater than
        # the initial estimated length
        if len(self._data) > self.est_len or self._exhausted:
            return len(self._data)
        else:
            return self.est_len

    def __iter__(self):
        # Yield all the known data first
        yield from self._data

        # Capture and yield data from the underlying iterator
        # until it is exhausted
        while True:
            try:
                item = next(self.iterable)
                self._data.append(item)
                yield item
            except StopIteration:
                self._exhausted = True
                return

    def __getitem__(self, index):
        if isinstance(index, int):
            # For a single index, if we haven't already loaded enough
            # data, we can load data until we have enough, and then
            # return the value from the loaded data
            if index < 0:
                raise IndexError("Negative indexes aren't supported")

            while len(self._data) <= index:
                try:
                    self._data.append(next(self.iterable))
                except StopIteration:
                    self._exhausted = True
                    raise IndexError("Underlying sequence exhausted")  # pylint: disable=raise-missing-from  # noqa: B904

            return self._data[index]
        elif isinstance(index, slice):
            # For a slice, we can load data until we reach 'stop'.
            # Once we have data including 'stop', then we can use
            # the underlying list to actually understand the mechanics
            # of the slicing operation.
            if index.start is not None and index.start < 0:
                raise IndexError("Negative indexes aren't supported")
            if index.stop is not None and index.stop < 0:
                raise IndexError("Negative indexes aren't supported")

            if index.step is not None and index.step < 0:
                largest_value = index.start + 1
            else:
                largest_value = index.stop

            if largest_value is not None:
                while len(self._data) <= largest_value:
                    try:
                        self._data.append(next(self.iterable))
                    except StopIteration:
                        self._exhausted = True
                        break
            else:
                self._data.extend(self.iterable)
            return self._data[index]
        else:
            raise TypeError("Unsupported index type")

    def __repr__(self):
        if self._exhausted:
            return "LazySequence({!r}, {!r})".format(  # noqa: UP032
                self._data,
                self.est_len,
            )
        else:
            return "LazySequence(itertools.chain({!r}, {!r}), {!r})".format(  # noqa: UP032
                self._data,
                self.iterable,
                self.est_len,
            )


class PaginatedAPIView(APIView):
    """
    An `APIView` class enhanced with the pagination methods of `GenericAPIView`.
    """
    # pylint: disable=attribute-defined-outside-init
    @property
    def paginator(self):
        """
        The paginator instance associated with the view, or `None`.
        """
        if not hasattr(self, '_paginator'):
            if self.pagination_class is None:  # pylint: disable=no-member
                self._paginator = None
            else:
                self._paginator = self.pagination_class()  # pylint: disable=no-member
        return self._paginator

    def paginate_queryset(self, queryset):
        """
        Return a single page of results, or `None` if pagination is disabled.
        """
        if self.paginator is None:
            return None
        return self.paginator.paginate_queryset(queryset, self.request, view=self)

    def get_paginated_response(self, data, *args, **kwargs):
        """
        Return a paginated style `Response` object for the given output data.
        """
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data, *args, **kwargs)


def require_post_params(required_params):
    """
    View decorator that ensures the required POST params are
    present.  If not, returns an HTTP response with status 400.

    Args:
        required_params (list): The required parameter keys.

    Returns:
        HttpResponse

    """
    def _decorator(func):  # pylint: disable=missing-docstring
        @wraps(func)
        def _wrapped(*args, **_kwargs):
            request = args[0]
            missing_params = set(required_params) - set(request.POST.keys())
            if missing_params:
                msg = "Missing POST parameters: {missing}".format(
                    missing=", ".join(missing_params)
                )
                return HttpResponseBadRequest(msg)
            else:
                return func(request)
        return _wrapped
    return _decorator


def get_course_key(request, course_id=None):
    if not course_id:
        return CourseKey.from_string(request.GET.get('course_id'))
    return CourseKey.from_string(course_id)


def verify_course_exists(missing_course_error_message=None):
    """
    A decorator to wrap a view function that takes `course_key` as a parameter.

    Raises:
        An API error if the `course_key` is invalid, or if no `CourseOverview` exists for the given key.
    """

    if not missing_course_error_message:
        missing_course_error_message = "Unknown course {course}"

    def _verify_course_exists(view_func):
        @wraps(view_func)
        def wrapped_function(self, request, **kwargs):
            """
            Wraps the given view_function.
            """
            try:
                course_key = get_course_key(request, kwargs.get('course_id'))
            except InvalidKeyError as error:
                raise self.api_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    developer_message='The provided course key cannot be parsed.',
                    error_code='invalid_course_key'
                ) from error
            if not CourseOverview.course_exists(course_key):
                raise self.api_error(
                    status_code=status.HTTP_404_NOT_FOUND,
                    developer_message=missing_course_error_message.format(course=str(course_key)),
                    error_code='course_does_not_exist'
                )

            return view_func(self, request, **kwargs)
        return wrapped_function
    return _verify_course_exists


def validate_course_key(course_key_string: str) -> CourseKey:
    """
    Validate and parse a course_key string, if supported.

    Args:
        course_key_string (str): string course key to validate

    Returns:
        CourseKey: validated course key

    Raises:
        ValidationError: DRF Validation error in case the course key is invalid
    """
    try:
        course_key = CourseKey.from_string(course_key_string)
    except InvalidKeyError as error:
        raise serializers.ValidationError(f"{course_key_string} is not a valid CourseKey") from error
    if course_key.deprecated:
        raise serializers.ValidationError("Deprecated CourseKeys (Org/Course/Run) are not supported.")
    return course_key
