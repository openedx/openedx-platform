"""
CCX API URLs.
"""


from django.urls import include, path  # noqa: I001

app_name = 'ccx_api'
urlpatterns = [
    path('v0/', include('lms.djangoapps.ccx.api.v0.urls')),
]
