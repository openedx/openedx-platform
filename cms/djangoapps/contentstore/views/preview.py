# lint-amnesty, pylint: disable=missing-module-docstring

import logging
from functools import partial

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseBadRequest
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from opaque_keys.edx.keys import UsageKey
from opaque_keys.edx.locator import LibraryContainerLocator
from rest_framework.request import Request
from web_fragments.fragment import Fragment
from xblock.django.request import django_to_webob_request, webob_to_django_response
from xblock.exceptions import NoSuchHandlerError, NotFoundError, ProcessingError
from xblock.runtime import KvsFieldData

from openedx.core.djangoapps.video_config.services import VideoConfigService
from openedx.core.djangoapps.discussions.services import DiscussionConfigService
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError as XModuleNotFoundError
from xmodule.modulestore.django import XBlockI18nService, modulestore
from xmodule.partitions.partitions_service import PartitionService
from xmodule.services import SettingsService, TeamsConfigurationService
from xmodule.studio_editable import has_author_view
from xmodule.util.sandboxing import SandboxService
from xmodule.util.builtin_assets import add_webpack_js_to_fragment
from xmodule.x_module import AUTHOR_VIEW, PREVIEW_VIEWS, STUDENT_VIEW, XModuleMixin
from cms.djangoapps.xblock_config.models import StudioConfig
from cms.djangoapps.contentstore.toggles import individualize_anonymous_user_id
from cms.lib.xblock.field_data import CmsFieldData
from cms.lib.xblock.upstream_sync import UpstreamLink
from common.djangoapps.static_replace.services import ReplaceURLService
from common.djangoapps.static_replace.wrapper import replace_urls_wrapper
from common.djangoapps.student.models import anonymous_id_for_user
from common.djangoapps.edxmako.shortcuts import render_to_string
from common.djangoapps.edxmako.services import MakoService
from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from openedx.core.lib.license import wrap_with_license
from openedx.core.lib.cache_utils import CacheService
from openedx.core.lib.xblock_utils import (
    request_token,
    wrap_fragment,
    wrap_xblock,
    wrap_xblock_aside
)

from ..utils import StudioPermissionsService, get_visibility_partition_info
from .access import get_user_role
from .session_kv_store import SessionKeyValueStore

__all__ = ['preview_handler']

log = logging.getLogger(__name__)


@login_required
@xframe_options_exempt
def preview_handler(request, usage_key_string, handler, suffix=''):
    """
    Dispatch an AJAX action to an xblock

    usage_key_string: The usage_key_string-id of the block to dispatch to, passed through `quote_slashes`
    handler: The handler to execute
    suffix: The remainder of the url to be passed to the handler
    """
    usage_key = UsageKey.from_string(usage_key_string)

    block = modulestore().get_item(usage_key)
    instance = _load_preview_block(request, block)

    # Let the module handle the AJAX
    req = django_to_webob_request(request)
    try:
        resp = instance.handle(handler, req, suffix)

    except NoSuchHandlerError:
        log.exception("XBlock %s attempted to access missing handler %r", instance, handler)
        raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

    except (XModuleNotFoundError, NotFoundError):
        log.exception("Module indicating to user that request doesn't exist")
        raise Http404  # lint-amnesty, pylint: disable=raise-missing-from

    except ProcessingError:
        log.warning("Module raised an error while processing AJAX request",
                    exc_info=True)
        return HttpResponseBadRequest()

    except Exception:
        log.exception("error processing ajax call")
        raise

    return webob_to_django_response(resp)


def handler_url(block, handler_name, suffix='', query='', thirdparty=False):  # lint-amnesty, pylint: disable=unused-argument
    """
    Handler URL function for Preview
    """
    return reverse('preview_handler', kwargs={
        'usage_key_string': str(block.scope_ids.usage_id),
        'handler': handler_name,
        'suffix': suffix,
    }) + '?' + query


def preview_applicable_aside_types(block, applicable_aside_types=None):
    """
    Remove acid_aside and honor the config record
    """
    if not StudioConfig.asides_enabled(block.scope_ids.block_type):
        return []

    # TODO: aside_type != 'acid_aside' check should be removed once AcidBlock is only installed during tests
    # (see https://openedx.atlassian.net/browse/TE-811)
    return [
        aside_type
        for aside_type in applicable_aside_types(block)
        if aside_type != 'acid_aside'
    ]


def render_child_placeholder(block, view_name, context, wrap_block=None):
    """
    Renders a placeholder XBlock.
    """
    return wrap_block(block, view_name, Fragment(), context)


