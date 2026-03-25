"""
Miscellaneous waffle switches that both LMS and Studio need to access
"""


from edx_toggles.toggles import WaffleSwitch  # noqa: I001

# Namespace
WAFFLE_NAMESPACE = 'course_experience'

# Switches
# .. toggle_name: course_experience.enable_about_sidebar_html
# .. toggle_implementation: WaffleSwitch
# .. toggle_default: False
# .. toggle_description: Used to determine whether to show custom HTML in the sidebar on the internal course about page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-01-26
ENABLE_COURSE_ABOUT_SIDEBAR_HTML = WaffleSwitch(f'{WAFFLE_NAMESPACE}.enable_about_sidebar_html', __name__)
