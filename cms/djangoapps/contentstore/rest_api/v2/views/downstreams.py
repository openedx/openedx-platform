"""
API Views for managing & syncing links between upstream & downstream content

API paths (We will move these into proper api_doc_tools annotations soon
https://github.com/openedx/edx-platform/issues/35653):

  /api/contentstore/v2/downstreams/{usage_key_string}

    GET: Inspect a single downstream block's link to upstream content.
      200: Upstream link details successfully fetched. Returns UpstreamLink (may contain an error_message).
      404: Downstream block not found or user lacks permission to edit it.

    DELETE: Sever a single downstream block's link to upstream content.
      204: Block successfully unlinked (or it wasn't linked in the first place). No response body.
      404: Downstream block not found or user lacks permission to edit it.

    PUT: Establish or modify a single downstream block's link to upstream content. An authoring client could use this
         endpoint to add library content in a two-step process, specifically: (1) add a blank block to a course, then
         (2) link it to a content library with ?sync=True.
      REQUEST BODY: {
        "upstream_ref": str,  // reference to upstream block (eg, library block usage key)
        "sync": bool,  // whether to sync in upstream content (False by default)
      }
      200: Downstream block's upstream link successfully edited (and synced, if requested). Returns UpstreamLink.
      400: upstream_ref is malformed, missing, or inaccessible.
      400: Content at upstream_ref does not support syncing.
      404: Downstream block not found or user lacks permission to edit it.

  /api/contentstore/v2/downstreams/{usage_key_string}/sync

    POST: Sync a downstream block with upstream content.
      200: Downstream block successfully synced with upstream content.
      400: Downstream block is not linked to upstream content.
      400: Upstream is malformed, missing, or inaccessible.
      400: Upstream block does not support syncing.
      404: Downstream block not found or user lacks permission to edit it.

    DELETE: Decline an available sync for a downstream block.
      204: Sync successfuly dismissed. No response body.
      400: Downstream block is not linked to upstream content.
      404: Downstream block not found or user lacks permission to edit it.

  /api/contentstore/v2/upstream/{usage_key_string}/downstream-links

    GET: List all downstream blocks linked to a library block.
        200: A list of downstream usage_keys linked to the library block.

  /api/contentstore/v2/downstreams
  /api/contentstore/v2/downstreams?course_id=course-v1:A+B+C&ready_to_sync=true
      GET: List downstream blocks that can be synced, filterable by course or sync-readiness.
        200: A paginated list of applicable & accessible downstream blocks. Entries are ComponentLinks.

  /api/contentstore/v2/downstreams/<course_key>/summary
      GET: List summary of links by course key
        200: A list of summary of links by course key
        Example:
        [
            {
                "upstream_context_title": "CS problems 3",
                "upstream_context_key": "lib:OpenedX:CSPROB3",
                "ready_to_sync_count": 11,
                "total_count": 14
            },
            {
                "upstream_context_title": "CS problems 2",
                "upstream_context_key": "lib:OpenedX:CSPROB2",
                "ready_to_sync_count": 15,
                "total_count": 24
            },
        ]

UpstreamLink response schema:
  {
    "upstream_ref": string?
    "version_synced": string?,
    "version_available": string?,
    "version_declined": string?,
    "error_message": string?,
    "ready_to_sync": Boolean
  }
"""

import logging
from itertools import chain

from attrs import asdict as attrs_asdict
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.db.models import QuerySet
from edx_rest_framework_extensions.paginators import DefaultPagination
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryLocatorV2, LibraryUsageLocatorV2
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.fields import BooleanField
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from xblock.core import XBlock

from cms.djangoapps.contentstore.models import ComponentLink, ContainerLink, EntityLinkBase
from cms.djangoapps.contentstore.rest_api.v2.serializers import (
    PublishableEntityLinkSerializer,
    PublishableEntityLinksSummarySerializer,
)
from cms.djangoapps.contentstore.xblock_storage_handlers.view_handlers import sync_library_content
from cms.lib.xblock.upstream_sync import (
    BadDownstream,
    BadUpstream,
    NoUpstream,
    UpstreamLink,
    UpstreamLinkException,
    decline_sync,
    sever_upstream_link,
)
from cms.lib.xblock.upstream_sync_block import fetch_customizable_fields_from_block
from cms.lib.xblock.upstream_sync_container import fetch_customizable_fields_from_container
from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access
from openedx.core.djangoapps.content_libraries import api as lib_api
from openedx.core.djangoapps.video_config.transcripts_utils import clear_transcripts
from openedx.core.lib.api.view_utils import DeveloperErrorViewMixin, view_auth_classes
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