def preview_layout_asides(block, context, frag, view_name, aside_frag_fns, wrap_aside=None):
    """
    Custom layout of asides for preview
    """
    position_for_asides = '<!-- footer for xblock_aside -->'
    result = Fragment()
    result.add_fragment_resources(frag)

    for aside, aside_fn in aside_frag_fns:
        aside_frag = aside_fn(block, context)
        if aside_frag.content != '':
            aside_frag_wrapped = wrap_aside(block, aside, view_name, aside_frag, context)
            aside.save()
            result.add_fragment_resources(aside_frag_wrapped)
            replacement = position_for_asides + aside_frag_wrapped.content
            frag.content = frag.content.replace(position_for_asides, replacement)

    result.add_content(frag.content)
    return result


def _prepare_runtime_for_preview(request, block):
    """
    Sets properties in the runtime of the specified block that is
    required for rendering block previews.

    request: The active django request
    block: An XBlock
    """

    course_id = block.location.course_key
    display_name_only = (block.category == 'static_tab')

    wrappers = [
        # This wrapper wraps the block in the template specified above
        partial(
            wrap_xblock,
            'PreviewRuntime',
            display_name_only=display_name_only,
            usage_id_serializer=str,
            request_token=request_token(request)
        ),

        # This wrapper replaces urls in the output that start with /static
        # with the correct course-specific url for the static content
        partial(replace_urls_wrapper, replace_url_service=ReplaceURLService, static_replace_only=True),
        _studio_wrap_xblock,
    ]

    wrappers_asides = [
        partial(
            wrap_xblock_aside,
            'PreviewRuntime',
            usage_id_serializer=str,
            request_token=request_token(request)
        )
    ]

    mako_service = MakoService(namespace_prefix='lms.')
    if settings.FEATURES.get("LICENSING", False):
        # stick the license wrapper in front
        wrappers.insert(0, partial(wrap_with_license, mako_service=mako_service))

    anonymous_user_id = deprecated_anonymous_user_id = 'student'
    if individualize_anonymous_user_id(course_id):
        anonymous_user_id = anonymous_id_for_user(request.user, course_id)
        # See the docstring of `DjangoXBlockUserService`.
        deprecated_anonymous_user_id = anonymous_id_for_user(request.user, None)

    # NOTE: As of Ulmo, these services only apply to the preview views. If you want a service to be present in all
    # Studio ModuleStoreRuntimes, then add it to load_services_for_studio.
    # HISTORICAL CONTEXT: Until Ulmo, the `block.runtime._services.update(service)` call below would
    # actually update the services dictionary for all runtimes, as `_services` was aliased between them.
    # This caused a grading bug, under certain conditions, so it was fixed
    # in https://github.com/openedx/openedx-platform/pull/37825; now, every runtime gets a fresh,
    # independent copy of `_services`. That's good, except that some Studio code had become dependent
    # on the bugged behavior and thus expected the "preview" services below to be present in all Studio runtimes.
    # We fixed the known instance of that bugged assumption here:
    # https://github.com/openedx/openedx-platform/pull/37900.
    # This comment is left here as a note for future devs investigating similar bugs.
    services = {
        "studio_user_permissions": StudioPermissionsService(request.user),
        "i18n": XBlockI18nService,
        'mako': mako_service,
        "settings": SettingsService(),
        "user": DjangoXBlockUserService(
            request.user,
            user_role=get_user_role(request.user, course_id),
            anonymous_user_id=anonymous_user_id,
            deprecated_anonymous_user_id=deprecated_anonymous_user_id,
        ),
        "partitions": StudioPartitionService(course_id=course_id),
        "teams_configuration": TeamsConfigurationService(),
        "sandbox": SandboxService(contentstore=contentstore, course_id=course_id),
        "cache": CacheService(cache),
        'replace_urls': ReplaceURLService,
        'video_config': VideoConfigService(),
        'discussion_config_service': DiscussionConfigService(),
    }

    block.runtime.get_block_for_descriptor = partial(_load_preview_block, request)
    block.runtime.mixins = settings.XBLOCK_MIXINS

    # Set up functions to modify the fragment produced by student_view
    block.runtime.wrappers = wrappers
    block.runtime.wrappers_asides = wrappers_asides
    block.runtime._services.update(services)  # pylint: disable=protected-access

    # xmodules can check for this attribute during rendering to determine if
    # they are being rendered for preview (i.e. in Studio)
    block.runtime.is_author_mode = True
    block.runtime.handler_url_override = handler_url
    block.runtime.applicable_aside_types_override = preview_applicable_aside_types
    block.runtime.render_child_placeholder = partial(
        render_child_placeholder,
        wrap_block=block.runtime.wrap_xblock
    )
    block.runtime.layout_asides_override = partial(
        preview_layout_asides,
        wrap_aside=block.runtime.wrap_aside
    )


class StudioPartitionService(PartitionService):
    """
    A runtime mixin to allow the display and editing of component visibility based on user partitions.
    """
    def get_user_group_id_for_partition(self, user, user_partition_id):
        """
        Override this method to return None, as the split_test_block calls this
        to determine which group a user should see, but is robust to getting a return
        value of None meaning that all groups should be shown.
        """
        return None


