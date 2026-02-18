"""Django rules-based permissions for tagging"""

from __future__ import annotations

from typing import Union

import django.contrib.auth.models
import openedx_tagging.rules as oel_tagging
import rules
from organizations.models import Organization

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access
from common.djangoapps.student.role_helpers import get_course_roles, get_role_cache
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    OrgContentCreatorRole,
    OrgInstructorRole,
    OrgLibraryUserRole,
    OrgStaffRole
)

from .models import TaxonomyOrg
from .utils import check_taxonomy_context_key_org, get_context_key_from_key_string, rules_cache


UserType = Union[django.contrib.auth.models.User, django.contrib.auth.models.AnonymousUser]


def is_org_admin(user: UserType, orgs: list[Organization] | None = None) -> bool:
    """
    Return True if the given user is an admin for any of the given orgs.
    """
    return len(get_admin_orgs(user, orgs)) > 0


def is_org_user(user: UserType, orgs: list[Organization]) -> bool:
    """
    Return True if the given user is a member of any of the given orgs.
    """
    return len(get_user_orgs(user, orgs)) > 0


def get_admin_orgs(user: UserType, orgs: list[Organization] | None = None) -> list[Organization]:
    """
    Returns a list of orgs that the given user is an org-level staff, from the given list of orgs.

    If no orgs are provided, check all orgs
    """
    org_list = rules_cache.get_orgs() if orgs is None else orgs
    return [
        org for org in org_list if OrgStaffRole(org=org.short_name).has_user(user)
    ]


def _get_content_creator_orgs(user: UserType, orgs: list[Organization]) -> list[Organization]:
    """
    Returns a list of orgs that the given user is an org-level library user or instructor, from the given list of orgs.
    """
    return [
        org for org in orgs if (
            OrgLibraryUserRole(org=org.short_name).has_user(user) or
            OrgInstructorRole(org=org.short_name).has_user(user) or
            OrgContentCreatorRole(org=org.short_name).has_user(user)
        )
    ]


def _get_course_user_orgs(user: UserType, orgs: list[Organization]) -> list[Organization]:
    """
    Returns a list of orgs for courses where the given user is staff or instructor, from the given list of orgs.

    Note: The user does not have org-level access to these orgs, only course-level access. So when checking ObjectTag
    permissions, ensure that the user has staff/instructor access to the course/library with that object_id.
    """
    if not orgs:
        return []

    def user_has_role_ignore_course_id(user, role_name, org_name) -> bool:
        """
        Returns True if the given user has the given role for the given org, OR for any courses in this org.
        """
        # We use the user's RoleCache here to avoid re-querying.
        roles_cache = get_role_cache(user)
        course_roles = get_course_roles(user)
        return any(
            access_role.role in roles_cache.get_roles(role_name) and
            access_role.org == org_name
            for access_role in course_roles
        )

    return [
        org for org in orgs if (
            user_has_role_ignore_course_id(user, CourseStaffRole.ROLE, org.short_name) or
            user_has_role_ignore_course_id(user, CourseInstructorRole.ROLE, org.short_name)
        )
    ]


def _get_library_user_orgs(user: UserType, orgs: list[Organization]) -> list[Organization]:
    """
    Returns a list of orgs (from the given list of orgs) that are associated with libraries that the given user has
    explicitly been granted access to.

    Note: If no libraries exist for the given orgs, then no orgs will be returned, even though the user may be permitted
    to access future libraries created in these orgs.
    Nor does this mean the user may access all libraries in this org: library permissions are granted per library.
    """
    library_orgs = rules_cache.get_library_orgs(user, [org.short_name for org in orgs])
    return list(set(library_orgs).intersection(orgs))


def get_user_orgs(user: UserType, orgs: list[Organization] | None = None) -> list[Organization]:
    """
    Return a list of orgs that the given user is a member of (instructor or content creator),
    from the given list of orgs.
    """
    org_list = rules_cache.get_orgs() if orgs is None else orgs
    content_creator_orgs = _get_content_creator_orgs(user, org_list)
    course_user_orgs = _get_course_user_orgs(user, org_list)
    library_user_orgs = _get_library_user_orgs(user, org_list)
    user_orgs = list(set(content_creator_orgs) | set(course_user_orgs) | set(library_user_orgs))

    return user_orgs


