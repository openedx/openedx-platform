"""
Branding API endpoint urls.
"""


from django.urls import path  # noqa: I001

from lms.djangoapps.branding.views import footer

urlpatterns = [
    path('footer', footer, name="branding_footer"),
]
