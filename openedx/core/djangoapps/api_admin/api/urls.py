"""
URL definitions for api access request API.
"""


from django.urls import include  # noqa: I001
from django.urls import path

app_name = 'api_admin'
urlpatterns = [
    path('v1/', include('openedx.core.djangoapps.api_admin.api.v1.urls')),
]
