"""
Constants used throughout the program_enrollments V1 API.
"""


# Captures strings composed of alphanumeric characters a-f and dashes.
PROGRAM_UUID_PATTERN = r'(?P<program_uuid>[A-Fa-f0-9-]+)'

# Maximum number of students that may be enrolled at once.
MAX_ENROLLMENT_RECORDS = 25

# The name of the key that identifies students for POST/PATCH requests
REQUEST_STUDENT_KEY = 'student_key'


class CourseRunProgressStatuses:
    """
    Statuses that a course run can be in with respect to user progress.
    """
    IN_PROGRESS = 'in_progress'
    UPCOMING = 'upcoming'
    COMPLETED = 'completed'

    __ALL__ = (
        IN_PROGRESS,
        UPCOMING,
        COMPLETED,
    )
