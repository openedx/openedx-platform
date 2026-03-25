"""
Certificates API v0 URLs.
"""


from django.conf import settings  # noqa: I001
from django.urls import include, path, re_path

from lms.djangoapps.certificates.apis.v0 import views

CERTIFICATES_URLS = ([
    re_path(
        r'^{username}/courses/{course_id}/$'.format(
            username=settings.USERNAME_PATTERN,
            course_id=settings.COURSE_ID_PATTERN
        ),
        views.CertificatesDetailView.as_view(), name='detail'
    ),
    re_path(
        r'^{username}/$'.format(
            username=settings.USERNAME_PATTERN
        ),
        views.CertificatesListView.as_view(), name='list'
    ),
], 'certificates')

app_name = 'v0'
urlpatterns = [
    path('certificates/', include(CERTIFICATES_URLS)),
]