logger = logging.getLogger(__name__)


class _AuthenticatedRequest(Request):
    """
    Alias for the `Request` class which tells mypy to assume that `.user` is not an AnonymousUser.

    Using this does NOT ensure the request is actually authenticated--
    you will some other way to ensure that, such as `@view_auth_classes(is_authenticated=True)`.
    """
    user: User


class DownstreamListPaginator(DefaultPagination):
    """Custom paginator for downstream entity links"""
    page_size = 100
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        if 'no_page' in request.query_params:
            return queryset

        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data, *args, **kwargs):
        if 'no_page' in args[0].query_params:
            return Response(data)
        response = super().get_paginated_response(data)
        # replace next and previous links by next and previous page number
        response.data.update({
            'next_page_num': self.page.next_page_number() if self.page.has_next() else None,
            'previous_page_num': self.page.previous_page_number() if self.page.has_previous() else None,
        })
        return response


@view_auth_classes()
class DownstreamListView(DeveloperErrorViewMixin, APIView):
    """
    [ 🛑 UNSTABLE ]
    List all items (components and containers) wich are linked to an upstream context, with optional filtering.

    * `course_key_string`: Get the links of a specific course.
    * `upstream_key`: Get the dowstream links of a spscific upstream component or container.
    * `ready_to_sync`: Boolean to filter links that are ready to sync.
    * `use_top_level_parents`: Set to True to return the top-level parents instead of downstream child,
       if this parent exists.
    * `item_type`: Filter the links by `components` or `containers`.
    """

    def get(self, request: _AuthenticatedRequest):
        """
        Fetches publishable entity links for given course key
        """
        course_key_string = request.GET.get('course_id')
        upstream_key = request.GET.get('upstream_key')
        ready_to_sync = request.GET.get('ready_to_sync')
        use_top_level_parents = request.GET.get('use_top_level_parents')
        item_type = request.GET.get('item_type')

        link_filter: dict[str, CourseKey | UsageKey | LibraryContainerLocator | bool] = {}
        paginator = DownstreamListPaginator()

        if course_key_string is None and upstream_key is None and not request.user.is_superuser:
            # This case without course or upstream filter means that the user need permissions to
            # multiple courses/libraries, so raise `PermissionDenied` if the user is not superuser.
            raise PermissionDenied

        if course_key_string:
            try:
                course_key = CourseKey.from_string(course_key_string)
                link_filter["downstream_context_key"] = course_key
            except InvalidKeyError as exc:
                raise ValidationError(detail=f"Malformed course key: {course_key_string}") from exc

            if not has_studio_read_access(request.user, course_key):
                raise PermissionDenied
        if ready_to_sync is not None:
            link_filter["ready_to_sync"] = BooleanField().to_internal_value(ready_to_sync)
        if use_top_level_parents is not None:
            link_filter["use_top_level_parents"] = BooleanField().to_internal_value(use_top_level_parents)
        if upstream_key:
            try:
                upstream_usage_key = UsageKey.from_string(upstream_key)
                link_filter["upstream_usage_key"] = upstream_usage_key

                # Verify that the user has permission to view the library that contains
                # the upstream component
                lib_api.require_permission_for_library_key(
                    LibraryLocatorV2.from_string(str(upstream_usage_key.context_key)),
                    request.user,
                    permission=lib_api.permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
                )
                # At this point we just need to bring components
                item_type = 'components'
            except InvalidKeyError:
                try:
                    upstream_container_key = LibraryContainerLocator.from_string(upstream_key)
                    link_filter["upstream_container_key"] = upstream_container_key
                    # Verify that the user has permission to view the library that contains
                    # the upstream container
                    lib_api.require_permission_for_library_key(
                        upstream_container_key.lib_key,
                        request.user,
                        permission=lib_api.permissions.CAN_VIEW_THIS_CONTENT_LIBRARY,
                    )
                    # At this point we just need to bring containers
                    item_type = 'containers'
                except InvalidKeyError as exc:
                    raise ValidationError(detail=f"Malformed key: {upstream_key}") from exc
        links: list[EntityLinkBase] | QuerySet[EntityLinkBase] = []
        if item_type is None or item_type == 'all':
            # itertools.chain() efficiently concatenates multiple iterables into one iterator,
            # yielding items from each in sequence without creating intermediate lists.
            links = list(chain(
                ComponentLink.filter_links(**link_filter),
                ContainerLink.filter_links(**link_filter)
            ))

            if use_top_level_parents is not None:
                # Delete duplicates. From `ComponentLink` and `ContainerLink`
                # repeated containers may come in this case:
                # If we have a `Unit A` and a `Component B`, if you update and publish
                # both, form `ComponentLink` and `ContainerLink` you get the same `Unit A`.
                links = self._remove_duplicates(links)

        elif item_type == 'components':
            links = ComponentLink.filter_links(**link_filter)
        elif item_type == 'containers':
            links = ContainerLink.filter_links(**link_filter)
        paginated_links = paginator.paginate_queryset(links, self.request, view=self)
        serializer = PublishableEntityLinkSerializer(paginated_links, many=True)
        return paginator.get_paginated_response(serializer.data, self.request)

    def _remove_duplicates(self, links: list[EntityLinkBase]) -> list[EntityLinkBase]:
        """
        Remove duplicates based on `EntityLinkBase.downstream_usage_key`
        """
        seen_keys = set()
        unique_links = []

        for link in links:
            if link.downstream_usage_key not in seen_keys:
                seen_keys.add(link.downstream_usage_key)
                unique_links.append(link)

        return unique_links


