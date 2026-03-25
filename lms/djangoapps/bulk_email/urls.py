"""
URLs for bulk_email app
"""

from django.conf import settings  # noqa: I001
from django.urls import re_path

from . import views

urlpatterns = [
    re_path(
        r'^email/optout/(?P<token>[a-zA-Z0-9-_=]+)/{}/$'.format(
            settings.COURSE_ID_PATTERN,
        ),
        views.opt_out_email_updates,
        name='bulk_email_opt_out',
    ),
]
