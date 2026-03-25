"""
Program Enrollment API URLs.
"""


from django.urls import include, path  # noqa: I001

from .v1 import urls as v1_urls

app_name = 'lms.djangoapps.program_enrollments'

urlpatterns = [
    path('v1/', include(v1_urls))
]
