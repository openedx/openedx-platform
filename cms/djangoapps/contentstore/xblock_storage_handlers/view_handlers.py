"""
Service functions for xblock views, as found in:

- contentstore/views/block.py
- rest_api/v1/viewx/xblock.py

We extracted all the logic from the `xblock_handler` endpoint that lives in
contentstore/views/block.py to this file, because the logic is reused in another view now.
Along with it, we moved the business logic of the other views in that file, since that is related.
"""
import logging
from datetime import datetime
from uuid import uuid4

from attrs import asdict
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils.translation import gettext as _
from edx_django_utils.plugins import pluggable_override
from openedx.core.djangoapps.content_libraries.api import ContainerMetadata, ContainerType, LibraryXBlockMetadata
from openedx.core.djangoapps.content_tagging.api import get_object_tag_counts
from edx_proctoring.api import (
    does_backend_support_onboarding,
    get_exam_by_content_id,
    get_exam_configuration_dashboard_url,
)
from edx_proctoring.exceptions import ProctoredExamNotFoundException
from help_tokens.core import HelpUrlExpert
from opaque_keys.edx.locator import LibraryUsageLocator, LibraryUsageLocatorV2
from pytz import UTC
from xblock.core import XBlock
from xblock.fields import Scope
from .xblock_helpers import get_block_key_string

from cms.djangoapps.contentstore.helpers import StaticFileNotices
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from cms.lib.ai_aside_summary_config import AiAsideSummaryConfig
from cms.lib.xblock.upstream_sync import BadUpstream, UpstreamLink
from cms.lib.xblock.upstream_sync_block import sync_from_upstream_block
from cms.lib.xblock.upstream_sync_container import sync_from_upstream_container
from common.djangoapps.static_replace import replace_static_urls
from common.djangoapps.student.auth import (
    has_studio_read_access,
    has_studio_write_access,
)
from common.djangoapps.util.date_utils import get_default_time_display
from common.djangoapps.util.json_request import JsonResponse, expect_json
from common.djangoapps.util.proctoring import show_review_rules
from openedx.core.djangoapps.bookmarks import api as bookmarks_api
from openedx.core.djangoapps.content_tagging.toggles import is_tagging_feature_disabled
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.djangoapps.video_config.toggles import PUBLIC_VIDEO_SHARE
from openedx.core.lib.gating import api as gating_api
from openedx.core.lib.cache_utils import request_cached
from openedx.core.lib.xblock_utils import get_icon
from openedx.core.toggles import ENTRANCE_EXAMS
from xmodule.course_block import DEFAULT_START_DATE
from xmodule.modulestore import EdxJSONEncoder, ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.draft_and_published import DIRECT_ONLY_CATEGORIES
from xmodule.modulestore.exceptions import InvalidLocationError, ItemNotFoundError
from xmodule.modulestore.inheritance import own_metadata
from xmodule.tabs import CourseTabList

from ..utils import (
    ancestor_has_staff_lock,
    find_release_date_source,
    find_staff_lock_source,
    get_split_group_display_name,
    get_user_partition_info,
    get_visibility_partition_info,
    has_children_visible_to_specific_partition_groups,
    is_currently_visible_to_students,
    is_self_paced,
    get_taxonomy_tags_widget_url,
    load_services_for_studio,
    duplicate_block,
)

from .create_xblock import create_xblock
from .xblock_helpers import usage_key_with_run
from ..helpers import (
    concat_static_file_notices,
    get_parent_xblock,
    import_staged_content_from_user_clipboard,
    import_static_assets_for_library_sync,
    is_unit,
    xblock_embed_lms_url,
    xblock_lms_url,
    xblock_primary_child_category,
    xblock_studio_url,
    xblock_type_display_name,
)

log = logging.getLogger(__name__)

CREATE_IF_NOT_FOUND = ["course_info"]

# Useful constants for defining predicates
NEVER = lambda x: False
ALWAYS = lambda x: True


def _filter_entrance_exam_grader(graders):
    """
    If the entrance exams feature is enabled we need to hide away the grader from
    views/controls like the 'Grade as' dropdown that allows a course author to select
    the grader type for a given section of a course
    """
    if ENTRANCE_EXAMS.is_enabled():
        graders = [
            grader for grader in graders if grader.get("type") != "Entrance Exam"
        ]
    return graders


def _is_library_component_limit_reached(usage_key):
    """
    Verify if the library has reached the maximum number of components allowed in it
    """
    store = modulestore()
    parent = store.get_item(usage_key)
    if not parent.has_children:
        # Limit cannot be applied on such items
        return False
    total_children = len(parent.children)
    return total_children + 1 > settings.MAX_BLOCKS_PER_CONTENT_LIBRARY


def _get_block_parent_children(xblock):
    '''
    Extract parent ID information from the given xblock and report it in the response
    Extract child ID information from the given xblock and report it in the response

    Note that no effort is made to look up all settings for this xblock's parent or childrent;
    the blocks are merely identified. If further informaiton regarding them is required, another
    call with those blocks as subjects may be made into this handler.
    '''
    response = {}
    if hasattr(xblock, "parent") and xblock.parent:
        response["parent"] = {
            "block_type": xblock.parent.block_type,
            "block_id": xblock.parent.block_id
        }
    if hasattr(xblock, "children") and xblock.children:
        response["children"] = [
            {
                "block_type": child.block_type,
                "block_id": child.block_id
            }
            for child in xblock.children
        ]
    return response


