"""
Views for the credit Django app.
"""


import datetime
import logging

from zoneinfo import ZoneInfo
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from edx_rest_framework_extensions.auth.jwt.authentication import JwtAuthentication
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from rest_framework import generics, mixins, permissions, views, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from openedx.core.djangoapps.credit.api import create_credit_request
from openedx.core.djangoapps.credit.exceptions import (
    CreditApiBadRequest,
    InvalidCourseKey,
    InvalidCreditRequest,
    UserNotEligibleException
)
from openedx.core.djangoapps.credit.models import (
    CREDIT_PROVIDER_ID_REGEX,
    CreditCourse,
    CreditEligibility,
    CreditProvider,
    CreditRequest
)
from openedx.core.djangoapps.credit.serializers import (
    CreditCourseSerializer,
    CreditEligibilitySerializer,
    CreditProviderCallbackSerializer,
    CreditProviderSerializer
)
from openedx.core.lib.api.authentication import BearerAuthentication
from openedx.core.lib.api.mixins import PutAsCreateMixin
from openedx.core.lib.api.permissions import IsStaffOrOwner

log = logging.getLogger(__name__)
AUTHENTICATION_CLASSES = (JwtAuthentication, BearerAuthentication, SessionAuthentication,)


class CreditProviderViewSet(viewsets.ReadOnlyModelViewSet):
    """ Credit provider endpoints. """

    lookup_field = 'provider_id'
    lookup_value_regex = CREDIT_PROVIDER_ID_REGEX
    authentication_classes = AUTHENTICATION_CLASSES
    pagination_class = None
    permission_classes = (permissions.IsAuthenticated,)
    queryset = CreditProvider.objects.all()
    serializer_class = CreditProviderSerializer

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)

        # Filter by provider ID
        provider_ids = self.request.GET.get('provider_ids', None)

        if provider_ids:
            provider_ids = provider_ids.split(',')
            queryset = queryset.filter(provider_id__in=provider_ids)

        return queryset


class CreditProviderRequestCreateView(views.APIView):
    """ Creates a credit request for the given user and course, if the user is eligible for credit."""

    authentication_classes = AUTHENTICATION_CLASSES
    permission_classes = (permissions.IsAuthenticated, IsStaffOrOwner,)

    def post(self, request, provider_id):
        """ POST handler. """
        # Get the provider, or return HTTP 404 if it doesn't exist
        provider = generics.get_object_or_404(CreditProvider, provider_id=provider_id)

        # Validate the course key
        course_key = request.data.get('course_key')
        try:
            course_key = CourseKey.from_string(course_key)
        except InvalidKeyError:
            raise InvalidCourseKey(course_key)  # lint-amnesty, pylint: disable=raise-missing-from

        # Validate the username
        username = request.data.get('username')
        if not username:
            raise ValidationError({'detail': 'A username must be specified.'})

        # Ensure the user is actually eligible to receive credit
        if not CreditEligibility.is_user_eligible_for_credit(course_key, username):
            raise UserNotEligibleException(course_key, username)

        try:
            credit_request = create_credit_request(course_key, provider.provider_id, username)
            return Response(credit_request)
        except CreditApiBadRequest as ex:
            raise InvalidCreditRequest(str(ex))  # lint-amnesty, pylint: disable=raise-missing-from


class CreditProviderCallbackView(views.APIView):
    """ Callback used by credit providers to update credit request status. """

    # This endpoint should be open to all external credit providers.
    authentication_classes = ()
    permission_classes = ()

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, provider_id):
        """ POST handler. """
        provider = generics.get_object_or_404(CreditProvider, provider_id=provider_id)
        data = request.data

        # Ensure the input data is valid
        serializer = CreditProviderCallbackSerializer(data=data, provider=provider)
        serializer.is_valid(raise_exception=True)

        # Update the credit request status
        request_uuid = data['request_uuid']
        new_status = data['status']
        credit_request = generics.get_object_or_404(CreditRequest, uuid=request_uuid, provider=provider)
        old_status = credit_request.status
        credit_request.status = new_status
        credit_request.save()

        log.info(
            'Updated [%s] CreditRequest [%s] from status [%s] to [%s].',
            provider_id, request_uuid, old_status, new_status
        )

        return Response()


class CreditEligibilityView(generics.ListAPIView):
    """ Returns eligibility for a user-course combination. """

    authentication_classes = AUTHENTICATION_CLASSES
    pagination_class = None
    permission_classes = (permissions.IsAuthenticated, IsStaffOrOwner)
    serializer_class = CreditEligibilitySerializer
    queryset = CreditEligibility.objects.all()

    def filter_queryset(self, queryset):
        username = self.request.GET.get('username')
        course_key = self.request.GET.get('course_key')

        if not (username and course_key):
            raise ValidationError(
                {'detail': 'Both the course_key and username querystring parameters must be supplied.'})

        course_key = str(course_key)

        try:
            course_key = CourseKey.from_string(course_key)
        except InvalidKeyError:
            raise ValidationError({'detail': f'[{course_key}] is not a valid course key.'})  # lint-amnesty, pylint: disable=raise-missing-from
        return queryset.filter(
            username=username,
            course__course_key=course_key,
            deadline__gt=datetime.datetime.now(ZoneInfo("UTC"))
        )


class CreditCourseViewSet(PutAsCreateMixin, mixins.UpdateModelMixin, viewsets.ReadOnlyModelViewSet):
    """ CreditCourse endpoints. """

    lookup_field = 'course_key'
    lookup_value_regex = settings.COURSE_KEY_REGEX
    queryset = CreditCourse.objects.all()
    serializer_class = CreditCourseSerializer
    authentication_classes = AUTHENTICATION_CLASSES
    permission_classes = (permissions.IsAuthenticated, permissions.IsAdminUser)

    # In Django Rest Framework v3, there is a default pagination
    # class that transmutes the response data into a dictionary
    # with pagination information.  The original response data (a list)
    # is stored in a "results" value of the dictionary.
    # For backwards compatibility with the existing API, we disable
    # the default behavior by setting the pagination_class to None.
    pagination_class = None

    # This CSRF exemption only applies when authenticating without SessionAuthentication.
    # SessionAuthentication will enforce CSRF protection.
    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        # Convert the serialized course key into a CourseKey instance
        # so we can look up the object.
        course_key = self.kwargs.get(self.lookup_field)
        if course_key is not None:
            self.kwargs[self.lookup_field] = CourseKey.from_string(course_key)

        return super().get_object()
