"""
    Viewset for auth/saml/v0/samlconfiguration/
"""

from rest_framework import routers  # noqa: I001

from .views import SAMLConfigurationViewSet

saml_configuration_router = routers.DefaultRouter()
saml_configuration_router.register(r'saml_configuration', SAMLConfigurationViewSet, basename="saml_configuration")
urlpatterns = saml_configuration_router.urls