def handle_xblock(request, usage_key_string=None):
    """
    Service method with all business logic for handling xblock requests.
    This method is used both by the internal xblock_handler API and by
    the public CMS API.
    """
    if usage_key_string:

        usage_key = usage_key_with_run(usage_key_string)

        access_check = (
            has_studio_read_access
            if request.method == "GET"
            else has_studio_write_access
        )
        if not access_check(request.user, usage_key.course_key):
            raise PermissionDenied()

        if request.method == "GET":
            accept_header = request.META.get("HTTP_ACCEPT", "application/json")

            if "application/json" in accept_header:
                fields = request.GET.get("fields", "").split(",")
                if "graderType" in fields:
                    # right now can't combine output of this w/ output of get_block_info, but worthy goal
                    return JsonResponse(
                        CourseGradingModel.get_section_grader_type(usage_key)
                    )
                elif "ancestorInfo" in fields:
                    xblock = get_xblock(usage_key, request.user)
                    ancestor_info = _create_xblock_ancestor_info(
                        xblock, is_concise=True
                    )
                    return JsonResponse(ancestor_info)
                # TODO: pass fields to get_block_info and only return those
                with modulestore().bulk_operations(usage_key.course_key):
                    response = get_block_info(get_xblock(usage_key, request.user))
                    if "customReadToken" in fields:
                        parent_children = _get_block_parent_children(get_xblock(usage_key, request.user))
                        response.update(parent_children)
                return JsonResponse(response)
            else:
                return HttpResponse(status=406)

        elif request.method == "DELETE":
            _delete_item(usage_key, request.user)
            return JsonResponse()
        else:  # Since we have a usage_key, we are updating an existing xblock.
            modified_xblock = modify_xblock(usage_key, request)
            return modified_xblock

    elif request.method in ("PUT", "POST"):
        if "duplicate_source_locator" in request.json:
            parent_usage_key = usage_key_with_run(request.json["parent_locator"])
            duplicate_source_usage_key = usage_key_with_run(
                request.json["duplicate_source_locator"]
            )
            source_course = duplicate_source_usage_key.course_key
            dest_course = parent_usage_key.course_key
            if not has_studio_write_access(
                request.user, dest_course
            ) or not has_studio_read_access(request.user, source_course):
                raise PermissionDenied()

            # Libraries have a maximum component limit enforced on them
            if isinstance(
                parent_usage_key, LibraryUsageLocator
            ) and _is_library_component_limit_reached(parent_usage_key):
                return JsonResponse(
                    {
                        "error": _(
                            "Libraries cannot have more than {limit} components"
                        ).format(limit=settings.MAX_BLOCKS_PER_CONTENT_LIBRARY)
                    },
                    status=400,
                )

            dest_usage_key = duplicate_block(
                parent_usage_key,
                duplicate_source_usage_key,
                request.user,
                display_name=request.json.get('display_name'),
            )

            return JsonResponse(
                {
                    "locator": str(dest_usage_key),
                    "courseKey": str(dest_usage_key.course_key),
                }
            )
        else:
            return _create_block(request)
    elif request.method == "PATCH":
        if "move_source_locator" in request.json:
            move_source_usage_key = usage_key_with_run(
                request.json.get("move_source_locator")
            )
            target_parent_usage_key = usage_key_with_run(
                request.json.get("parent_locator")
            )
            target_index = request.json.get("target_index")
            if not has_studio_write_access(
                request.user, target_parent_usage_key.course_key
            ) or not has_studio_read_access(
                request.user, target_parent_usage_key.course_key
            ):
                raise PermissionDenied()
            return _move_item(
                move_source_usage_key,
                target_parent_usage_key,
                request.user,
                target_index,
            )

        return JsonResponse(
            {"error": "Patch request did not recognise any parameters to handle."},
            status=400,
        )
    else:
        return HttpResponseBadRequest(
            "Only instance creation is supported without a usage key.",
            content_type="text/plain",
        )


def modify_xblock(usage_key, request):
    request_data = request.json
    return _save_xblock(
        request.user,
        get_xblock(usage_key, request.user),
        data=request_data.get("data"),
        children_strings=request_data.get("children"),
        metadata=request_data.get("metadata"),
        nullout=request_data.get("nullout"),
        grader_type=request_data.get("graderType"),
        is_prereq=request_data.get("isPrereq"),
        prereq_usage_key=request_data.get("prereqUsageKey"),
        prereq_min_score=request_data.get("prereqMinScore"),
        prereq_min_completion=request_data.get("prereqMinCompletion"),
        publish=request_data.get("publish"),
        fields=request_data.get("fields"),
        summary_configuration_enabled=request_data.get("summary_configuration_enabled"),
    )


def save_xblock_with_callback(xblock, user, old_metadata=None, old_content=None):
    """
    Updates the xblock in the modulestore.
    But before doing so, it calls the xblock's editor_saved callback function,
    and after doing so, it calls the xblock's post_editor_saved callback function.
    """
    if old_metadata is None:
        old_metadata = own_metadata(xblock)
    if old_content is None:
        old_content = xblock.get_explicitly_set_fields_by_scope(Scope.content)
    load_services_for_studio(xblock.runtime, user)
    xblock.editor_saved(user, old_metadata, old_content)
    xblock_updated = modulestore().update_item(xblock, user.id)
    xblock_updated.post_editor_saved(user, old_metadata, old_content)
    return xblock_updated


def _save_xblock(
    user,
    xblock,
    data=None,
    children_strings=None,
    metadata=None,
    nullout=None,
    grader_type=None,
    is_prereq=None,
    prereq_usage_key=None,
    prereq_min_score=None,
    prereq_min_completion=None,
    publish=None,
    fields=None,
    summary_configuration_enabled=None,
):  # lint-amnesty, pylint: disable=too-many-statements
    """
    Saves xblock w/ its fields. Has special processing for grader_type, publish, and nullout and Nones in metadata.
    nullout means to truly set the field to None whereas nones in metadata mean to unset them (so they revert
    to default).
    """
    store = modulestore()
    # Perform all xblock changes within a (single-versioned) transaction
    with store.bulk_operations(xblock.location.course_key):
        # Don't allow updating an xblock and discarding changes in a single operation (unsupported by UI).
        if publish == "discard_changes":
            store.revert_to_published(xblock.location, user.id)
            # Returning the same sort of result that we do for other save operations. In the future,
            # we may want to return the full XBlockInfo.
            return JsonResponse({"id": str(xblock.location)})

        old_metadata = own_metadata(xblock)
        old_content = xblock.get_explicitly_set_fields_by_scope(Scope.content)

        if data:
            # TODO Allow any scope.content fields not just "data" (exactly like the get below this)
            xblock.data = data
        else:
            data = old_content["data"] if "data" in old_content else None

        if fields:
            for field_name in fields:
                setattr(xblock, field_name, fields[field_name])

        if children_strings is not None:
            children = []
            for child_string in children_strings:
                children.append(usage_key_with_run(child_string))

            # if new children have been added, remove them from their old parents
            new_children = set(children) - set(xblock.children)
            for new_child in new_children:
                old_parent_location = store.get_parent_location(new_child)
                if old_parent_location:
                    old_parent = store.get_item(old_parent_location)
                    old_parent.children.remove(new_child)
                    old_parent = save_xblock_with_callback(old_parent, user)
                else:
                    # the Studio UI currently doesn't present orphaned children, so assume this is an error
                    return JsonResponse(
                        {
                            "error": "Invalid data, possibly caused by concurrent authors."
                        },
                        400,
                    )

            # make sure there are no old children that became orphans
            # In a single-author (no-conflict) scenario, all children in the persisted list on the server should be
            # present in the updated list.  If there are any children that have been dropped as part of this update,
            # then that would be an error.
            #
            # We can be even more restrictive in a multi-author (conflict), by returning an error whenever
            # len(old_children) > 0. However, that conflict can still be "merged" if the dropped child had been
            # re-parented. Hence, the check for the parent in the any statement below.
            #
            # Note that this multi-author conflict error should not occur in modulestores (such as Split) that support
            # atomic write transactions.  In Split, if there was another author who moved one of the "old_children"
            # into another parent, then that child would have been deleted from this parent on the server. However,
            # this is error could occur in modulestores (such as Draft) that do not support atomic write-transactions
            old_children = set(xblock.children) - set(children)
            if any(
                store.get_parent_location(old_child) == xblock.location
                for old_child in old_children
            ):
                # since children are moved as part of a single transaction, orphans should not be created
                return JsonResponse(
                    {"error": "Invalid data, possibly caused by concurrent authors."},
                    400,
                )

            # set the children on the xblock
            xblock.children = children

        # also commit any metadata which might have been passed along
        if nullout is not None or metadata is not None:
            # the postback is not the complete metadata, as there's system metadata which is
            # not presented to the end-user for editing. So let's use the original (existing_item) and
            # 'apply' the submitted metadata, so we don't end up deleting system metadata.
            if nullout is not None:
                for metadata_key in nullout:
                    setattr(xblock, metadata_key, None)

            # update existing metadata with submitted metadata (which can be partial)
            # IMPORTANT NOTE: if the client passed 'null' (None) for a piece of metadata that means 'remove it'. If
            # the intent is to make it None, use the nullout field
            if metadata is not None:
                for metadata_key, value in metadata.items():
                    field = xblock.fields[metadata_key]

                    if value is None:
                        field.delete_from(xblock)
                    else:
                        try:
                            value = field.from_json(value)
                        except ValueError as verr:
                            reason = _("Invalid data")
                            if str(verr):
                                reason = _("Invalid data ({details})").format(
                                    details=str(verr)
                                )
                            return JsonResponse({"error": reason}, 400)

                        field.write_to(xblock, value)

        validate_and_update_xblock_due_date(xblock)
        # update the xblock and call any xblock callbacks
        xblock = save_xblock_with_callback(xblock, user, old_metadata, old_content)

        # for static tabs, their containing course also records their display name
        course = store.get_course(xblock.location.course_key)
        if xblock.location.block_type == "static_tab":
            # find the course's reference to this tab and update the name.
            static_tab = CourseTabList.get_tab_by_slug(
                course.tabs, xblock.location.name
            )
            # only update if changed
            if static_tab:
                update_tab = False
                if static_tab["name"] != xblock.display_name:
                    static_tab["name"] = xblock.display_name
                    update_tab = True
                if static_tab["course_staff_only"] != xblock.course_staff_only:
                    static_tab["course_staff_only"] = xblock.course_staff_only
                    update_tab = True
                if update_tab:
                    store.update_item(course, user.id)

        result = {
            "id": str(xblock.location),
            "data": data,
            "metadata": own_metadata(xblock),
        }

        if grader_type is not None:
            result.update(
                CourseGradingModel.update_section_grader_type(xblock, grader_type, user)
            )

        # Save gating info
        if xblock.category == "sequential" and course.enable_subsection_gating:
            if is_prereq is not None:
                if is_prereq:
                    gating_api.add_prerequisite(
                        xblock.location.course_key, xblock.location
                    )
                else:
                    gating_api.remove_prerequisite(xblock.location)
                result["is_prereq"] = is_prereq

            if prereq_usage_key is not None:
                gating_api.set_required_content(
                    xblock.location.course_key,
                    xblock.location,
                    prereq_usage_key,
                    prereq_min_score,
                    prereq_min_completion,
                )

        # If publish is set to 'republish' and this item is not in direct only categories and has previously been
        # published, then this item should be republished. This is used by staff locking to ensure that changing the
        # draft value of the staff lock will also update the published version, but only at the unit level.
        if publish == "republish" and xblock.category not in DIRECT_ONLY_CATEGORIES:
            if modulestore().has_published_version(xblock):
                publish = "make_public"

        # Make public after updating the xblock, in case the caller asked for both an update and a publish.
        if publish == "make_public":
            modulestore().publish(xblock.location, user.id)

        # If summary_configuration_enabled is not None, use AIAsideSummary to update it.
        if xblock.category == "vertical" and summary_configuration_enabled is not None:
            AiAsideSummaryConfig(course.id).set_summary_settings(xblock.location, {
                'enabled': summary_configuration_enabled
            })

        # Note that children aren't being returned until we have a use case.
        return JsonResponse(result, encoder=EdxJSONEncoder)


