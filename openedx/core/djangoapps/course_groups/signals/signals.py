"""
Cohorts related signals.
"""

from django.dispatch import Signal  # noqa: I001

# providing_args=['user', 'course_key']
COHORT_MEMBERSHIP_UPDATED = Signal()
