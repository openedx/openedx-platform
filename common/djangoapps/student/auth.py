"""
The application interface to roles which checks whether any user trying to change
authorization has authorization to do so, which infers authorization via role hierarchy
(GlobalStaff is superset of auths of course instructor, ...), which consults the config
to decide whether to check course creator role, and other such functions.
"""


from ccx_keys.locator import CCXBlockUsageLocator, CCXLocator
from django.conf import settings
from django.core.exceptions import PermissionDenied
from opaque_keys.edx.locator import LibraryLocator
from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import COURSES_MANAGE_ADVANCED_SETTINGS

from openedx.core import toggles as core_toggles
from common.djangoapps.student.roles import (
    CourseBetaTesterRole,
    CourseCreatorRole,
    CourseInstructorRole,
    CourseLimitedStaffRole,
    CourseRole,
    CourseStaffRole,
    GlobalStaff,
    LibraryUserRole,
    OrgContentCreatorRole,
    OrgInstructorRole,
    OrgLibraryUserRole,
    OrgStaffRole,
    strict_role_checking,
)

# Studio permissions:
STUDIO_EDIT_ROLES = 8
STUDIO_VIEW_USERS = 4
STUDIO_EDIT_CONTENT = 2
STUDIO_VIEW_CONTENT = 1
STUDIO_NO_PERMISSIONS = 0
# In addition to the above, one is always allowed to "demote" oneself to a lower role within a course, or remove oneself


def is_ccx_course(course_key):
    """
    Check whether the course locator maps to a CCX course; this is important
    because we don't allow access to CCX courses in Studio.
    """
    return isinstance(course_key, CCXLocator) or isinstance(course_key, CCXBlockUsageLocator)  # lint-amnesty, pylint: disable=consider-merging-isinstance


def user_has_role(user, role):
    """
    Check whether this user has access to this role (either direct or implied)
    :param user:
    :param role: an AccessRole
    """
    if not user.is_active:
        return False
    # Do cheapest check first even though it's not the direct one
    if GlobalStaff().has_user(user):
        return True
    # CourseCreator is odd b/c it can be disabled via config
    if isinstance(role, CourseCreatorRole):
        # completely shut down course creation setting
        if settings.FEATURES.get('DISABLE_COURSE_CREATION', False):
            return False
        # wide open course creation setting
        if not settings.FEATURES.get('ENABLE_CREATOR_GROUP', False):
            return True

    if role.has_user(user):
        return True
    # If not, then check inferred permissions
    if (isinstance(role, (CourseStaffRole, CourseBetaTesterRole)) and
            CourseInstructorRole(role.course_key).has_user(user)):
        return True

    return False


def get_user_permissions(user, course_key, org=None, service_variant=None):
    """
    Get the bitmask of permissions that this user has in the given course context.
    Can also set course_key=None and pass in an org to get the user's
    permissions for that organization as a whole.

    :param user: a user
    :param course_key: a CourseKey or None
    :param org: an organization name or None
    :param service_variant: the variant of the service (lms or cms). Permissions may differ between the two,
        see the HACK comment in the function for more details.
    """
    if org is None:
        org = course_key.org
        course_key = course_key.for_branch(None)
    else:
        assert course_key is None
    # No one has studio permissions for CCX courses
    if is_ccx_course(course_key):
        return STUDIO_NO_PERMISSIONS
    all_perms = STUDIO_EDIT_ROLES | STUDIO_VIEW_USERS | STUDIO_EDIT_CONTENT | STUDIO_VIEW_CONTENT
    # global staff, org instructors, and course instructors have all permissions:
    if GlobalStaff().has_user(user) or OrgInstructorRole(org=org).has_user(user):
        return all_perms
    if course_key and user_has_role(user, CourseInstructorRole(course_key)):
        return all_perms
    # HACK: Limited Staff should not have studio read access. However, since many LMS views depend on the
    #  `has_course_author_access` check and `course_author_access_required` decorator, we have to allow write access
    #  by returning STUDIO_EDIT_CONTENT, if the request is made from LMS, until the permissions become more granular.
    #  For example, there could be STUDIO_VIEW_COHORTS and STUDIO_EDIT_COHORTS specifically for the cohorts endpoint,
    #  which is used to display the "Cohorts" tab of the Instructor Dashboard. If the request is made from the CMS,
    #  then STUDIO_NO_PERMISSIONS is returned instead.
    #  The permissions matrix from the RBAC project (https://github.com/openedx/platform-roadmap/issues/246) shows that
    #  the LMS and Studio permissions will be separated as a part of this project. Once this is done (and this code is
    #  not removed during its implementation), we can replace the Limited Staff permissions with more granular ones.
    if course_key and user_has_role(user, CourseLimitedStaffRole(course_key)):
        if (service_variant or settings.SERVICE_VARIANT) == 'lms':
            return STUDIO_EDIT_CONTENT
        else:
            return STUDIO_NO_PERMISSIONS

    # Staff have all permissions except EDIT_ROLES:
    with strict_role_checking():
        if OrgStaffRole(org=org).has_user(user) or (course_key and user_has_role(user, CourseStaffRole(course_key))):
            return STUDIO_VIEW_USERS | STUDIO_EDIT_CONTENT | STUDIO_VIEW_CONTENT

    # Otherwise, for libraries, users can view only:
    if course_key and isinstance(course_key, LibraryLocator):
        if OrgLibraryUserRole(org=org).has_user(user) or user_has_role(user, LibraryUserRole(course_key)):
            return STUDIO_VIEW_USERS | STUDIO_VIEW_CONTENT
    return STUDIO_NO_PERMISSIONS


