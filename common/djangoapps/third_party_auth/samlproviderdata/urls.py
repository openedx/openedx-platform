"""
    url mappings for auth/saml/v0/providerdata/
"""

from rest_framework import routers  # noqa: I001

from .views import SAMLProviderDataViewSet

saml_provider_data_router = routers.DefaultRouter()
saml_provider_data_router.register(r'provider_data', SAMLProviderDataViewSet, basename="saml_provider_data")
urlpatterns = saml_provider_data_router.urls