@login_required
@expect_json
def create_item(request):
    """
    Exposes internal helper method without breaking existing bindings/dependencies
    """
    return _create_block(request)


def sync_library_content(
    downstream: XBlock,
    request,
    store,
    top_level_parent: XBlock | None = None,
) -> StaticFileNotices:
    """
    Handle syncing library content for given xblock depending on its upstream type.
    It can sync unit containers and lower level xblocks.
    """
    link = UpstreamLink.get_for_block(downstream)
    upstream_key = link.upstream_key
    request_data = getattr(request, "json", getattr(request, "data", {}))
    override_customizations = request_data.get("override_customizations", False)
    keep_custom_fields = request_data.get("keep_custom_fields", [])
    if isinstance(upstream_key, LibraryUsageLocatorV2):
        lib_block = sync_from_upstream_block(
            downstream=downstream,
            user=request.user,
            top_level_parent=top_level_parent,
            override_customizations=override_customizations,
            keep_custom_fields=keep_custom_fields,
        )
        if lib_block:
            static_file_notices = import_static_assets_for_library_sync(downstream, lib_block, request)
        else:
            static_file_notices = StaticFileNotices()
        store.update_item(downstream, request.user.id)
    else:
        with store.bulk_operations(downstream.usage_key.context_key):
            upstream_children = sync_from_upstream_container(downstream=downstream, user=request.user)
            downstream_children = downstream.get_children()
            downstream_children_keys = [child.upstream for child in downstream_children]
            # Sync the children:
            notices = []
            # Store final children keys to update order of items in containers
            children = []

            top_level_downstream_parent = top_level_parent or downstream

            for i, upstream_child in enumerate(upstream_children):
                if isinstance(upstream_child, LibraryXBlockMetadata):
                    upstream_key = str(upstream_child.usage_key)
                    block_type = upstream_child.usage_key.block_type
                elif isinstance(upstream_child, ContainerMetadata):
                    upstream_key = str(upstream_child.container_key)
                    match upstream_child.container_type:
                        case ContainerType.Unit:
                            block_type = "vertical"
                        case ContainerType.Subsection:
                            block_type = "sequential"
                        case _:
                            # We don't support other container types for now.
                            log.error(
                                "Unexpected upstream child container type: %s",
                                upstream_child.container_type,
                            )
                            continue
                else:
                    log.error(
                        "Unexpected type of upstream child: %s",
                        type(upstream_child),
                    )
                    continue

                if upstream_key not in downstream_children_keys:
                    # This upstream_child is new, create it.
                    downstream_child = store.create_child(
                        parent_usage_key=downstream.usage_key,
                        position=i,
                        user_id=request.user.id,
                        block_type=block_type,
                        # TODO: Can we generate a unique but friendly block_id, perhaps using upstream block_id
                        block_id=f"{block_type}{uuid4().hex[:8]}",
                        fields={
                            "upstream": upstream_key,
                            "top_level_downstream_parent_key": get_block_key_string(
                                top_level_downstream_parent.usage_key,
                            ),
                        },
                    )
                else:
                    downstream_child_old_index = downstream_children_keys.index(upstream_key)
                    downstream_child = downstream_children[downstream_child_old_index]

                children.append(downstream_child.usage_key)

                result = sync_library_content(
                    downstream=downstream_child,
                    request=request,
                    store=store,
                    top_level_parent=top_level_downstream_parent,
                )
                notices.append(result)

            for child in downstream_children:
                if child.usage_key not in children:
                    # This downstream block was added, or deleted from upstream block.
                    # NOTE: This will also delete any local additions to a unit in the next upstream sync.
                    store.delete_item(child.usage_key, user_id=request.user.id)
            downstream.children = children
            store.update_item(downstream, request.user.id)
        static_file_notices = concat_static_file_notices(notices)
    return static_file_notices


