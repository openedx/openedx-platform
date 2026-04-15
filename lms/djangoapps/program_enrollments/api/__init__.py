"""
Python API exposed by the program_enrollments app to other in-process apps.

The functions are split into separate files for code organization, but they
are imported into here so they can be imported directly from
`lms.djangoapps.program_enrollments.api`.

When adding new functions to this API, add them to the appropriate module
within the /api/ folder, and then "expose" them here by importing them.

We use explicit imports here because (1) it hides internal variables in the
sub-modules and (2) it provides a nice catalog of functions for someone
using this API.
"""


from .grades import iter_program_course_grades  # noqa: F401
from .linking import link_program_enrollment_to_lms_user, link_program_enrollments  # noqa: F401
from .reading import (
    fetch_program_course_enrollments,  # noqa: F401
    fetch_program_course_enrollments_by_students,  # noqa: F401
    fetch_program_enrollments,  # noqa: F401
    fetch_program_enrollments_by_student,  # noqa: F401
    fetch_program_enrollments_by_students,  # noqa: F401
    get_external_key_by_user_and_course,  # noqa: F401
    get_org_key_for_program,  # noqa: F401
    get_program_course_enrollment,  # noqa: F401
    get_program_enrollment,  # noqa: F401
    get_provider_slug,  # noqa: F401
    get_saml_providers_for_organization,  # noqa: F401
    get_users_by_external_keys,  # noqa: F401
    get_users_by_external_keys_and_org_key,  # noqa: F401
    is_course_staff_enrollment,  # noqa: F401
)
from .writing import (
    change_program_course_enrollment_status,  # noqa: F401
    change_program_enrollment_status,  # noqa: F401
    create_program_course_enrollment,  # noqa: F401
    create_program_enrollment,  # noqa: F401
    enroll_in_masters_track,  # noqa: F401
    write_program_course_enrollments,  # noqa: F401
    write_program_enrollments,  # noqa: F401
)