@view_auth_classes()
class DownstreamSummaryView(DeveloperErrorViewMixin, APIView):
    """
    [ 🛑 UNSTABLE ]
    Serves course->library publishable entity links summary
    """
    def get(self, request: _AuthenticatedRequest, course_key_string: str):
        """
        Fetches publishable entity links summary for given course key
        Example:
        [
            {
                "upstream_context_title": "CS problems 3",
                "upstream_context_key": "lib:OpenedX:CSPROB3",
                "ready_to_sync_count": 11,
                "total_count": 14
                "last_published_at": "2025-05-02T20:20:44.989042Z"
            },
            {
                "upstream_context_title": "CS problems 2",
                "upstream_context_key": "lib:OpenedX:CSPROB2",
                "ready_to_sync_count": 15,
                "total_count": 24,
                "last_published_at": "2025-05-03T21:20:44.989042Z"
            },
        ]
        """
        try:
            course_key = CourseKey.from_string(course_key_string)
        except InvalidKeyError as exc:
            raise ValidationError(detail=f"Malformed course key: {course_key_string}") from exc

        if not has_studio_read_access(request.user, course_key):
            raise PermissionDenied

        # Gets all links of the Course, using the
        # top-level parents filter (see `filter_links()` for more info about top-level parents).
        # `itertools.chain()` efficiently concatenates multiple iterables into one iterator,
        # yielding items from each in sequence without creating intermediate lists.
        links = list(chain(
            ComponentLink.filter_links(
                downstream_context_key=course_key,
                use_top_level_parents=True,
            ),
            ContainerLink.filter_links(
                downstream_context_key=course_key,
                use_top_level_parents=True,
            ),
        ))

        # Delete duplicates. From `ComponentLink` and `ContainerLink`
        # repeated containers may come in this case:
        # If we have a `Unit A` and a `Component B`, if you update and publish
        # both, form `ComponentLink` and `ContainerLink` you get the same `Unit A`.
        links = self._remove_duplicates(links)
        result = {}

        for link in links:
            # We iterate each list to do the counting by Library (`context_key`)
            context_key = link.upstream_context_key

            if context_key not in result:
                result[context_key] = {
                    "upstream_context_key": context_key,
                    "upstream_context_title": link.upstream_context_title,
                    "ready_to_sync_count": 0,
                    "total_count": 0,
                    "last_published_at": None,
                }

            # Total count
            result[context_key]["total_count"] += 1

            # Ready to sync count, it also checks if the container has
            # descendants that need sync (`ready_to_sync_from_children`).
            if link.ready_to_sync or link.ready_to_sync_from_children:  # type: ignore[attr-defined]
                result[context_key]["ready_to_sync_count"] += 1

            # The Max `published_at` value
            # An AttributeError may be thrown if copied/pasted an unpublished item from library to course.
            # That case breaks all the course library sync page.
            # TODO: Delete this `try` after avoid copy/paster unpublished items.
            try:
                published_at = link.published_at
            except AttributeError:
                published_at = None
            if published_at is not None and (
                result[context_key]["last_published_at"] is None
                or result[context_key]["last_published_at"] < published_at
            ):
                result[context_key]["last_published_at"] = published_at

        serializer = PublishableEntityLinksSummarySerializer(list(result.values()), many=True)
        return Response(serializer.data)

    def _remove_duplicates(self, links: list[EntityLinkBase]) -> list[EntityLinkBase]:
        """
        Remove duplicates based on `EntityLinkBase.downstream_usage_key`
        """
        seen_keys = set()
        unique_links = []

        for link in links:
            if link.downstream_usage_key not in seen_keys:
                seen_keys.add(link.downstream_usage_key)
                unique_links.append(link)

        return unique_links


