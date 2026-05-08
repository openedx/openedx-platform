"""
This file contains (or should), all access control logic for the courseware.
Ideally, it will be the only place that needs to know about any special settings
like DISABLE_START_DATES.

Note: The access control logic in this file does NOT check for enrollment in
  a course.  It is expected that higher layers check for enrollment so we
  don't have to hit the enrollments table on every block load.

  If enrollment is to be checked, use get_course_with_access in courseware.courses.
  It is a wrapper around has_access that additionally checks for enrollment.
"""


import logging

from django.conf import settings  # pylint: disable=unused-import
from django.contrib.auth.models import AnonymousUser
from edx_django_utils.monitoring import function_trace
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_authz.constants.permissions import COURSES_VIEW_COURSE
from xblock.core import XBlock

from common.djangoapps.student import auth
from common.djangoapps.student.models import CourseEnrollmentAllowed
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseCcxCoachRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseStaffRole,
    GlobalStaff,
    OrgInstructorRole,
    OrgStaffRole,
    SupportStaffRole,
)
from common.djangoapps.util import (  # pylint: disable=useless-import-alias
    milestones_helpers as milestones_helpers,
)
from common.djangoapps.util.milestones_helpers import (
    any_unfulfilled_milestones,
    get_pre_requisite_courses_not_completed,
    is_prerequisite_courses_enabled,
)
from lms.djangoapps.ccx.custom_exception import CCXLocatorValidationException
from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.courseware.access_response import (
    CatalogVisibilityError,
    IncorrectPartitionGroupError,
    MilestoneAccessError,
    MobileAvailabilityError,
    NoAllowedPartitionGroupsError,
    OldMongoAccessError,
    VisibilityError,
)
from lms.djangoapps.courseware.access_utils import (
    ACCESS_DENIED,
    ACCESS_GRANTED,
    check_course_open_for_learner,
    check_start_date,
    debug,
)
from lms.djangoapps.courseware.masquerade import get_masquerade_role, is_masquerading_as_student
from lms.djangoapps.courseware.toggles import course_is_invitation_only
from lms.djangoapps.mobile_api.models import IgnoreMobileAvailableFlagConfig
from openedx.core import toggles as core_toggles
from openedx.core.djangoapps.authz.decorators import user_has_course_permission
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.features.course_duration_limits.access import check_course_expired
from xmodule.course_block import (  # pylint: disable=wrong-import-order
    CATALOG_VISIBILITY_ABOUT,
    CATALOG_VISIBILITY_CATALOG_AND_ABOUT,
    CourseBlock,
)
from xmodule.error_block import ErrorBlock  # pylint: disable=wrong-import-order
from xmodule.partitions.partitions import (  # pylint: disable=wrong-import-order
    NoSuchUserPartitionError,
    NoSuchUserPartitionGroupError,
)

log = logging.getLogger(__name__)


def has_ccx_coach_role(user, course_key):
    """
    Check if user is a coach on this ccx.

    Arguments:
        user (User): the user whose course access we are checking.
        course_key (CCXLocator): Key to CCX.

    Returns:
        bool: whether user is a coach on this ccx or not.
    """
    if hasattr(course_key, 'ccx'):
        ccx_id = course_key.ccx
        role = CourseCcxCoachRole(course_key)

        if role.has_user(user):
            list_ccx = CustomCourseForEdX.objects.filter(
                course_id=course_key.to_course_locator(),
                coach=user
            )
            if list_ccx.exists():
                coach_ccx = list_ccx[0]
                return str(coach_ccx.id) == ccx_id
    else:
        raise CCXLocatorValidationException("Invalid CCX key. To verify that "
                                            "user is a coach on CCX, you must provide key to CCX")
    return False


def has_cms_access(user, course_key):
    """
    Check if user has access to the CMS. When requesting from the LMS, a user with the
    limited staff access role needs access to the CMS APIs, but not the CMS site. This
    function accounts for this edge case when determining if a user has access to the CMS
    site.

    Arguments:
        user (User): the user whose course access we are checking.
        course_key: Key to course.

    Returns:
        bool: whether user has access to the CMS site.
    """
    has_course_author_access = auth.has_course_author_access(user, course_key)
    is_limited_staff = auth.user_has_role(
        user, CourseLimitedStaffRole(course_key)
    ) and not GlobalStaff().has_user(user)

    if is_limited_staff and has_course_author_access:
        return False

    return has_course_author_access


