"""
Tests for the Third Party Auth permissions
"""

import ddt
from django.test import RequestFactory, TestCase
from edx_rest_framework_extensions.auth.jwt.tests.utils import generate_jwt
from rest_framework.response import Response
from rest_framework.views import APIView

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.api.permissions import TPA_PERMISSIONS
from openedx.core.djangolib.testing.utils import skip_unless_lms

IDP_SLUG_TESTSHIB = 'testshib'
PROVIDER_ID_TESTSHIB = 'saml-' + IDP_SLUG_TESTSHIB


@ddt.ddt
@skip_unless_lms
class ThirdPartyAuthPermissionTest(TestCase):
    """ Tests for third party auth TPA_PERMISSIONS """

    class SomeTpaClassView(APIView):
        """view used to test TPA_permissions"""
        permission_classes = (TPA_PERMISSIONS,)
        required_scopes = ['tpa:read']

        def get(self, request, provider_id=None):  # pylint: disable=unused-argument
            return Response(data="Success")

    def _create_user(self, is_superuser=False, is_staff=False):
        return UserFactory(username='this_user', is_superuser=is_superuser, is_staff=is_staff)

    def _create_request(self, auth_header=None):
        url = '/'
        extra = dict(HTTP_AUTHORIZATION=auth_header) if auth_header else {}
        return RequestFactory().get(url, **extra)

    def _create_session(self, request, user):
        request.user = user

    def _create_jwt_header(self, user, is_restricted=False, scopes=None, filters=None):
        token = generate_jwt(user, is_restricted=is_restricted, scopes=scopes, filters=filters)
        return f"JWT {token}"

    def test_anonymous_fails(self):
        request = self._create_request()
        response = self.SomeTpaClassView().dispatch(request)
        assert response.status_code == 401

    @ddt.data(
        (True, False, 200),
        (False, True, 200),
        (False, False, 403),
    )
    @ddt.unpack
    def test_session_with_user_permission(self, is_superuser, is_staff, expected_status_code):
        user = self._create_user(is_superuser=is_superuser, is_staff=is_staff)
        request = self._create_request()
        self._create_session(request, user)

        response = self.SomeTpaClassView().dispatch(request)
        assert response.status_code == expected_status_code

    @ddt.data(
        # unrestricted (for example, jwt cookies)
        dict(
            is_restricted=False,
            expected_response=403,
        ),

        # restricted (note: further test cases for scopes and filters are in tests below)
        dict(
            is_restricted=True,
            expected_response=403,
        ),
    )
    @ddt.unpack
    def test_jwt_without_scopes_and_filters(
            self,
            is_restricted,
            expected_response,
    ):
        user = self._create_user()

        auth_header = self._create_jwt_header(user, is_restricted=is_restricted)
        request = self._create_request(
            auth_header=auth_header,
        )

        response = self.SomeTpaClassView().dispatch(request)
        assert response.status_code == expected_response

    @ddt.data(
        # valid scopes
        dict(scopes=['tpa:read'], expected_response=200),
        dict(scopes=['tpa:read', 'another_scope'], expected_response=200),

        # invalid scopes
        dict(scopes=[], expected_response=403),
        dict(scopes=['another_scope'], expected_response=403),
    )
    @ddt.unpack
    def test_jwt_scopes(self, scopes, expected_response):
        self._assert_jwt_restricted_case(
            scopes=scopes,
            filters=['tpa_provider:some_tpa_provider'],
            expected_response=expected_response,
        )

    @ddt.data(
        # valid provider filters
        dict(
            filters=['tpa_provider:some_tpa_provider', 'tpa_provider:another_tpa_provider'],
            expected_response=200,
        ),

        # invalid provider filters
        dict(
            filters=['tpa_provider:another_tpa_provider'],
            expected_response=403,
        ),
        dict(
            filters=[],
            expected_response=403,
        ),
    )
    @ddt.unpack
    def test_jwt_org_filters(self, filters, expected_response):
        self._assert_jwt_restricted_case(
            scopes=['tpa:read'],
            filters=filters,
            expected_response=expected_response,
        )

    def _assert_jwt_restricted_case(self, scopes, filters, expected_response):
        """
        Asserts the provided scopes and filters result in the expected response
        for a restricted JWT.
        """
        user = self._create_user()

        auth_header = self._create_jwt_header(user, is_restricted=True, scopes=scopes, filters=filters)
        request = self._create_request(auth_header=auth_header)

        response = self.SomeTpaClassView().dispatch(request, provider_id='some_tpa_provider')
        assert response.status_code == expected_response
