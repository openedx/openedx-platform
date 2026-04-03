"""
Tests for SAMLProviderConfig endpoints
"""
import copy
import re
from uuid import uuid4

from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.http import urlencode
from enterprise.constants import ENTERPRISE_ADMIN_ROLE, ENTERPRISE_LEARNER_ROLE
from enterprise.models import EnterpriseCustomer, EnterpriseCustomerIdentityProvider
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.models import SAMLConfiguration, SAMLProviderConfig
from common.djangoapps.third_party_auth.tests.samlutils import set_jwt_cookie
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth
from common.djangoapps.third_party_auth.utils import convert_saml_slug_provider_id

# country here refers to the URN provided by a user's IDP
SINGLE_PROVIDER_CONFIG = {
    'entity_id': 'id',
    'metadata_source': 'http://test.url',
    'name': 'name-of-config',
    'enabled': 'true',
    'slug': 'test-slug',
    'country': 'https://example.customer.com/countrycode',
    'attr_first_name': 'jon',
    'attr_last_name': 'snow',
}

SINGLE_PROVIDER_CONFIG_2 = copy.copy(SINGLE_PROVIDER_CONFIG)
SINGLE_PROVIDER_CONFIG_2['name'] = 'name-of-config-2'
SINGLE_PROVIDER_CONFIG_2['slug'] = 'test-slug-2'
SINGLE_PROVIDER_CONFIG_2['display_name'] = 'display-name'
SINGLE_PROVIDER_CONFIG_2['entity_id'] = 'id-2'

SINGLE_PROVIDER_CONFIG_3 = copy.copy(SINGLE_PROVIDER_CONFIG)
SINGLE_PROVIDER_CONFIG_3['name'] = 'name-of-config-3'
SINGLE_PROVIDER_CONFIG_3['slug'] = 'test-slug-3'
SINGLE_PROVIDER_CONFIG_3['entity_id'] = 'id-3'


ENTERPRISE_ID = str(uuid4())
ENTERPRISE_ID_NON_EXISTENT = str(uuid4())


