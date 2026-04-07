"""
Viewset for auth/saml/v0/samlproviderconfig
"""

from django.db.utils import IntegrityError
from django.shortcuts import get_list_or_404
from edx_rbac.mixins import PermissionRequiredMixin
from enterprise.models import EnterpriseCustomer, EnterpriseCustomerIdentityProvider
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import ParseError, ValidationError
from rest_framework.response import Response

from common.djangoapps.third_party_auth.utils import validate_uuid4_string

from ..models import SAMLProviderConfig
from ..utils import convert_saml_slug_provider_id
from .serializers import SAMLProviderConfigSerializer


class SAMLProviderMixin:
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SAMLProviderConfigSerializer


class SAMLProviderConfigViewSet(PermissionRequiredMixin, SAMLProviderMixin, viewsets.ModelViewSet):
    """
    A View to handle SAMLProviderConfig CRUD

    Usage:
        NOTE: Only the GET request requires a request parameter, otherwise pass the uuid as part
        of the post body

        GET /auth/saml/v0/provider_config/?enterprise-id=uuid
        POST /auth/saml/v0/provider_config/ -d postData (must contain 'enterprise_customer_uuid')
        DELETE /auth/saml/v0/provider_config/:pk -d postData (must contain 'enterprise_customer_uuid')
        PATCH /auth/saml/v0/provider_config/:pk -d postData (must contain 'enterprise_customer_uuid')

    permission_required refers to the Django permission name defined
    in enterprise.rules.
    The associated rule will allow edx-rbac to check if the EnterpriseCustomer
    returned by the get_permission_object method here, can be
    accessed by the user making this request (request.user)
    Access is only allowed if the user has the system role
    of 'ENTERPRISE_ADMIN' which is defined in enterprise.constants
    """
    permission_required = 'enterprise.can_access_admin_dashboard'

    def get_queryset(self):
        """
        Find and return the matching providerconfig for the given enterprise uuid
        if an association exists in EnterpriseCustomerIdentityProvider model
        """
        if self.requested_enterprise_uuid is None:
            raise ParseError('Required enterprise_customer_uuid is missing')
        enterprise_customer_idps = get_list_or_404(
            EnterpriseCustomerIdentityProvider,
            enterprise_customer__uuid=self.requested_enterprise_uuid
        )
        slug_list = [idp.provider_id for idp in enterprise_customer_idps]
        saml_config_ids = [
            config.id for config in SAMLProviderConfig.objects.current_set() if config.provider_id in slug_list
        ]
        return SAMLProviderConfig.objects.filter(id__in=saml_config_ids)

    def destroy(self, request, *args, **kwargs):
        saml_provider_config = self.get_object()
        config_id = saml_provider_config.id
        provider_config_provider_id = saml_provider_config.provider_id
        customer_uuid = self.requested_enterprise_uuid
        try:
            enterprise_customer = EnterpriseCustomer.objects.get(pk=customer_uuid)
        except EnterpriseCustomer.DoesNotExist:
            raise ValidationError(f'Enterprise customer not found at uuid: {customer_uuid}')  # lint-amnesty, pylint: disable=raise-missing-from

        enterprise_saml_provider = EnterpriseCustomerIdentityProvider.objects.filter(
            enterprise_customer=enterprise_customer,
            provider_id=provider_config_provider_id,
        )
        enterprise_saml_provider.delete()
        SAMLProviderConfig.objects.filter(id=saml_provider_config.id).update(archived=True, enabled=False)
        return Response(data=config_id, status=status.HTTP_200_OK)

    @property
    def requested_enterprise_uuid(self):
        """
        The enterprise customer uuid from request params or post body
        """
        if self.request.method in ('POST', 'PUT'):
            uuid_str = self.request.POST.get('enterprise_customer_uuid')
            if uuid_str is None:
                raise ParseError('Required enterprise_customer_uuid is missing')
            return uuid_str
        else:
            uuid_str = self.request.query_params.get('enterprise_customer_uuid')
            if validate_uuid4_string(uuid_str) is False:
                raise ParseError('Invalid UUID enterprise_customer_id')
            return uuid_str

    def get_permission_object(self):
        """
        Retrieve an EnterpriseCustomer uuid to do auth against
        Right now this is the same as from the request object
        meaning that only users belonging to the same enterprise
        can access these endpoints, we have to sort out the operator role use case
        """
        return self.requested_enterprise_uuid

    def create(self, request, *args, **kwargs):
        """
        Process POST /auth/saml/v0/provider_config/ {postData}
        """

        customer_uuid = self.requested_enterprise_uuid
        try:
            enterprise_customer = EnterpriseCustomer.objects.get(pk=customer_uuid)
        except EnterpriseCustomer.DoesNotExist:
            raise ValidationError(f'Enterprise customer not found at uuid: {customer_uuid}')  # lint-amnesty, pylint: disable=raise-missing-from

        # Create the samlproviderconfig model first
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
        except IntegrityError as exc:
            return Response(str(exc), status=status.HTTP_400_BAD_REQUEST)

        # Associate the enterprise customer with the provider
        association_obj = EnterpriseCustomerIdentityProvider(
            enterprise_customer=enterprise_customer,
            provider_id=convert_saml_slug_provider_id(serializer.data['slug'])
        )
        association_obj.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