def has_studio_write_access(user, course_key, service_variant=None):
    """
    Return True if user has studio write access to the given course.
    Note that the CMS permissions model is with respect to courses.
    There is a super-admin permissions if user.is_staff is set.
    Also, since we're unifying the user database between LMS and CAS,
    I'm presuming that the course instructor (formally known as admin)
    will not be in both INSTRUCTOR and STAFF groups, so we have to cascade our
    queries here as INSTRUCTOR has all the rights that STAFF do.

    :param user:
    :param course_key: a CourseKey
    :param service_variant: the variant of the service (lms or cms). Permissions may differ between the two,
        see the comment in get_user_permissions for more details.
    """
    return bool(STUDIO_EDIT_CONTENT & get_user_permissions(user, course_key, service_variant=service_variant))


def has_course_author_access(user, course_key, service_variant=None):
    """
    Old name for has_studio_write_access
    """
    return has_studio_write_access(user, course_key, service_variant=service_variant)


def has_studio_advanced_settings_access(user):
    """
    If DISABLE_ADVANCED_SETTINGS feature is enabled, only Django Superuser
    or Django Staff can access "Advanced Settings".

    By default, this feature is disabled.
    """
    return (
        not settings.FEATURES.get('DISABLE_ADVANCED_SETTINGS', False)
        or user.is_staff
        or user.is_superuser
    )


def has_studio_read_access(user, course_key):
    """
    Return True if user is allowed to view this course/library in studio.
    Will also return True if user has write access in studio (has_course_author_access)

    There is currently no such thing as read-only course access in studio, but
    there is read-only access to content libraries.
    """
    return bool(STUDIO_VIEW_CONTENT & get_user_permissions(user, course_key))


def check_course_advanced_settings_access(user, course_key, access_type='read'):
    """
    Check if user has access to advanced settings for a course.

    Uses openedx-authz when AUTHZ_COURSE_AUTHORING_FLAG is enabled,
    otherwise falls back to legacy permission checks.

    If the DISABLE_ADVANCED_SETTINGS feature flag is on, then authz will not be used for the
    permission check.

    Args:
        user: Django user object
        course_key: CourseKey for the course
        access_type: Type of access to check. Options:
            - 'read': Check studio read access (default)
            - 'write': Check studio write access
            - 'feature_restricted': Check access based on the DISABLE_ADVANCED_SETTINGS feature

    Returns:
        bool: True if user has permission, False otherwise
    """
    if core_toggles.AUTHZ_COURSE_AUTHORING_FLAG.is_enabled(course_key):
        # For feature_restricted access type, check DISABLE_ADVANCED_SETTINGS feature
        if (
            access_type == 'feature_restricted'
            and settings.FEATURES.get('DISABLE_ADVANCED_SETTINGS', False)
        ):
            # When feature is disabled, only staff/superuser can access (bypass authz)
            return user.is_staff or user.is_superuser
        # Otherwise check authz permission
        return authz_api.is_user_allowed(user.username, COURSES_MANAGE_ADVANCED_SETTINGS.identifier, str(course_key))

    # Legacy permission checks
    if access_type == 'read':
        return has_studio_read_access(user, course_key)
    if access_type == 'feature_restricted':
        return has_studio_advanced_settings_access(user)
    if access_type == 'write':
        return has_studio_write_access(user, course_key)

    raise ValueError(f"Invalid access_type: {access_type}")


def is_content_creator(user, org):
    """
    Check if the user has the role to create content.

    This function checks if the User has role to create content
    or if the org is supplied, it checks for Org level course content
    creator.
    """
    return (user_has_role(user, CourseCreatorRole()) or
            user_has_role(user, OrgContentCreatorRole(org=org)))


def add_users(caller, role, *users):
    """
    The caller requests adding the given users to the role. Checks that the caller
    has sufficient authority.

    :param caller: a user
    :param role: an AccessRole
    """
    _check_caller_authority(caller, role)
    role.add_users(*users)


def remove_users(caller, role, *users):
    """
    The caller requests removing the given users from the role. Checks that the caller
    has sufficient authority.

    :param caller: a user
    :param role: an AccessRole
    """
    # can always remove self (at this layer)
    if not(len(users) == 1 and caller == users[0]):
        _check_caller_authority(caller, role)
    role.remove_users(*users)


def update_org_role(caller, role, user, orgs):
    """
    The caller requests updating the Org role for the user. Checks that the caller has
    sufficient authority.

    :param caller: an user
    :param role: an AccessRole class
    :param user: an user for which org roles are updated
    :param orgs: List of organization names to update the org role
    """
    _check_caller_authority(caller, role())
    existing_org_roles = set(role().get_orgs_for_user(user))
    orgs_roles_to_create = list(set(orgs) - existing_org_roles)
    org_roles_to_delete = list(existing_org_roles - set(orgs))
    for org in orgs_roles_to_create:
        role(org=org).add_users(user)
    for org in org_roles_to_delete:
        role(org=org).remove_users(user)


def _check_caller_authority(caller, role):
    """
    Internal function to check whether the caller has authority to manipulate this role
    :param caller: a user
    :param role: an AccessRole
    """
    if not (caller.is_authenticated and caller.is_active):
        raise PermissionDenied
    # superuser
    if GlobalStaff().has_user(caller):
        return

    if isinstance(role, (GlobalStaff, CourseCreatorRole, OrgContentCreatorRole)):  # lint-amnesty, pylint: disable=no-else-raise
        raise PermissionDenied
    elif isinstance(role, CourseRole):  # instructors can change the roles w/in their course
        if not user_has_role(caller, CourseInstructorRole(role.course_key)):
            raise PermissionDenied
