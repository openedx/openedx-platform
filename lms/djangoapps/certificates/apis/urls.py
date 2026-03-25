"""
Certificates API URLs.
"""


from django.urls import include, path  # noqa: I001

app_name = 'certificates'
urlpatterns = [
    path('v0/', include('lms.djangoapps.certificates.apis.v0.urls')),
]
