""" URLs for User Authentication """

from django.urls import include, path  # noqa: I001

from .views import auth, login, login_form

urlpatterns = [
    # TODO move contents of urls_common here once CMS no longer has its own login
    path('', include('openedx.core.djangoapps.user_authn.urls_common')),
    path('api/', include('openedx.core.djangoapps.user_authn.api.urls')),
    path('account/finish_auth', login.finish_auth, name='finish_auth'),
    path('auth/jwks.json', auth.get_public_signing_jwks, name='get_public_signing_jwks'),
]


# Backwards compatibility with old URL structure, but serve the new views
urlpatterns += [
    path('login', login_form.login_and_registration_form,
         {'initial_mode': 'login'}, name='signin_user'),
    path('register', login_form.login_and_registration_form,
         {'initial_mode': 'register'}, name='register_user'),
    path('password_assistance', login_form.login_and_registration_form,
         {'initial_mode': 'reset'}, name='password_assistance'),
]
