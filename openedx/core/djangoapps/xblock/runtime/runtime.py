"""
Common base classes for all new XBlock runtimes.
"""
import logging
from typing import Callable, Protocol
from urllib.parse import urljoin  # pylint: disable=import-error

import crum
from common.djangoapps.student.models import anonymous_id_for_user
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from completion.models import BlockCompletion
from completion.services import CompletionService
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from eventtracking import tracker
from opaque_keys.edx.keys import UsageKeyV2, LearningContextKey
from web_fragments.fragment import Fragment
from xblock.core import XBlock
from xblock.exceptions import NoSuchServiceError
from xblock.field_data import DictFieldData, FieldData, SplitFieldData
from xblock.fields import Scope, ScopeIds
from xblock.runtime import IdReader, KvsFieldData, MemoryIdManager, Runtime

from xmodule.errortracker import make_error_tracker
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.django import XBlockI18nService
from xmodule.services import EventPublishingService, RebindUserService
from xmodule.util.sandboxing import SandboxService
from common.djangoapps.edxmako.services import MakoService
from common.djangoapps.static_replace.services import ReplaceURLService
from common.djangoapps.track import contexts as track_contexts
from common.djangoapps.track import views as track_views
from common.djangoapps.xblock_django.user_service import DjangoXBlockUserService
from lms.djangoapps.courseware.model_data import DjangoKeyValueStore, FieldDataCache
from lms.djangoapps.grades.api import signals as grades_signals
from openedx.core.types import User as UserType
from openedx.core.djangoapps.enrollments.services import EnrollmentsService
from openedx.core.djangoapps.xblock.apps import get_xblock_app_config
from openedx.core.djangoapps.xblock.data import AuthoredDataMode, StudentDataMode, LatestVersion
from openedx.core.djangoapps.xblock.runtime.ephemeral_field_data import EphemeralKeyValueStore
from openedx.core.djangoapps.xblock.runtime.mixin import LmsBlockMixin
from openedx.core.djangoapps.xblock.utils import get_xblock_id_for_anonymous_user
from openedx.core.lib.cache_utils import CacheService
from openedx.core.lib.xblock_utils import wrap_fragment, xblock_local_resource_url, request_token

from .id_managers import OpaqueKeyReader
from .shims import RuntimeShim, XBlockShim

log = logging.getLogger(__name__)
User = get_user_model()


def make_track_function():
    """
    Make a tracking function that logs what happened, for XBlock events.
    """
    current_request = crum.get_current_request()

    def function(event_type, event):
        return track_views.server_track(current_request, event_type, event, page='x_module')
    return function


class GetHandlerFunction(Protocol):
    """ Type definition for our "get handler" callback """
    def __call__(
        self,
        usage_key: UsageKeyV2,
        handler_name: str,
        user: UserType | None,
        *,
        version: int | LatestVersion = LatestVersion.AUTO,
    ) -> str:
        ...


