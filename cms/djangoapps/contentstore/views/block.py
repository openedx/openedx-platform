"""Views for blocks."""

import logging
from collections import OrderedDict
from functools import partial

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment
from rest_framework.decorators import api_view

from cms.djangoapps.contentstore.utils import load_services_for_studio
from cms.lib.xblock.authoring_mixin import VISIBILITY_VIEW
from common.djangoapps.edxmako.shortcuts import render_to_response, render_to_string
from common.djangoapps.student.auth import (
    has_studio_read_access,
    has_studio_write_access,
)
from common.djangoapps.util.json_request import JsonResponse, expect_json
from openedx.core.lib.xblock_utils import (
    hash_resource,
    request_token,
    wrap_xblock,
    wrap_xblock_aside,
)
from openedx.core.lib.api.view_utils import view_auth_classes
from xmodule.modulestore.django import (
    modulestore,
)  # lint-amnesty, pylint: disable=wrong-import-order
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled

from xmodule.x_module import (
    AUTHOR_VIEW,
    PREVIEW_VIEWS,
    STUDENT_VIEW,
    STUDIO_VIEW,
)  # lint-amnesty, pylint: disable=wrong-import-order


from ..helpers import (
    is_unit,
)
from .preview import get_preview_fragment
from .component import _get_item_in_course
from ..utils import get_container_handler_context

from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import (
    handle_xblock,
    create_xblock_info,
    get_block_info,
    get_xblock,
    delete_orphans,
)
from cms.djangoapps.contentstore.xblock_storage_handlers.xblock_helpers import (
    usage_key_with_run,
    get_tags_count,
)


__all__ = [
    "orphan_handler",
    "xblock_handler",
    "xblock_view_handler",
    "xblock_outline_handler",
    "xblock_container_handler",
]

log = logging.getLogger(__name__)

CREATE_IF_NOT_FOUND = ["course_info"]

# Useful constants for defining predicates
NEVER = lambda x: False
ALWAYS = lambda x: True


# Disable atomic requests so transactions made during the request commit immediately instead of waiting for the end of
# the request transaction. This is necessary so the async tasks launched by the current process can see the changes made
# during the request. One example is the async tasks launched when courses are published before the request
# ends, which end up reading from an outdated state of the database. For more information see the discussion in the
# following PR: https://github.com/openedx/edx-platform/pull/34020
@transaction.non_atomic_requests
# @require_http_methods(("DELETE", "GET", "PUT", "POST", "PATCH"))
@view_auth_classes()
@expect_json
@api_view(http_method_names=["DELETE", "GET", "PUT", "POST", "PATCH"])
def xblock_handler(request, usage_key_string=None):
    """
    The restful handler for xblock requests.

    DELETE
        json: delete this xblock instance from the course.
    GET
        json: returns representation of the xblock (locator id, data, and metadata).
              if ?fields=graderType, it returns the graderType for the unit instead of the above.
              if ?fields=ancestorInfo, it returns ancestor info of the xblock.
        html: returns HTML for rendering the xblock (which includes both the "preview" view and the "editor" view)
    PUT or POST or PATCH
        json: if xblock locator is specified, update the xblock instance. The json payload can contain
              these fields, all optional:
                :data: the new value for the data.
                :children: the unicode representation of the UsageKeys of children for this xblock.
                :metadata: new values for the metadata fields. Any whose values are None will be deleted not set
                       to None! Absent ones will be left alone.
                :fields: any other xblock fields to be set. Only supported by update.
                    This is represented as a dictionary:
                        {'field_name': 'field_value'}
                :nullout: which metadata fields to set to None
                :graderType: change how this unit is graded
                :isPrereq: Set this xblock as a prerequisite which can be used to limit access to other xblocks
                :prereqUsageKey: Use the xblock identified by this usage key to limit access to this xblock
                :prereqMinScore: The minimum score that needs to be achieved on the prerequisite xblock
                        identifed by prereqUsageKey. Ranging from 0 to 100.
                :prereqMinCompletion: The minimum completion percentage that needs to be achieved on the
                        prerequisite xblock identifed by prereqUsageKey. Ranging from 0 to 100.
                :publish: can be:
                  'make_public': publish the content
                  'republish': publish this item *only* if it was previously published
                  'discard_changes' - reverts to the last published version
                Note: If 'discard_changes', the other fields will not be used; that is, it is not possible
                to update and discard changes in a single operation.
              The JSON representation on the updated xblock (minus children) is returned.

              if usage_key_string is not specified, create a new xblock instance, either by duplicating
              an existing xblock, or creating an entirely new one. The json playload can contain
              these fields:
                :parent_locator: parent for new xblock, required for duplicate, move and create new instance
                :duplicate_source_locator: if present, use this as the source for creating a duplicate copy
                :move_source_locator: if present, use this as the source item for moving
                :target_index: if present, use this as the target index for moving an item to a particular index
                    otherwise target_index is calculated. It is sent back in the response.
                :category: type of xblock, required if duplicate_source_locator is not present.
                :display_name: name for new xblock, optional
                :boilerplate: template name for populating fields, optional and only used
                     if duplicate_source_locator is not present
                :staged_content: use "clipboard" to paste from the OLX user's clipboard. (Incompatible with all other
                     fields except parent_locator)
                :library_content_key: the key of the library content to add. (Incompatible with
                     all other fields except parent_locator)
              The locator (unicode representation of a UsageKey) for the created xblock (minus children) is returned.
    """
    return handle_xblock(request, usage_key_string)


