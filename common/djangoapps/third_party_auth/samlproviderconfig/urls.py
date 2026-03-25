"""
    Viewset for auth/saml/v0/providerconfig/
"""

from rest_framework import routers  # noqa: I001

from .views import SAMLProviderConfigViewSet

saml_provider_config_router = routers.DefaultRouter()
saml_provider_config_router.register(r'provider_config', SAMLProviderConfigViewSet, basename="saml_provider_config")
urlpatterns = saml_provider_config_router.urls
