"""
Toggles for the course blocks app.
"""

from openedx.core.djangoapps.waffle_utils import CourseWaffleFlag

COURSE_BLOCKS_NAMESPACE = "course_blocks"

# .. toggle_name: course_blocks.show_hidden_content_without_links
# .. toggle_implementation: CourseWaffleFlag
# .. toggle_default: False
# .. toggle_description: Changes how the HiddenContentTransformer enforces the `hide_after_due` setting.
#   When disabled, past-due content is removed from the course block structure, so it disappears from the course
#   outline entirely. When enabled, the content stays in the block structure and is flagged instead, so that the
#   outline still lists it, but without links to it. Access to the content itself is unaffected either way because it
#   is enforced by access checks rather than by this transformer.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2026-09-10
SHOW_HIDDEN_CONTENT_WITHOUT_LINKS = CourseWaffleFlag(
    f"{COURSE_BLOCKS_NAMESPACE}.show_hidden_content_without_links", __name__
)
