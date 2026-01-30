"""
Feature toggles used across the platform. Toggles should only be added to this module if we don't have a better place
for them. Generally speaking, they should be added to the most appropriate app or repo.
"""
from edx_toggles.toggles import SettingToggle

# .. toggle_name: ENTRANCE_EXAMS
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: Enable entrance exams feature. When enabled, students see an exam xblock as the first unit
#   of the course.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-12-01
# .. toggle_tickets: https://openedx.atlassian.net/browse/SOL-40
ENTRANCE_EXAMS = SettingToggle(
    "ENTRANCE_EXAMS", default=False, module_name=__name__
)