@login_required
@expect_json
def _create_block(request):
    """View for create blocks."""
    parent_locator = request.json["parent_locator"]
    usage_key = usage_key_with_run(parent_locator)
    if not has_studio_write_access(request.user, usage_key.course_key):
        raise PermissionDenied()

    if request.json.get("staged_content") == "clipboard":
        # Paste from the user's clipboard (content_staging app clipboard, not browser clipboard) into 'usage_key':
        try:
            created_xblock, notices = import_staged_content_from_user_clipboard(
                parent_key=usage_key,
                request=request,
            )
        except Exception:  # pylint: disable=broad-except
            log.exception(
                "Could not paste component into location {}".format(usage_key)
            )
            return JsonResponse(
                {"error": _("There was a problem pasting your component.")}, status=400
            )
        if created_xblock is None:
            return JsonResponse(
                {"error": _("Your clipboard is empty or invalid.")}, status=400
            )
        return JsonResponse({
            "locator": str(created_xblock.location),
            "courseKey": str(created_xblock.location.course_key),
            "static_file_notices": asdict(notices),
            "upstreamRef": str(created_xblock.upstream),
        })

    category = request.json["category"]
    if isinstance(usage_key, LibraryUsageLocator):
        # Only these categories are supported at this time.
        if category not in ["html", "problem", "video"]:
            return HttpResponseBadRequest(
                "Category '%s' not supported for Libraries" % category,
                content_type="text/plain",
            )

        if _is_library_component_limit_reached(usage_key):
            return JsonResponse(
                {
                    "error": _(
                        "Libraries cannot have more than {limit} components"
                    ).format(limit=settings.MAX_BLOCKS_PER_CONTENT_LIBRARY)
                },
                status=400,
            )

    created_block = create_xblock(
        parent_locator=parent_locator,
        user=request.user,
        category=category,
        display_name=request.json.get("display_name"),
        boilerplate=request.json.get("boilerplate"),
    )

    response = {
        "locator": str(created_block.location),
        "courseKey": str(created_block.location.course_key),
    }
    # If it contains library_content_key, the block is being imported from a v2 library
    # so it needs to be synced with upstream block.
    if upstream_ref := request.json.get("library_content_key"):
        # Set `created_block.upstream` and then sync this with the upstream (library) version.
        created_block.upstream = upstream_ref
        try:
            store = modulestore()
            static_file_notices = sync_library_content(created_block, request, store)
        except BadUpstream as exc:
            _delete_item(created_block.location, request.user)
            log.exception(
                f"Could not sync to new block at '{created_block.usage_key}' "
                f"using provided library_content_key='{upstream_ref}'"
            )
            return JsonResponse({"error": str(exc)}, status=400)
        response["upstreamRef"] = upstream_ref
        response["static_file_notices"] = asdict(static_file_notices)
        response["parent_locator"] = parent_locator

    return JsonResponse(response)


def _get_source_index(source_usage_key, source_parent):
    """
    Get source index position of the XBlock.

    Arguments:
        source_usage_key (BlockUsageLocator): Locator of source item.
        source_parent (XBlock): A parent of the source XBlock.

    Returns:
        source_index (int): Index position of the xblock in a parent.
    """
    try:
        source_index = source_parent.children.index(source_usage_key)
        return source_index
    except ValueError:
        return None


def is_source_item_in_target_parents(source_item, target_parent):
    """
    Returns True if source item is found in target parents otherwise False.

    Arguments:
        source_item (XBlock): Source Xblock.
        target_parent (XBlock): Target XBlock.
    """
    target_ancestors = _create_xblock_ancestor_info(target_parent, is_concise=True)[
        "ancestors"
    ]
    for target_ancestor in target_ancestors:
        if str(source_item.location) == target_ancestor["id"]:
            return True
    return False


def _move_item(source_usage_key, target_parent_usage_key, user, target_index=None):
    """
    Move an existing xblock as a child of the supplied target_parent_usage_key.

    Arguments:
        source_usage_key (BlockUsageLocator): Locator of source item.
        target_parent_usage_key (BlockUsageLocator): Locator of target parent.
        target_index (int): If provided, insert source item at provided index location in target_parent_usage_key item.

    Returns:
        JsonResponse: Information regarding move operation. It may contains error info if an invalid move operation
            is performed.
    """
    # Get the list of all parentable component type XBlocks.
    parent_component_types = list(
        {
            name
            for name, class_ in XBlock.load_classes()
            if getattr(class_, "has_children", False)
        }
        - set(DIRECT_ONLY_CATEGORIES)
    )

    store = modulestore()
    with store.bulk_operations(source_usage_key.course_key):
        source_item = store.get_item(source_usage_key)
        source_parent = source_item.get_parent()
        target_parent = store.get_item(target_parent_usage_key)
        source_type = source_item.category
        target_parent_type = target_parent.category
        error = None

        # Store actual/initial index of the source item. This would be sent back with response,
        # so that with Undo operation, it would easier to move back item to it's original/old index.
        source_index = _get_source_index(source_usage_key, source_parent)

        valid_move_type = {
            "sequential": "vertical",
            "chapter": "sequential",
        }

        if (
            valid_move_type.get(target_parent_type, "") != source_type
            and target_parent_type not in parent_component_types
        ):
            error = _(
                "You can not move {source_type} into {target_parent_type}."
            ).format(
                source_type=source_type,
                target_parent_type=target_parent_type,
            )
        elif (
            source_parent.location == target_parent.location
            or source_item.location in target_parent.children
        ):
            error = _("Item is already present in target location.")
        elif source_item.location == target_parent.location:
            error = _("You can not move an item into itself.")
        elif is_source_item_in_target_parents(source_item, target_parent):
            error = _("You can not move an item into it's child.")
        elif target_parent_type == "split_test":
            error = _("You can not move an item directly into content experiment.")
        elif source_index is None:
            error = _("{source_usage_key} not found in {parent_usage_key}.").format(
                source_usage_key=str(source_usage_key),
                parent_usage_key=str(source_parent.location),
            )
        else:
            try:
                target_index = int(target_index) if target_index is not None else None
                if (
                    target_index is not None
                    and len(target_parent.children) < target_index
                ):
                    error = _(
                        "You can not move {source_usage_key} at an invalid index ({target_index})."
                    ).format(
                        source_usage_key=str(source_usage_key),
                        target_index=target_index,
                    )
            except ValueError:
                error = _(
                    "You must provide target_index ({target_index}) as an integer."
                ).format(target_index=target_index)
        if error:
            return JsonResponse({"error": error}, status=400)

        # When target_index is provided, insert xblock at target_index position, otherwise insert at the end.
        insert_at = (
            target_index if target_index is not None else len(target_parent.children)
        )

        store.update_item_parent(
            item_location=source_item.location,
            new_parent_location=target_parent.location,
            old_parent_location=source_parent.location,
            insert_at=insert_at,
            user_id=user.id,
        )

        log.info(
            "MOVE: %s moved from %s to %s at %d index",
            str(source_usage_key),
            str(source_parent.location),
            str(target_parent_usage_key),
            insert_at,
        )

        context = {
            "move_source_locator": str(source_usage_key),
            "parent_locator": str(target_parent_usage_key),
            "source_index": target_index if target_index is not None else source_index,
        }
        return JsonResponse(context)