@function_trace('has_access')
def has_access(user, action, obj, course_key=None):
    """
    Check whether a user has the access to do action on obj.  Handles any magic
    switching based on various settings.

    Things this module understands:
    - start dates for blocks
    - visible_to_staff_only for blocks
    - DISABLE_START_DATES
    - different access for instructor, staff, course staff, and students.
    - mobile_available flag for course blocks

    user: a Django user object. May be anonymous. If none is passed,
                    anonymous is assumed

    obj: The object to check access for.  A block, location, or
                    certain special strings (e.g. 'global')

    action: A string specifying the action that the client is trying to perform.

    actions depend on the obj type, but include e.g. 'enroll' for courses.  See the
    type-specific functions below for the known actions for that type.

    course_key: A course_key specifying which course run this access is for.
        Required when accessing anything other than a CourseBlock, 'global',
        or a location with category 'course'

    Returns an AccessResponse object.  It is up to the caller to actually
    deny access in a way that makes sense in context.
    """
    # Just in case user is passed in as None, make them anonymous
    if not user:
        user = AnonymousUser()

    # delegate the work to type-specific functions.
    # (start with more specific types, then get more general)
    if isinstance(obj, CourseBlock):
        return _has_access_course(user, action, obj)

    if isinstance(obj, CourseOverview):
        return _has_access_course(user, action, obj)

    if isinstance(obj, ErrorBlock):
        return _has_access_error_block(user, action, obj, course_key)

    # NOTE: any block access checkers need to go above this
    if isinstance(obj, XBlock):
        return _has_access_to_block(user, action, obj, course_key)

    if isinstance(obj, CourseKey):
        return _has_access_course_key(user, action, obj)

    if isinstance(obj, UsageKey):
        return _has_access_location(user, action, obj, course_key)

    if isinstance(obj, str):
        return _has_access_string(user, action, obj)

    # Passing an unknown object here is a coding error, so rather than
    # returning a default, complain.
    raise TypeError("Unknown object type in has_access(): '{}'"  # noqa: UP032
                    .format(type(obj)))


def has_staff_access_to_preview_mode(user, course_key):
    """
    Checks if given user can access course in preview mode.
    A user can access a course in preview mode only if User has staff access to course.
    """
    has_admin_access_to_course = any(administrative_accesses_to_course_for_user(user, course_key))

    return has_admin_access_to_course or is_masquerading_as_student(user, course_key)


def _can_view_courseware_with_prerequisites(user, course):
    """
    Checks if a user has access to a course based on its prerequisites.

    If the user is staff or anonymous, immediately grant access.
    Else, return whether or not the prerequisite courses have been passed.

    Arguments:
        user (User): the user whose course access we are checking.
        course (AType): the course for which we are checking access.
            where AType is CourseBlock, CourseOverview, or any other
            class that represents a course and has the attributes .location
            and .id.
    """

    def _is_prerequisites_disabled():
        """
        Checks if prerequisites are disabled in the settings.
        """
        return ACCESS_DENIED if is_prerequisite_courses_enabled() else ACCESS_GRANTED

    return (
        _is_prerequisites_disabled()
        or _has_staff_access_to_block(user, course, course.id)
        or user.is_anonymous
        or _has_fulfilled_prerequisites(user, [course.id])
    )


@function_trace('_can_load_course_on_mobile')
def _can_load_course_on_mobile(user, course):
    """
    Checks if a user can view the given course on a mobile device.

    This function only checks mobile-specific access restrictions. Other access
    restrictions such as start date and the .visible_to_staff_only flag must
    be checked by callers in *addition* to the return value of this function.

    Arguments:
        user (User): the user whose course access we are checking.
        course (CourseBlock|CourseOverview): the course for which we are
            checking access.

    Returns:
        bool: whether the course can be accessed on mobile.
    """
    return (
        is_mobile_available_for_user(user, course) and
        (
            _has_staff_access_to_block(user, course, course.id) or
            _has_fulfilled_all_milestones(user, course.id)
        )
    )


