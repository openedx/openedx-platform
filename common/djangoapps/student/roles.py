"""
Classes used to model the roles used in the courseware. Each role is responsible for checking membership,
adding users, removing users, and listing members
"""


import logging
from abc import ABCMeta, abstractmethod
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from opaque_keys.edx.django.models import CourseKeyField
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from openedx_authz.api import users as authz_api
from openedx_authz.api.data import CourseOverviewData, RoleAssignmentData
from openedx_authz.constants import roles as authz_roles

from common.djangoapps.student.models import CourseAccessRole
from common.djangoapps.student.signals.signals import emit_course_access_role_added, emit_course_access_role_removed
from openedx.core.lib.cache_utils import get_cache
from openedx.core.toggles import enable_authz_course_authoring

log = logging.getLogger(__name__)

# A list of registered access roles.
REGISTERED_ACCESS_ROLES = {}

# A mapping of roles to the roles that they inherit permissions from.
ACCESS_ROLES_INHERITANCE = {}

# The key used to store roles for a user in the cache that do not belong to a course or do not have a course id.
ROLE_CACHE_UNGROUPED_ROLES__KEY = 'ungrouped'


def get_authz_role_from_legacy_role(legacy_role: str) -> str:
    return authz_roles.LEGACY_COURSE_ROLE_EQUIVALENCES.get(legacy_role, None)


def get_legacy_role_from_authz_role(authz_role: str) -> str:
    return next((k for k, v in authz_roles.LEGACY_COURSE_ROLE_EQUIVALENCES.items() if v == authz_role), None)


def authz_add_role(user: User, authz_role: str, course_key: str):
    """
    Add a user's role in a course if not already added.
    Args:
        user (User): The user whose role is being changed.
        authz_role (str): The new authorization role to assign (authz role, not legacy).
        course_key (str): The course key where the role change is taking effect.
    """
    course_locator = CourseLocator.from_string(course_key)

    # Check if the user is not already assigned this role for this course
    existing_assignments = authz_api.get_user_role_assignments_in_scope(
        user_external_key=user.username,
        scope_external_key=course_key
    )
    existing_roles = [existing_role.external_key
                      for existing_assignment in existing_assignments
                      for existing_role in existing_assignment.roles]

    if authz_role in existing_roles:
        return

    # Assign new role
    authz_api.assign_role_to_user_in_scope(
        user_external_key=user.username,
        role_external_key=authz_role,
        scope_external_key=course_key
    )
    legacy_role = get_legacy_role_from_authz_role(authz_role)
    emit_course_access_role_added(user, course_locator, course_locator.org, legacy_role)

def authz_get_all_course_assignments_for_user(user: User) -> list[RoleAssignmentData]:
    """
    Get all course assignments for a user.
    """
    assignments = authz_api.get_user_role_assignments(user_external_key=user.username)
    # filter courses only
    filtered_assignments = [
        assignment for assignment in assignments
        if isinstance(assignment.scope, CourseOverviewData)
    ]
    return filtered_assignments

def get_org_from_key(key: str) -> str:
    """
    Get the org from a course key.
    """
    parsed_key = CourseKey.from_string(key)
    return parsed_key.org

def register_access_role(cls):
    """
    Decorator that allows access roles to be registered within the roles module and referenced by their
    string values.

    Assumes that the decorated class has a "ROLE" attribute, defining its type and an optional "BASE_ROLE" attribute,
    defining the role that it inherits permissions from.

    """
    try:
        role_name = cls.ROLE
        REGISTERED_ACCESS_ROLES[role_name] = cls
    except AttributeError:
        log.exception("Unable to register Access Role with attribute 'ROLE'.")

    if base_role := getattr(cls, "BASE_ROLE", None):
        ACCESS_ROLES_INHERITANCE.setdefault(base_role, set()).add(cls.ROLE)

    return cls


