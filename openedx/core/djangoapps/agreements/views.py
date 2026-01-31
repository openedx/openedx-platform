"""
Views served by the Agreements app
"""

import edx_api_doc_tools as apidocs
from django import forms
from django.conf import settings
from drf_yasg import openapi
from opaque_keys.edx.keys import CourseKey
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student import auth
from common.djangoapps.student.roles import CourseStaffRole

from .api import (
    create_integrity_signature,
    create_lti_pii_signature,
    create_user_agreement_record,
    get_integrity_signature,
    get_latest_user_agreement_record
)
from .models import UserAgreement
from .serializers import IntegritySignatureSerializer, LTIPIISignatureSerializer, UserAgreementRecordSerializer, \
    UserAgreementSerializer
from ...lib.api.view_utils import view_auth_classes


def is_user_course_or_global_staff(user, course_id):
    """
    Return whether a user is course staff for a given course, described by the course_id,
    or is global staff.
    """

    return user.is_staff or auth.user_has_role(user, CourseStaffRole(CourseKey.from_string(course_id)))


class AuthenticatedAPIView(APIView):
    """
    Authenticated API View.
    """
    permission_classes = (IsAuthenticated,)


class IntegritySignatureView(AuthenticatedAPIView):
    """
    Endpoint for an Integrity Signature
    /integrity_signature/{course_id}

    Supports:
        HTTP GET: Returns an existing signed integrity agreement (by course id and user)

    HTTP GET
        ** Scenarios **
        ?username=xyz
        returns an existing signed integrity agreement for the given user and course

    HTTP POST
        * If an integrity signature does not exist for the user + course, creates one and
          returns it. If one does exist, returns the existing signature.
    """

    def get(self, request, course_id):
        """
        In order to check whether the user has signed the integrity agreement for a given course.

        Should return the following:
            username (str)
            course_id (str)
            created_at (str)

        If a username is not given, it should default to the requesting user (or masqueraded user).
        Only staff should be able to access this endpoint for other users.
        """
        if not settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE'):
            return Response(
                status=status.HTTP_404_NOT_FOUND,
            )

        # check that user can make request
        user = request.user.username
        requested_user = request.GET.get('username')
        is_staff = is_user_course_or_global_staff(request.user, course_id)

        if not is_staff and requested_user and (user != requested_user):
            return Response(
                status=status.HTTP_403_FORBIDDEN,
                data={
                    "message": "User does not have permission to view integrity agreement."
                }
            )

        username = requested_user if requested_user else user
        signature = get_integrity_signature(username, course_id)

        if signature is None:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = IntegritySignatureSerializer(signature)
        return Response(serializer.data)

    def post(self, request, course_id):
        """
        Create an integrity signature for the requesting user and course. If a signature
        already exists, returns the existing signature instead of creating a new one.

        /api/agreements/v1/integrity_signature/{course_id}

        Example response:
            {
                username: "janedoe",
                course_id: "org.2/course_2/Run_2",
                created_at: "2021-04-23T18:25:43.511Z"
            }
        """
        if not settings.FEATURES.get('ENABLE_INTEGRITY_SIGNATURE'):
            return Response(
                status=status.HTTP_404_NOT_FOUND,
            )

        username = request.user.username
        signature = create_integrity_signature(username, course_id)
        serializer = IntegritySignatureSerializer(signature)
        return Response(serializer.data)