@view_auth_classes(is_authenticated=True)
class DownstreamView(DeveloperErrorViewMixin, APIView):
    """
    [ 🛑 UNSTABLE ]
    Inspect or manage an XBlock's link to upstream content.
    """
    def get(self, request: _AuthenticatedRequest, usage_key_string: str) -> Response:
        """
        Inspect an XBlock's link to upstream content.
        """
        downstream = _load_accessible_block(request.user, usage_key_string, require_write_access=False)
        return Response(UpstreamLink.try_get_for_block(downstream).to_json(include_child_info=True))

    def put(self, request: _AuthenticatedRequest, usage_key_string: str) -> Response:
        """
        Edit an XBlock's link to upstream content.
        """
        downstream = _load_accessible_block(request.user, usage_key_string, require_write_access=True)
        new_upstream_ref = request.data.get("upstream_ref")

        # Set `downstream.upstream` so that we can try to sync and/or fetch.
        # Note that, if this fails and we raise a 4XX, then we will not call modulstore().update_item,
        # thus preserving the former value of `downstream.upstream`.
        downstream.upstream = new_upstream_ref
        sync_param = request.data.get("sync", "false")
        if isinstance(sync_param, str):
            sync_param = sync_param.lower()
        if sync_param not in ["true", "false", True, False]:
            raise ValidationError({"sync": "must be 'true' or 'false'"})
        try:
            if sync_param == "true" or sync_param is True:
                sync_library_content(
                    downstream=downstream,
                    request=request,
                    store=modulestore()
                )
            else:
                # Even if we're not syncing (i.e., updating the downstream's values with the upstream's), we still need
                # to fetch the upstream's customizable values and store them as hidden fields on the downstream. This
                # ensures that downstream authors can restore defaults based on the upstream.
                link = UpstreamLink.get_for_block(downstream)
                if isinstance(link.upstream_key, LibraryUsageLocatorV2):
                    fetch_customizable_fields_from_block(downstream=downstream, user=request.user)
                else:
                    assert isinstance(link.upstream_key, LibraryContainerLocator)
                    fetch_customizable_fields_from_container(downstream=downstream)
        except BadDownstream as exc:
            logger.exception(
                "'%s' is an invalid downstream; refusing to set its upstream to '%s'",
                usage_key_string,
                new_upstream_ref,
            )
            raise ValidationError(str(exc)) from exc
        except BadUpstream as exc:
            logger.exception(
                "'%s' is an invalid upstream reference; refusing to set it as upstream of '%s'",
                new_upstream_ref,
                usage_key_string,
            )
            raise ValidationError({"upstream_ref": str(exc)}) from exc
        except NoUpstream as exc:
            raise ValidationError({"upstream_ref": "value missing"}) from exc
        modulestore().update_item(downstream, request.user.id)
        # Note: We call `get_for_block` (rather than `try_get_for_block`) because if anything is wrong with the
        #       upstream at this point, then that is completely unexpected, so it's appropriate to let the 500 happen.
        return Response(UpstreamLink.get_for_block(downstream).to_json())

    def delete(self, request: _AuthenticatedRequest, usage_key_string: str) -> Response:
        """
        Sever an XBlock's link to upstream content.
        """
        downstream = _load_accessible_block(request.user, usage_key_string, require_write_access=True)
        affected_blocks: list[XBlock] = []
        # Get the upstream ref before severing the link, so we can delete
        # the corresponding ComponentLink or ContainerLink below.
        upstream_ref = downstream.upstream
        try:

            affected_blocks = sever_upstream_link(downstream)

            # Remove the ComponentLink or ContainerLink, if it exists.
            if upstream_ref:
                try:
                    ComponentLink.get_by_downstream_usage_key(downstream.usage_key).delete()
                except ComponentLink.DoesNotExist:
                    try:
                        ContainerLink.get_by_downstream_usage_key(downstream.usage_key).delete()
                    except ContainerLink.DoesNotExist:
                        # If neither link exists, that's fine--we just wanted to clean up if possible.
                        pass
        except NoUpstream:
            logger.exception(
                "Tried to DELETE upstream link of '%s', but it wasn't linked to anything in the first place. "
                "Will do nothing. ",
                usage_key_string,
            )
        finally:
            if affected_blocks:
                # If we successfully severed the upstream link, then we need to update the affected blocks.
                with modulestore().bulk_operations(downstream.usage_key.context_key):
                    for block in affected_blocks:
                        modulestore().update_item(block, request.user.id)

        return Response(status=204)