@contextmanager
def strict_role_checking():
    """
    Context manager that temporarily disables role inheritance.

    You may want to use it to check if a user has a base role. For example, if a user has `CourseLimitedStaffRole`,
    by enclosing `has_role` call with this context manager, you can check it has the `CourseStaffRole` too. This is
    useful when derived roles have less permissions than their base roles, but users can have both roles at the same.
    """
    OLD_ACCESS_ROLES_INHERITANCE = ACCESS_ROLES_INHERITANCE.copy()
    ACCESS_ROLES_INHERITANCE.clear()
    yield
    ACCESS_ROLES_INHERITANCE.update(OLD_ACCESS_ROLES_INHERITANCE)


def get_role_cache_key_for_course(course_key=None):
    """
    Get the cache key for the course key.
    """
    return str(course_key) if course_key else ROLE_CACHE_UNGROUPED_ROLES__KEY


@dataclass(frozen=True)
class AuthzCompatCourseAccessRole:
    """
    Generic data class for storing CourseAccessRole-compatible data
    to be used inside BulkRoleCache and RoleCache.
    This allows the cache to store both legacy and openedx-authz compatible roles
    """
    user_id: int
    username: str
    org: str
    course_id: str  # Course key
    role: str


def get_authz_compat_course_access_roles_for_user(user: User) -> set[AuthzCompatCourseAccessRole]:
    """
    Retrieve all CourseAccessRole objects for a given user and convert them to AuthzCompatCourseAccessRole objects.
    """
    compat_role_assignments = set()
    assignments = authz_get_all_course_assignments_for_user(user)
    for assignment in assignments:
        for role in assignment.roles:
            legacy_role = get_legacy_role_from_authz_role(authz_role=role.external_key)
            course_key = assignment.scope.external_key
            org = get_org_from_key(course_key)
            compat_role = AuthzCompatCourseAccessRole(
                user_id=user.id,
                username=user.username,
                org=org,
                course_id=course_key,
                role=legacy_role
            )
            compat_role_assignments.add(compat_role)
    return compat_role_assignments


class BulkRoleCache:  # pylint: disable=missing-class-docstring
    """
    This class provides a caching mechanism for roles grouped by users and courses,
    using a nested dictionary structure to optimize lookup performance. The cache structure is designed as follows:

    {
        user_id_1: {
            course_id_1: {role1, role2, role3},  # Set of roles associated with course_id_1
            course_id_2: {role4, role5, role6},  # Set of roles associated with course_id_2
            [ROLE_CACHE_UNGROUPED_ROLES_KEY]: {role7, role8}  # Set of roles not tied to any specific course or library
        },
        user_id_2: { ... }  # Similar structure for another user
    }

    - Each top-level dictionary entry keys by `user_id` to access role data for a specific user.
    - Nested within each user's dictionary, entries are keyed by `course_id` grouping roles by course.
    - The special key `ROLE_CACHE_UNGROUPED_ROLES_KEY` (a constant defined above)
        stores roles that are not associated with any specific course or library.
    """

    CACHE_NAMESPACE = "student.roles.BulkRoleCache"
    CACHE_KEY = 'roles_by_user'

    @classmethod
    def prefetch(cls, users):  # pylint: disable=missing-function-docstring
        roles_by_user = defaultdict(lambda: defaultdict(set))
        get_cache(cls.CACHE_NAMESPACE)[cls.CACHE_KEY] = roles_by_user

        # Legacy roles
        for role in CourseAccessRole.objects.filter(user__in=users).select_related('user'):
            user_id = role.user.id
            course_id = get_role_cache_key_for_course(role.course_id)

            # Add role to the set in roles_by_user[user_id][course_id]
            user_roles_set_for_course = roles_by_user[user_id][course_id]
            compat_role = AuthzCompatCourseAccessRole(
                user_id=role.user.id,
                username=role.user.username,
                org=role.org,
                course_id=role.course_id,
                role=role.role
            )
            user_roles_set_for_course.add(compat_role)

        # openedx-authz roles
        for user in users:
            compat_roles = get_authz_compat_course_access_roles_for_user(user)
            for role in compat_roles:
                course_id = get_role_cache_key_for_course(role.course_id)
                user_roles_set_for_course = roles_by_user[user.id][course_id]
                user_roles_set_for_course.add(compat_role)

        users_without_roles = [u for u in users if u.id not in roles_by_user]
        for user in users_without_roles:
            roles_by_user[user.id] = {}

    @classmethod
    def get_user_roles(cls, user):
        return get_cache(cls.CACHE_NAMESPACE)[cls.CACHE_KEY][user.id]