class LTIPIISignatureView(AuthenticatedAPIView):
    """
    Endpoint for a LTI PII Signature
    /lti_pii_signature/{course_id}

    HTTP POST
        * If an LTI PII signature does not exist for the user + course, creates one and
          returns it. If one does exist, returns the existing signature.
    """

    def post(self, request, course_id):
        """
        Create an LTI PII signature for the requesting user and course. If a signature
        already exists, returns the existing signature instead of creating a new one.

        /api/agreements/v1/lti_pii_signature/{course_id}

        Example response:
            {
                username: "janedoe",
                course_id: "org.2/course_2/Run_2",
                created_at: "2021-04-23T18:25:43.511Z"
            }
        """
        if not settings.FEATURES.get('ENABLE_LTI_PII_ACKNOWLEDGEMENT'):
            return Response(
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = LTIPIISignatureSerializer(data=request.data)
        statusStr = ""
        if serializer.is_valid():
            username = request.user.username
            lti_tools = request.data.get("lti_tools")
            signature = create_lti_pii_signature(username, course_id, lti_tools)
            serializer = LTIPIISignatureSerializer(signature)
            statusStr = status.HTTP_200_OK
        else:
            statusStr = status.HTTP_500_INTERNAL_SERVER_ERROR
        return Response(data=serializer.data, status=statusStr)


@view_auth_classes(is_authenticated=True)
class UserAgreementRecordsView(APIView):
    """
    Endpoint for the user agreement records API.
    """

    class QueryFilterForm(forms.Form):
        """
        Query parameters for the GET method.
        """
        after = forms.DateTimeField(required=False)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'agreement_type',
                apidocs.ParameterLocation.PATH,
                description="Agreement ID/Type",
            ),
            openapi.Parameter(
                'after',
                apidocs.ParameterLocation.QUERY,
                required=False,
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATETIME,
                description="Return records after this date/time",
            ),
        ],
        responses={
            200: UserAgreementRecordSerializer,
            400: "Bad Request",
            404: "Not Found",
        },
    )
    def get(self, request, agreement_type):
        """
        Get a user's acknowledgement record for this agreement type.
        """
        params = UserAgreementRecordsView.QueryFilterForm(request.query_params)
        if not params.is_valid():
            return Response(status=status.HTTP_400_BAD_REQUEST)
        record = get_latest_user_agreement_record(request.user, agreement_type, params.cleaned_data.get('after'))
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UserAgreementRecordSerializer(record)
        return Response(serializer.data)

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'agreement_type',
                apidocs.ParameterLocation.PATH,
                description="Agreement ID/Type",
            ),
        ],
        responses={
            200: UserAgreementRecordSerializer,
            400: "Bad Request",
        },
    )
    def post(self, request, agreement_type):
        """
        Marks a user's acknowledgement of this agreement type.
        """
        record = create_user_agreement_record(request.user, agreement_type)
        serializer = UserAgreementRecordSerializer(record)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@view_auth_classes(is_authenticated=True)
class UserAgreementsViewSet(viewsets.GenericViewSet):
    """
    Endpoint for the user agreements API.
    """

    @apidocs.schema(
        parameters=[
            apidocs.string_parameter(
                'agreement_type',
                apidocs.ParameterLocation.PATH,
                description="Agreement ID/Type",
            ),
        ],
        responses={
            200: UserAgreementSerializer,
            400: "Bad Request",
            404: "Not Found",
        },
    )
    def get(self, request, agreement_type):
        """
        Get all user agreements for this agreement type.
        """
        agreement = UserAgreement.objects.get(type=agreement_type)
        # if agreement is None:
        #     return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UserAgreementSerializer(agreement)
        return Response(serializer.data)

    @apidocs.schema(
        parameters=[
            openapi.Parameter(
                'agreement_type',
                apidocs.ParameterLocation.QUERY,
                required=False,
                type=openapi.TYPE_ARRAY,
                items=openapi.Items(type=openapi.TYPE_STRING),
                description="Agreement ID/Type",
            ),
        ],
        responses={
            200: UserAgreementSerializer,
            400: "Bad Request",
            404: "Not Found",
        },
    )
    def list(self, request):
        """
        Get all user agreements for this agreement type.
        """
        types = request.query_params.getlist('agreement_type', None)
        agreements = UserAgreement.objects.all()
        if types:
            agreements = agreements.filter(type__in=types)
        # if agreement is None:
        #     return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UserAgreementSerializer(agreements, many=True)
        return Response(serializer.data)
