"""
This file contains all the classes used by has_access for error handling
"""


from django.utils.translation import gettext as _

from xmodule.course_metadata_utils import DEFAULT_START_DATE


class AccessResponse:
    """Class that represents a response from a has_access permission check."""
    def __init__(self, has_access, error_code=None, developer_message=None, user_message=None,
                 additional_context_user_message=None, user_fragment=None):
        """
        Creates an AccessResponse object.

        Arguments:
            has_access (bool): if the user is granted access or not
            error_code (String): optional - default is None. Unique identifier
                for the specific type of error
            developer_message (String): optional - default is None. Message
                to show the developer
            user_message (String): optional - default is None. Message to
                show the user
            additional_context_user_message (String): optional - default is None. Message to
                show the user when additional context like the course name is necessary
            user_fragment (:py:class:`~web_fragments.fragment.Fragment`): optional -
                An html fragment to display to the user if their access is denied
        """
        self.has_access = has_access
        self.error_code = error_code
        self.developer_message = developer_message
        self.user_message = user_message
        self.additional_context_user_message = additional_context_user_message
        self.user_fragment = user_fragment
        if has_access:
            assert error_code is None

    def __bool__(self):
        """
        Overrides bool().

        Allows for truth value testing of AccessResponse objects, so callers
        who do not need the specific error information can check if access
        is granted.

        Returns:
            bool: whether or not access is granted

        """
        return self.has_access

    __nonzero__ = __bool__

    def to_json(self):
        """
        Creates a serializable JSON representation of an AccessResponse object.

        Returns:
            dict: JSON representation
        """
        return {
            "has_access": self.has_access,
            "error_code": self.error_code,
            "developer_message": self.developer_message,
            "user_message": self.user_message,
            "additional_context_user_message": self.additional_context_user_message,
            "user_fragment": self.user_fragment,
        }

    def __repr__(self):
        return "AccessResponse({!r}, {!r}, {!r}, {!r}, {!r}, {!r})".format(  # noqa: UP032
            self.has_access,
            self.error_code,
            self.developer_message,
            self.user_message,
            self.additional_context_user_message,
            self.user_fragment,
        )

    def __eq__(self, other):
        if not isinstance(other, AccessResponse):
            return False

        return (
            self.has_access == other.has_access and
            self.error_code == other.error_code and
            self.developer_message == other.developer_message and
            self.user_message == other.user_message and
            self.additional_context_user_message == other.additional_context_user_message and
            self.user_fragment == other.user_fragment
        )


class AccessError(AccessResponse):
    """
    Class that holds information about the error in the case of an access
    denial in has_access. Contains the error code, user and developer
    messages. Subclasses represent specific errors.
    """
    def __init__(self, error_code, developer_message, user_message,
                 additional_context_user_message=None, user_fragment=None):
        """
        Creates an AccessError object.

        An AccessError object represents an AccessResponse where access is
        denied (has_access is False).

        Arguments:
            error_code (String): unique identifier for the specific type of
            error developer_message (String): message to show the developer
            user_message (String): message to show the user
            additional_context_user_message (String): message to show user with additional context like the course name
            user_fragment (:py:class:`~web_fragments.fragment.Fragment`): HTML to show the user

        """
        super().__init__(False, error_code, developer_message,
                         user_message, additional_context_user_message, user_fragment)


class StartDateError(AccessError):
    """
    Access denied because the course has not started yet and the user
    is not staff
    """
    def __init__(self, start_date, display_error_to_user=True):
        """
        Arguments:
            display_error_to_user: If True, display this error to users in the UI.
        """
        error_code = "course_not_started"
        if start_date == DEFAULT_START_DATE:
            developer_message = "Course has not started"
            user_message = _("Course has not started")
        else:
            developer_message = f"Course does not start until {start_date}"
            user_message = _("Course does not start until {}"  # pylint: disable=translation-of-non-string
                             .format(f"{start_date:%B %d, %Y}"))
        super().__init__(
            error_code,
            developer_message,
            user_message if display_error_to_user else None
        )


class StartDateEnterpriseLearnerError(AccessError):
    """
    Access denied because the course has not started yet and the user is not staff.  Use this error when this user is
    also an enterprise learner and enrolled in the requested course.
    """
    def __init__(self, start_date, display_error_to_user=True):
        """
        Arguments:
            display_error_to_user: If True, display this error to users in the UI.
        """
        error_code = "course_not_started_enterprise_learner"
        if start_date == DEFAULT_START_DATE:
            developer_message = "Course has not started, and the learner is enrolled via an enterprise subsidy."
            user_message = _("Course has not started")
        else:
            developer_message = (
                f"Course does not start until {start_date}, and the learner is enrolled via an enterprise subsidy."
            )
            user_message = _("Course does not start until {}"  # pylint: disable=translation-of-non-string
                             .format(f"{start_date:%B %d, %Y}"))
        super().__init__(
            error_code,
            developer_message,
            user_message if display_error_to_user else None
        )