def _can_enroll_courselike(user, courselike):
    """
    Ascertain if the user can enroll in the given courselike object.

    Arguments:
        user (User): The user attempting to enroll.
        courselike (CourseBlock or CourseOverview): The object representing the
            course in which the user is trying to enroll.

    Returns:
        AccessResponse, indicating whether the user can enroll.
    """
    # Courselike objects (CourseBlock and CourseOverview) have an attribute named `id`
    # which actually points to a CourseKey. Sigh.
    course_key = courselike.id

    course_enrollment_open = courselike.is_enrollment_open()

    user_has_staff_access = _has_staff_access_to_block(user, courselike, course_key)

    # If the user appears in CourseEnrollmentAllowed paired with the given course key,
    # they may enroll, except if the CEA has already been used by a different user.
    # Note that as dictated by the legacy database schema, the filter call includes
    # a `course_id` kwarg which requires a CourseKey.
    if user is not None and user.is_authenticated:
        cea = CourseEnrollmentAllowed.objects.filter(email=user.email, course_id=course_key).first()
        if cea:
            # DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED flag is used to disable enrollment for user invited
            # to a course if user is registering when the course enrollment is closed
            if (
                settings.FEATURES.get('DISABLE_ALLOWED_ENROLLMENT_IF_ENROLLMENT_CLOSED') and
                not course_enrollment_open and
                not user_has_staff_access
            ):
                return ACCESS_DENIED
            elif cea.valid_for_user(user):
                return ACCESS_GRANTED
            else:
                debug("Deny: CEA was already consumed by a different user {} and can't be used again by {}".format(  # noqa: UP032  # pylint: disable=line-too-long
                    cea.user.id,
                    user.id,
                ))
                return ACCESS_DENIED

    if user_has_staff_access:
        return ACCESS_GRANTED

    # Access denied when the course requires an invitation
    if course_is_invitation_only(courselike):
        debug("Deny: invitation only")
        return ACCESS_DENIED

    if course_enrollment_open:
        debug("Allow: in enrollment period")
        return ACCESS_GRANTED

    return ACCESS_DENIED


