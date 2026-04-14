"""Url configuration for the auth module."""

from django.urls import include, path, re_path

from .views import (
    IdPRedirectView,
    disconnect_json_view,
    inactive_user_view,
    lti_login_and_complete_view,
    post_to_custom_auth_form,
    saml_metadata_view,
)

urlpatterns = [
    path('auth/inactive', inactive_user_view, name="third_party_inactive_redirect"),
    path('auth/custom_auth_entry', post_to_custom_auth_form, name='tpa_post_to_custom_auth_form'),
    re_path(r'^auth/saml/metadata.xml', saml_metadata_view),
    re_path(r'^auth/login/(?P<backend>lti)/$', lti_login_and_complete_view),
    path('auth/idp_redirect/<slug:provider_slug>', IdPRedirectView.as_view(), name="idp_redirect"),
    # Custom JSON disconnect endpoint to avoid CORS issues
    re_path(r'^auth/disconnect_json/(?P<backend>[^/]+)/$', disconnect_json_view, name='custom_disconnect_json'),
    re_path(
        r'^auth/disconnect_json/(?P<backend>[^/]+)/(?P<association_id>\d+)/$',
        disconnect_json_view,
        name='custom_disconnect_json_individual'
    ),
    path('auth/', include('social_django.urls', namespace='social')),
    path('auth/saml/v0/', include('common.djangoapps.third_party_auth.saml_configuration.urls')),
    # NOTE: The following routes under auth/saml/v0/ are registered by the
    # edx-enterprise plugin (enterprise/api/v1/urls.py). Do not re-register
    # routes at these paths:
    #   auth/saml/v0/provider_config/
    #   auth/saml/v0/provider_data/
    # See docs/decisions/0025-saml-admin-views-in-enterprise-plugin.rst
]