class RoleCache:
    """
    A cache of the AuthzCompatCourseAccessRoles held by a particular user.
    Internal data structures should be accessed by getter and setter methods;
    don't use `_roles_by_course_id` or `_roles` directly.
    _roles_by_course_id: This is the data structure as saved in the RequestCache.
        It contains all roles for a user as a dict that's keyed by course_id.
        The key ROLE_CACHE_UNGROUPED_ROLES__KEY is used for all roles
        that are not associated with a course.
    _roles: This is a set of all roles for a user, ungrouped. It's used for some types of
        lookups and collected from _roles_by_course_id on initialization
        so that it doesn't need to be recalculated.

    """
    def __init__(self, user):
        try:
            self._roles_by_course_id = BulkRoleCache.get_user_roles(user)
        except KeyError:
            self._roles_by_course_id = {}

            # openedx-authz compatibility implementation
            compat_roles = get_authz_compat_course_access_roles_for_user(user)
            for compat_role in compat_roles:
                course_id = get_role_cache_key_for_course(compat_role.course_id)
                if not self._roles_by_course_id.get(course_id):
                    self._roles_by_course_id[course_id] = set()
                self._roles_by_course_id[course_id].add(compat_role)

            # legacy implementation
            roles = CourseAccessRole.objects.filter(user=user).all()
            for role in roles:
                course_id = get_role_cache_key_for_course(role.course_id)
                if not self._roles_by_course_id.get(course_id):
                    self._roles_by_course_id[course_id] = set()
                compat_role = AuthzCompatCourseAccessRole(
                    user_id=user.id,
                    username=user.username,
                    org=role.org,
                    course_id=role.course_id,
                    role=role.role
                )
                self._roles_by_course_id[course_id].add(compat_role)
        self._roles = set()
        for roles_for_course in self._roles_by_course_id.values():
            self._roles.update(roles_for_course)

    @staticmethod
    def get_roles(role: str) -> set[str]:
        """
        Return the roles that should have the same permissions as the specified role.
        """
        return ACCESS_ROLES_INHERITANCE.get(role, set()) | {role}

    @property
    def all_roles_set(self):
        return self._roles

    @property
    def roles_by_course_id(self):
        return self._roles_by_course_id

    def has_role(self, role, course_id, org):
        """
        Return whether this RoleCache contains a role with the specified role
        or a role that inherits from the specified role, course_id and org.
        """
        course_id_string = get_role_cache_key_for_course(course_id)
        course_roles = self._roles_by_course_id.get(course_id_string, [])
        return any(
            access_role.role in self.get_roles(role) and access_role.org == org
            for access_role in course_roles
        )


