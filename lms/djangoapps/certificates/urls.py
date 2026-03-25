"""
URLs for the certificates app.
"""


from django.conf import settings  # noqa: I001
from django.urls import path, re_path

from lms.djangoapps.certificates import views

app_name = 'certificates'
urlpatterns = [
    # Certificates HTML view end point to render web certs by user and course
    re_path(
        fr'^user/(?P<user_id>[^/]*)/course/{settings.COURSE_ID_PATTERN}',
        views.unsupported_url,
        name='unsupported_url'
    ),

    re_path(
        fr'^course/{settings.COURSE_ID_PATTERN}',
        views.render_preview_certificate,
        name='preview_cert'
    ),

    # Certificates HTML view end point to render web certs by certificate_uuid
    re_path(
        r'^(?P<certificate_uuid>[0-9a-f]{32})$',
        views.render_cert_by_uuid,
        name='render_cert_by_uuid'
    ),

    # End-points used by student support
    # The views in the lms/djangoapps/support use these end-points
    # to retrieve certificate information and regenerate certificates.
    path('search', views.search_certificates, name="search"),
    path('regenerate', views.regenerate_certificate_for_user, name="regenerate_certificate_for_user"),
    path('generate', views.generate_certificate_for_user, name="generate_certificate_for_user"),
]