@login_required
def delete_item(request, usage_key):
    """
    Exposes internal helper method without breaking existing bindings/dependencies
    """
    _delete_item(usage_key, request.user)


def _delete_item(usage_key, user):
    """
    Deletes an existing xblock with the given usage_key.
    If the xblock is a Static Tab, removes it from course.tabs as well.
    """
    store = modulestore()

    with store.bulk_operations(usage_key.course_key):
        # VS[compat] cdodge: This is a hack because static_tabs also have references from the course block, so
        # if we add one then we need to also add it to the policy information (i.e. metadata)
        # we should remove this once we can break this reference from the course to static tabs
        if usage_key.block_type == "static_tab":
            course = store.get_course(usage_key.course_key)
            existing_tabs = course.tabs or []
            course.tabs = [
                tab
                for tab in existing_tabs
                if tab.get("url_slug") != usage_key.block_id
            ]
            store.update_item(course, user.id)

        # Delete user bookmarks
        bookmarks_api.delete_bookmarks(usage_key)
        store.delete_item(usage_key, user.id)


def delete_orphans(course_usage_key, user_id, commit=False):
    """
    Helper function to delete orphans for a given course.
    If `commit` is False, this function does not actually remove
    the orphans.
    """
    store = modulestore()
    blocks = store.get_orphans(course_usage_key)
    branch = course_usage_key.branch
    if commit:
        with store.bulk_operations(course_usage_key):
            for blockloc in blocks:
                revision = ModuleStoreEnum.RevisionOption.all
                # specify branches when deleting orphans
                if branch == ModuleStoreEnum.BranchName.published:
                    revision = ModuleStoreEnum.RevisionOption.published_only
                store.delete_item(blockloc, user_id, revision=revision)
    return [str(block) for block in blocks]


def get_xblock(usage_key, user):
    """
    Returns the xblock for the specified usage key. Note: if failing to find a key with a category
    in the CREATE_IF_NOT_FOUND list, an xblock will be created and saved automatically.
    """
    store = modulestore()
    with store.bulk_operations(usage_key.course_key):
        try:
            return store.get_item(usage_key, depth=None)
        except ItemNotFoundError:
            if usage_key.block_type in CREATE_IF_NOT_FOUND:
                # Create a new one for certain categories only. Used for course info handouts.
                return store.create_item(
                    user.id,
                    usage_key.course_key,
                    usage_key.block_type,
                    block_id=usage_key.block_id,
                )
            else:
                raise
        except InvalidLocationError:
            log.error("Can't find item by location.")
            return JsonResponse(
                {"error": "Can't find item by location: " + str(usage_key)}, 404
            )


def get_block_info(
    xblock,
    rewrite_static_links=True,
    include_ancestor_info=False,
    include_publishing_info=False,
    include_children_predicate=False,
):
    """
    metadata, data, id representation of a leaf block fetcher.
    :param usage_key: A UsageKey
    """
    with modulestore().bulk_operations(xblock.location.course_key):
        data = getattr(xblock, "data", "")
        if rewrite_static_links:
            data = replace_static_urls(data, None, course_id=xblock.location.course_key)

        # Pre-cache has changes for the entire course because we'll need it for the ancestor info
        # Except library blocks which don't [yet] use draft/publish
        if not isinstance(xblock.location, LibraryUsageLocator):
            modulestore().has_changes(
                modulestore().get_course(xblock.location.course_key, depth=None)
            )

        # Note that children aren't being returned until we have a use case.
        xblock_info = create_xblock_info(
            xblock,
            data=data,
            metadata=own_metadata(xblock),
            include_ancestor_info=include_ancestor_info,
            include_children_predicate=include_children_predicate
        )
        if include_publishing_info:
            add_container_page_publishing_info(xblock, xblock_info)

        return xblock_info


def _get_gating_info(course, xblock):
    """
    Returns a dict containing gating information for the given xblock which
    can be added to xblock info responses.

    Arguments:
        course (CourseBlock): The course
        xblock (XBlock): The xblock

    Returns:
        dict: Gating information
    """
    info = {}
    if xblock.category == "sequential" and course.enable_subsection_gating:
        if not hasattr(course, "gating_prerequisites"):
            # Cache gating prerequisites on course block so that we are not
            # hitting the database for every xblock in the course
            course.gating_prerequisites = gating_api.get_prerequisites(course.id)
        info["is_prereq"] = gating_api.is_prerequisite(course.id, xblock.location)
        info["prereqs"] = [
            p
            for p in course.gating_prerequisites
            if str(xblock.location) not in p["namespace"]
        ]
        (
            prereq,
            prereq_min_score,
            prereq_min_completion,
        ) = gating_api.get_required_content(course.id, xblock.location)
        info["prereq"] = prereq
        info["prereq_min_score"] = prereq_min_score
        info["prereq_min_completion"] = prereq_min_completion
        if prereq:
            info["visibility_state"] = VisibilityState.gated
    return info