class AccessRole(metaclass=ABCMeta):
    """
    Object representing a role with particular access to a resource
    """

    @abstractmethod
    def has_user(self, user):
        """
        Return whether the supplied django user has access to this role.
        """
        return False

    @abstractmethod
    def add_users(self, *users):
        """
        Add the role to the supplied django users.
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def remove_users(self, *users):
        """
        Remove the role from the supplied django users.
        """
        pass  # pylint: disable=unnecessary-pass

    @abstractmethod
    def users_with_role(self):
        """
        Return a django QuerySet for all of the users with this role
        """
        return User.objects.none()


class GlobalStaff(AccessRole):
    """
    The global staff role
    """
    def has_user(self, user):
        return bool(user and user.is_staff)

    def add_users(self, *users):
        for user in users:
            if user.is_authenticated and user.is_active:
                user.is_staff = True
                user.save()

    def remove_users(self, *users):
        for user in users:
            # don't check is_authenticated nor is_active on purpose
            user.is_staff = False
            user.save()

    def users_with_role(self):
        raise Exception("This operation is un-indexed, and shouldn't be used")


class RoleBase(AccessRole):
    """
    Roles by type (e.g., instructor, beta_user) and optionally org, course_key
    """
    def __init__(self, role_name, org='', course_key=None):
        """
        Create role from required role_name w/ optional org and course_key. You may just provide a role
        name if it's a global role (not constrained to an org or course). Provide org if constrained to
        an org. Provide org and course if constrained to a course. Although, you should use the subclasses
        for all of these.
        """
        super().__init__()

        self.org = org
        self.course_key = course_key
        self._role_name = role_name

    # pylint: disable=arguments-differ
    def has_user(self, user, check_user_activation=True):
        """
        Check if the supplied django user has access to this role.

        Arguments:
            user: user to check against access to role
            check_user_activation: Indicating whether or not we need to check
                user activation while checking user roles
        Return:
            bool identifying if user has that particular role or not
        """
        if check_user_activation and not (user.is_authenticated and user.is_active):
            return False

        # pylint: disable=protected-access
        if not hasattr(user, '_roles'):
            # Cache a list of tuples identifying the particular roles that a user has
            # Stored as tuples, rather than django models, to make it cheaper to construct objects for comparison
            user._roles = RoleCache(user)

        return user._roles.has_role(self._role_name, self.course_key, self.org)

    def _authz_add_users(self, users):
        """
        Add the supplied django users to this role.
        AuthZ compatibility layer
        """
        role = get_authz_role_from_legacy_role(self.ROLE)
        # silently ignores anonymous and inactive users so that any that are
        # legit get updated.
        for user in users:
            if user.is_authenticated and user.is_active:
                authz_add_role(
                    user=user,
                    authz_role=role,
                    course_key=str(self.course_key),
                )
                if hasattr(user, '_roles'):
                    del user._roles

    def _legacy_add_users(self, users):
        """
        Add the supplied django users to this role.
        legacy implementation
        """
        # silently ignores anonymous and inactive users so that any that are
        # legit get updated.
        from common.djangoapps.student.models import (  # pylint: disable=redefined-outer-name, reimported
            CourseAccessRole,
        )
        for user in users:
            if user.is_authenticated and user.is_active:
                CourseAccessRole.objects.get_or_create(
                    user=user, role=self._role_name, course_id=self.course_key, org=self.org
                )
                if hasattr(user, '_roles'):
                    del user._roles

    def add_users(self, *users):
        """
        Add the supplied django users to this role.
        """
        if enable_authz_course_authoring(self.course_key):
            self._authz_add_users(users)
        else:
            self._legacy_add_users(users)

    def _authz_remove_users(self, users):
        """
        Remove the supplied django users from this role.
        AuthZ compatibility layer
        """
        usernames = [user.username for user in users]
        role = get_authz_role_from_legacy_role(self.ROLE)
        course_key_str = str(self.course_key)
        course_locator = CourseLocator.from_string(course_key_str)
        authz_api.batch_unassign_role_from_users(
            users=usernames,
            role_external_key=role,
            scope_external_key=course_key_str
        )
        for user in users:
            emit_course_access_role_removed(user, course_locator, course_locator.org, self.ROLE)
            if hasattr(user, '_roles'):
                del user._roles

    def _legacy_remove_users(self, users):
        """
        Remove the supplied django users from this role.
        legacy implementation
        """
        entries = CourseAccessRole.objects.filter(
            user__in=users, role=self._role_name, org=self.org, course_id=self.course_key
        )
        entries.delete()
        for user in users:
            if hasattr(user, '_roles'):
                del user._roles

    def remove_users(self, *users):
        """
        Remove the supplied django users from this role.
        """
        if enable_authz_course_authoring(self.course_key):
            self._authz_remove_users(users)
        else:
            self._legacy_remove_users(users)

    def _authz_users_with_role(self):
        """
        Return a django QuerySet for all of the users with this role
        AuthZ compatibility layer
        """
        role = get_authz_role_from_legacy_role(self.ROLE)
        users_data = authz_api.get_users_for_role_in_scope(
            role_external_key=role,
            scope_external_key=str(self.course_key)
        )
        usernames = [user_data.username for user_data in users_data]
        entries = User.objects.filter(username__in=usernames)
        return entries

    def _legacy_users_with_role(self):
        """
        Return a django QuerySet for all of the users with this role
        legacy implementation
        """
        # Org roles don't query by CourseKey, so use CourseKeyField.Empty for that query
        if self.course_key is None:
            self.course_key = CourseKeyField.Empty
        entries = User.objects.filter(
            courseaccessrole__role=self._role_name,
            courseaccessrole__org=self.org,
            courseaccessrole__course_id=self.course_key
        )
        return entries

    def users_with_role(self):
        """
        Return a django QuerySet for all of the users with this role
        """
        if enable_authz_course_authoring(self.course_key):
            return self._authz_users_with_role()
        else:
            return self._legacy_users_with_role()

    def _authz_get_orgs_for_user(self, user) -> list[str]:
        """
        Returns a list of org short names for the user with given role.
        AuthZ compatibility layer
        """
        role = get_authz_role_from_legacy_role(self._role_name)
        assignments = authz_api.get_user_role_assignments_filtered(
            user_external_key=user.username,
            role_external_key=role,
        )
        orgs = {assignment.scope.org for assignment in assignments if assignment.scope.org is not None}
        return list(orgs)

    def _legacy_get_orgs_for_user(self, user) -> list[str]:
        """
        Returns a list of org short names for the user with given role.
        legacy implementation
        """
        return list(CourseAccessRole.objects.filter(user=user, role=self._role_name).values_list('org', flat=True))

    def get_orgs_for_user(self, user):
        """
        Returns a list of org short names for the user with given role.
        """
        if enable_authz_course_authoring(self.course_key):
            return self._authz_get_orgs_for_user(user)
        else:
            return self._legacy_get_orgs_for_user(user)

    def has_org_for_user(self, user: User, org: str | None = None) -> bool:
        """
        Checks whether a user has a specific role within an org.

        Arguments:
            user: user to check against access to role
            org: optional org to check against access to role,
                if not specified, will return True if the user has access to at least one org
        """
        if enable_authz_course_authoring(self.course_key):
            orgs_with_role = self.get_orgs_for_user(user)
            if org:
                return org in orgs_with_role
            return len(orgs_with_role) > 0
        else:
            # Use ORM query directly for performance
            filter_params = {
                'user': user,
                'role': self._role_name
            }
            if org:
                filter_params['org'] = org
            return CourseAccessRole.objects.filter(**filter_params).exists()

class CourseRole(RoleBase):
    """
    A named role in a particular course
    """
    def __init__(self, role, course_key):
        """
        Args:
            course_key (CourseKey)
        """
        super().__init__(role, course_key.org, course_key)

    @classmethod
    def _authz_course_group_already_exists(cls, course_key):  # pylint: disable=bad-classmethod-argument
        # AuthZ compatibility layer
        return len(authz_api.get_all_user_role_assignments_in_scope(scope_external_key=str(course_key))) > 0

    @classmethod
    def _legacy_course_group_already_exists(cls, course_key):  # pylint: disable=bad-classmethod-argument
        # Legacy implementation
        return CourseAccessRole.objects.filter(org=course_key.org, course_id=course_key).exists()

    @classmethod
    def course_group_already_exists(cls, course_key):  # pylint: disable=bad-classmethod-argument
        """
        Returns whether role assignations for a course already exist
        """
        if enable_authz_course_authoring(course_key):
            return cls._authz_course_group_already_exists(course_key)
        else:
            return cls._legacy_course_group_already_exists(course_key)

    def __repr__(self):
        return f'<{self.__class__.__name__}: course_key={self.course_key}>'


class OrgRole(RoleBase):
    """
    A named role in a particular org independent of course
    """
    def __repr__(self):
        return f'<{self.__class__.__name__}>'


@register_access_role
class CourseStaffRole(CourseRole):
    """A Staff member of a course"""
    ROLE = 'staff'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseLimitedStaffRole(CourseStaffRole):
    """A Staff member of a course without access to Studio."""

    ROLE = 'limited_staff'
    BASE_ROLE = CourseStaffRole.ROLE


@register_access_role
class CourseInstructorRole(CourseRole):
    """A course Instructor"""
    ROLE = 'instructor'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseFinanceAdminRole(CourseRole):
    """A course staff member with privileges to review financial data."""
    ROLE = 'finance_admin'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseSalesAdminRole(CourseRole):
    """A course staff member with privileges to perform sales operations. """
    ROLE = 'sales_admin'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseBetaTesterRole(CourseRole):
    """A course Beta Tester"""
    ROLE = 'beta_testers'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class LibraryUserRole(CourseRole):
    """
    A user who can view a library and import content from it, but not edit it.
    Used in Studio only.
    """
    ROLE = 'library_user'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


class CourseCcxCoachRole(CourseRole):
    """A CCX Coach"""
    ROLE = 'ccx_coach'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseDataResearcherRole(CourseRole):
    """A Data Researcher"""
    ROLE = 'data_researcher'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


class OrgStaffRole(OrgRole):
    """An organization staff member"""
    def __init__(self, *args, **kwargs):
        super().__init__('staff', *args, **kwargs)


class OrgInstructorRole(OrgRole):
    """An organization instructor"""
    def __init__(self, *args, **kwargs):
        super().__init__('instructor', *args, **kwargs)


@register_access_role
class OrgContentCreatorRole(OrgRole):
    """An organization content creator"""

    ROLE = "org_course_creator_group"

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


class OrgLibraryUserRole(OrgRole):
    """
    A user who can view any libraries in an org and import content from them, but not edit them.
    Used in Studio only.
    """
    ROLE = LibraryUserRole.ROLE

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


class OrgDataResearcherRole(OrgRole):
    """A Data Researcher"""
    ROLE = 'data_researcher'

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class CourseCreatorRole(RoleBase):
    """
    This is the group of people who have permission to create new courses (we may want to eventually
    make this an org based role).
    """
    ROLE = "course_creator_group"

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


@register_access_role
class SupportStaffRole(RoleBase):
    """
    Student support team members.
    """
    ROLE = "support"

    def __init__(self, *args, **kwargs):
        super().__init__(self.ROLE, *args, **kwargs)


class UserBasedRole:
    """
    Backward mapping: given a user, manipulate the courses and roles
    """
    def __init__(self, user, role):
        """
        Create a UserBasedRole accessor: for a given user and role (e.g., "instructor")
        """
        self.user = user
        self.role = role

    def has_course(self, course_key):
        """
        Return whether the role's user has the configured role access to the passed course
        """
        if not (self.user.is_authenticated and self.user.is_active):
            return False

        # pylint: disable=protected-access
        if not hasattr(self.user, '_roles'):
            self.user._roles = RoleCache(self.user)

        return self.user._roles.has_role(self.role, course_key, course_key.org)

    def add_course(self, *course_keys):
        """
        Grant this object's user the object's role for the supplied courses
        """
        if self.user.is_authenticated and self.user.is_active:
            authz_role = get_authz_role_from_legacy_role(self.role)
            for course_key in course_keys:
                if enable_authz_course_authoring(course_key):
                    # AuthZ compatibility layer
                    authz_add_role(
                        user=self.user,
                        authz_role=authz_role,
                        course_key=str(course_key),
                    )
                else:
                    entry = CourseAccessRole(user=self.user, role=self.role, course_id=course_key, org=course_key.org)
                    entry.save()
            if hasattr(self.user, '_roles'):
                del self.user._roles
        else:
            raise ValueError("user is not active. Cannot grant access to courses")

    def remove_courses(self, *course_keys):
        """
        Remove the supplied courses from this user's configured role.
        """
        # CourseAccessRoles for courses managed by AuthZ should already be removed, so always doing this is ok
        entries = CourseAccessRole.objects.filter(user=self.user, role=self.role, course_id__in=course_keys)
        entries.delete()
        # Execute bulk delete on AuthZ
        role = get_authz_role_from_legacy_role(self.role)
        for course_key in course_keys:
            course_key_str = str(course_key)
            success = authz_api.unassign_role_from_user(
                user_external_key=self.user.username,
                role_external_key=role,
                scope_external_key=course_key_str
            )
            if success:
                course_locator = CourseLocator.from_string(course_key_str)
                emit_course_access_role_removed(self.user, course_locator, course_locator.org, self.role)

        if hasattr(self.user, '_roles'):
            del self.user._roles

    def courses_with_role(self) -> set[AuthzCompatCourseAccessRole]:
        """
        Return a set of AuthzCompatCourseAccessRole for all of the courses with this user x (or derived from x) role.
        """
        roles = RoleCache.get_roles(self.role)
        legacy_assignments = CourseAccessRole.objects.filter(role__in=roles, user=self.user)

        # Get all assignments for a user to a role
        new_authz_roles = [get_authz_role_from_legacy_role(role) for role in roles]
        all_authz_user_assignments = authz_get_all_course_assignments_for_user(self.user)

        all_assignments = set()

        for legacy_assignment in legacy_assignments:
            for role in roles:
                all_assignments.add(AuthzCompatCourseAccessRole(
                    user_id=self.user.id,
                    username=self.user.username,
                    org=legacy_assignment.org,
                    course_id=legacy_assignment.course_id,
                    role=role
                ))

        for assignment in all_authz_user_assignments:
            for role in assignment.roles:
                if role.external_key not in new_authz_roles:
                    continue
                legacy_role = get_legacy_role_from_authz_role(authz_role=role.external_key)
                course_key = assignment.scope.external_key
                org = get_org_from_key(course_key)
                all_assignments.add(AuthzCompatCourseAccessRole(
                    user_id=self.user.id,
                    username=self.user.username,
                    org=org,
                    course_id=course_key,
                    role=legacy_role
                ))

        return all_assignments

    def has_courses_with_role(self, org: str | None = None) -> bool:
        """
        Return whether this user has any courses with this role and optional org (or derived roles)

        Arguments:
            org (str): Optional org to filter by
        """
        roles = RoleCache.get_roles(self.role)
        # First check if we have any legacy assignment with an optimized ORM query
        filter_params = {
            'user': self.user,
            'role__in': roles
        }
        if org:
            filter_params['org'] = org
        has_legacy_assignments = CourseAccessRole.objects.filter(**filter_params).exists()
        if has_legacy_assignments:
            return True

        # Then check for authz assignments
        new_authz_roles = [get_authz_role_from_legacy_role(role) for role in roles]
        all_authz_user_assignments = authz_get_all_course_assignments_for_user(self.user)

        for assignment in all_authz_user_assignments:
            for role in assignment.roles:
                if role.external_key not in new_authz_roles:
                    continue
                if org is None:
                    # There is at least one assignment, short circuit
                    return True
                course_key = assignment.scope.external_key
                parsed_org = get_org_from_key(course_key)
                if org == parsed_org:
                    return True
        return False
