"""
Classes used to model the roles used in the courseware. Each role is responsible for checking membership,
adding users, removing users, and listing members
"""


from collections import defaultdict
import logging
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from opaque_keys.edx.django.models import CourseKeyField

from openedx.core.lib.cache_utils import get_cache
from common.djangoapps.student.models import CourseAccessRole

log = logging.getLogger(__name__)

# A list of registered access roles.
REGISTERED_ACCESS_ROLES = {}

# A mapping of roles to the roles that they inherit permissions from.
ACCESS_ROLES_INHERITANCE = {}

# The key used to store roles for a user in the cache that do not belong to a course or do not have a course id.
ROLE_CACHE_UNGROUPED_ROLES__KEY = 'ungrouped'


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


class BulkRoleCache:  # lint-amnesty, pylint: disable=missing-class-docstring
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
    def prefetch(cls, users):  # lint-amnesty, pylint: disable=missing-function-docstring
        roles_by_user = defaultdict(lambda: defaultdict(set))
        get_cache(cls.CACHE_NAMESPACE)[cls.CACHE_KEY] = roles_by_user

        for role in CourseAccessRole.objects.filter(user__in=users).select_related('user'):
            user_id = role.user.id
            course_id = get_role_cache_key_for_course(role.course_id)

            # Add role to the set in roles_by_user[user_id][course_id]
            user_roles_set_for_course = roles_by_user[user_id][course_id]
            user_roles_set_for_course.add(role)

        users_without_roles = [u for u in users if u.id not in roles_by_user]
        for user in users_without_roles:
            roles_by_user[user.id] = {}

    @classmethod
    def get_user_roles(cls, user):
        return get_cache(cls.CACHE_NAMESPACE)[cls.CACHE_KEY][user.id]


class RoleCache:
    """
    A cache of the CourseAccessRoles held by a particular user.
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
            roles = CourseAccessRole.objects.filter(user=user).all()
            for role in roles:
                course_id = get_role_cache_key_for_course(role.course_id)
                if not self._roles_by_course_id.get(course_id):
                    self._roles_by_course_id[course_id] = set()
                self._roles_by_course_id[course_id].add(role)
        self._roles = set()
        for roles_for_course in self._roles_by_course_id.values():
            self._roles.update(roles_for_course)

    @staticmethod
    def get_roles(role):
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
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

    @abstractmethod
    def remove_users(self, *users):
        """
        Remove the role from the supplied django users.
        """
        pass  # lint-amnesty, pylint: disable=unnecessary-pass

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

    def add_users(self, *users):
        """
        Add the supplied django users to this role.
        """
        # silently ignores anonymous and inactive users so that any that are
        # legit get updated.
        from common.djangoapps.student.models import CourseAccessRole  # lint-amnesty, pylint: disable=redefined-outer-name, reimported
        for user in users:
            if user.is_authenticated and user.is_active:
                CourseAccessRole.objects.get_or_create(
                    user=user, role=self._role_name, course_id=self.course_key, org=self.org
                )
                if hasattr(user, '_roles'):
                    del user._roles

    def remove_users(self, *users):
        """
        Remove the supplied django users from this role.
        """
        entries = CourseAccessRole.objects.filter(
            user__in=users, role=self._role_name, org=self.org, course_id=self.course_key
        )
        entries.delete()
        for user in users:
            if hasattr(user, '_roles'):
                del user._roles

    def users_with_role(self):
        """
        Return a django QuerySet for all of the users with this role
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

    def get_orgs_for_user(self, user):
        """
        Returns a list of org short names for the user with given role.
        """
        return CourseAccessRole.objects.filter(user=user, role=self._role_name).values_list('org', flat=True)


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
    def course_group_already_exists(self, course_key):  # lint-amnesty, pylint: disable=bad-classmethod-argument
        return CourseAccessRole.objects.filter(org=course_key.org, course_id=course_key).exists()

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
            for course_key in course_keys:
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
        entries = CourseAccessRole.objects.filter(user=self.user, role=self.role, course_id__in=course_keys)
        entries.delete()
        if hasattr(self.user, '_roles'):
            del self.user._roles

    def courses_with_role(self):
        """
        Return a django QuerySet for all of the courses with this user x (or derived from x) role. You can access
        any of these properties on each result record:
        * user (will be self.user--thus uninteresting)
        * org
        * course_id
        * role (will be self.role--thus uninteresting)
        """
        return CourseAccessRole.objects.filter(role__in=RoleCache.get_roles(self.role), user=self.user)
