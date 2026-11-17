"""Error types the Grades API v2 can return."""

from edx_rest_framework_extensions.errors import register_error_type
from rest_framework.exceptions import NotFound, PermissionDenied


class WritableGradebookDisabled(PermissionDenied):
    """The writable gradebook is switched off for the course being addressed."""

    default_detail = 'The writable gradebook is not enabled for this course.'
    default_code = 'writable_gradebook_disabled'


class GradesFrozen(PermissionDenied):
    """The course ended long enough ago that its grades may no longer be changed."""

    default_detail = 'Grades are frozen for this course.'
    default_code = 'grades_frozen'


class SubsectionUnavailable(NotFound):
    """The subsection addressed is not available to the learner addressed."""

    default_detail = 'The subsection is not available to this learner.'
    default_code = 'subsection_unavailable'


register_error_type(
    WritableGradebookDisabled,
    'grades/writable-gradebook-disabled',
    'Writable Gradebook Disabled',
)
register_error_type(GradesFrozen, 'grades/grades-frozen', 'Grades Frozen')
register_error_type(SubsectionUnavailable, 'grades/subsection-unavailable', 'Subsection Unavailable')
