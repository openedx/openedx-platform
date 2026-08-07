"""
Contentstore API URLs.
"""

from django.urls import include, path

from .v0 import urls as v0_urls
from .v1 import urls as v1_urls
from .v2 import urls as v2_urls
from .v3 import urls as v3_urls
from .v4 import urls as v4_urls

app_name = 'cms.djangoapps.contentstore'

urlpatterns = [
    path('v0/', include(v0_urls)),
    path('v1/', include(v1_urls)),
    path('v2/', include(v2_urls)),
    path('v3/', include(v3_urls)),
    path('v4/', include(v4_urls)),
]