def _load_preview_block(request: Request, block: XModuleMixin):
    """
    Return a preview XBlock instantiated from the supplied block. Will use mutable fields
    if XBlock supports an author_view. Otherwise, will use immutable fields and student_view.

    request: The active django request
    block: An XModuleMixin
    """
    student_data = KvsFieldData(SessionKeyValueStore(request))
    if has_author_view(block):
        wrapper = partial(CmsFieldData, student_data=student_data)
    else:
        wrapper = partial(LmsFieldData, student_data=student_data)

    _prepare_runtime_for_preview(request, block)

    block.bind_for_student(
        request.user.id,
        [wrapper]
    )
    return block


def _is_xblock_reorderable(xblock, context):
    """
    Returns true if the specified xblock is in the set of reorderable xblocks
    otherwise returns false.
    """
    try:
        return xblock.location in context['reorderable_items']
    except KeyError:
        return False


# pylint: disable=unused-argument
def _studio_wrap_xblock(xblock, view, frag, context, display_name_only=False):
    """
    Wraps the results of rendering an XBlock view in a div which adds a header and Studio action buttons.
    """
    # Allow some imported components to be edited by authors in course.
    editable_library_components = ["html"]
    # Only add the Studio wrapper when on the container page. The "Pages" page will remain as is for now.
    if not context.get('is_pages_view', None) and view in PREVIEW_VIEWS:
        root_xblock = context.get('root_xblock')
        is_root = root_xblock and xblock.location == root_xblock.location
        is_reorderable = _is_xblock_reorderable(xblock, context)
        selected_groups_label = get_visibility_partition_info(xblock)['selected_groups_label']
        if selected_groups_label:
            selected_groups_label = _('Access restricted to: {list_of_groups}').format(list_of_groups=selected_groups_label)  # lint-amnesty, pylint: disable=line-too-long
        course = modulestore().get_course(xblock.location.course_key)

        can_edit = context.get('can_edit', True)
        can_add = context.get('can_add', True)
        can_move = context.get('can_move', True)
        root_upstream_link = UpstreamLink.try_get_for_block(root_xblock, log_error=False)
        upstream_link = UpstreamLink.try_get_for_block(xblock, log_error=False)
        if (
            root_upstream_link.error_message is None
            and isinstance(root_upstream_link.upstream_key, LibraryContainerLocator)
        ):
            # If this unit is linked to a library unit, for now we make it completely read-only
            # because when it is synced, all local changes like added components will be lost.
            # (This is only on the frontend; the backend doesn't enforce it)
            can_edit = False
            can_add = False
            can_move = False

        if upstream_link.error_message is None and upstream_link.upstream_ref:
            can_edit = xblock.category in editable_library_components

        # Is this a course or a library?
        is_course = xblock.context_key.is_course
        tags_count_map = context.get('tags_count_map')
        tags_count = 0
        if tags_count_map:
            tags_count = tags_count_map.get(str(xblock.location), 0)
        template_context = {
            'xblock_context': context,
            'xblock': xblock,
            'show_preview': context.get('show_preview', True),
            'content': frag.content,
            'is_root': is_root,
            'is_reorderable': is_reorderable,
            'can_edit': can_edit,
            'can_edit_visibility': can_edit and context.get('can_edit_visibility', is_course),
            'course_authoring_url': settings.COURSE_AUTHORING_MICROFRONTEND_URL,
            'is_loading': context.get('is_loading', False),
            'is_selected': context.get('is_selected', False),
            'selectable': context.get('selectable', False),
            'selected_groups_label': selected_groups_label,
            'can_add': can_add,
            # Generally speaking, "if you can add, you can delete". One exception is itembank (Problem Bank)
            # which has its own separate "add" workflow but uses the normal delete workflow for its child blocks.
            'can_delete': can_add or (root_xblock and root_xblock.scope_ids.block_type == "itembank" and can_edit),
            'can_move': can_move,
            'language': getattr(course, 'language', None),
            'is_course': is_course,
            'tags_count': tags_count,
            'can_edit_title': True,  # This is always true even for imported components
        }

        add_webpack_js_to_fragment(frag, "js/factories/xblock_validation")

        html = render_to_string('studio_xblock_wrapper.html', template_context)
        frag = wrap_fragment(frag, html)
    return frag


def get_preview_fragment(request, block, context):
    """
    Returns the HTML returned by the XModule's student_view or author_view (if available),
    specified by the block and idx.
    """
    block = _load_preview_block(request, block)

    preview_view = AUTHOR_VIEW if has_author_view(block) else STUDENT_VIEW

    try:
        fragment = block.render(preview_view, context)
    except Exception as exc:                          # pylint: disable=broad-except
        log.warning("Unable to render %s for %r", preview_view, block, exc_info=True)
        fragment = Fragment(render_to_string('html_error.html', {'message': str(exc)}))
    return fragment