@rules.predicate
def can_create_taxonomy(user: UserType) -> bool:
    """
    Returns True if the given user can create a taxonomy.

    Taxonomy admins and org-level staff can create taxonomies.
    """
    # Taxonomy admins can view any taxonomy
    if oel_tagging.is_taxonomy_admin(user):
        return True

    # Org-level staff can create taxonomies associated with one of their orgs.
    if is_org_admin(user):
        return True

    return False


@rules.predicate
def can_view_taxonomy(user: UserType, taxonomy: oel_tagging.Taxonomy) -> bool:
    """
    Returns True if the given user can view the given taxonomy.

    Taxonomy admins can view any taxonomy.
    Org-level staff can view any taxonomy that is associated with one of their orgs.
    Org-level course creators and instructors can view any enabled taxonomy that is owned by one of their orgs.
    """
    # The following code allows METHOD permission (GET) in the viewset for everyone
    if not taxonomy:
        return True

    taxonomy = taxonomy.cast()

    # Taxonomy admins can view any taxonomy
    if oel_tagging.is_taxonomy_admin(user):
        return True

    is_all_org, taxonomy_orgs = TaxonomyOrg.get_organizations(taxonomy)

    # Enabled all-org taxonomies can be viewed by any registered user
    if is_all_org:
        return taxonomy.enabled

    # Org-level staff can view any taxonomy that is associated with one of their orgs.
    if is_org_admin(user, taxonomy_orgs):
        return True

    # Org-level course creators and instructors can view any enabled taxonomy that is owned by one of their orgs.
    if is_org_user(user, taxonomy_orgs):
        return taxonomy.enabled

    return False


@rules.predicate
def can_change_taxonomy(user: UserType, taxonomy: oel_tagging.Taxonomy) -> bool:
    """
    Returns True if the given user can edit the given taxonomy.

    System definied taxonomies cannot be edited
    Taxonomy admins can edit any non system defined taxonomies
    Only taxonomy admins can edit all org taxonomies
    Org-level staff can edit any taxonomy that is associated with one of their orgs.
    """
    # The following code allows METHOD permission (PUT, PATCH) in the viewset for everyone
    if not taxonomy:
        return True

    taxonomy = taxonomy.cast()

    # System definied taxonomies cannot be edited
    if taxonomy.system_defined:
        return False

    # Taxonomy admins can edit any non system defined taxonomies
    if oel_tagging.is_taxonomy_admin(user):
        return True

    is_all_org, taxonomy_orgs = TaxonomyOrg.get_organizations(taxonomy)

    # Only taxonomy admins can edit all org taxonomies
    if is_all_org:
        return False

    # Org-level staff can edit any taxonomy that is associated with one of their orgs.
    if is_org_admin(user, taxonomy_orgs):
        return True

    return False


@rules.predicate
def can_change_object_tag_objectid(user: UserType, object_id: str) -> bool:
    """
    Everyone that has permission to edit the object should be able to tag it.
    """
    if not object_id:
        return True

    try:
        context_key = get_context_key_from_key_string(object_id)
        assert context_key.org
    except (ValueError, AssertionError):
        return False

    if has_studio_write_access(user, context_key):
        return True

    object_org = rules_cache.get_orgs([context_key.org])
    return bool(object_org) and is_org_admin(user, object_org)


@rules.predicate
def can_view_object_tag_taxonomy(user: UserType, taxonomy: oel_tagging.Taxonomy) -> bool:
    """
    Only enabled taxonomy and users with permission to view this taxonomy can view object tags
    from that taxonomy.

    This rule is different from can_view_taxonomy because it checks if the taxonomy is enabled.
    """
    # Note: in the REST API, where we're dealing with multiple taxonomies at once, permissions
    # are also enforced by ObjectTagTaxonomyOrgFilterBackend.
    return not taxonomy or (taxonomy.cast().enabled and can_view_taxonomy(user, taxonomy))