@skip_unless_thirdpartyauth()
class SAMLProviderConfigTests(APITestCase):
    """
    API Tests for SAMLProviderConfig REST endpoints
    The skip annotation above exists because we currently cannot run this test in
    the cms mode in CI builds, where the third_party_auth application is not loaded
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory.create(username='testuser', password='testpwd')
        cls.site, _ = Site.objects.get_or_create(domain='example.com')
        cls.enterprise_customer = EnterpriseCustomer.objects.create(
            uuid=ENTERPRISE_ID,
            name='test-ep',
            slug='test-ep',
            site=cls.site)
        cls.samlproviderconfig, _ = SAMLProviderConfig.objects.get_or_create(
            entity_id=SINGLE_PROVIDER_CONFIG['entity_id'],
            metadata_source=SINGLE_PROVIDER_CONFIG['metadata_source'],
            slug=SINGLE_PROVIDER_CONFIG['slug'],
            country=SINGLE_PROVIDER_CONFIG['country'],
        )
        cls.samlconfiguration, _ = SAMLConfiguration.objects.get_or_create(
            enabled=True,
            site=cls.site,
            slug='edxSideTest',
        )

    def setUp(self):  # pylint: disable=super-method-not-called
        set_jwt_cookie(self.client, self.user, [(ENTERPRISE_ADMIN_ROLE, ENTERPRISE_ID)])
        self.client.force_authenticate(user=self.user)

    def test_get_one_config_by_enterprise_uuid_found(self):
        """
        GET auth/saml/v0/provider_config/?enterprise_customer_uuid=id=id
        """

        # for GET to work, we need an association present
        EnterpriseCustomerIdentityProvider.objects.get_or_create(
            provider_id=convert_saml_slug_provider_id(self.samlproviderconfig.slug),
            enterprise_customer_id=ENTERPRISE_ID
        )
        urlbase = reverse('saml_provider_config-list')
        query_kwargs = {'enterprise_customer_uuid': ENTERPRISE_ID}
        url = f'{urlbase}?{urlencode(query_kwargs)}'

        response = self.client.get(url, format='json')

        assert response.status_code == status.HTTP_200_OK
        results = response.data['results']
        assert len(results) == 1
        assert results[0]['entity_id'] == SINGLE_PROVIDER_CONFIG['entity_id']
        assert results[0]['metadata_source'] == SINGLE_PROVIDER_CONFIG['metadata_source']
        assert response.data['results'][0]['country'] == SINGLE_PROVIDER_CONFIG['country']
        assert re.match(r"test-slug-\d{4}", results[0]['display_name'])
        assert SAMLProviderConfig.objects.count() == 1

    def test_get_one_config_by_enterprise_uuid_invalid_uuid(self):
        """
        GET auth/saml/v0/provider_config/?enterprise_customer_uuid=invalidUUID
        """
        urlbase = reverse('saml_provider_config-list')
        query_kwargs = {'enterprise_customer_uuid': 'invalid_uuid'}
        url = f'{urlbase}?{urlencode(query_kwargs)}'

        response = self.client.get(url, format='json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_one_config_by_enterprise_uuid_not_found(self):
        """
        GET auth/saml/v0/provider_config/?enterprise_customer_uuid=valid-but-nonexistent-uuid
        """

        # the user must actually be authorized for this enterprise
        # since we are testing auth passes but association to samlproviderconfig is not found
        set_jwt_cookie(self.client, self.user, [(ENTERPRISE_ADMIN_ROLE, ENTERPRISE_ID_NON_EXISTENT)])
        self.client.force_authenticate(user=self.user)

        urlbase = reverse('saml_provider_config-list')
        query_kwargs = {'enterprise_customer_uuid': ENTERPRISE_ID_NON_EXISTENT}
        url = f'{urlbase}?{urlencode(query_kwargs)}'
        orig_count = SAMLProviderConfig.objects.count()

        response = self.client.get(url, format='json')

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert SAMLProviderConfig.objects.count() == orig_count

    def test_create_one_config(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        data = copy.copy(SINGLE_PROVIDER_CONFIG_2)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        orig_count = SAMLProviderConfig.objects.count()

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        assert SAMLProviderConfig.objects.count() == (orig_count + 1)
        provider_config = SAMLProviderConfig.objects.get(slug=SINGLE_PROVIDER_CONFIG_2['slug'])
        assert provider_config.name == 'name-of-config-2'
        assert provider_config.country == SINGLE_PROVIDER_CONFIG_2['country']
        assert provider_config.attr_username == SINGLE_PROVIDER_CONFIG['attr_first_name']
        assert provider_config.display_name == SINGLE_PROVIDER_CONFIG_2['display_name']

        # check association has also been created
        assert EnterpriseCustomerIdentityProvider.objects.filter(
            provider_id=convert_saml_slug_provider_id(provider_config.slug)
        ).exists(), 'Cannot find EnterpriseCustomer-->SAMLProviderConfig association'

    def test_create_one_config_fail_non_existent_enterprise_uuid(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        data = copy.copy(SINGLE_PROVIDER_CONFIG_2)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID_NON_EXISTENT
        orig_count = SAMLProviderConfig.objects.count()

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert SAMLProviderConfig.objects.count() == orig_count

        # check association has NOT been created
        assert not EnterpriseCustomerIdentityProvider.objects.filter(
            provider_id=convert_saml_slug_provider_id(SINGLE_PROVIDER_CONFIG_2['slug'])
        ).exists(), 'Did not expect to find EnterpriseCustomer-->SAMLProviderConfig association'

    def test_create_one_config_with_absent_enterprise_uuid(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        data = copy.copy(SINGLE_PROVIDER_CONFIG_2)
        orig_count = SAMLProviderConfig.objects.count()

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert SAMLProviderConfig.objects.count() == orig_count

    def test_create_one_config_with_no_country_urn(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        provider_config_no_country = {
            'entity_id': 'id2',
            'metadata_source': 'http://test.url',
            'name': 'name-of-config-no-country',
            'enabled': 'true',
            'slug': 'test-slug-none',
            'enterprise_customer_uuid': ENTERPRISE_ID,
        }

        response = self.client.post(url, provider_config_no_country)
        assert response.status_code == status.HTTP_201_CREATED
        provider_config = SAMLProviderConfig.objects.get(slug='test-slug-none')
        assert provider_config.country == ''

    def test_create_one_config_with_empty_country_urn(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        provider_config_blank_country = {
            'entity_id': 'id-empty-country-urn',
            'metadata_source': 'http://test.url',
            'name': 'name-of-config-blank-country',
            'enabled': 'true',
            'slug': 'test-slug-empty',
            'enterprise_customer_uuid': ENTERPRISE_ID,
            'country': '',
        }

        response = self.client.post(url, provider_config_blank_country)
        assert response.status_code == status.HTTP_201_CREATED
        provider_config = SAMLProviderConfig.objects.get(slug='test-slug-empty')
        assert provider_config.country == ''

    def test_unauthenticated_request_is_forbidden(self):
        self.client.logout()
        urlbase = reverse('saml_provider_config-list')
        query_kwargs = {'enterprise_customer_uuid': ENTERPRISE_ID}
        url = f'{urlbase}?{urlencode(query_kwargs)}'
        set_jwt_cookie(self.client, self.user, [(ENTERPRISE_LEARNER_ROLE, ENTERPRISE_ID)])
        response = self.client.get(url, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

        self.client.logout()
        set_jwt_cookie(self.client, self.user, [(ENTERPRISE_ADMIN_ROLE, ENTERPRISE_ID_NON_EXISTENT)])
        response = self.client.get(url, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_create_one_config_with_samlconfiguration(self):
        """
        POST auth/saml/v0/provider_config/ -d data
        """
        url = reverse('saml_provider_config-list')
        data = copy.copy(SINGLE_PROVIDER_CONFIG_3)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        data['saml_config_id'] = self.samlconfiguration.id

        response = self.client.post(url, data)

        assert response.status_code == status.HTTP_201_CREATED
        provider_config = SAMLProviderConfig.objects.get(slug=SINGLE_PROVIDER_CONFIG_3['slug'])
        assert provider_config.saml_configuration == self.samlconfiguration

    def test_unique_entity_id_constraint_with_different_slug(self):
        """
        Test that a config cannot be created with an entity ID if another config already exists with that entity ID and
        a different slug
        """
        with self.assertLogs() as ctx:
            url = reverse('saml_provider_config-list')
            data = copy.copy(SINGLE_PROVIDER_CONFIG)
            data['enterprise_customer_uuid'] = ENTERPRISE_ID
            data['slug'] = 'some-other-slug'

            response = self.client.post(url, data)

        # 7/21/22 : Disabling the exception on duplicate entity ID's because of existing data.
        assert ctx.records[-2].msg == f"Entity ID: {data['entity_id']} already taken"
        assert response.status_code == status.HTTP_201_CREATED
        # assert response.status_code == status.HTTP_400_BAD_REQUEST
        # assert len(SAMLProviderConfig.objects.all()) == 1
        # assert str(response.data.get('non_field_errors')[0]) == f"Entity ID: {data['entity_id']} already taken"

    def test_unique_entity_id_constraint_with_same_slug(self):
        """
        Test that a config can be created/edited using the same entity ID as an existing config as long as it shares an
        entity ID.
        """
        url = reverse('saml_provider_config-list')
        data = copy.copy(SINGLE_PROVIDER_CONFIG)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        data['name'] = 'some-other-name'

        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_201_CREATED
        assert len(SAMLProviderConfig.objects.all()) == 2
        assert response.data.get('name') == 'some-other-name'

    def test_api_deleting_provider_configs(self):
        """
        Test deleting a provider config.
        """
        EnterpriseCustomerIdentityProvider.objects.get_or_create(
            provider_id=convert_saml_slug_provider_id(self.samlproviderconfig.slug),
            enterprise_customer_id=ENTERPRISE_ID
        )
        url = reverse('saml_provider_config-list')
        data = {}
        data['enterprise_customer_uuid'] = ENTERPRISE_ID

        response = self.client.delete(
            url + f'{str(self.samlproviderconfig.id)}/?enterprise_customer_uuid={ENTERPRISE_ID}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(SAMLProviderConfig.objects.all()) == 1
        assert SAMLProviderConfig.objects.first().archived

    def test_api_deleting_config_then_using_deleted_entity_id(self):
        """
        Test deleting a config then creating a new config with the entity ID of the deleted config
        """
        EnterpriseCustomerIdentityProvider.objects.get_or_create(
            provider_id=convert_saml_slug_provider_id(self.samlproviderconfig.slug),
            enterprise_customer_id=ENTERPRISE_ID
        )
        url = reverse('saml_provider_config-list')
        data = {}
        data['enterprise_customer_uuid'] = ENTERPRISE_ID

        response = self.client.delete(
            url + f'{str(self.samlproviderconfig.id)}/?enterprise_customer_uuid={ENTERPRISE_ID}'
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(SAMLProviderConfig.objects.all()) == 1
        assert SAMLProviderConfig.objects.first().archived

        data = copy.copy(SINGLE_PROVIDER_CONFIG)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        data['entity_id'] = SINGLE_PROVIDER_CONFIG['entity_id']
        data['slug'] = 'idk-something-else'

        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_201_CREATED
        assert len(SAMLProviderConfig.objects.all()) == 2

    def test_using_an_edited_configs_entity_id_after_deleting(self):
        """
        Test that editing an existing config then removing it still allows new configs to use the deleted config's
        entity ID
        """
        EnterpriseCustomerIdentityProvider.objects.get_or_create(
            provider_id=convert_saml_slug_provider_id(self.samlproviderconfig.slug),
            enterprise_customer_id=ENTERPRISE_ID
        )
        url = reverse('saml_provider_config-list')

        data = copy.copy(SINGLE_PROVIDER_CONFIG)
        data['saml_config_id'] = self.samlconfiguration.id
        data['name'] = 'a new name'

        response = self.client.patch(
            url + f'{str(self.samlproviderconfig.id)}/?enterprise_customer_uuid={ENTERPRISE_ID}',
            data,
        )
        assert response.status_code == status.HTTP_200_OK
        assert len(SAMLProviderConfig.objects.all()) == 2

        data = {}
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        response = self.client.delete(
            url + f'{str(response.data.get("id"))}/?enterprise_customer_uuid={ENTERPRISE_ID}'
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(SAMLProviderConfig.objects.all()) == 2

        data = copy.copy(SINGLE_PROVIDER_CONFIG_3)
        data['enterprise_customer_uuid'] = ENTERPRISE_ID
        data['saml_config_id'] = self.samlconfiguration.id

        response = self.client.post(url, data)
        assert response.status_code == status.HTTP_201_CREATED
        assert len(SAMLProviderConfig.objects.all()) == 3
