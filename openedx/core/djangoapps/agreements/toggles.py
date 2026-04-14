"""
Toggle for lti pii acknowledgement feature.
"""

from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

# .. toggle_name: agreements.enable_lti_pii_acknowledgement
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Enables the lti pii acknowledgement feature for a course
# .. toggle_warning: None
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2023-10
# .. toggle_target_removal_date: None
# .. toggle_tickets: MST-2055


ENABLE_LTI_PII_ACKNOWLEDGEMENT = CourseWaffleFlag('agreements.enable_lti_pii_acknowledgement', __name__)


def lti_pii_acknowledgment_enabled(course_key):
    """
    Returns a boolean if lti pii acknowledgements are enabled for a course.
    """
    if isinstance(course_key, str):
        course_key = CourseKey.from_string(course_key)
    return ENABLE_LTI_PII_ACKNOWLEDGEMENT.is_enabled(course_key)