@pluggable_override("OVERRIDE_CREATE_XBLOCK_INFO")
def create_xblock_info(  # lint-amnesty, pylint: disable=too-many-statements
    xblock,
    data=None,
    metadata=None,
    include_ancestor_info=False,
    include_child_info=False,
    course_outline=False,
    include_children_predicate=NEVER,
    parent_xblock=None,
    graders=None,
    user=None,
    course=None,
    is_concise=False,
    summary_configuration=None,
    tags=None,
):
    """
    Creates the information needed for client-side XBlockInfo.

    If data or metadata are not specified, their information will not be added
    (regardless of whether or not the xblock actually has data or metadata).

    There are three optional boolean parameters:
      include_ancestor_info - if true, ancestor info is added to the response
      include_child_info - if true, direct child info is included in the response
      is_concise - if true, returns the concise version of xblock info, default is false.
      course_outline - if true, the xblock is being rendered on behalf of the course outline.
        There are certain expensive computations that do not need to be included in this case.

    In addition, an optional include_children_predicate argument can be provided to define whether or
    not a particular xblock should have its children included.

    You can customize the behavior of this function using the `OVERRIDE_CREATE_XBLOCK_INFO` pluggable override point.
    For example:
    >>> def create_xblock_info(default_fn, xblock, *args, **kwargs):
    ...     xblock_info = default_fn(xblock, *args, **kwargs)
    ...     xblock_info['icon'] = xblock.icon_override
    ...     return xblock_info
    """
    is_library_block = isinstance(xblock.location, LibraryUsageLocator)
    is_xblock_unit = is_unit(xblock, parent_xblock)
    # this should not be calculated for Sections and Subsections on Unit page or for library blocks
    has_changes = None
    if (is_xblock_unit or course_outline) and not is_library_block:
        has_changes = modulestore().has_changes(xblock)

    if graders is None:
        if not is_library_block:
            graders = CourseGradingModel.fetch(xblock.location.course_key).graders
        else:
            graders = []

    # Filter the graders data as needed
    graders = _filter_entrance_exam_grader(graders)

    # We need to load the course in order to retrieve user partition information.
    # For this reason, we load the course once and re-use it when recursively loading children.
    if course is None:
        course = modulestore().get_course(xblock.location.course_key)

    # Compute the child info first so it can be included in aggregate information for the parent
    should_visit_children = include_child_info and (
        course_outline and not is_xblock_unit or not course_outline
    )

    if summary_configuration is None:
        summary_configuration = AiAsideSummaryConfig(xblock.location.course_key)

    if should_visit_children and xblock.has_children:
        child_info = _create_xblock_child_info(
            xblock,
            course_outline,
            graders,
            include_children_predicate=include_children_predicate,
            user=user,
            course=course,
            is_concise=is_concise,
            summary_configuration=summary_configuration,
        )
    else:
        child_info = None

    release_date = _get_release_date(xblock, user)

    if xblock.category != "course" and not is_concise:
        visibility_state = _compute_visibility_state(
            xblock, child_info, is_xblock_unit and has_changes, is_self_paced(course)
        )
    else:
        visibility_state = None
    published = (
        modulestore().has_published_version(xblock) if not is_library_block else None
    )
    published_on = (
        get_default_time_display(xblock.published_on)
        if published and xblock.published_on
        else None
    )

    # defining the default value 'True' for delete, duplicate, drag and add new child actions
    # in xblock_actions for each xblock.
    # The unlinkable action is set to None by default, which means the action is not applicable for
    # any xblock unless explicitly set to True or False for a specific xblock condition.
    xblock_actions = {
        "deletable": True,
        "draggable": True,
        "childAddable": True,
        "duplicable": True,
        "unlinkable": None,
    }
    explanatory_message = None

    # is_entrance_exam is inherited metadata.
    if xblock.category == "chapter" and getattr(xblock, "is_entrance_exam", None):
        # Entrance exam section should not be deletable, draggable and not have 'New Subsection' button.
        xblock_actions["deletable"] = xblock_actions["childAddable"] = xblock_actions[
            "draggable"
        ] = False
        if parent_xblock is None:
            parent_xblock = get_parent_xblock(xblock)

        # Translators: The {pct_sign} here represents the percent sign, i.e., '%'
        # in many languages. This is used to avoid Transifex's misinterpreting of
        # '% o'. The percent sign is also translatable as a standalone string.
        explanatory_message = _(
            "Students must score {score}{pct_sign} or higher to access course materials."
        ).format(
            score=int(parent_xblock.entrance_exam_minimum_score_pct * 100),
            # Translators: This is the percent sign. It will be used to represent
            # a percent value out of 100, e.g. "58%" means "58/100".
            pct_sign=_("%"),
        )

    xblock_info = {
        "id": str(xblock.location),
        "display_name": xblock.display_name_with_default,
        "category": xblock.category,
        "has_children": xblock.has_children,
    }

    if course is not None and PUBLIC_VIDEO_SHARE.is_enabled(xblock.location.course_key):
        xblock_info.update(
            {
                "video_sharing_enabled": True,
                "video_sharing_options": course.video_sharing_options,
                "video_sharing_doc_url": HelpUrlExpert.the_one().url_for_token(
                    "social_sharing"
                ),
            }
        )

    if xblock.category == "course":
        discussions_config = DiscussionsConfiguration.get(course.id)
        show_unit_level_discussions_toggle = (
            discussions_config.enabled
            and discussions_config.supports_in_context_discussions()
            and discussions_config.enable_in_context
            and discussions_config.unit_level_visibility
        )
        xblock_info["unit_level_discussions"] = show_unit_level_discussions_toggle

    if is_concise:
        if child_info and child_info.get("children", []):
            xblock_info["child_info"] = child_info
        # Groups are labelled with their internal ids, rather than with the group name. Replace id with display name.
        group_display_name = get_split_group_display_name(xblock, course)
        xblock_info["display_name"] = (
            group_display_name if group_display_name else xblock_info["display_name"]
        )
    else:
        user_partitions = get_user_partition_info(xblock, course=course)
        xblock_info.update(
            {
                "edited_on": get_default_time_display(xblock.subtree_edited_on)
                if xblock.subtree_edited_on
                else None,
                "edited_on_raw": str(xblock.subtree_edited_on)
                if xblock.subtree_edited_on
                else None,
                "published": published,
                "published_on": published_on,
                "studio_url": xblock_studio_url(xblock, parent_xblock),
                "lms_url": xblock_lms_url(xblock),
                "embed_lms_url": xblock_embed_lms_url(xblock),
                "released_to_students": datetime.now(UTC) > xblock.start,
                "release_date": release_date,
                "visibility_state": visibility_state,
                "has_explicit_staff_lock": xblock.fields[
                    "visible_to_staff_only"
                ].is_set_on(xblock),
                "start": xblock.fields["start"].to_json(xblock.start),
                "graded": xblock.graded,
                "due_date": get_default_time_display(xblock.due),
                "due": xblock.fields["due"].to_json(xblock.due),
                "relative_weeks_due": xblock.relative_weeks_due,
                "format": xblock.format,
                "course_graders": [grader.get("type") for grader in graders],
                "has_changes": has_changes,
                "actions": xblock_actions,
                "explanatory_message": explanatory_message,
                "group_access": xblock.group_access,
                "user_partitions": user_partitions,
                "show_correctness": xblock.show_correctness,
                "hide_from_toc": xblock.hide_from_toc,
                "enable_hide_from_toc_ui": settings.FEATURES.get("ENABLE_HIDE_FROM_TOC_UI", False),
                "xblock_type": get_icon(xblock),
            }
        )

        if xblock.category == "sequential":
            xblock_info.update(
                {
                    "hide_after_due": xblock.hide_after_due,
                }
            )
        elif xblock.category in ("chapter", "course"):
            if xblock.category == "chapter":
                xblock_info.update(
                    {
                        "highlights": xblock.highlights,
                    }
                )
            elif xblock.category == "course":
                xblock_info.update(
                    {
                        "highlights_enabled_for_messaging": course.highlights_enabled_for_messaging,
                        "end": xblock.fields["end"].to_json(xblock.end),
                    }
                )
            xblock_info.update(
                {
                    # used to be controlled by a waffle switch, now just always enabled
                    "highlights_enabled": True,
                    # used to be controlled by a waffle flag, now just always disabled
                    "highlights_preview_only": False,
                    "highlights_doc_url": HelpUrlExpert.the_one().url_for_token(
                        "content_highlights"
                    ),
                }
            )

        # update xblock_info with special exam information if the feature flag is enabled
        if settings.FEATURES.get("ENABLE_SPECIAL_EXAMS"):
            if xblock.category == "course":
                xblock_info.update(
                    {
                        "enable_proctored_exams": xblock.enable_proctored_exams,
                        "create_zendesk_tickets": xblock.create_zendesk_tickets,
                        "enable_timed_exams": xblock.enable_timed_exams,
                    }
                )
            elif xblock.category == "sequential":
                rules_url = settings.PROCTORING_SETTINGS.get("LINK_URLS", {}).get(
                    "online_proctoring_rules", ""
                )

                # Only call does_backend_support_onboarding if not using an LTI proctoring provider
                if course.proctoring_provider != 'lti_external':
                    supports_onboarding = does_backend_support_onboarding(
                        course.proctoring_provider
                    )
                # NOTE: LTI proctoring doesn't support onboarding. If that changes, this value should change to True.
                else:
                    supports_onboarding = False

                proctoring_exam_configuration_link = None

                # only call get_exam_configuration_dashboard_url if not using an LTI proctoring provider
                if xblock.is_proctored_exam and (course.proctoring_provider != 'lti_external'):
                    try:
                        proctoring_exam_configuration_link = (
                            get_exam_configuration_dashboard_url(
                                course.id, xblock_info["id"]
                            )
                        )
                    except Exception as e:  # pylint: disable=broad-except
                        log.error(
                            f"Error while getting proctoring exam configuration link: {e}"
                        )

                xblock_info.update(
                    {
                        "is_proctored_exam": xblock.is_proctored_exam,
                        "was_exam_ever_linked_with_external": _was_xblock_ever_exam_linked_with_external(
                            course, xblock
                        ),
                        "online_proctoring_rules": rules_url,
                        "is_practice_exam": xblock.is_practice_exam,
                        "is_onboarding_exam": xblock.is_onboarding_exam,
                        "is_time_limited": xblock.is_time_limited,
                        "exam_review_rules": xblock.exam_review_rules,
                        "default_time_limit_minutes": xblock.default_time_limit_minutes,
                        "proctoring_exam_configuration_link": proctoring_exam_configuration_link,
                        "supports_onboarding": supports_onboarding,
                        "show_review_rules": show_review_rules(course.proctoring_provider),
                    }
                )

        # Update with gating info
        xblock_info.update(_get_gating_info(course, xblock))
        # Also add upstream info
        upstream_info = UpstreamLink.try_get_for_block(xblock, log_error=False).to_json()
        xblock_info["upstream_info"] = upstream_info

        if upstream_info["upstream_ref"]:
            # Disable adding or removing children component if xblock is imported from library
            xblock_actions["childAddable"] = False
            # Enable unlinking only for top level imported components
            xblock_actions["unlinkable"] = not upstream_info["top_level_parent_key"]

        if is_xblock_unit:
            # if xblock is a Unit we add the discussion_enabled option
            xblock_info["discussion_enabled"] = xblock.discussion_enabled

        if xblock.category == "sequential":
            # Entrance exam subsection should be hidden. in_entrance_exam is
            # inherited metadata, all children will have it.
            if getattr(xblock, "in_entrance_exam", False):
                xblock_info["is_header_visible"] = False

        if data is not None:
            xblock_info["data"] = data
        if metadata is not None:
            xblock_info["metadata"] = metadata
        if include_ancestor_info:
            xblock_info["ancestor_info"] = _create_xblock_ancestor_info(
                xblock, course_outline, include_child_info=True
            )
        if child_info:
            xblock_info["child_info"] = child_info
        if visibility_state == VisibilityState.staff_only:
            xblock_info["ancestor_has_staff_lock"] = ancestor_has_staff_lock(
                xblock, parent_xblock
            )
        else:
            xblock_info["ancestor_has_staff_lock"] = False
        if tags is not None:
            xblock_info["tags"] = tags

        # Don't show the "Manage Tags" option and tag counts if the DISABLE_TAGGING_FEATURE waffle is true
        xblock_info["is_tagging_feature_disabled"] = is_tagging_feature_disabled()
        if not is_tagging_feature_disabled():
            xblock_info["taxonomy_tags_widget_url"] = get_taxonomy_tags_widget_url()
            xblock_info["course_authoring_url"] = settings.COURSE_AUTHORING_MICROFRONTEND_URL

        if course_outline:
            if xblock_info["has_explicit_staff_lock"]:
                xblock_info["staff_only_message"] = True
            elif child_info and child_info["children"]:
                xblock_info["staff_only_message"] = all(
                    child["staff_only_message"] for child in child_info["children"]
                )
            else:
                xblock_info["staff_only_message"] = False

            if xblock_info["hide_from_toc"]:
                xblock_info["hide_from_toc_message"] = True
            elif child_info and child_info["children"]:
                xblock_info["hide_from_toc_message"] = all(
                    child["hide_from_toc_message"] for child in child_info["children"]
                )
            else:
                xblock_info["hide_from_toc_message"] = False

            if not is_tagging_feature_disabled():
                xblock_info["course_tags_count"] = _get_course_tags_count(course.id)
                xblock_info["tag_counts_by_block"] = _get_course_block_tags(xblock.location.context_key)

            xblock_info[
                "has_partition_group_components"
            ] = has_children_visible_to_specific_partition_groups(xblock)
        xblock_info["user_partition_info"] = get_visibility_partition_info(
            xblock, course=course
        )

        if course_outline or is_xblock_unit:
            # Previously, ENABLE_COPY_PASTE_UNITS was a feature flag; now it's always on.
            xblock_info["enable_copy_paste_units"] = True

        if is_xblock_unit and summary_configuration.is_enabled():
            xblock_info["summary_configuration_enabled"] = summary_configuration.is_summary_enabled(xblock_info['id'])

    return xblock_info