@function_trace('_has_access_course')
def _has_access_course(user, action, courselike):
    """
    Check if user has access to a course.

    Arguments:
        user (User): the user whose course access we are checking.
        action (string): The action that is being checked.
        courselike (CourseBlock or CourseOverview): The object
            representing the course that the user wants to access.

    Valid actions:

    'load' -- load the courseware, see inside the course
    'load_forum' -- can load and contribute to the forums (one access level for now)
    'load_mobile' -- can load from a mobile context
    'enroll' -- enroll.  Checks for enrollment window.
    'see_exists' -- can see that the course exists.
    'staff' -- staff access to course.
    'see_in_catalog' -- user is able to see the course listed in the course catalog.
    'see_about_page' -- user is able to see the course about page.
    """
    @function_trace('can_load')
    def can_load():
        """
        Can this user load this course?

        NOTE: this is not checking whether user is actually enrolled in the course.
        """
        # N.B. I'd love a better way to handle this pattern, without breaking the
        # shortcircuiting logic. Maybe AccessResponse needs to grow a
        # fluent interface?
        #
        # return (
        #     _visible_to_nonstaff_users(courselike).and(
        #         check_course_open_for_learner, user, courselike
        #     ).and(
        #         _can_view_courseware_with_prerequisites, user, courselike
        #     )
        # ).or(
        #     _has_staff_access_to_block, user, courselike, courselike.id
        # )
        if courselike.id.deprecated:  # we no longer support accessing Old Mongo courses
            return OldMongoAccessError(courselike)

        visible_to_nonstaff = _visible_to_nonstaff_users(courselike)
        if not visible_to_nonstaff:
            staff_access = _has_staff_access_to_block(user, courselike, courselike.id)
            if staff_access:
                return staff_access
            else:
                return visible_to_nonstaff

        open_for_learner = check_course_open_for_learner(user, courselike)
        if not open_for_learner:
            staff_access = _has_staff_access_to_block(user, courselike, courselike.id)
            if staff_access:
                return staff_access
            else:
                return open_for_learner

        view_with_prereqs = _can_view_courseware_with_prerequisites(user, courselike)
        if not view_with_prereqs:
            staff_access = _has_staff_access_to_block(user, courselike, courselike.id)
            if staff_access:
                return staff_access
            else:
                return view_with_prereqs

        has_not_expired = check_course_expired(user, courselike)
        if not has_not_expired:
            staff_access = _has_staff_access_to_block(user, courselike, courselike.id)
            if staff_access:
                return staff_access
            else:
                return has_not_expired

        return ACCESS_GRANTED

    @function_trace('can_enroll')
    def can_enroll():
        """
        Returns whether the user can enroll in the course.
        """
        return _can_enroll_courselike(user, courselike)

    @function_trace('see_exists')
    def see_exists():
        """
        Can see if can enroll, but also if can load it: if user enrolled in a course and now
        it's past the enrollment period, they should still see it.
        """
        return ACCESS_GRANTED if (can_load() or can_enroll()) else ACCESS_DENIED

    @function_trace('can_see_in_catalog')
    def can_see_in_catalog():
        """
        Implements the "can see course in catalog" logic if a course should be visible in the main course catalog
        In this case we use the catalog_visibility property on the course block
        but also allow course staff to see this.
        """
        catalog_response = _has_catalog_visibility(courselike, CATALOG_VISIBILITY_CATALOG_AND_ABOUT)
        if catalog_response:
            return ACCESS_GRANTED
        if _has_staff_access_to_block(user, courselike, courselike.id):
            return ACCESS_GRANTED
        # Return the typed CatalogVisibilityError so downstream handlers
        # can provide a meaningful error message instead of a generic 404.
        return catalog_response

    def legacy_can_see_about_page():
        both_response = _has_catalog_visibility(courselike, CATALOG_VISIBILITY_CATALOG_AND_ABOUT)
        if both_response:
            return ACCESS_GRANTED
        about_response = _has_catalog_visibility(courselike, CATALOG_VISIBILITY_ABOUT)
        if about_response:
            return ACCESS_GRANTED
        if _has_staff_access_to_block(user, courselike, courselike.id):
            return ACCESS_GRANTED
        # Return the typed CatalogVisibilityError so downstream handlers
        # can provide a meaningful error message instead of a generic 404.
        return both_response

    @function_trace('can_see_about_page')
    def can_see_about_page():
        """
        Implements the "can see course about page" logic if a course about page should be visible
        In this case we use the catalog_visibility property on the course block
        but also allow course staff to see this.
        """
        if user and not user.is_anonymous and core_toggles.enable_authz_course_authoring(courselike.id):
            is_authz_allowed = user_has_course_permission(user, COURSES_VIEW_COURSE.identifier, courselike.id)
            return ACCESS_GRANTED if is_authz_allowed else CatalogVisibilityError()
        return legacy_can_see_about_page()

    checkers = {
        'load': can_load,
        'load_mobile': lambda: can_load() and _can_load_course_on_mobile(user, courselike),
        'enroll': can_enroll,
        'see_exists': see_exists,
        'staff': lambda: _has_staff_access_to_block(user, courselike, courselike.id),
        'instructor': lambda: _has_instructor_access_to_block(user, courselike, courselike.id),
        'see_in_catalog': can_see_in_catalog,
        'see_about_page': can_see_about_page,
    }

    return _dispatch(checkers, action, user, courselike)


def _has_access_error_block(user, action, block, course_key):
    """
    Only staff should see error blocks.

    Valid actions:
    'load' -- load this block, showing it to the user.
    'staff' -- staff access to block.
    """
    def check_for_staff():
        return _has_staff_access_to_block(user, block, course_key)

    checkers = {
        'load': check_for_staff,
        'staff': check_for_staff,
        'instructor': lambda: _has_instructor_access_to_block(user, block, course_key)
    }

    return _dispatch(checkers, action, user, block)


