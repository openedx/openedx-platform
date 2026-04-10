"""
LTI Provider API endpoint urls.
"""


from django.conf import settings
from django.urls import re_path

from lms.djangoapps.lti_provider import views

urlpatterns = [
    re_path(
        r'^courses/{course_id}/{usage_id}$'.format(  # noqa: UP032
            course_id=settings.COURSE_ID_PATTERN,
            usage_id=settings.USAGE_ID_PATTERN
        ),
        views.lti_launch, name="lti_provider_launch"),
]
