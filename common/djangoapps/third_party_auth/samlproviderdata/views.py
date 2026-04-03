"""
    Viewset for auth/saml/v0/samlproviderdata
"""
import logging
from datetime import datetime

from django.http import Http404
from django.shortcuts import get_object_or_404
from edx_rbac.mixins import PermissionRequiredMixin
from enterprise.models import EnterpriseCustomerIdentityProvider
from requests.exceptions import HTTPError, MissingSchema, SSLError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from common.djangoapps.third_party_auth.utils import (
    convert_saml_slug_provider_id,
    create_or_update_bulk_saml_provider_data,
    fetch_metadata_xml,
    parse_metadata_xml,
    validate_uuid4_string
)

from ..models import SAMLProviderConfig, SAMLProviderData
from .serializers import SAMLProviderDataSerializer

log = logging.getLogger(__name__)


class SAMLProviderDataMixin:
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SAMLProviderDataSerializer


class SAMLProviderDataViewSet(PermissionRequiredMixin, SAMLProviderDataMixin, viewsets.ModelViewSet):
    """
    A View to handle SAMLProviderData CRUD.
    Uses the edx-rbac mixin PermissionRequiredMixin to apply enterprise authorization

    Usage:
        NOTE: Only the GET request requires a request parameter, otherwise pass the uuid as part
        of the post body

        GET /auth/saml/v0/provider_data/?enterprise-id=uuid
        POST /auth/saml/v0/provider_data/ -d postData (must contain 'enterprise_customer_uuid')
        DELETE /auth/saml/v0/provider_data/:pk -d postData (must contain 'enterprise_customer_uuid')
        PATCH /auth/saml/v0/provider_data/:pk -d postData (must contain 'enterprise_customer_uuid')
        POST /auth/saml/v0/provider_data/sync_provider_data (fetches metadata info from metadata url provided)

    """
    permission_required = 'enterprise.can_access_admin_dashboard'

    def get_queryset(self):
        """
        Find and return the matching providerid for the given enterprise uuid
        Note: There is no direct association between samlproviderdata and enterprisecustomer.
        So we make that association in code via samlproviderdata > samlproviderconfig ( via entity_id )
        then, we fetch enterprisecustomer via samlproviderconfig > enterprisecustomer ( via association table )
        """
        if self.requested_enterprise_uuid is None:
            raise ParseError('Required enterprise_customer_uuid is missing')
        enterprise_customer_idp = get_object_or_404(
            EnterpriseCustomerIdentityProvider,
            enterprise_customer__uuid=self.requested_enterprise_uuid
        )
        try:
            saml_provider = SAMLProviderConfig.objects.current_set().get(
                slug=convert_saml_slug_provider_id(enterprise_customer_idp.provider_id))
        except SAMLProviderConfig.DoesNotExist:
            raise Http404('No matching SAML provider found.')  # lint-amnesty, pylint: disable=raise-missing-from
        provider_data_id = self.request.parser_context.get('kwargs').get('pk')
        if provider_data_id:
            return SAMLProviderData.objects.filter(id=provider_data_id)
        return SAMLProviderData.objects.filter(entity_id=saml_provider.entity_id)

    @property
    def requested_enterprise_uuid(self):
        """
        The enterprise customer uuid from request params or post body
        """
        if self.request.method in ('POST', 'PATCH'):
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
        Retrieve an EnterpriseCustomer to do auth against
        """
        return self.requested_enterprise_uuid

    @action(detail=False, methods=['post', 'put'])
    def sync_provider_data(self, request):
        """
        Creates or updates a SAMProviderData record using info fetched from remote SAML metadata
        For now we will require entityID but in future we will enhance this to try and extract entityID
        from the metadata file, and make entityId optional, and return error response if there are
        multiple entityIDs listed so that the user can choose and retry with a specified entityID
        """
        entity_id = request.POST.get('entity_id')
        metadata_url = request.POST.get('metadata_url')
        sso_url = request.POST.get('sso_url')
        public_keys = request.POST.get('public_key')
        if not entity_id:
            return Response('entity_id is required', status.HTTP_400_BAD_REQUEST)
        if not metadata_url and not (sso_url and public_keys):
            return Response('either metadata_url or sso and public key are required', status.HTTP_400_BAD_REQUEST)
        if metadata_url and (sso_url or public_keys):
            return Response(
                'either metadata_url or sso and public key can be provided, not both', status.HTTP_400_BAD_REQUEST
            )

        if metadata_url:
            # part 1: fetch information from remote metadata based on metadataUrl in samlproviderconfig
            try:
                xml = fetch_metadata_xml(metadata_url)
            except (SSLError, MissingSchema, HTTPError) as ex:
                msg = f'Could not verify provider metadata url. Exc type: {type(ex).__name__}'
                log.warning(msg)
                return Response(msg, status.HTTP_406_NOT_ACCEPTABLE)

            # part 2: create/update samlproviderdata
            log.info("Processing IdP with entityID %s", entity_id)
            public_keys, sso_url, expires_at = parse_metadata_xml(xml, entity_id)
        else:
            now = datetime.now()
            expires_at = now.replace(year=now.year + 10)
        changed = create_or_update_bulk_saml_provider_data(entity_id, public_keys, sso_url, expires_at)
        if changed:
            str_message = f" Created new record(s) for SAMLProviderData for entityID {entity_id}"
            log.info(str_message)
            response = str_message
            http_status = status.HTTP_201_CREATED
        else:
            str_message = f" Updated existing SAMLProviderData record(s) for entityID {entity_id}"
            log.info(str_message)
            response = str_message
            http_status = status.HTTP_200_OK
        return Response(response, status=http_status)
