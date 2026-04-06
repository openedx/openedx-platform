"""
Contentstore API URLs.
"""

from django.urls import include, path

from .v0 import urls as v0_urls
from .v1 import urls as v1_urls
from .v2 import urls as v2_urls

app_name = 'cms.djangoapps.contentstore'

urlpatterns = [
    path('v0/', include(v0_urls)),
    path('v1/', include(v1_urls)),
    path('v2/', include(v2_urls))
]