class XBlockRuntime(RuntimeShim, Runtime):
    """
    This class manages one or more instantiated XBlocks for a particular user,
    providing those XBlocks with the standard XBlock runtime API (and some
    Open edX-specific additions) so that it can interact with the platform,
    and the platform can interact with it.

    The main reason we cannot make the runtime a long-lived singleton is that
    the XBlock runtime API requires 'user_id' to be a property of the runtime,
    not an argument passed in when loading particular blocks.
    """

    # ** Do not add any XModule compatibility code to this class **
    # Add it to RuntimeShim instead, to help keep legacy code isolated.

    # Feature flags:

    # This runtime can save state for users who aren't logged in:
    suppports_state_for_anonymous_users = True

    # Instance variables:

    user: UserType | None
    user_id: int | str | None
    # dict of FieldData stores for our loaded XBlocks. Key is the block's scope_ids.
    block_field_datas: dict[ScopeIds, FieldData | None]
    # dict of FieldDataCache objects for XBlock with database-based user state
    django_field_data_caches: dict[LearningContextKey, FieldDataCache]
    # keep track of view name (student_view, studio_view, etc)
    # currently only used to track if we're in the studio_view (see below under service())
    view_name: str | None
    # backing store for authored field data (mostly content+settings scopes)
    authored_data_store: FieldData

    def __init__(
        self,
        user: UserType | None,
        *,
        handler_url: GetHandlerFunction,
        student_data_mode: StudentDataMode,
        authored_data_mode: AuthoredDataMode,
        authored_data_store: FieldData,
        id_reader: IdReader | None = None,
    ):
        super().__init__(
            id_reader=id_reader or OpaqueKeyReader(),
            mixins=(
                LmsBlockMixin,  # Adds Non-deprecated LMS/Studio functionality
                XBlockShim,  # Adds deprecated LMS/Studio functionality / backwards compatibility
            ),
            default_class=None,
            select=None,
            id_generator=MemoryIdManager(),  # We don't really use id_generator until we need to support asides
        )
        assert student_data_mode in (StudentDataMode.Ephemeral, StudentDataMode.Persisted)
        self.authored_data_mode = authored_data_mode
        self.authored_data_store = authored_data_store
        self.children_data_store = None
        self.student_data_mode = student_data_mode
        self.handler_url_fn = handler_url
        self.user = user
        # self.user_id must be set as a separate attribute since base class sets it:
        if self.user is None:
            self.user_id = None
        elif self.user.is_anonymous:
            self.user_id = get_xblock_id_for_anonymous_user(user)
        else:
            self.user_id = self.user.id
        self.block_field_datas = {}
        self.django_field_data_caches = {}
        self.view_name = None

    def handler_url(self, block, handler_name: str, suffix='', query='', thirdparty=False):
        """
        Get the URL to a specific handler.
        """
        if thirdparty:
            log.warning("thirdparty handlers are not supported by this runtime for XBlock %s.", type(block))

        # Note: it's important that we call handlers based on the same version of the block
        # (draft block -> draft data available to handler; published block -> published data available to handler)
        kwargs = {}
        if hasattr(block, "_runtime_requested_version"):  # pylint: disable=protected-access
            if self.authored_data_mode == AuthoredDataMode.DEFAULT_DRAFT:
                default_version = LatestVersion.DRAFT
            else:
                default_version = LatestVersion.PUBLISHED
            if block._runtime_requested_version != default_version:  # pylint: disable=protected-access
                kwargs["version"] = block._runtime_requested_version  # pylint: disable=protected-access
        url = self.handler_url_fn(block.usage_key, handler_name, self.user, **kwargs)
        if suffix:
            if not url.endswith('/'):
                url += '/'
            url += suffix
        if query:
            url += '&' if '?' in url else '?'
            url += query
        return url

    def resource_url(self, resource):
        raise NotImplementedError("resource_url is not supported by Open edX.")

    def local_resource_url(self, block: XBlock, uri: str) -> str:
        """
        Get the absolute URL to a resource file (like a CSS/JS file or an image)
        that is part of an XBlock's python module.
        """
        relative_url = xblock_local_resource_url(block, uri)
        site_root_url = get_xblock_app_config().get_site_root_url()
        absolute_url = urljoin(site_root_url, relative_url)
        return absolute_url

    def publish(self, block: XBlock, event_type: str, event_data: dict):
        """ Handle XBlock events like grades and completion """
        special_handler = self.get_event_handler(event_type)
        if special_handler:
            special_handler(block, event_data)
        else:
            self.log_event_to_tracking_log(block, event_type, event_data)

    def get_event_handler(self, event_type: str) -> Callable[[XBlock, dict], None] | None:
        """
        Return an appropriate function to handle the event.

        Returns None if no special processing is required.
        """
        if self.user_id is None:
            # We don't/cannot currently record grades or completion for anonymous users.
            return None
        # In the future when/if we support masquerading, need to be careful here not to affect the user's grades
        if event_type == 'grade':
            return self.handle_grade_event
        elif event_type == 'completion':
            return self.handle_completion_event
        return None

    def log_event_to_tracking_log(self, block: XBlock, event_type: str, event_data: dict) -> None:
        """
        Log this XBlock event to the tracking log
        """
        log_context = track_contexts.context_dict_for_learning_context(block.scope_ids.usage_id.context_key)
        if self.user_id:
            log_context['user_id'] = self.user_id
        log_context['asides'] = {}
        track_function = make_track_function()
        with tracker.get_tracker().context(event_type, log_context):
            track_function(event_type, event_data)

    def handle_grade_event(self, block: XBlock, event: dict):
        """
        Submit a grade for the block.
        """
        if self.user and not self.user.is_anonymous:
            # TODO: we shouldn't be using an LMS API here.
            # https://github.com/openedx/edx-platform/issues/37660
            grades_signals.SCORE_PUBLISHED.send(
                sender=None,
                block=block,
                user=self.user,
                raw_earned=event['value'],
                raw_possible=event['max_value'],
                only_if_higher=event.get('only_if_higher'),
                score_deleted=event.get('score_deleted'),
                grader_response=event.get('grader_response')
            )

    def handle_completion_event(self, block: XBlock, event: dict):
        """
        Submit a completion object for the block.
        """
        if not ENABLE_COMPLETION_TRACKING_SWITCH.is_enabled():
            return
        BlockCompletion.objects.submit_completion(
            user=self.user,
            block_key=block.scope_ids.usage_id,
            completion=event['completion'],
        )

    def applicable_aside_types(self, block: XBlock):
        """ Disable XBlock asides in this runtime """
        return []

    def parse_xml_file(self, fileobj):
        # Deny access to the inherited method
        raise NotImplementedError("XML Serialization is only supported with LearningCoreXBlockRuntime")

    def add_node_as_child(self, block, node):
        """
        Called by XBlock.parse_xml to treat a child node as a child block.
        """
        # Deny access to the inherited method
        raise NotImplementedError("XML Serialization is only supported with LearningCoreXBlockRuntime")

    def service(self, block: XBlock, service_name: str):
        """
        Return a service, or None.
        Services are objects implementing arbitrary other interfaces.
        """
        # TODO: Do these declarations actually help with anything? Maybe this check should
        # be removed from here and from XBlock.runtime
        declaration = block.service_declaration(service_name)
        context_key = block.scope_ids.usage_id.context_key
        if declaration is None:
            raise NoSuchServiceError(f"Service {service_name!r} was not requested.")
        # Most common service is field-data so check that first:
        if service_name == "field-data":
            if block.scope_ids not in self.block_field_datas:
                try:
                    self.block_field_datas[block.scope_ids] = self._init_field_data_for_block(block)
                except:
                    # Don't try again pointlessly every time another field is accessed
                    self.block_field_datas[block.scope_ids] = None
                    raise
            return self.block_field_datas[block.scope_ids]
        elif service_name == "completion":
            return CompletionService(user=self.user, context_key=context_key)
        elif service_name == "user":
            if self.user is None:
                raise RuntimeError("Cannot access user service when there is no user bound to the XBlock.")
            if self.user.is_anonymous:
                deprecated_anonymous_student_id = self.user_id
            else:
                deprecated_anonymous_student_id = anonymous_id_for_user(self.user, course_id=None)

            return DjangoXBlockUserService(
                self.user,
                # The value should be updated to whether the user is staff in the context when Learning Core runtime
                # adds support for courses.
                user_is_staff=self.user.is_staff,  # type: ignore
                anonymous_user_id=self.anonymous_student_id,
                # See the docstring of `DjangoXBlockUserService`.
                deprecated_anonymous_user_id=deprecated_anonymous_student_id
            )
        elif service_name == "mako":
            # The mako service has a seperate "preview" engine used for views in CMS that mimic LMS views.
            # Said engine is only configured for CMS. Therefore, when we are in CMS but not the studio_view,
            # we want to use the preview engine. The mako service uses a 'lms.main' namespace to indicate
            # the preview engine, and 'main' otherwise.
            # For backwards compatibility, we check the student_data_mode (Ephemeral indicates CMS) and the
            # view_name for 'studio_view.' self.view_name is set by render() below.
            if self.student_data_mode == StudentDataMode.Ephemeral and self.view_name != 'studio_view':
                return MakoService(namespace_prefix='lms.')
            return MakoService()
        elif service_name == "i18n":
            return XBlockI18nService(block=block)
        elif service_name == 'sandbox':
            context_key = block.scope_ids.usage_id.context_key
            return SandboxService(contentstore=contentstore, course_id=context_key)
        elif service_name == 'cache':
            return CacheService(cache)
        elif service_name == 'replace_urls':
            return ReplaceURLService(xblock=block, lookup_asset_url=self._lookup_asset_url)
        elif service_name == 'rebind_user':
            # this service should ideally be initialized with all the arguments of prepare_runtime_for_user
            # but only the positional arguments are passed here as the other arguments are too
            # specific to the lms.block_render module
            return RebindUserService(
                self.user,
                context_key,
                track_function=make_track_function(),
                request_token=request_token(crum.get_current_request()),
            )
        elif service_name == 'publish':
            return EventPublishingService(self.user, context_key, make_track_function())
        elif service_name == 'enrollments':
            return EnrollmentsService()
        elif service_name == 'error_tracker':
            return make_error_tracker()
        elif service_name == 'video_config':
            # Import here to avoid circular dependency
            from openedx.core.djangoapps.video_config.services import VideoConfigService
            return VideoConfigService()
        elif service_name == 'discussion_config_service':
            from openedx.core.djangoapps.discussions.services import DiscussionConfigService
            return DiscussionConfigService()

        # Otherwise, fall back to the base implementation which loads services
        # defined in the constructor:
        return super().service(block, service_name)

    def _init_field_data_for_block(self, block: XBlock) -> FieldData:
        """
        Initialize the FieldData implementation for the specified XBlock
        """
        if self.user is None:
            # No user is specified, so we want to ignore any user-specific data. We cannot throw an
            # error here because the XBlock loading process will write to the user_state if we have
            # mutable fields.
            student_data_store = DictFieldData({})
        elif self.user.is_anonymous:
            # This is an anonymous (non-registered) user:
            assert isinstance(self.user_id, str) and self.user_id.startswith("anon")
            kvs = EphemeralKeyValueStore()
            student_data_store = KvsFieldData(kvs)
        elif self.student_data_mode == StudentDataMode.Ephemeral:
            # We're in an environment like Studio where we want to let the
            # author test blocks out but not permanently save their state.
            kvs = EphemeralKeyValueStore()
            student_data_store = KvsFieldData(kvs)
        else:
            # Use database-backed field data (i.e. store user_state in StudentModule)
            context_key = block.scope_ids.usage_id.context_key
            if context_key not in self.django_field_data_caches:
                field_data_cache = FieldDataCache(
                    [block], course_id=context_key, user=self.user, asides=None, read_only=False,
                )
                self.django_field_data_caches[context_key] = field_data_cache
            else:
                field_data_cache = self.django_field_data_caches[context_key]
                field_data_cache.add_blocks_to_cache([block])
            student_data_store = KvsFieldData(kvs=DjangoKeyValueStore(field_data_cache))

        return SplitFieldData({
            Scope.content: self.authored_data_store,
            Scope.settings: self.authored_data_store,
            Scope.parent: self.authored_data_store,
            Scope.children: self.children_data_store,
            Scope.user_state_summary: student_data_store,
            Scope.user_state: student_data_store,
            Scope.user_info: student_data_store,
            Scope.preferences: student_data_store,
        })

    def render(self, block: XBlock, view_name: str, context: dict | None = None):
        """
        Render a specific view of an XBlock.
        """
        # Users who aren't logged in are not allowed to view any views other
        # than public_view. They may call any handlers though.
        if (self.user is None or self.user.is_anonymous) and view_name != 'public_view':
            raise PermissionDenied

        # track this so service() can know
        self.view_name = view_name

        # We also need to override this method because some XBlocks in the
        # edx-platform codebase use methods from builtin_assets.py,
        # which create relative URLs (/static/studio/bundles/webpack-foo.js).
        # We want all resource URLs to be absolute, such as is done when
        # local_resource_url() is used.
        fragment = super().render(block, view_name, context)
        needs_fix = False
        for resource in fragment.resources:
            if resource.kind == 'url' and resource.data.startswith('/'):
                needs_fix = True
                break
        if needs_fix:
            log.warning("XBlock %s returned relative resource URLs, which are deprecated", block.scope_ids.usage_id)
            # The Fragment API is mostly immutable, so changing a resource requires this:
            frag_data = fragment.to_dict()
            for resource in frag_data['resources']:
                if resource['kind'] == 'url' and resource['data'].startswith('/'):
                    log.debug("-> Relative resource URL: %s", resource['data'])
                    resource['data'] = get_xblock_app_config().get_site_root_url() + resource['data']
            fragment = Fragment.from_dict(frag_data)

        # Apply any required transforms to the fragment.
        # We could move to doing this in wrap_xblock() and/or use an array of
        # wrapper methods like the ConfigurableFragmentWrapper mixin does.
        fragment = wrap_fragment(
            fragment,
            ReplaceURLService(xblock=block, lookup_asset_url=self._lookup_asset_url).replace_urls(fragment.content)
        )

        return fragment

    def _lookup_asset_url(self, block: XBlock, asset_path: str):  # pylint: disable=unused-argument
        """
        Return an absolute URL for the specified static asset file that may
        belong to this XBlock.

        e.g. if the XBlock settings have a field value like "/static/foo.png"
        then this method will be called with asset_path="foo.png" and should
        return a URL like https://cdn.none/xblock/f843u89789/static/foo.png

        If the asset file is not recognized, return None
        """
        # Subclasses should override this
        return None