@rules.predicate
def can_view_object_tag_objectid(user: UserType, object_id: str) -> bool:
    """
    Everyone that has permission to view the object should be able to view its tags.
    """
    if not object_id:
        raise ValueError("object_id must be provided")

    if not user.is_authenticated:
        return False

    try:
        context_key = get_context_key_from_key_string(object_id)
        assert context_key.org
    except (ValueError, AssertionError):
        return False

    if has_studio_read_access(user, context_key):
        return True

    object_org = rules_cache.get_orgs([context_key.org])
    return bool(object_org) and (is_org_admin(user, object_org) or is_org_user(user, object_org))


@rules.predicate
def can_remove_object_tag_objectid(user: UserType, object_id: str) -> bool:
    """
    Everyone that has permission to edit the object should be able remove tags from it.
    """
    if not object_id:
        raise ValueError("object_id must be provided")

    if not user.is_authenticated:
        return False

    try:
        context_key = get_context_key_from_key_string(object_id)
        assert context_key.org
    except (ValueError, AssertionError):
        return False

    if has_studio_write_access(user, context_key):
        return True

    object_org = rules_cache.get_orgs([context_key.org])
    return bool(object_org) and is_org_admin(user, object_org)


@rules.predicate
def can_change_object_tag(
    user: UserType, perm_obj: oel_tagging.ObjectTagPermissionItem | None = None
) -> bool:
    """
    Returns True if the given user may change object tags with the given taxonomy + object_id.

    Adds additional checks to ensure the taxonomy is available for use with the object_id's org.
    """
    if oel_tagging.can_change_object_tag(user, perm_obj):
        if perm_obj and perm_obj.taxonomy and perm_obj.object_id:
            try:
                context_key = get_context_key_from_key_string(perm_obj.object_id)
            except ValueError:
                return False  # pragma: no cover

            return check_taxonomy_context_key_org(perm_obj.taxonomy, context_key)

        return True
    return False


@rules.predicate
def can_change_taxonomy_tag(user: UserType, tag: oel_tagging.Tag | None = None) -> bool:
    """
    Even taxonomy admins cannot add tags to system taxonomies (their tags are system-defined), or free-text taxonomies
    (these don't have predefined tags).
    """
    taxonomy = tag.taxonomy if tag else None
    if taxonomy:
        taxonomy = taxonomy.cast()
    return oel_tagging.is_taxonomy_admin(user) and (
        not tag
        or not taxonomy
        or (bool(taxonomy) and not taxonomy.allow_free_text and not taxonomy.system_defined)
    )


# Taxonomy
rules.set_perm("oel_tagging.add_taxonomy", can_create_taxonomy)
rules.set_perm("oel_tagging.change_taxonomy", can_change_taxonomy)
rules.set_perm("oel_tagging.delete_taxonomy", can_change_taxonomy)
rules.set_perm("oel_tagging.view_taxonomy", can_view_taxonomy)
rules.add_perm("oel_tagging.update_orgs", oel_tagging.is_taxonomy_admin)

# Tag
rules.set_perm("oel_tagging.add_tag", can_change_taxonomy_tag)
rules.set_perm("oel_tagging.change_tag", can_change_taxonomy_tag)
rules.set_perm("oel_tagging.delete_tag", can_change_taxonomy_tag)
rules.set_perm("oel_tagging.view_tag", rules.always_allow)

# ObjectTag
rules.set_perm("oel_tagging.add_objecttag", can_change_object_tag)
rules.set_perm("oel_tagging.change_objecttag", can_change_object_tag)
rules.set_perm("oel_tagging.delete_objecttag", can_change_object_tag)
rules.set_perm("oel_tagging.can_tag_object", can_change_object_tag)

# This perms are used in the tagging rest api from openedx_tagging that is exposed in the CMS. They are overridden here
# to include Organization and objects permissions.
rules.set_perm("oel_tagging.view_objecttag_taxonomy", can_view_object_tag_taxonomy)
rules.set_perm("oel_tagging.view_objecttag_objectid", can_view_object_tag_objectid)
rules.set_perm("oel_tagging.change_objecttag_taxonomy", can_view_object_tag_taxonomy)
rules.set_perm("oel_tagging.change_objecttag_objectid", can_change_object_tag_objectid)
rules.set_perm("oel_tagging.remove_objecttag_objectid", can_remove_object_tag_objectid)
