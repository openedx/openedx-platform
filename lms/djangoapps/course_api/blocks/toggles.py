"""
Toggles for Course API.
"""


from edx_toggles.toggles import WaffleFlag  # noqa: I001

COURSE_BLOCKS_API_NAMESPACE = 'course_blocks_api'

# .. toggle_name: course_blocks_api.hide_access_denials
# .. toggle_implementation: WaffleFlag
# .. toggle_default: False
# .. toggle_description: Waffle flag to hide access denial messages in the course blocks.
# .. toggle_use_cases: temporary, open_edx
# .. toggle_creation_date: 2019-09-27
# .. toggle_target_removal_date: None
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
HIDE_ACCESS_DENIALS_FLAG = WaffleFlag(
    f'{COURSE_BLOCKS_API_NAMESPACE}.hide_access_denials', __name__
)