def _has_group_access(block, user, course_key):
    """
    This function returns a boolean indicating whether or not `user` has
    sufficient group memberships to "load" a block
    """
    # Allow staff and instructors roles group access, as they are not masquerading as a student.
    if get_user_role(user, course_key) in ['staff', 'instructor']:
        return ACCESS_GRANTED

    # use merged_group_access which takes group access on the block's
    # parents / ancestors into account
    merged_access = block.merged_group_access

    # resolve the partition IDs in group_access to actual
    # partition objects, skipping those which contain empty group directives.
    # If a referenced partition could not be found, it will be denied
    # If the partition is found but is no longer active (meaning it's been disabled)
    # then skip the access check for that partition.
    partitions = []
    for partition_id, group_ids in merged_access.items():
        try:
            partition = block._get_user_partition(partition_id)  # pylint: disable=protected-access

            # check for False in merged_access, which indicates that at least one
            # partition's group list excludes all students.
            if group_ids is False:
                log.warning("Group access check excludes all students, access will be denied.", exc_info=True)
                return NoAllowedPartitionGroupsError(partition)

            if partition.active:
                if group_ids is not None:
                    partitions.append(partition)
            else:
                log.debug(
                    "Skipping partition with ID %s in course %s because it is no longer active",
                    partition.id, course_key
                )
        except NoSuchUserPartitionError:
            log.warning("Error looking up user partition, access will be denied.", exc_info=True)
            return ACCESS_DENIED

    # next resolve the group IDs specified within each partition
    partition_groups = []
    try:
        for partition in partitions:
            groups = [
                partition.get_group(group_id)
                for group_id in merged_access[partition.id]
            ]
            if groups:
                partition_groups.append((partition, groups))
    except NoSuchUserPartitionGroupError:
        log.warning("Error looking up referenced user partition group, access will be denied.", exc_info=True)
        return ACCESS_DENIED

    # finally: check that the user has a satisfactory group assignment
    # for each partition.

    # missing_groups is the list of groups that the user is NOT in but would NEED to be in order to be granted access.
    # For each partition there are group(s) of users that are granted access to this content.
    # Below, we loop through each partition and check if the user belongs to one of the appropriate group(s). If they do
    # not that group is added to their list of missing_groups.
    # If missing_groups is empty, the user is granted access.
    # If missing_groups is NOT empty, we generate an error based on one of the particular groups they are missing.
    missing_groups = []
    block_key = block.scope_ids.usage_id
    for partition, groups in partition_groups:
        user_group = partition.scheme.get_group_for_user(
            course_key,
            user,
            partition,
        )
        if user_group not in groups:
            missing_groups.append((
                partition,
                user_group,
                groups,
                partition.access_denied_message(block_key, user, user_group, groups),
                partition.access_denied_fragment(block, user, user_group, groups),
            ))

    if missing_groups:
        # Prefer groups with explanatory messages
        # False < True, so the default order and `is None` results in groups with messages coming first
        ordered_groups = sorted(missing_groups, key=lambda details: (details[3] is None, details[4] is None))
        partition, user_group, allowed_groups, message, fragment = ordered_groups[0]
        return IncorrectPartitionGroupError(
            partition=partition,
            user_group=user_group,
            allowed_groups=allowed_groups,
            user_message=message,
            user_fragment=fragment,
        )

    # all checks passed.
    return ACCESS_GRANTED


def _has_access_to_block(user, action, block, course_key=None):
    """
    Check if user has access to this block.

    Valid actions:
    'load' -- load this block, showing it to the user.
    'staff' -- staff access to block.

    NOTE: This is the fallback logic for blocks that don't have custom policy
    (e.g. courses).  If you call this method directly instead of going through
    has_access(), it will not do the right thing.
    """
    def can_load():
        """
        NOTE: This does not check that the student is enrolled in the course
        that contains this block.  We may or may not want to allow non-enrolled
        students to see blocks.  If not, views should check the course, so we
        don't have to hit the enrollments table on every block load.
        """
        # If the user (or the role the user is currently masquerading as) does not have
        # access to this content, then deny access. The problem with calling _has_staff_access_to_block
        # before this method is that _has_staff_access_to_block short-circuits and returns True
        # for staff users in preview mode.
        group_access_response = _has_group_access(block, user, course_key)
        if not group_access_response:
            return group_access_response

        # If the user has staff access, they can load the block and checks below are not needed.
        staff_access_response = _has_staff_access_to_block(user, block, course_key)
        if staff_access_response:
            return staff_access_response

        return (
            _visible_to_nonstaff_users(block, display_error_to_user=False) and
            (
                _has_detached_class_tag(block) or
                check_start_date(
                    user,
                    block.days_early_for_beta,
                    block.start,
                    course_key,
                    display_error_to_user=False
                )
            )
        )

    checkers = {
        'load': can_load,
        'staff': lambda: _has_staff_access_to_block(user, block, course_key),
        'instructor': lambda: _has_instructor_access_to_block(user, block, course_key)
    }

    return _dispatch(checkers, action, user, block)


