"""
Defines the URL routes for this app.
"""
from django.conf import settings
from django.shortcuts import redirect
from django.urls import include, path, re_path
from rest_framework import routers

from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers

from . import views as user_api_views
from .models import UserPreference

USER_API_ROUTER = routers.DefaultRouter()
USER_API_ROUTER.register(r'users', user_api_views.UserViewSet)
USER_API_ROUTER.register(r'user_prefs', user_api_views.UserPreferenceViewSet)

def account_settings_redirect_view(request):
    """
    Backward-compatible redirect for /account and /account/settings to the
    Account MFE.

    Unlike a plain RedirectView, this re-evaluates ACCOUNT_MICROFRONTEND_URL
    per request so site-aware configuration is honored.

    Any third-party-auth error message from the pipeline is *not* forwarded
    here as a query param: SocialAuthExceptionMiddleware already leaves it in
    the Django messages framework, and the Account MFE reads it directly via
    ThirdPartyAuthErrorMessageView (openedx/core/djangoapps/user_api/views.py).
    """
    account_mfe_url = configuration_helpers.get_value(
        'ACCOUNT_MICROFRONTEND_URL',
        settings.ACCOUNT_MICROFRONTEND_URL,
    )
    return redirect(account_mfe_url)

urlpatterns = [
    # This redirect is needed for backward compatibility with the old URL structure for the authentication
    # workflows using third-party authentication providers until the authentication workflows fully support
    # the URL structure with MFEs.
    re_path(r'^account(?:/settings)?/?$', account_settings_redirect_view),
    path('user_api/v1/', include(USER_API_ROUTER.urls)),
    re_path(
        fr'^user_api/v1/preferences/(?P<pref_key>{UserPreference.KEY_REGEX})/users/$',
        user_api_views.PreferenceUsersListView.as_view()
    ),
    re_path(
        r'^user_api/v1/forum_roles/(?P<name>[a-zA-Z]+)/users/$',
        user_api_views.ForumRoleUsersListView.as_view()
    ),

    path('user_api/v1/preferences/email_opt_in/', user_api_views.UpdateEmailOptInPreference.as_view(),
         name="preferences_email_opt_in_legacy"
         ),
    path('user_api/v1/preferences/time_zones/', user_api_views.CountryTimeZoneListView.as_view(),
         ),
]