@request_cached()
def _get_course_tags_count(course_key) -> dict:
    """
    Get the count of tags that are applied to the course as a dict: {course_key: tags_count}
    """
    if not course_key.is_course:
        return {}  # Unsupported key type

    return get_object_tag_counts(str(course_key), count_implicit=True)


@request_cached()
def _get_course_block_tags(course_key) -> dict:
    """
    Get the count of tags that are applied to each block in this course, as a dict.
    """
    if not course_key.is_course:
        return {}  # Unsupported key type, e.g. a library

    # Create a pattern to match the IDs of all blocks, e.g. "block-v1:org+course+run+type@*"
    catch_all_key = course_key.make_usage_key("*", "x")
    catch_all_key_pattern = str(catch_all_key).rsplit("@*", 1)[0] + "@*"

    return get_object_tag_counts(catch_all_key_pattern, count_implicit=True)


def _was_xblock_ever_exam_linked_with_external(course, xblock):
    """
    Determine whether this XBlock is or was ever configured as an external proctored exam.

    If this block is *not* currently an externally linked proctored exam, the best way for us to tell
    whether it was was *ever* such is by checking whether
    edx-proctoring has an exam record associated with the block's ID,
    and the exam record has external_id.
    If an exception is not raised, then we know that such a record exists,
    indicating that this *was* once an externally linked proctored exam.

    Arguments:
        course (CourseBlock)
        xblock (XBlock)

    Returns: bool
    """
    try:
        exam = get_exam_by_content_id(course.id, xblock.location)
        return bool("external_id" in exam and exam["external_id"])
    except ProctoredExamNotFoundException:
        pass
    return False


def add_container_page_publishing_info(xblock, xblock_info):
    """
    Adds information about the xblock's publish state to the supplied
    xblock_info for the container page.
    """

    def safe_get_username(user_id):
        """
        Guard against bad user_ids, like the infamous "**replace_user**".
        Note that this will ignore our special known IDs (ModuleStoreEnum.UserID).
        We should consider adding special handling for those values.

        :param user_id: the user id to get the username of
        :return: username, or None if the user does not exist or user_id is None
        """
        if user_id:
            try:
                return User.objects.get(id=user_id).username
            except:  # pylint: disable=bare-except
                pass

        return None

    xblock_info["edited_by"] = safe_get_username(xblock.subtree_edited_by)
    xblock_info["published_by"] = safe_get_username(xblock.published_by)
    xblock_info["currently_visible_to_students"] = is_currently_visible_to_students(
        xblock
    )
    xblock_info[
        "has_partition_group_components"
    ] = has_children_visible_to_specific_partition_groups(xblock)
    if xblock_info["release_date"]:
        xblock_info["release_date_from"] = _get_release_date_from(xblock)
    if xblock_info["visibility_state"] == VisibilityState.staff_only:
        xblock_info["staff_lock_from"] = _get_staff_lock_from(xblock)
    else:
        xblock_info["staff_lock_from"] = None