def _has_access_location(user, action, location, course_key):
    """
    Check if user has access to this location.

    Valid actions:
    'staff' : True if the user has staff access to this location

    NOTE: if you add other actions, make sure that

     has_access(user, location, action) == has_access(user, get_item(location), action)
    """
    checkers = {
        'staff': lambda: _has_staff_access_to_location(user, location, course_key)
    }

    return _dispatch(checkers, action, user, location)


def _has_access_course_key(user, action, course_key):
    """
    Check if user has access to the course with this course_key

    Valid actions:
    'staff' : True if the user has staff access to this location
    'instructor' : True if the user has staff access to this location
    """
    checkers = {
        'staff': lambda: _has_staff_access_to_location(user, None, course_key),
        'instructor': lambda: _has_instructor_access_to_location(user, None, course_key),
    }

    return _dispatch(checkers, action, user, course_key)


def _has_access_string(user, action, perm):
    """
    Check if user has certain special access, specified as string.  Valid strings:

    'global'

    Valid actions:

    'staff' -- global staff access.
    'support' -- access to student support functionality
    'certificates' --- access to view and regenerate certificates for other users.
    """

    def check_staff():
        """
        Checks for staff access
        """
        if perm != 'global':
            debug("Deny: invalid permission '%s'", perm)
            return ACCESS_DENIED
        return ACCESS_GRANTED if GlobalStaff().has_user(user) else ACCESS_DENIED

    def check_support():
        """Check that the user has access to the support UI. """
        if perm != 'global':
            return ACCESS_DENIED
        return (
            ACCESS_GRANTED if GlobalStaff().has_user(user) or SupportStaffRole().has_user(user)
            else ACCESS_DENIED
        )

    checkers = {
        'staff': check_staff,
        'support': check_support,
        'certificates': check_support,
    }

    return _dispatch(checkers, action, user, perm)


#####  Internal helper methods below

def _dispatch(table, action, user, obj):
    """
    Helper: call table[action], raising a nice pretty error if there is no such key.

    user and object passed in only for error messages and debugging
    """
    if action in table:
        result = table[action]()
        debug("%s user %s, object %s, action %s",
              'ALLOWED' if result else 'DENIED',
              user,
              str(obj.location) if isinstance(obj, XBlock) else str(obj),
              action)
        return result

    raise ValueError("Unknown action for object type '{}': '{}'".format(  # noqa: UP032
        type(obj), action))


def _has_instructor_access_to_location(user, location, course_key=None):
    if course_key is None:
        course_key = location.course_key
    return _has_access_to_course(user, 'instructor', course_key)


def _has_staff_access_to_location(user, location, course_key=None):
    if course_key is None:
        course_key = location.course_key
    return _has_access_to_course(user, 'staff', course_key)


def _has_access_to_course(user, access_level, course_key):
    """
    Returns True if the given user has access_level (= staff or
    instructor) access to the course with the given course_key.
    This ensures the user is authenticated and checks if global staff or has
    staff / instructor access.

    access_level = string, either "staff" or "instructor"
    """
    if user is None or (not user.is_authenticated):
        debug("Deny: no user or anon user")
        return ACCESS_DENIED

    if is_masquerading_as_student(user, course_key):
        return ACCESS_DENIED

    global_staff, staff_access, instructor_access = administrative_accesses_to_course_for_user(user, course_key)

    if global_staff:
        debug("Allow: user.is_staff")
        return ACCESS_GRANTED

    if access_level not in ('staff', 'instructor'):
        log.debug("Error in access._has_access_to_course access_level=%s unknown", access_level)
        debug("Deny: unknown access level")
        return ACCESS_DENIED

    if staff_access and access_level == 'staff':
        debug("Allow: user has course staff access")
        return ACCESS_GRANTED

    if instructor_access and access_level in ('staff', 'instructor'):
        debug("Allow: user has course instructor access")
        return ACCESS_GRANTED

    debug("Deny: user did not have correct access")
    return ACCESS_DENIED