@view_auth_classes()
@expect_json
@api_view(http_method_names=["GET"])
def xblock_view_handler(request, usage_key_string, view_name):
    """
    The restful handler for requests for rendered xblock views.

    Returns a json object containing two keys:
        html: The rendered html of the view
        resources: A list of tuples where the first element is the resource hash, and
            the second is the resource description
    """
    usage_key = usage_key_with_run(usage_key_string)
    if not has_studio_read_access(request.user, usage_key.course_key):
        raise PermissionDenied()

    accept_header = request.META.get("HTTP_ACCEPT", "application/json")

    if "application/json" in accept_header:
        store = modulestore()
        xblock = store.get_item(usage_key)
        container_views = [
            "container_preview",
            "reorderable_container_child_preview",
            "container_child_preview",
        ]

        # wrap the generated fragment in the xmodule_editor div so that the javascript
        # can bind to it correctly
        xblock.runtime.wrappers.append(
            partial(
                wrap_xblock,
                "StudioRuntime",
                usage_id_serializer=str,
                request_token=request_token(request),
            )
        )

        xblock.runtime.wrappers_asides.append(
            partial(
                wrap_xblock_aside,
                "StudioRuntime",
                usage_id_serializer=str,
                request_token=request_token(request),
                extra_classes=["wrapper-comp-plugins"],
            )
        )

        if view_name in (STUDIO_VIEW, VISIBILITY_VIEW):
            if view_name == STUDIO_VIEW:
                load_services_for_studio(xblock.runtime, request.user)

            try:
                fragment = xblock.render(view_name)
            # catch exceptions indiscriminately, since after this point they escape the
            # dungeon and surface as uneditable, unsaveable, and undeletable
            # component-goblins.
            except Exception as exc:  # pylint: disable=broad-except
                log.debug(
                    "Unable to render %s for %r", view_name, xblock, exc_info=True
                )
                fragment = Fragment(
                    render_to_string("html_error.html", {"message": str(exc)})
                )

        elif view_name in PREVIEW_VIEWS + container_views:
            is_pages_view = (
                view_name == STUDENT_VIEW
            )  # Only the "Pages" view uses student view in Studio
            can_edit = has_studio_write_access(request.user, usage_key.course_key)

            # Determine the items to be shown as reorderable. Note that the view
            # 'reorderable_container_child_preview' is only rendered for xblocks that
            # are being shown in a reorderable container, so the xblock is automatically
            # added to the list.
            reorderable_items = set()
            if view_name == "reorderable_container_child_preview":
                reorderable_items.add(xblock.location)

            paging = None
            try:
                if request.GET.get("enable_paging", "false") == "true":
                    paging = {
                        "page_number": int(request.GET.get("page_number", 0)),
                        "page_size": int(request.GET.get("page_size", 0)),
                    }
            except ValueError:
                return HttpResponse(
                    content="Couldn't parse paging parameters: enable_paging: "
                    "{}, page_number: {}, page_size: {}".format(
                        request.GET.get("enable_paging", "false"),
                        request.GET.get("page_number", 0),
                        request.GET.get("page_size", 0),
                    ),
                    status=400,
                    content_type="text/plain",
                )

            force_render = request.GET.get("force_render", None)

            # Fetch tags of xblock and children components
            tags_count_map = {}
            if not is_tagging_feature_disabled():
                tags_count_map = get_tags_count(xblock, include_children=True)

            # Set up the context to be passed to each XBlock's render method.
            context = request.GET.dict()
            context.update(
                {
                    # This setting disables the recursive wrapping of xblocks
                    "is_pages_view": is_pages_view or view_name == AUTHOR_VIEW,
                    "is_unit_page": is_unit(xblock),
                    "can_edit": can_edit,
                    "root_xblock": xblock
                    if (view_name == "container_preview")
                    else None,
                    "reorderable_items": reorderable_items,
                    "paging": paging,
                    "force_render": force_render,
                    "item_url": "/container/{usage_key}",
                    "tags_count_map": tags_count_map,
                }
            )
            fragment = get_preview_fragment(request, xblock, context)

            # Note that the container view recursively adds headers into the preview fragment,
            # so only the "Pages" view requires that this extra wrapper be included.
            display_label = xblock.display_name or xblock.scope_ids.block_type
            if not xblock.display_name and xblock.scope_ids.block_type == "html":
                display_label = _("Text")
            if is_pages_view:
                fragment.content = render_to_string(
                    "component.html",
                    {
                        "xblock_context": context,
                        "xblock": xblock,
                        "locator": usage_key,
                        "preview": fragment.content,
                        "label": display_label,
                    },
                )
        else:
            raise Http404

        hashed_resources = OrderedDict()
        for resource in fragment.resources:
            hashed_resources[hash_resource(resource)] = resource._asdict()

        fragment_content = fragment.content
        if isinstance(fragment_content, bytes):
            fragment_content = fragment.content.decode("utf-8")

        return JsonResponse(
            {"html": fragment_content, "resources": list(hashed_resources.items())}
        )

    else:
        return HttpResponse(status=406)


