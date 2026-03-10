"""
Views related to content libraries.
A content library is a structure containing XBlocks which can be re-used in the
multiple courses.
"""


import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryLocator, LibraryUsageLocator
from organizations.api import ensure_organization
from organizations.exceptions import InvalidOrganizationException
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import DuplicateCourseError

from cms.djangoapps.course_creators.views import get_course_creator_status
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.auth import (
    STUDIO_EDIT_ROLES,
    STUDIO_VIEW_USERS,
    get_user_permissions,
    has_studio_read_access,
    has_studio_write_access,
)
from common.djangoapps.student.roles import (
    CourseInstructorRole,
    CourseStaffRole,
    LibraryUserRole,
    OrgStaffRole,
    UserBasedRole,
)
from common.djangoapps.util.json_request import JsonResponse, JsonResponseBadRequest, expect_json

from ..utils import add_instructor, reverse_library_url
from ..toggles import libraries_v1_enabled
from .component import CONTAINER_TEMPLATES, get_component_templates
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import create_xblock_info
from .user import user_with_role

__all__ = ['library_handler', 'manage_library_users']

log = logging.getLogger(__name__)


def _user_can_create_library_for_org(user, org=None):
    """
    Helper method for returning the library creation status for a particular user,
    taking into account the libraries_v1_enabled toggle.

    if the ENABLE_CREATOR_GROUP value is False, then any user can create a library (in any org),
    if library creation is enabled.

    if the ENABLE_CREATOR_GROUP value is true, then what a user can do varies by thier role.

    Global Staff: can make libraries in any org.
    Course Creator Group Members: can make libraries in any org.
    Organization Staff: Can make libraries in the organization for which they are staff.
    Course Staff: Can make libraries in the organization which has courses of which they are staff.
    Course Admin: Can make libraries in the organization which has courses of which they are Admin.
    """
    if not libraries_v1_enabled():
        return False
    elif user.is_staff:
        return True
    elif settings.FEATURES.get('ENABLE_CREATOR_GROUP', False):
        org_filter_params = {}
        if org:
            org_filter_params['org'] = org
        is_course_creator = get_course_creator_status(user) == 'granted'
        has_org_staff_role = OrgStaffRole().get_orgs_for_user(user).filter(**org_filter_params).exists()
        has_course_staff_role = (
            UserBasedRole(user=user, role=CourseStaffRole.ROLE)
            .courses_with_role()
            .filter(**org_filter_params)
            .exists()
        )
        has_course_admin_role = (
            UserBasedRole(user=user, role=CourseInstructorRole.ROLE)
            .courses_with_role()
            .filter(**org_filter_params)
            .exists()
        )
        return is_course_creator or has_org_staff_role or has_course_staff_role or has_course_admin_role
    else:
        # EDUCATOR-1924: DISABLE_LIBRARY_CREATION overrides DISABLE_COURSE_CREATION, if present.
        disable_library_creation = settings.FEATURES.get('DISABLE_LIBRARY_CREATION', None)
        disable_course_creation = settings.FEATURES.get('DISABLE_COURSE_CREATION', False)
        if disable_library_creation is not None:
            return not disable_library_creation
        else:
            return not disable_course_creation


def user_can_view_create_library_button(user):
    """
    Helper method for displaying the visibilty of the create_library_button.
    """
    return _user_can_create_library_for_org(user)


def user_can_create_library(user, org):
    """
    Helper method for to check if user can create library for given org.
    """
    if org is None:
        return False
    return _user_can_create_library_for_org(user, org)


@login_required
@ensure_csrf_cookie
@require_http_methods(('GET', 'POST'))
def library_handler(request, library_key_string=None):
    """
    RESTful interface to most content library related functionality.
    """
    if not libraries_v1_enabled():
        log.exception("Attempted to use the content library API when the libraries feature is disabled.")
        raise Http404  # Should never happen because we test the feature in urls.py also

    if request.method == 'POST':
        if library_key_string is not None:
            return HttpResponseNotAllowed(("POST",))
        return _create_library(request)

    else:
        if library_key_string:
            return _display_library(library_key_string, request)

        return _list_libraries(request)


def _display_library(library_key_string, request):
    """
    Displays single library
    """
    library_key = CourseKey.from_string(library_key_string)
    if not isinstance(library_key, LibraryLocator):
        log.exception("Non-library key passed to content libraries API.")  # Should never happen due to url regex
        raise Http404  # This is not a library
    if not has_studio_read_access(request.user, library_key):
        log.exception(
            "User %s tried to access library %s without permission",
            request.user.username, str(library_key)
        )
        raise PermissionDenied()

    library = modulestore().get_library(library_key)
    if library is None:
        log.exception("Library %s not found", str(library_key))
        raise Http404

    response_format = 'html'
    if (
            request.GET.get('format', 'html') == 'json' or
            'application/json' in request.META.get('HTTP_ACCEPT', 'text/html')
    ):
        response_format = 'json'

    return library_blocks_view(library, request.user, response_format)


def _list_libraries(request):
    """
    List all accessible libraries, after applying filters in the request
    Query params:
        org - The organization used to filter libraries
        text_search - The string used to filter libraries by searching in title, id or org
    """
    org = request.GET.get('org', '')
    text_search = request.GET.get('text_search', '').lower()

    if org:
        libraries = modulestore().get_libraries(org=org)
    else:
        libraries = modulestore().get_libraries()

    lib_info = [
        {
            "display_name": lib.display_name,
            "library_key": str(lib.location.library_key),
        }
        for lib in libraries
        if (
            (
                text_search in lib.display_name.lower() or
                text_search in lib.location.library_key.org.lower() or
                text_search in lib.location.library_key.library.lower()
            ) and
            has_studio_read_access(request.user, lib.location.library_key)
        )
    ]
    return JsonResponse(lib_info)


