"""
Common URLs for User Authentication

Note: The split between urls.py and urls_common.py is hopefully temporary.
For now, this is needed because of difference in CMS and LMS that have
not yet been cleaned up.

This is also home to urls for endpoints that have been consolidated from other djangoapps,
which leads to inconsistent prefixing.

"""


from django.conf import settings
from django.contrib.auth.views import PasswordResetCompleteView
from django.urls import path, re_path

from .views import auto_auth, login, logout, password_reset, register
from .views.password_reset import PasswordResetConfirmWrapper

urlpatterns = [
    # Registration
    path('create_account', register.RegistrationView.as_view(), name='create_account'),

    # Moved from user_api/legacy_urls.py
    path('api/user/v1/account/registration/', register.RegistrationView.as_view(),
         name="user_api_registration"
         ),
    # `user_api` prefix is preserved for backwards compatibility.
    path('user_api/v1/account/registration/', register.RegistrationView.as_view(),
         name="user_api_registration_legacy"),

    # V2 is created to avoid backward compatibility issue with confirm_email
    path('api/user/v2/account/registration/', register.RegistrationView.as_view(),
         name="user_api_registration_v2"
         ),
    # legacy url
    path('user_api/v2/account/registration/', register.RegistrationView.as_view(),
         name="user_api_registration_v2_legacy"),

    # Moved from user_api/urls.py
    # `api/user` prefix is preserved for backwards compatibility.
    path('api/user/v1/validation/registration', register.RegistrationValidationView.as_view(),
         name='registration_validation'
         ),

    path('login_ajax', login.login_user, name="login_api"),

    # Moved from user_api/legacy_urls.py
    re_path(
        r'^api/user/(?P<api_version>v(1|2))/account/login_session/$',
        login.LoginSessionView.as_view(),
        name="user_api_login_session"
    ),
    # `user_api` prefix is preserved for backwards compatibility.
    re_path(r'^user_api/(?P<api_version>v(1|2))/account/login_session/$', login.LoginSessionView.as_view(),
            name="user_api_login_session_legacy"),

    # Login Refresh of JWT Cookies
    path('login_refresh', login.login_refresh, name="login_refresh"),

    # WARNING: This is similar to auth_backends ^logout/$ (which has a
    # trailing slash); LMS uses this view, but Studio links to the
    # auth_backends logout view.
    path('logout', logout.LogoutView.as_view(), name='logout'),

    # Moved from user_api/legacy_urls.py
    path('api/user/v1/account/password_reset/', password_reset.PasswordResetView.as_view(),
         name="user_api_password_reset"
         ),
    # legacy url
    path('user_api/v1/account/password_reset/', password_reset.PasswordResetView.as_view(),
         name="user_api_password_reset_legacy"),

    # Password reset api views.
    path('password_reset/', password_reset.password_reset, name='password_reset'),
    re_path(
        r'^password_reset_confirm/(?P<uidb36>[0-9A-Za-z]+)-(?P<token>.+)/$',
        PasswordResetConfirmWrapper.as_view(),
        name='password_reset_confirm',
    ),
    path('account/password', password_reset.password_change_request_handler, name='password_change_request'),

    # authn MFE flow
    path('api/user/v1/account/password_reset/token/validate/', password_reset.PasswordResetTokenValidation.as_view(),
         name="user_api_password_reset_token_validate"
         ),
    # legacy url
    path('user_api/v1/account/password_reset/token/validate/', password_reset.PasswordResetTokenValidation.as_view(),
         name="user_api_password_reset_token_validate_legacy"),

    # authn MFE reset flow
    re_path(
        r'^password/reset/(?P<uidb36>[0-9A-Za-z]+)-(?P<token>.+)/$',
        password_reset.LogistrationPasswordResetView.as_view(),
        name='logistration_password_reset',
    ),
]

# password reset django views (see above for password reset views)
urlpatterns += [
    path('password_reset_complete/', PasswordResetCompleteView.as_view(),
         name='password_reset_complete',
         ),
]

# enable automatic login
if settings.AUTOMATIC_AUTH_FOR_TESTING:
    urlpatterns += [
        path('auto_auth', auto_auth.auto_auth),
    ]
