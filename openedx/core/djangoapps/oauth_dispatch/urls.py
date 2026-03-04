"""
OAuth2 wrapper urls
"""


from django.conf import settings
from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt

from . import views

urlpatterns = [
    re_path(r'^authorize/?$', csrf_exempt(views.AuthorizationView.as_view()), name='authorize'),
    re_path(r'^access_token/?$', csrf_exempt(views.AccessTokenView.as_view()), name='access_token'),
    re_path(r'^revoke_token/?$', csrf_exempt(views.RevokeTokenView.as_view()), name='revoke_token'),
]

if settings.ENABLE_THIRD_PARTY_AUTH:
    urlpatterns += [
        path('exchange_access_token/<str:backend>/', csrf_exempt(views.AccessTokenExchangeView.as_view()),
             name='exchange_access_token',
             ),
    ]
