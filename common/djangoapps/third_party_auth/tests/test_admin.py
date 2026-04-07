"""
Tests third_party_auth admin views
"""

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms import models
from django.urls import reverse

from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.third_party_auth.admin import OAuth2ProviderConfigAdmin
from common.djangoapps.third_party_auth.models import OAuth2ProviderConfig
from common.djangoapps.third_party_auth.tests import testutil
from common.djangoapps.third_party_auth.tests.utils import skip_unless_thirdpartyauth

TEST_PASSWORD = 'Password1234'


# This is necessary because cms does not implement third party auth
@skip_unless_thirdpartyauth()
class Oauth2ProviderConfigAdminTest(testutil.TestCase):
    """
    Tests for oauth2 provider config admin
    """
    def test_oauth2_provider_edit_icon_image(self):
        """
        Test that we can update an OAuth provider's icon image from the admin
        form.

        OAuth providers are updated using KeyedConfigurationModelAdmin, which
        updates models by adding a new instance that replaces the old one,
        instead of editing the old instance directly.

        Updating the icon image is tricky here because
        KeyedConfigurationModelAdmin copies data over from the previous
        version by injecting its attributes into request.GET, but the icon
        ends up in request.FILES. We need to ensure that the value is
        prepopulated correctly, and that we can clear and update the image.
        """
        # Login as a super user
        user = UserFactory.create(is_staff=True, is_superuser=True, password=TEST_PASSWORD)
        user.save()
        self.client.login(username=user.username, password=TEST_PASSWORD)

        # Get baseline provider count
        providers = OAuth2ProviderConfig.objects.all()
        pcount = len(providers)

        # Create a provider
        provider1 = self.configure_dummy_provider(
            enabled=True,
            icon_class='',
            icon_image=SimpleUploadedFile('icon.svg', b'<svg><rect width="50" height="100"/></svg>'),
        )

        # Get the provider instance with active flag
        providers = OAuth2ProviderConfig.objects.all()
        assert len(providers) == 1
        assert providers[pcount].id == provider1.id

        # Edit the provider via the admin edit link
        admin = OAuth2ProviderConfigAdmin(provider1, AdminSite())
        update_url = reverse(f'admin:{admin.model._meta.app_label}_{admin.model._meta.model_name}_add')
        update_url += f"?source={provider1.pk}"

        # Remove the icon_image from the POST data, to simulate unchanged icon_image
        post_data = models.model_to_dict(provider1)
        del post_data['icon_image']
        # Remove max_session_length, was_valid_at and organization. A default null value must be POSTed
        # back as an absent value, rather than as a "null-like" included value.
        del post_data['max_session_length']
        del post_data['organization']
        del post_data['was_valid_at']

        # Change the name, to verify POST
        post_data['name'] = 'Another name'

        # Post the edit form: expecting redirect
        response = self.client.post(update_url, post_data)
        assert response.status_code == 302

        # Editing the existing provider creates a new provider instance
        providers = OAuth2ProviderConfig.objects.all()
        assert len(providers) == (pcount + 2)
        assert providers[pcount].id == provider1.id
        provider2 = providers[pcount + 1]

        # Ensure the icon_image was preserved on the new provider instance
        assert provider2.icon_image == provider1.icon_image
        assert provider2.name == post_data['name']