def administrative_accesses_to_course_for_user(user, course_key):
    """
    Returns types of access a user have for given course.
    """
    global_staff = GlobalStaff().has_user(user)

    staff_access = (
        CourseStaffRole(course_key).has_user(user) or
        OrgStaffRole(course_key.org).has_user(user)
    )

    instructor_access = (
        CourseInstructorRole(course_key).has_user(user) or
        OrgInstructorRole(course_key.org).has_user(user)
    )

    return global_staff, staff_access, instructor_access


@function_trace('_has_instructor_access_to_block')
def _has_instructor_access_to_block(user, block, course_key):
    """Helper method that checks whether the user has staff access to
    the course of the location.

    block: something that has a location attribute
    """
    return _has_instructor_access_to_location(user, block.location, course_key)


@function_trace('_has_staff_access_to_block')
def _has_staff_access_to_block(user, block, course_key):
    """Helper method that checks whether the user has staff access to
    the course of the location.

    block: something that has a location attribute
    """
    return _has_staff_access_to_location(user, block.location, course_key)


def _visible_to_nonstaff_users(block, display_error_to_user=True):
    """
    Returns if the object is visible to nonstaff users.

    Arguments:
        block: object to check
        display_error_to_user: If True, show an error message to the user say the content was hidden. Otherwise,
            hide the content silently.
    """
    if block.visible_to_staff_only:
        return VisibilityError(display_error_to_user=display_error_to_user)
    else:
        return ACCESS_GRANTED


def _can_access_block_with_milestones(user, block, course_key):
    """
    Returns if the object is blocked by an unfulfilled milestone.

    Args:
        user: the user trying to access this content
        block: the object being accessed
        course_key: key for the course
    """
    if milestones_helpers.get_course_content_milestones(
        course_key,
        str(block.location),
        'requires',
        user.id
    ):
        debug("Deny: user has not completed all milestones for content")
        return ACCESS_DENIED
    else:
        return ACCESS_GRANTED


def _has_detached_class_tag(block):
    """
    Returns if the given block's type is marked as detached.

    Arguments:
        block: object to check
    """
    return ACCESS_GRANTED if 'detached' in block._class_tags else ACCESS_DENIED  # pylint: disable=protected-access


def _has_fulfilled_all_milestones(user, course_id):
    """
    Returns whether the given user has fulfilled all milestones for the
    given course.

    Arguments:
        course_id: ID of the course to check
        user_id: ID of the user to check
    """
    return MilestoneAccessError() if any_unfulfilled_milestones(course_id, user.id) else ACCESS_GRANTED


def _has_fulfilled_prerequisites(user, course_id):
    """
    Returns whether the given user has fulfilled all prerequisites for the
    given course.

    Arguments:
        user: user to check
        course_id: ID of the course to check
    """
    return MilestoneAccessError() if get_pre_requisite_courses_not_completed(user, course_id) else ACCESS_GRANTED


def _has_catalog_visibility(course, visibility_type):
    """
    Returns whether the given course has the given visibility type
    """
    if course.catalog_visibility == visibility_type:
        return ACCESS_GRANTED
    return CatalogVisibilityError()


def _is_block_mobile_available(block):
    """
    Returns if block is available on mobile.
    """
    if IgnoreMobileAvailableFlagConfig.is_enabled() or block.mobile_available:
        return ACCESS_GRANTED
    else:
        return MobileAvailabilityError()


def is_mobile_available_for_user(user, block):
    """
    Returns whether the given course is mobile_available for the given user.
    Checks:
        mobile_available flag on the course
        Beta User and staff access overrides the mobile_available flag
    Arguments:
        block (CourseBlock|CourseOverview): course or overview of course in question
    """
    return (
        auth.user_has_role(user, CourseBetaTesterRole(block.id))
        or _has_staff_access_to_block(user, block, block.id)
        or _is_block_mobile_available(block)
    )


def get_user_role(user, course_key):
    """
    Return corresponding string if user has staff, instructor or student
    course role in LMS.
    """
    role = get_masquerade_role(user, course_key)
    if role:
        return role
    elif has_access(user, 'instructor', course_key):
        return 'instructor'
    elif has_access(user, 'staff', course_key):
        return 'staff'
    else:
        return 'student'
