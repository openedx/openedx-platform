"""
Constants for course groups.
"""
from django.utils.translation import gettext_lazy as _

COHORT_SCHEME = 'cohort'
RANDOM_SCHEME = 'random'
ENROLLMENT_SCHEME = 'enrollment_track'

CONTENT_GROUP_CONFIGURATION_NAME = _('Content Groups')
CONTENT_GROUP_CONFIGURATION_DESCRIPTION = _(
    'Use this group configuration to control access to content.'
)
