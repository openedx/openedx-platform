"""
Permissions for Content Libraries (v2, openedx_content-based)
"""
from bridgekeeper import perms, rules
from bridgekeeper.rules import Attribute, ManyRelation, Relation, blanket_rule, in_current_groups, Rule
from django.conf import settings
from django.db.models import Q

from openedx_authz import api as authz_api
from openedx_authz.constants.permissions import VIEW_LIBRARY

from openedx.core.djangoapps.content_libraries.models import ContentLibraryPermission

# Is the user active (and their email verified)?
is_user_active = rules.is_authenticated & rules.is_active
# Is the user global staff?
is_global_staff = is_user_active & rules.is_staff

# Helper rules used to define the permissions below

# Does the user have at least read permission for the specified library?
has_explicit_read_permission_for_library = (
    ManyRelation(
        'permission_grants',
        (Attribute('user', lambda user: user) | Relation('group', in_current_groups))
    )
    # We don't check 'access_level' here because all access levels grant read permission
)
# Does the user have at least author permission for the specified library?
has_explicit_author_permission_for_library = (
    ManyRelation(
        'permission_grants',
        (Attribute('user', lambda user: user) | Relation('group', in_current_groups)) & (
            Attribute('access_level', ContentLibraryPermission.AUTHOR_LEVEL) |
            Attribute('access_level', ContentLibraryPermission.ADMIN_LEVEL)
        )
    )
)
# Does the user have admin permission for the specified library?
has_explicit_admin_permission_for_library = (
    ManyRelation(
        'permission_grants',
        (Attribute('user', lambda user: user) | Relation('group', in_current_groups)) &
        Attribute('access_level', ContentLibraryPermission.ADMIN_LEVEL)
    )
)


# Are we in Studio? (Is there a better or more contextual way to define this, e.g. get from learning context?)
@blanket_rule
def is_studio_request(_):
    return settings.SERVICE_VARIANT == "cms"


@blanket_rule
def is_course_creator(user):
    from cms.djangoapps.course_creators.views import get_course_creator_status

    return get_course_creator_status(user) == 'granted'


class HasPermissionInContentLibraryScope(Rule):
    """Bridgekeeper rule that checks content library permissions via the openedx-authz system.

    This rule integrates the openedx-authz authorization system (backed by Casbin) with
    Bridgekeeper's declarative permission system. It checks if a user has been granted a
    specific permission (action) through their role assignments in the authorization system.

    The rule works by:
    1. Querying the authorization system to find library scopes where the user has this permission
    2. Parsing the library keys (org/slug) from the scopes
    3. Building database filters to match ContentLibrary models with those org/slug combinations

    Attributes:
        permission (PermissionData): The permission object representing the action to check
            (e.g., 'view', 'edit'). This is used to look up scopes in the authorization system.

        filter_keys (list[str]): The Django model fields to use when building QuerySet filters.
            Defaults to ['org', 'slug'] for ContentLibrary models.

            These fields are used to construct the Q object filters that match libraries
            based on the parsed components from library keys in authorization scopes.

            For ContentLibrary, library keys have the format 'lib:ORG:SLUG', which maps to:
            - 'org' -> filters on org__short_name (related Organization model)
            - 'slug' -> filters on slug field

            If filtering by different fields is needed, pass a custom list. For example:
            - ['org', 'slug'] - default for ContentLibrary (filters by org and slug)
            - ['id'] - filter by primary key (for other models)

    Examples:
        Basic usage with default filter_keys:
            >>> from bridgekeeper import perms
            >>> from openedx.core.djangoapps.content_libraries.permissions import HasPermissionInContentLibraryScope
            >>>
            >>> # Uses default filter_keys=['org', 'slug'] for ContentLibrary
            >>> can_view = HasPermissionInContentLibraryScope('view_library')
            >>> perms['libraries.view_library'] = can_view

        Compound permissions with boolean operators:
            >>> from bridgekeeper.rules import Attribute
            >>>
            >>> is_active = Attribute('is_active', True)
            >>> is_staff = Attribute('is_staff', True)
            >>> can_view = HasPermissionInContentLibraryScope('view_library')
            >>>
            >>> # User must be active AND (staff OR have explicit permission)
            >>> perms['libraries.view_library'] = is_active & (is_staff | can_view)

        QuerySet filtering (efficient, database-level):
            >>> from openedx.core.djangoapps.content_libraries.models import ContentLibrary
            >>>
            >>> # Gets all libraries user can view in a single SQL query
            >>> visible_libraries = perms['libraries.view_library'].filter(
            ...     request.user,
            ...     ContentLibrary.objects.all()
            ... )

        Individual object checks:
            >>> library = ContentLibrary.objects.get(org__short_name='DemoX', slug='CSPROB')
            >>> if perms['libraries.view_library'].check(request.user, library):
            ...     # User can view this specific library

    Note:
        The library keys in authorization scopes must have the format 'lib:ORG:SLUG'
        to match the ContentLibrary model's org.short_name and slug fields.
        For example, scope 'lib:DemoX:CSPROB' matches a library with
        org.short_name='DemoX' and slug='CSPROB'.
    """

    def __init__(self, permission: authz_api.PermissionData, filter_keys: list[str] | None = None):
        """Initialize the rule with the action and filter keys to filter on.

        Args:
            permission (PermissionData): The permission to check (e.g., 'view', 'edit').
            filter_keys (list[str]): The model fields to filter on when building QuerySet filters.
                Defaults to ['org', 'slug'] for ContentLibrary.
        """
        self.permission = permission
        self.filter_keys = filter_keys if filter_keys is not None else ["org", "slug"]

    def query(self, user):
        """Convert this rule to a Django Q object for QuerySet filtering.

        Args:
            user: The Django user object (must have a 'username' attribute).

        Returns:
            Q: A Django Q object that can be used to filter a QuerySet.
               The Q object combines multiple conditions using OR (|) operators,
               where each condition matches a library's org and slug fields:
               Q(org__short_name='OrgA' & slug='lib-a') | Q(org__short_name='OrgB' & slug='lib-b')

        Example:
            >>> # User has 'view' permission in scopes: ['lib:OrgA:lib-a', 'lib:OrgB:lib-b']
            >>> rule = HasPermissionInContentLibraryScope('view', filter_keys=['org', 'slug'])
            >>> q = rule.query(user)
            >>> # Results in: Q(org__short_name='OrgA', slug='lib-a') | Q(org__short_name='OrgB', slug='lib-b')
            >>>
            >>> # Apply to queryset
            >>> libraries = ContentLibrary.objects.filter(q)
            >>> # SQL: SELECT * FROM content_library
            >>> #      WHERE (org.short_name='OrgA' AND slug='lib-a')
            >>> #         OR (org.short_name='OrgB' AND slug='lib-b')
        """
        scopes = authz_api.get_scopes_for_user_and_permission(
            user.username,
            self.permission.identifier
        )

        library_keys = [scope.library_key for scope in scopes]

        if not library_keys:
            return Q(pk__in=[])  # No access, return Q that matches nothing

        # Build Q object: OR together (org AND slug) conditions for each library
        query = Q()
        for library_key in library_keys:
            query |= Q(org__short_name=library_key.org, slug=library_key.slug)

        return query

    def check(self, user, instance, *args, **kwargs):  # pylint: disable=arguments-differ
        """Check if user has permission for a specific object instance.

        This method is used for checking permission on individual objects rather
        than filtering a QuerySet. It extracts the scope from the object and
        checks if the user has the required permission in that scope via Casbin.

        Args:
            user: The Django user object (must have a 'username' attribute).
            instance: The Django model instance to check permission for.
            *args: Additional positional arguments (for compatibility with parent signature).
            **kwargs: Additional keyword arguments (for compatibility with parent signature).

        Returns:
            bool: True if the user has the permission in the object's scope,
                  False otherwise.

        Example:
            >>> rule = HasPermissionInContentLibraryScope('view')
            >>> can_view = rule.check(user, library)
            >>> # Checks if user has 'view' permission in scope 'lib:DemoX:CSPROB'
        """
        return authz_api.is_user_allowed(user.username, self.permission.identifier, str(instance.library_key))


