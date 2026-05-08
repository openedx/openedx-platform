"""All Error Types pertaining to Enrollment."""


class CourseEnrollmentError(Exception):
    """Generic Course Enrollment Error.

    Describes any error that may occur when reading or updating enrollment information for a user or a course.

    """

    def __init__(self, msg, data=None):
        super().__init__(msg)
        # Corresponding information to help resolve the error.
        self.data = data


class UserNotFoundError(CourseEnrollmentError):
    pass


class CourseEnrollmentClosedError(CourseEnrollmentError):
    pass


class CourseEnrollmentFullError(CourseEnrollmentError):
    pass


class CourseEnrollmentExistsError(CourseEnrollmentError):  # pylint: disable=missing-class-docstring
    enrollment = None

    def __init__(self, message, enrollment):
        super().__init__(message)
        self.enrollment = enrollment


class CourseModeNotFoundError(CourseEnrollmentError):
    """The requested course mode could not be found."""
    pass  # pylint: disable=unnecessary-pass


class EnrollmentNotFoundError(CourseEnrollmentError):
    """The requested enrollment could not be found."""
    pass  # pylint: disable=unnecessary-pass


class EnrollmentApiLoadError(CourseEnrollmentError):
    """The data API could not be loaded."""
    pass  # pylint: disable=unnecessary-pass


class InvalidEnrollmentAttribute(CourseEnrollmentError):
    """Enrollment Attributes could not be validated"""
    pass  # pylint: disable=unnecessary-pass


class CourseEnrollmentNotUpdatableError(CourseEnrollmentError):
    """The requested enrollment could not be updated."""