@view_auth_classes(is_authenticated=True)
class SyncFromUpstreamView(DeveloperErrorViewMixin, APIView):
    """
    [ 🛑 UNSTABLE ]
    Accept or decline an opportunity to sync a downstream block from its upstream content.
    """

    def post(self, request: _AuthenticatedRequest, usage_key_string: str) -> Response:
        """
        Pull latest updates to the block at {usage_key_string} from its linked upstream content.
        Optionally accepts json data in below format to control override of locally customized fields
        {
            "override_customizations": True or False (default: False),
            "keep_custom_fields": [] (If override_customizations is True, this key can be used to preserve
                some fields from override).
        }
        """
        downstream = _load_accessible_block(request.user, usage_key_string, require_write_access=True)
        try:
            if downstream.usage_key.block_type == "video":
                # Delete all transcripts so we can copy new ones from upstream
                clear_transcripts(downstream)
            static_file_notices = sync_library_content(
                downstream=downstream,
                request=request,
                store=modulestore()
            )
        except UpstreamLinkException as exc:
            logger.exception(
                "Could not sync from upstream '%s' to downstream '%s'",
                downstream.upstream,
                usage_key_string,
            )
            raise ValidationError(detail=str(exc)) from exc
        # Note: We call `get_for_block` (rather than `try_get_for_block`) because if anything is wrong with the
        #       upstream at this point, then that is completely unexpected, so it's appropriate to let the 500 happen.
        response = UpstreamLink.get_for_block(downstream).to_json()
        response["static_file_notices"] = attrs_asdict(static_file_notices)
        return Response(response)

    def delete(self, request: _AuthenticatedRequest, usage_key_string: str) -> Response:
        """
        Decline the latest updates to the block at {usage_key_string}.
        """
        downstream = _load_accessible_block(request.user, usage_key_string, require_write_access=True)
        if downstream.upstream is None:
            raise ValidationError(str(NoUpstream()))
        try:
            decline_sync(downstream, request.user.id)
        except (NoUpstream, BadUpstream, BadDownstream) as exc:
            # This is somewhat unexpected. If the upstream link is missing or invalid, then the downstream author
            # shouldn't have been prompted to accept/decline a sync in the first place. Of course, they could have just
            # hit the HTTP API anyway, or they could be viewing a Studio page which hasn't been refreshed in a while.
            # So, it's a 400, not a 500.
            logger.exception(
                "Tried to decline a sync to downstream '%s', but the upstream link '%s' is invalid.",
                usage_key_string,
                downstream.upstream,
            )
            raise ValidationError(str(exc)) from exc
        return Response(status=204)


def _load_accessible_block(user: User, usage_key_string: str, *, require_write_access: bool) -> XBlock:
    """
    Given a logged in-user and a serialized usage key of an upstream-linked XBlock, load it from the ModuleStore,
    raising a DRF-friendly exception if anything goes wrong.

    Raises NotFound if usage key is malformed, if the user lacks access, or if the block doesn't exist.
    """
    not_found = NotFound(detail=f"Block not found or not accessible: {usage_key_string}")
    try:
        usage_key = UsageKey.from_string(usage_key_string)
    except InvalidKeyError as exc:
        raise ValidationError(detail=f"Malformed block usage key: {usage_key_string}") from exc
    if require_write_access and not has_studio_write_access(user, usage_key.context_key):
        raise not_found
    if not has_studio_read_access(user, usage_key.context_key):
        raise not_found
    try:
        block = modulestore().get_item(usage_key)
    except ItemNotFoundError as exc:
        raise not_found from exc
    return block