@expect_json
def _create_library(request):
    """
    Helper method for creating a new library.
    """
    display_name = None
    try:
        display_name = request.json['display_name']
        org = request.json['org']
        ensure_organization(org)
        library = request.json.get('number', None)
        if library is None:
            library = request.json['library']
        if not user_can_create_library(request.user, org):
            raise PermissionDenied()
        store = modulestore()
        with store.default_store(ModuleStoreEnum.Type.split):
            new_lib = store.create_library(
                org=org,
                library=library,
                user_id=request.user.id,
                fields={"display_name": display_name},
            )
        # Give the user admin ("Instructor") role for this library:
        add_instructor(new_lib.location.library_key, request.user, request.user)
    except PermissionDenied as error:  # pylint: disable=unused-variable
        log.info(
            "User does not have the permission to create LIBRARY in this organization."
            "User: '%s' Org: '%s' LIBRARY #: '%s'.",
            request.user.id,
            org,
            library,
        )
        return JsonResponse({
            'error': _('User does not have the permission to create library in this organization '
                       'or course creation is disabled')},
            status=403
        )
    except KeyError as error:
        log.exception("Unable to create library - missing required JSON key.")
        return JsonResponseBadRequest({
            "ErrMsg": _("Unable to create library - missing required field '{field}'").format(field=str(error))
        })
    except InvalidKeyError as error:
        log.exception("Unable to create library - invalid key.")
        return JsonResponseBadRequest({
            "ErrMsg": _("Unable to create library '{name}'.\n\n{err}").format(name=display_name, err=str(error))
        })
    except DuplicateCourseError:
        log.exception("Unable to create library - one already exists with the same key.")
        return JsonResponseBadRequest({
            'ErrMsg': _(
                'There is already a library defined with the same '
                'organization and library code. Please '
                'change your library code so that it is unique within your organization.'
            )
        })
    except InvalidOrganizationException:
        log.exception("Unable to create library - %s is not a valid org short_name.", org)
        return JsonResponseBadRequest({
            'ErrMsg': _(
                "'{organization_key}' is not a valid organization identifier."
            ).format(organization_key=org)
        })

    lib_key_str = str(new_lib.location.library_key)
    return JsonResponse({
        'url': reverse_library_url('library_handler', lib_key_str),
        'library_key': lib_key_str,
    })


def library_blocks_view(library, user, response_format):
    """
    The main view of a course's content library.
    Shows all the XBlocks in the library, and allows adding/editing/deleting
    them.
    Can be called with response_format="json" to get a JSON-formatted list of
    the XBlocks in the library along with library metadata.

    Assumes that read permissions have been checked before calling this.
    """
    assert isinstance(library.location.library_key, LibraryLocator)
    assert isinstance(library.location, LibraryUsageLocator)

    children = library.children
    if response_format == "json":
        # The JSON response for this request is short and sweet:
        prev_version = library.runtime.course_entry.structure['previous_version']
        return JsonResponse({
            "display_name": library.display_name,
            "library_id": str(library.location.library_key),
            "version": str(library.runtime.course_entry.course_key.version_guid),
            "previous_version": str(prev_version) if prev_version else None,
            "blocks": [str(x) for x in children],
        })

    can_edit = has_studio_write_access(user, library.location.library_key)

    xblock_info = create_xblock_info(library, include_ancestor_info=False, graders=[])
    component_templates = get_component_templates(library, library=True) if can_edit else []

    return render_to_response('library.html', {
        'can_edit': can_edit,
        'context_library': library,
        'component_templates': component_templates,
        'xblock_info': xblock_info,
        'templates': CONTAINER_TEMPLATES,
    })


def manage_library_users(request, library_key_string):
    """
    Studio UI for editing the users within a library.

    Uses the /course_team/:library_key/:user_email/ REST API to make changes.
    """
    library_key = CourseKey.from_string(library_key_string)
    if not isinstance(library_key, LibraryLocator):
        raise Http404  # This is not a library
    user_perms = get_user_permissions(request.user, library_key)
    if not user_perms & STUDIO_VIEW_USERS:
        raise PermissionDenied()
    library = modulestore().get_library(library_key)
    if library is None:
        raise Http404

    # Segment all the users explicitly associated with this library, ensuring each user only has one role listed:
    instructors = set(CourseInstructorRole(library_key).users_with_role())
    staff = set(CourseStaffRole(library_key).users_with_role()) - instructors
    users = set(LibraryUserRole(library_key).users_with_role()) - instructors - staff

    formatted_users = []
    for user in instructors:
        formatted_users.append(user_with_role(user, 'instructor'))
    for user in staff:
        formatted_users.append(user_with_role(user, 'staff'))
    for user in users:
        formatted_users.append(user_with_role(user, 'library_user'))

    return render_to_response('manage_users_lib.html', {
        'context_library': library,
        'users': formatted_users,
        'allow_actions': bool(user_perms & STUDIO_EDIT_ROLES),
        'library_key': str(library_key),
        'lib_users_url': reverse_library_url('manage_library_users', library_key_string),
        'show_children_previews': library.show_children_previews
    })
