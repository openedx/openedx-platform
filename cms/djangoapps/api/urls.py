"""
URLs for the Studio API app
"""


from django.urls import include  # noqa: I001
from django.urls import path

app_name = 'cms.djangoapps.api'

urlpatterns = [
    path('v1/', include('cms.djangoapps.api.v1.urls', namespace='v1')),
]