class MilestoneAccessError(AccessError):
    """
    Access denied because the user has unfulfilled milestones
    """
    def __init__(self):
        error_code = "unfulfilled_milestones"
        developer_message = "User has unfulfilled milestones"
        user_message = _("You have unfulfilled milestones")
        super().__init__(error_code, developer_message, user_message)


class VisibilityError(AccessError):
    """
    Access denied because the user does not have the correct role to view this
    course.
    """
    def __init__(self, display_error_to_user=True):
        """
        Arguments:
            display_error_to_user: Should a message showing that access was denied to this content
                be shown to the user?
        """
        error_code = "not_visible_to_user"
        developer_message = "Course is not visible to this user"
        user_message = _("You do not have access to this course")
        super().__init__(
            error_code,
            developer_message,
            user_message if display_error_to_user else None
        )


class CatalogVisibilityError(AccessError):
    """
    Access denied because the course's catalog_visibility setting prevents
    the user from seeing this course.
    """
    def __init__(self):
        error_code = "not_visible_in_catalog"
        developer_message = "Course catalog visibility setting prevents access"
        user_message = _(
            "This course is not currently accessible. "
            "The course team has restricted access to this content. "
            "Please contact the course team for further assistance."
        )
        super().__init__(error_code, developer_message, user_message)


class MobileAvailabilityError(AccessError):
    """
    Access denied because the course is not available on mobile for the user
    """
    def __init__(self):
        error_code = "mobile_unavailable"
        developer_message = "Course is not available on mobile for this user"
        user_message = _("You do not have access to this course on a mobile device")
        super().__init__(error_code, developer_message, user_message)


class IncorrectPartitionGroupError(AccessError):
    """
    Access denied because the user is not in the correct user subset.
    """
    def __init__(self, partition, user_group, allowed_groups, user_message=None, user_fragment=None):
        error_code = "incorrect_user_group"
        developer_message = "In partition {}, user was in group {}, but only {} are allowed access".format(
            partition.name,
            user_group.name if user_group is not None else user_group,
            ", ".join(group.name for group in allowed_groups),
        )
        super().__init__(
            error_code=error_code,
            developer_message=developer_message,
            user_message=user_message,
            user_fragment=user_fragment
        )


class NoAllowedPartitionGroupsError(AccessError):
    """
    Access denied because the content is not allowed to any group in a partition.
    """
    def __init__(self, partition, user_message=None, user_fragment=None):
        error_code = "no_allowed_user_groups"
        developer_message = f"Group access for {partition.name} excludes all students"
        super().__init__(error_code, developer_message, user_message)


class EnrollmentRequiredAccessError(AccessError):
    """
    Access denied because the user must be enrolled in the course
    """
    def __init__(self):
        error_code = "enrollment_required"
        developer_message = "User must be enrolled in the course"
        user_message = _("You must be enrolled in the course")
        super().__init__(error_code, developer_message, user_message)


class IncorrectActiveEnterpriseAccessError(AccessError):
    """
    Access denied because the user must login with correct enterprise.
    """
    def __init__(self, enrollment_enterprise_name, active_enterprise_name):
        error_code = "incorrect_active_enterprise"
        developer_message = "User active enterprise should be same as EnterpriseCourseEnrollment enterprise."
        user_message = _("You are enrolled in this course with '{enrollment_enterprise_name}'. However, you are "
                         "currently logged in as a '{active_enterprise_name}' user. Please log in with "
                         "'{enrollment_enterprise_name}' to access this course.")
        user_message = user_message.format(
            enrollment_enterprise_name=enrollment_enterprise_name, active_enterprise_name=active_enterprise_name
        )
        super().__init__(error_code, developer_message, user_message)


class DataSharingConsentRequiredAccessError(AccessError):
    """
    Access denied because the user must give Data sharing consent before access it.
    """
    def __init__(self, consent_url):
        error_code = "data_sharing_access_required"
        developer_message = consent_url
        user_message = _("You must give Data Sharing Consent for the course")
        super().__init__(error_code, developer_message, user_message)


class AuthenticationRequiredAccessError(AccessError):
    """
    Access denied because the user must be authenticated to see it
    """
    def __init__(self):
        error_code = "authentication_required"
        developer_message = "User must be authenticated to view the course"
        user_message = _("You must be logged in to see this course")
        super().__init__(error_code, developer_message, user_message)


class OldMongoAccessError(AccessError):
    """
    Access denied because the course is in Old Mongo and we no longer support them. See DEPR-58.
    """
    def __init__(self, courselike):
        error_code = 'old_mongo'
        developer_message = 'Access to Old Mongo courses is unsupported'
        user_message = _('{course_name} is no longer available.').format(
            course_name=courselike.display_name_with_default,
        )
        super().__init__(error_code, developer_message, user_message)