########################### Permissions ###########################

# Is the user allowed to view XBlocks from the specified content library
# directly in the LMS, and interact with them?
# Note there is no is_authenticated/is_active check for this one - we allow
# anonymous users to learn if the library allows public learning.
CAN_LEARN_FROM_THIS_CONTENT_LIBRARY = 'content_libraries.learn_from_library'
perms[CAN_LEARN_FROM_THIS_CONTENT_LIBRARY] = (
    # Global staff can learn from any library:
    is_global_staff |
    # Regular and even anonymous users can learn if the library allows public learning:
    Attribute('allow_public_learning', True) |
    # Users/groups who are explicitly granted permission can learn from the library:
    (is_user_active & has_explicit_read_permission_for_library) |
    # Or, in Studio (but not the LMS) any users can access libraries with "public read" permissions:
    (is_studio_request & is_user_active & Attribute('allow_public_read', True))
)

# Is the user allowed to create content libraries?
CAN_CREATE_CONTENT_LIBRARY = 'content_libraries.create_library'
if settings.FEATURES.get('ENABLE_CREATOR_GROUP', False):
    perms[CAN_CREATE_CONTENT_LIBRARY] = is_global_staff | (is_user_active & is_course_creator)
else:
    perms[CAN_CREATE_CONTENT_LIBRARY] = is_global_staff

# Is the user allowed to view the specified content library in Studio,
# including to view the raw OLX and asset files?
CAN_VIEW_THIS_CONTENT_LIBRARY = 'content_libraries.view_library'
perms[CAN_VIEW_THIS_CONTENT_LIBRARY] = is_user_active & (
    # Global staff can access any library
    is_global_staff |
    # Libraries with "public read" permissions can be accessed only by course creators
    (Attribute('allow_public_read', True) & is_course_creator) |
    # Users can access libraries within their authorized scope (via Casbin/role-based permissions)
    HasPermissionInContentLibraryScope(VIEW_LIBRARY) |
    # Fallback to: the user must be part of the library's team (legacy permission system)
    has_explicit_read_permission_for_library
)

# Is the user allowed to edit the specified content library?
CAN_EDIT_THIS_CONTENT_LIBRARY = 'content_libraries.edit_library'
perms[CAN_EDIT_THIS_CONTENT_LIBRARY] = is_user_active & (
    is_global_staff | has_explicit_author_permission_for_library
)

# Is the user allowed to view the users/permissions of the specified content library?
CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM = 'content_libraries.view_library_team'
perms[CAN_VIEW_THIS_CONTENT_LIBRARY_TEAM] = perms[CAN_EDIT_THIS_CONTENT_LIBRARY]

# Is the user allowed to edit the users/permissions of the specified content library?
CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM = 'content_libraries.edit_library_team'
perms[CAN_EDIT_THIS_CONTENT_LIBRARY_TEAM] = is_user_active & (
    is_global_staff | has_explicit_admin_permission_for_library
)

# Is the user allowed to delete the specified content library?
CAN_DELETE_THIS_CONTENT_LIBRARY = 'content_libraries.delete_library'
perms[CAN_DELETE_THIS_CONTENT_LIBRARY] = is_user_active & (
    is_global_staff | has_explicit_admin_permission_for_library
)