class VisibilityState:
    """
    Represents the possible visibility states for an xblock:

      live - the block and all of its descendants are live to students (excluding staff only items)
        Note: Live means both published and released.

      ready - the block is ready to go live and all of its descendants are live or ready (excluding staff only items)
        Note: content is ready when it is published and scheduled with a release date in the future.

      unscheduled - the block and all of its descendants have no release date (excluding staff only items)
        Note: it is valid for items to be published with no release date in which case they are still unscheduled.

      needs_attention - the block or its descendants are not fully live, ready or unscheduled
        (excluding staff only items)
        For example: one subsection has draft content, or there's both unreleased and released content in one section.

      staff_only - all of the block's content is to be shown to staff only
        Note: staff only items do not affect their parent's state.

      gated - all of the block's content is to be shown to students only after the configured prerequisite is met
    """

    live = "live"
    ready = "ready"
    unscheduled = "unscheduled"
    needs_attention = "needs_attention"
    staff_only = "staff_only"
    gated = "gated"
    hide_from_toc = "hide_from_toc"


def _compute_visibility_state(
    xblock, child_info, is_unit_with_changes, is_course_self_paced=False
):
    """
    Returns the current publish state for the specified xblock and its children
    """
    if xblock.visible_to_staff_only:
        return VisibilityState.staff_only
    elif xblock.hide_from_toc:
        return VisibilityState.hide_from_toc
    elif is_unit_with_changes:
        # Note that a unit that has never been published will fall into this category,
        # as well as previously published units with draft content.
        return VisibilityState.needs_attention

    is_unscheduled = xblock.start == DEFAULT_START_DATE
    is_live = is_course_self_paced or datetime.now(UTC) > xblock.start
    if child_info and child_info.get("children", []):  # pylint: disable=too-many-nested-blocks
        all_staff_only = True
        all_unscheduled = True
        all_live = True
        all_hide_from_toc = True
        for child in child_info["children"]:
            child_state = child["visibility_state"]
            if child_state == VisibilityState.needs_attention:
                return child_state
            elif not child_state == VisibilityState.staff_only:
                all_staff_only = False
                if not child_state == VisibilityState.hide_from_toc:
                    all_hide_from_toc = False
                    if not child_state == VisibilityState.unscheduled:
                        all_unscheduled = False
                        if not child_state == VisibilityState.live:
                            all_live = False
        if all_staff_only:
            return VisibilityState.staff_only
        elif all_hide_from_toc:
            return VisibilityState.hide_from_toc
        elif all_unscheduled:
            return (
                VisibilityState.unscheduled
                if is_unscheduled
                else VisibilityState.needs_attention
            )
        elif all_live:
            return VisibilityState.live if is_live else VisibilityState.needs_attention
        else:
            return (
                VisibilityState.ready
                if not is_unscheduled
                else VisibilityState.needs_attention
            )
    if is_live:
        return VisibilityState.live
    elif is_unscheduled:
        return VisibilityState.unscheduled
    else:
        return VisibilityState.ready


def _create_xblock_ancestor_info(
    xblock, course_outline=False, include_child_info=False, is_concise=False
):
    """
    Returns information about the ancestors of an xblock. Note that the direct parent will also return
    information about all of its children.
    """
    ancestors = []

    def collect_ancestor_info(ancestor, include_child_info=False, is_concise=False):
        """
        Collect xblock info regarding the specified xblock and its ancestors.
        """
        if ancestor:
            direct_children_only = lambda parent: parent == ancestor
            ancestors.append(
                create_xblock_info(
                    ancestor,
                    include_child_info=include_child_info,
                    course_outline=course_outline,
                    include_children_predicate=direct_children_only,
                    is_concise=is_concise,
                )
            )
            collect_ancestor_info(get_parent_xblock(ancestor), is_concise=is_concise)

    collect_ancestor_info(
        get_parent_xblock(xblock),
        include_child_info=include_child_info,
        is_concise=is_concise,
    )
    return {"ancestors": ancestors}


def _create_xblock_child_info(
    xblock,
    course_outline,
    graders,
    include_children_predicate=NEVER,
    user=None,
    course=None,
    is_concise=False,
    summary_configuration=None,
):
    """
    Returns information about the children of an xblock, as well as about the primary category
    of xblock expected as children.
    """
    child_info = {}
    child_category = xblock_primary_child_category(xblock)
    if child_category:
        child_info = {
            "category": child_category,
            "display_name": xblock_type_display_name(
                child_category, default_display_name=child_category
            ),
        }
    if xblock.has_children and include_children_predicate(xblock):
        child_info["children"] = [
            create_xblock_info(
                child,
                include_child_info=True,
                course_outline=course_outline,
                include_children_predicate=include_children_predicate,
                parent_xblock=xblock,
                graders=graders,
                user=user,
                course=course,
                is_concise=is_concise,
                summary_configuration=summary_configuration,
            )
            for child in xblock.get_children()
        ]
    return child_info


def _get_release_date(xblock, user=None):
    """
    Returns the release date for the xblock, or None if the release date has never been set.
    """
    # If year of start date is less than 1900 then reset the start date to DEFAULT_START_DATE
    reset_to_default = False
    try:
        reset_to_default = xblock.start.year < 1900
    except (ValueError, AttributeError):
        # For old mongo courses, accessing the start attribute calls `to_json()`,
        # which raises a `ValueError` for years < 1900.
        reset_to_default = True

    if reset_to_default and user:
        xblock.start = DEFAULT_START_DATE
        xblock = save_xblock_with_callback(xblock, user)

    # Treat DEFAULT_START_DATE as a magic number that means the release date has not been set
    return (
        get_default_time_display(xblock.start)
        if xblock.start != DEFAULT_START_DATE
        else None
    )


def validate_and_update_xblock_due_date(xblock):
    """
    Validates the due date for the xblock, and set to None if pre-1900 due date provided
    """
    if xblock.due and xblock.due.year < 1900:
        xblock.due = None


def _get_release_date_from(xblock):
    """
    Returns a string representation of the section or subsection that sets the xblock's release date
    """
    return _xblock_type_and_display_name(find_release_date_source(xblock))


def _get_staff_lock_from(xblock):
    """
    Returns a string representation of the section or subsection that sets the xblock's release date
    """
    source = find_staff_lock_source(xblock)
    return _xblock_type_and_display_name(source) if source else None


def _xblock_type_and_display_name(xblock):
    """
    Returns a string representation of the xblock's type and display name
    """
    return _('{section_or_subsection} "{display_name}"').format(
        section_or_subsection=xblock_type_display_name(xblock),
        display_name=xblock.display_name_with_default,
    )
