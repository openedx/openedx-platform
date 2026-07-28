"""
This directory (openedx/core/lib) contains packages of utilities used by
both LMS and CMS. Packages with models should go in openedx/core/djangoapps instead.
Packages that are LMS-specific or CMS-specific should be in lms/ or cms/ instead.

This particular module contains a small handful of broadly useful utility functions.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_LMS_URLCONF = 'lms.urls'
_CMS_URLCONF = 'cms.urls'


def ensure_lms(message: str = "This code may only be called by LMS, but it was called by CMS"):
    """
    Assert that we're configured as LMS.

    Useful if you want to forbid learner/instructor-oriented code from accidentally
    running in the CMS process.
    """
    if settings.ROOT_URLCONF != _LMS_URLCONF:
        raise ImproperlyConfigured(
            f"{message}. Expected ROOT_URLCONF to be '{_LMS_URLCONF}', got '{settings.ROOT_URLCONF}'"
        )


def ensure_cms(message: str = "This code may only be called by CMS, but it was called by LMS"):
    """
    Assert that we're configured as CMS.

    Useful if you want to forbid authoring-oriented code from accidentally
    running in the LMS process.
    """
    if settings.ROOT_URLCONF != _CMS_URLCONF:
        raise ImproperlyConfigured(
            f"{message}. Expected ROOT_URLCONF to be '{_CMS_URLCONF}', got '{settings.ROOT_URLCONF}'"
        )
