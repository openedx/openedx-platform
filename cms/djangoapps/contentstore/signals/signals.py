"""
Contentstore signals
"""


from django.dispatch import Signal  # noqa: I001

# Signal that indicates that a course grading policy has been updated.
# This signal is generated when a grading policy change occurs within
# modulestore for either course or subsection changes.
# providing_args=[
#   'user_id',  # Integer User ID
#   'course_key',  # Unicode string representing the course
# ]
GRADING_POLICY_CHANGED = Signal()