@xframe_options_exempt
@view_auth_classes()
@expect_json
@api_view(http_method_names=["GET"])
def xblock_edit_view(request, usage_key_string):
    """
    Return rendered xblock edit view.

    Allows editing of an XBlock specified by the usage key.
    """
    usage_key = usage_key_with_run(usage_key_string)
    if not has_studio_read_access(request.user, usage_key.course_key):
        raise PermissionDenied()

    store = modulestore()

    with store.bulk_operations(usage_key.course_key):
        course, xblock, _, __ = _get_item_in_course(request, usage_key)
        container_handler_context = get_container_handler_context(request, usage_key, course, xblock)

        fragment = get_preview_fragment(request, xblock, {})

        hashed_resources = {
            hash_resource(resource): resource._asdict() for resource in fragment.resources
        }

        container_handler_context.update({
            "action_name": "edit",
            "resources": list(hashed_resources.items()),
        })

        return render_to_response('container_editor.html', container_handler_context)


@view_auth_classes()
@expect_json
@api_view(http_method_names=["GET"])
def xblock_outline_handler(request, usage_key_string):
    """
    The restful handler for requests for XBlock information about the block and its children.
    This is used by the course outline in particular to construct the tree representation of
    a course.
    """
    usage_key = usage_key_with_run(usage_key_string)
    if not has_studio_read_access(request.user, usage_key.course_key):
        raise PermissionDenied()

    response_format = request.GET.get("format", "html")
    if response_format == "json" or "application/json" in request.META.get(
        "HTTP_ACCEPT", "application/json"
    ):
        store = modulestore()
        with store.bulk_operations(usage_key.course_key):
            root_xblock = store.get_item(usage_key, depth=None)
            return JsonResponse(
                create_xblock_info(
                    root_xblock,
                    include_child_info=True,
                    course_outline=True,
                    include_children_predicate=lambda xblock: not xblock.category
                    == "vertical",
                )
            )
    else:
        raise Http404


@view_auth_classes()
@expect_json
@api_view(http_method_names=["GET"])
def xblock_container_handler(request, usage_key_string):
    """
    The restful handler for requests for XBlock information about the block and its children.
    This is used by the container page in particular to get additional information about publish state
    and ancestor state.
    """
    usage_key = usage_key_with_run(usage_key_string)

    if not has_studio_read_access(request.user, usage_key.course_key):
        raise PermissionDenied()

    response_format = request.GET.get("format", "html")
    if response_format == "json" or "application/json" in request.META.get(
        "HTTP_ACCEPT", "application/json"
    ):
        with modulestore().bulk_operations(usage_key.course_key):
            response = get_block_info(
                get_xblock(usage_key, request.user),
                include_ancestor_info=True,
                include_publishing_info=True,
            )
        return JsonResponse(response)
    else:
        raise Http404


@view_auth_classes()
@expect_json
@api_view(http_method_names=["GET", "DELETE"])
def orphan_handler(request, course_key_string):
    """
    View for handling orphan related requests. GET gets all of the current orphans.
    DELETE removes all orphans (requires is_staff access)

    An orphan is a block whose category is not in the DETACHED_CATEGORY list, is not the root, and is not reachable
    from the root via children
    """
    course_usage_key = CourseKey.from_string(course_key_string)
    if request.method == "GET":
        if has_studio_read_access(request.user, course_usage_key):
            return JsonResponse(
                [str(item) for item in modulestore().get_orphans(course_usage_key)]
            )
        else:
            raise PermissionDenied()
    if request.method == "DELETE":
        if request.user.is_staff:
            deleted_items = delete_orphans(
                course_usage_key, request.user.id, commit=True
            )
            return JsonResponse({"deleted": deleted_items})
        else:
            raise PermissionDenied()
