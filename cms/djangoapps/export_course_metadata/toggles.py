"""
Toggles for export_course_metadata app
"""

from edx_toggles.toggles import WaffleFlag

# .. toggle_name: export_course_metadata
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Export of course metadata (initially to s3 for use by braze)
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2021-03-01
# .. toggle_target_removal_date: None
# .. toggle_tickets: AA-461
EXPORT_COURSE_METADATA_FLAG = WaffleFlag('cms.export_course_metadata', __name__)  # pylint: disable=toggle-missing-annotation
