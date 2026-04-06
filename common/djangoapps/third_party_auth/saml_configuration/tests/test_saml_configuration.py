"""
Tests for SAMLConfiguration endpoints
"""
from django.contrib.sites.models import Site
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.models import SAMLConfiguration
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth

SAML_CONFIGURATIONS = [
    {
        'site': 1,
        'slug': 'testing',
        'private_key': 'TestingKey',
        'public_key': 'TestingKey',
        'entity_id': 'example.com',
        'is_public': True,
    },
    {
        'site': 2,
        'slug': 'testing2',
        'private_key': 'TestingKey2',
        'public_key': 'TestingKey2',
        'entity_id': 'edx.example.com',
        'is_public': True,
    },
]

PRIV_CONFIGURATIONS = [
    {
        'site': 1,
        'slug': 'testing3',
        'private_key': 'TestingKey',
        'public_key': 'TestingKey',
        'entity_id': 'example.com',
        'is_public': False,
    },
]

TEST_PASSWORD = 'testpwd'


@skip_unless_thirdpartyauth()
class SAMLConfigurationTests(APITestCase):
    """
    API Tests for SAMLConfiguration objects retrieval.
    The skip annotation above exists because we currently cannot run this test in
    the cms mode in CI builds, where the third_party_auth application is not loaded
    """
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = UserFactory.create(username='testuser', password=TEST_PASSWORD)
        cls.site, _ = Site.objects.get_or_create(domain='example.com')
        for config in SAML_CONFIGURATIONS:
            cls.samlconfiguration = SAMLConfiguration.objects.get_or_create(
                site=cls.site,
                slug=config['slug'],
                private_key=config['private_key'],
                public_key=config['public_key'],
                entity_id=config['entity_id'],
                is_public=config['is_public']
            )
        for config in PRIV_CONFIGURATIONS:
            cls.samlconfiguration = SAMLConfiguration.objects.get_or_create(
                site=cls.site,
                slug=config['slug'],
                private_key=config['private_key'],
                public_key=config['public_key'],
                entity_id=config['entity_id'],
                is_public=config['is_public']
            )

    def setUp(self):
        super().setUp()
        self.client.login(username=self.user.username, password=TEST_PASSWORD)

    def test_get_saml_configurations_successful(self):
        url = reverse('saml_configuration-list')
        response = self.client.get(url, format='json')
        assert response.status_code == status.HTTP_200_OK

        # We ultimately just need ids and slugs, so let's just check those.
        results = response.data['results']
        assert results[0]['id'] == SAML_CONFIGURATIONS[0]['site']
        assert results[0]['slug'] == SAML_CONFIGURATIONS[0]['slug']
        assert results[1]['id'] == SAML_CONFIGURATIONS[1]['site']
        assert results[1]['slug'] == SAML_CONFIGURATIONS[1]['slug']

    def test_get_saml_configurations_noprivate(self):
        # Verify we have 3 saml configuration objects: 2 public, 1 private.
        total_object_count = SAMLConfiguration.objects.count()
        assert total_object_count == 3

        url = reverse('saml_configuration-list')
        response = self.client.get(url, format='json')

        # We should only see 2 results, since 1 out of 3 are private
        # and our queryset only returns public configurations.
        results = response.data['results']
        assert len(results) == 2

    def test_unauthenticated_user_get_saml_configurations(self):
        self.client.logout()
        url = reverse('saml_configuration-list')
        response = self.client.get(url, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
