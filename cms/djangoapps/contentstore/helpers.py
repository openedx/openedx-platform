"""
Helper methods for Studio views.

Before adding more stuff here, take a look at:
https://github.com/openedx/edx-platform/issues/37637
Only Studio-specfic helper functions should be added here.
Platform-wide Python APIs should be added to an appropriate api.py file instead.
"""
from __future__ import annotations
import json
import logging
import pathlib
import urllib
from lxml import etree
from mimetypes import guess_type
import re

from attrs import frozen, Factory
from django.core.files.base import ContentFile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import DefinitionLocator, LocalId
from openedx.core.djangoapps.content_tagging.types import TagValuesByObjectIdDict
from xblock.core import XBlock
from xblock.fields import ScopeIds
from xblock.runtime import IdGenerator
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.django import modulestore
from xmodule.xml_block import XmlMixin
from openedx.core.djangoapps.video_config.transcripts_utils import Transcript, build_components_import_path
from edxval.api import (
    create_external_video,
    create_or_update_video_transcript,
)

from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from cms.lib.xblock.upstream_sync import UpstreamLink, UpstreamLinkException
from cms.lib.xblock.upstream_sync_block import fetch_customizable_fields_from_block
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
import openedx.core.djangoapps.content_staging.api as content_staging_api
import openedx.core.djangoapps.content_tagging.api as content_tagging_api
from openedx.core.djangoapps.content_staging.data import LIBRARY_SYNC_PURPOSE

from .utils import reverse_course_url, reverse_library_url, reverse_usage_url

log = logging.getLogger(__name__)


User = get_user_model()


# Note: Grader types are used throughout the platform but most usages are simply in-line
# strings.  In addition, new grader types can be defined on the fly anytime one is needed
# (because they're just strings). This dict is an attempt to constrain the sprawl in Studio.
GRADER_TYPES = {
    "HOMEWORK": "Homework",
    "LAB": "Lab",
    "ENTRANCE_EXAM": "Entrance Exam",
    "MIDTERM_EXAM": "Midterm Exam",
    "FINAL_EXAM": "Final Exam"
}


def get_parent_xblock(xblock):
    """
    Returns the xblock that is the parent of the specified xblock, or None if it has no parent.
    """
    locator = xblock.location
    store = modulestore()
    parent_location = store.get_parent_location(locator)

    if parent_location is None:
        return None
    return store.get_item(parent_location)


def is_unit(xblock, parent_xblock=None):
    """
    Returns true if the specified xblock is a vertical that is treated as a unit.
    A unit is a vertical that is a direct child of a sequential (aka a subsection).
    """
    if xblock.category == 'vertical':
        if parent_xblock is None:
            parent_xblock = get_parent_xblock(xblock)
        parent_category = parent_xblock.category if parent_xblock else None
        return parent_category == 'sequential'
    return False


def is_library_content(xblock):
    """
    Returns true if the specified xblock is library content.
    """
    return xblock.category == 'library_content'


def get_parent_if_split_test(xblock):
    """
    Returns the parent of the specified xblock if it is a split test, otherwise returns None.
    """
    parent_xblock = get_parent_xblock(xblock)
    if parent_xblock and parent_xblock.category == 'split_test':
        return parent_xblock


def xblock_has_own_studio_page(xblock, parent_xblock=None):
    """
    Returns true if the specified xblock has an associated Studio page. Most xblocks do
    not have their own page but are instead shown on the page of their parent. There
    are a few exceptions:
      1. Courses
      2. Verticals that are either:
        - themselves treated as units
        - a direct child of a unit
      3. XBlocks that support children
    """
    category = xblock.category

    if is_unit(xblock, parent_xblock):
        return True
    elif category == 'vertical':
        if parent_xblock is None:
            parent_xblock = get_parent_xblock(xblock)
        return is_unit(parent_xblock) if parent_xblock else False

    # All other xblocks with children have their own page
    return xblock.has_children


def xblock_studio_url(xblock, parent_xblock=None, find_parent=False):
    """
    Returns the Studio editing URL for the specified xblock.

    You can pass the parent xblock as an optimization, to avoid needing to load
    it twice, as sometimes the parent has to be checked.

    If you pass in a leaf block that doesn't have its own Studio page, this will
    normally return None, but if you use find_parent=True, this will find the
    nearest ancestor (usually the parent unit) that does have a Studio page and
    return that URL.
    """
    if not xblock_has_own_studio_page(xblock, parent_xblock):
        if find_parent:
            while xblock and not xblock_has_own_studio_page(xblock, parent_xblock):
                xblock = parent_xblock or get_parent_xblock(xblock)
                parent_xblock = None
            if not xblock:
                return None
        else:
            return None
    category = xblock.category
    if category == 'course':
        return reverse_course_url('course_handler', xblock.location.course_key)
    elif category in ('chapter', 'sequential'):
        return '{url}?show={usage_key}'.format(
            url=reverse_course_url('course_handler', xblock.location.course_key),
            usage_key=urllib.parse.quote(str(xblock.location))
        )
    elif category == 'library':
        library_key = xblock.location.course_key
        return reverse_library_url('library_handler', library_key)
    else:
        return reverse_usage_url('container_handler', xblock.location)


def xblock_lms_url(xblock) -> str:
    """
    Returns the LMS URL for the specified xblock.

    Args:
        xblock: The xblock to get the LMS URL for.

    Returns:
        str: The LMS URL for the specified xblock.
    """
    lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    return f"{lms_root_url}/courses/{xblock.location.course_key}/jump_to/{xblock.location}"


def xblock_embed_lms_url(xblock) -> str:
    """
    Returns the LMS URL for the specified xblock in embed mode.

    Args:
        xblock: The xblock to get the LMS URL for.

    Returns:
        str: The LMS URL for the specified xblock in embed mode.
    """
    lms_root_url = configuration_helpers.get_value('LMS_ROOT_URL', settings.LMS_ROOT_URL)
    return f"{lms_root_url}/xblock/{xblock.location}"


def xblock_type_display_name(xblock, default_display_name=None):
    """
    Returns the display name for the specified type of xblock. Note that an instance can be passed in
    for context dependent names, e.g. a vertical beneath a sequential is a Unit.

    :param xblock: An xblock instance or the type of xblock (as a string).
    :param default_display_name: The default value to return if no display name can be found.
    :return:
    """

    if hasattr(xblock, 'category'):
        category = xblock.category
        if category == 'vertical' and not is_unit(xblock):
            return _('Vertical')
    else:
        category = xblock
    if category == 'chapter':
        return _('Section')
    elif category == 'sequential':
        return _('Subsection')
    elif category == 'vertical':
        return _('Unit')
    elif category == 'problem':
        # The problem XBlock's display_name.default is not helpful ("Blank Problem") but changing it could have
        # too many ripple effects in other places, so we have a special case for capa problems here.
        # Note: With a ProblemBlock instance, we could actually check block.problem_types to give a more specific
        # description like "Multiple Choice Problem", but that won't work if our 'block' argument is just the block_type
        # string ("problem").
        return _('Problem')
    elif category == 'library_v2':
        return _('Library Content')
    elif category == 'itembank':
        return _('Problem Bank')
    component_class = XBlock.load_class(category)
    if hasattr(component_class, 'display_name') and component_class.display_name.default:
        return _(component_class.display_name.default)  # lint-amnesty, pylint: disable=translation-of-non-string
    else:
        return default_display_name


def xblock_primary_child_category(xblock):
    """
    Returns the primary child category for the specified xblock, or None if there is not a primary category.
    """
    category = xblock.category
    if category == 'course':
        return 'chapter'
    elif category == 'chapter':
        return 'sequential'
    elif category == 'sequential':
        return 'vertical'
    return None


def remove_entrance_exam_graders(course_key, user):
    """
    Removes existing entrance exam graders attached to the specified course
    Typically used when adding/removing an entrance exam.
    """
    grading_model = CourseGradingModel.fetch(course_key)
    graders = grading_model.graders
    for i, grader in enumerate(graders):
        if grader['type'] == GRADER_TYPES['ENTRANCE_EXAM']:
            CourseGradingModel.delete_grader(course_key, i, user)


class ImportIdGenerator(IdGenerator):
    """
    Modulestore's IdGenerator doesn't work for importing single blocks as OLX,
    so we implement our own
    """

    def __init__(self, context_key):
        super().__init__()
        self.context_key = context_key

    def create_aside(self, definition_id, usage_id, aside_type):
        """ Generate a new aside key """
        raise NotImplementedError()

    def create_usage(self, def_id) -> UsageKey:
        """ Generate a new UsageKey for an XBlock """
        # Note: Split modulestore will detect this temporary ID and create a new block ID when the XBlock is saved.
        return self.context_key.make_usage_key(def_id.block_type, LocalId())

    def create_definition(self, block_type, slug=None) -> DefinitionLocator:
        """ Generate a new definition_id for an XBlock """
        # Note: Split modulestore will detect this temporary ID and create a new definition ID when the XBlock is saved.
        return DefinitionLocator(block_type, LocalId(block_type))


@frozen
class StaticFileNotices:
    """ Information about what static files were updated (or not) when pasting content into another course """
    new_files: list[str] = Factory(list)
    conflicting_files: list[str] = Factory(list)
    error_files: list[str] = Factory(list)


def _rewrite_static_asset_references(downstream_xblock: XBlock, substitutions: dict[str, str], user_id: int) -> None:
    """
    Rewrite the static asset references in the OLX string to point to the new locations in the course.
    """
    store = modulestore()
    if hasattr(downstream_xblock, "data"):
        data_with_substitutions = downstream_xblock.data
        for old_static_ref, new_static_ref in substitutions.items():
            data_with_substitutions = _replace_strings(
                data_with_substitutions,
                old_static_ref,
                new_static_ref,
            )
        downstream_xblock.data = data_with_substitutions
        store.update_item(downstream_xblock, user_id)

    for child in downstream_xblock.get_children():
        _rewrite_static_asset_references(child, substitutions, user_id)


def _insert_static_files_into_downstream_xblock(
    downstream_xblock: XBlock, staged_content_id: int, request
) -> StaticFileNotices:
    """
    Gets static files from staged content, and inserts them into the downstream XBlock.
    """
    static_files = content_staging_api.get_staged_content_static_files(staged_content_id)
    notices, substitutions = _import_files_into_course(
        course_key=downstream_xblock.context_key,
        staged_content_id=staged_content_id,
        static_files=static_files,
        usage_key=downstream_xblock.usage_key,
    )
    # FIXME: This code shouldn't have any special cases for specific block types like video
    # in the future.
    if downstream_xblock.usage_key.block_type == 'video':
        _import_transcripts(
            downstream_xblock,
            staged_content_id=staged_content_id,
            static_files=static_files,
        )

    if substitutions:
        # Rewrite the OLX's static asset references to point to the new
        # locations for those assets. See _import_files_into_course for more
        # info on why this is necessary.
        _rewrite_static_asset_references(downstream_xblock, substitutions, request.user.id)

    return notices


def _replace_strings(obj: dict | list | str, old_str: str, new_str: str):
    """
    Replacing any instances of the given `old_str` string with `new_str` in any strings found in the the given object.

    Returns the updated object.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            obj[key] = _replace_strings(value, old_str, new_str)

    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            obj[index] = _replace_strings(item, old_str, new_str)

    elif isinstance(obj, str):
        return obj.replace(old_str, new_str)

    return obj


def import_staged_content_from_user_clipboard(parent_key: UsageKey, request) -> tuple[XBlock | None, StaticFileNotices]:
    """
    Import a block (along with its children and any required static assets) from
    the "staged" OLX in the user's clipboard.

    Does not deal with permissions or REST stuff - do that before calling this.

    Returns (1) the newly created block on success or None if the clipboard is
    empty, and (2) a summary of changes made to static files in the destination
    course.
    """
    from cms.djangoapps.contentstore.views.preview import _load_preview_block

    user_clipboard = content_staging_api.get_user_clipboard(request.user.id)
    if not user_clipboard:
        # Clipboard is empty or expired/error/loading
        return None, StaticFileNotices()
    olx_str = content_staging_api.get_staged_content_olx(user_clipboard.content.id)
    node = etree.fromstring(olx_str)
    store = modulestore()
    with store.bulk_operations(parent_key.course_key):
        parent_descriptor = store.get_item(parent_key)
        # Some blocks like drag-and-drop only work here with the full XBlock runtime loaded:
        parent_xblock = _load_preview_block(request, parent_descriptor)
        new_xblock = _import_xml_node_to_parent(
            node,
            parent_xblock,
            store,
            user=request.user,
            slug_hint=(
                user_clipboard.source_usage_key.block_id
                if isinstance(user_clipboard.source_usage_key, UsageKey) else None
            ),
            tags=user_clipboard.content.tags,
        )

        usage_key = new_xblock.usage_key
        if usage_key.block_type == 'video':
            # The edx_video_id must always be new so as not
            # to interfere with the data of the copied block
            new_xblock.edx_video_id = create_external_video(display_name='external video')
            store.update_item(new_xblock, request.user.id)

        notices = _insert_static_files_into_downstream_xblock(new_xblock, user_clipboard.content.id, request)

    return new_xblock, notices


def import_static_assets_for_library_sync(downstream_xblock: XBlock, lib_block: XBlock, request) -> StaticFileNotices:
    """
    Import the static assets from the library xblock to the downstream xblock
    through staged content. Also updates the OLX references to point to the new
    locations of those assets in the downstream course.

    Does not deal with permissions or REST stuff - do that before calling this.

    Returns a summary of changes made to static files in the destination
    course.
    """
    if not lib_block.runtime.get_block_assets(lib_block, fetch_asset_data=False):
        return StaticFileNotices()
    staged_content = content_staging_api.stage_xblock_temporarily(lib_block, request.user.id, LIBRARY_SYNC_PURPOSE)
    if not staged_content:
        # expired/error/loading
        return StaticFileNotices()

    store = modulestore()
    try:
        with store.bulk_operations(downstream_xblock.context_key):
            # FIXME: This code shouldn't have any special cases for specific block types like video
            # in the future.
            if downstream_xblock.usage_key.block_type == 'video' and not downstream_xblock.edx_video_id:
                # If the `downstream_xblock` is a new created block, we need to create
                # a new `edx_video_id` to import the transcripts.
                downstream_xblock.edx_video_id = create_external_video(display_name='external video')
                store.update_item(downstream_xblock, request.user.id)

            # Now handle static files that need to go into Files & Uploads.
            # If the required files already exist, nothing will happen besides updating the olx.
            notices = _insert_static_files_into_downstream_xblock(downstream_xblock, staged_content.id, request)
    finally:
        staged_content.delete()

    return notices


def _fetch_and_set_upstream_link(
    copied_from_block: str,
    copied_from_version_num: int,
    temp_xblock: XBlock,
    user: User
):
    """
    Fetch and set upstream link for the given xblock which is being pasted. This function handles following cases:
    * the xblock is copied from a v2 library; the library block is set as upstream.
    * the xblock is copied from a course; no upstream is set, only copied_from_block is set.
    * the xblock is copied from a course where the source block was imported from a library; the original library block
      is set as upstream.
    """
    # Try to link the pasted block (downstream) to the copied block (upstream).
    temp_xblock.upstream = copied_from_block
    try:
        upstream_link = UpstreamLink.get_for_block(temp_xblock)
    except UpstreamLinkException:
        # Usually this will fail. For example, if the copied block is a modulestore course block, it can't be an
        # upstream. That's fine! Instead, we store a reference to where this block was copied from, in the
        # 'copied_from_block' field (from AuthoringMixin).

        # In case if the source block was imported from a library, we need to check its upstream
        # and set the same upstream link for the new block.
        source_descriptor = modulestore().get_item(UsageKey.from_string(copied_from_block))
        if source_descriptor.upstream:
            _fetch_and_set_upstream_link(
                source_descriptor.upstream,
                source_descriptor.upstream_version,
                temp_xblock,
                user,
            )
        else:
            # else we store a reference to where this block was copied from, in the 'copied_from_block'
            # field (from AuthoringMixin).
            temp_xblock.upstream = None
            temp_xblock.copied_from_block = copied_from_block
    else:
        # But if it doesn't fail, then populate the `upstream_version` field based on what was copied. Note that
        # this could be the latest published version, or it could be an an even newer draft version.
        temp_xblock.upstream_version = copied_from_version_num
        # Also, fetch upstream values (`upstream_display_name`, etc.).
        # Recall that the copied block could be a draft. So, rather than fetching from the published upstream (which
        # could be older), fetch from the copied block itself. That way, if an author customizes a field, but then
        # later wants to restore it, it will restore to the value that the field had when the block was pasted. Of
        # course, if the author later syncs updates from a *future* published upstream version, then that will fetch
        # new values from the published upstream content.
        if isinstance(upstream_link.upstream_key, UsageKey):  # only if upstream is a block, not a container
            fetch_customizable_fields_from_block(downstream=temp_xblock, user=user, upstream=temp_xblock)
            # Although the above function will set all customisable fields to match its upstream_* counterpart
            # We copy the downstream_customized list to the new block to avoid overriding user customisations on sync
            # So we will have:
            # temp_xblock.display_name == temp_xblock.upstream_display_name
            # temp_xblock.data == temp_xblock.upstream_data   # for html blocks
            # Even then we want to set `downstream_customized` value to avoid overriding user customisations on sync
            downstream_customized = temp_xblock.xml_attributes.get("downstream_customized", '[]')
            temp_xblock.downstream_customized = json.loads(downstream_customized)


def _import_xml_node_to_parent(
    node,
    parent_xblock: XBlock,
    # The modulestore we're using
    store,
    # The user who is performing this operation
    user: User,
    # Hint to use as usage ID (block_id) for the new XBlock
    slug_hint: str | None = None,
    # Content tags applied to the source XBlock(s)
    tags: TagValuesByObjectIdDict | None = None,
) -> XBlock:
    """
    Given an XML node representing a serialized XBlock (OLX), import it into modulestore 'store' as a child of the
    specified parent block. Recursively copy children as needed.
    """
    # pylint: disable=too-many-statements

    runtime = parent_xblock.runtime
    parent_key = parent_xblock.scope_ids.usage_id
    block_type = node.tag
    node_copied_from = node.attrib.get('copied_from_block', None)
    node_copied_version = node.attrib.get('copied_from_version', None)

    # Modulestore's IdGenerator here is SplitMongoIdManager which is assigned
    # by SplitModuleStoreRuntime and since we need our custom ImportIdGenerator
    # here we are temporaraliy swtiching it.
    original_id_generator = runtime.id_generator

    # Generate the new ID:
    runtime.id_generator = ImportIdGenerator(parent_key.context_key)
    def_id = runtime.id_generator.create_definition(block_type, slug_hint)
    usage_id = runtime.id_generator.create_usage(def_id)
    keys = ScopeIds(None, block_type, def_id, usage_id)
    # parse_xml is a really messy API. We pass both 'keys' and 'id_generator' and, depending on the XBlock, either
    # one may be used to determine the new XBlock's usage key, and the other will be ignored. e.g. video ignores
    # 'keys' and uses 'id_generator', but the default XBlock parse_xml ignores 'id_generator' and uses 'keys'.
    # For children of this block, obviously only id_generator is used.
    xblock_class = runtime.load_block_type(block_type)
    # Note: if we find a case where any XBlock needs access to the block-specific static files that were saved to
    # export_fs during copying, we could make them available here via runtime.resources_fs before calling parse_xml.
    # However, currently the only known case for that is video block's transcript files, and those will
    # automatically be "carried over" to the new XBlock even in a different course because the video ID is the same,
    # and VAL will thus make the transcript available.

    child_nodes = []

    if issubclass(xblock_class, XmlMixin):
        # Hack: XBlocks that use "XmlMixin" have their own XML parsing behavior, and in particular if they encounter
        # an XML node that has no children and has only a "url_name" attribute, they'll try to load the XML data
        # from an XML file in runtime.resources_fs. But that file doesn't exist here. So we set at least one
        # additional attribute here to make sure that url_name is not the only attribute; otherwise in some cases,
        # XmlMixin.parse_xml will try to load an XML file that doesn't exist, giving an error. The name and value
        # of this attribute don't matter and should be ignored.
        node.attrib["x-is-pointer-node"] = "no"

    if not xblock_class.has_children:
        # No children to worry about. The XML may contain child nodes, but they're not XBlocks.
        temp_xblock = xblock_class.parse_xml(node, runtime, keys)
    else:
        # We have to handle the children ourselves, because there are lots of complex interactions between
        #    * the vanilla XBlock parse_xml() method, and its lack of API for "create and save a new XBlock"
        #    * the XmlMixin version of parse_xml() which only works with XMLImportingModuleStoreRuntime,
        #      not modulestore or the v2 runtime
        #    * the modulestore APIs for creating and saving a new XBlock, which work but don't support XML parsing.
        # We can safely assume that if the XBLock class supports children, every child node will be the XML
        # serialization of a child block, in order. For blocks that don't support children, their XML content/nodes
        # could be anything (e.g. HTML, capa)
        node_without_children = etree.Element(node.tag, **node.attrib)
        temp_xblock = xblock_class.parse_xml(node_without_children, runtime, keys)
        child_nodes = list(node)

    if issubclass(xblock_class, XmlMixin) and "x-is-pointer-node" in getattr(temp_xblock, "data", ""):
        # Undo the "pointer node" hack if needed (e.g. for capa problems)
        temp_xblock.data = re.sub(r'([^>]+) x-is-pointer-node="no"', r'\1', temp_xblock.data, count=1)

    # Restore the original id_generator
    runtime.id_generator = original_id_generator

    if xblock_class.has_children and temp_xblock.children:
        raise NotImplementedError("We don't yet support pasting XBlocks with children")

    if node_copied_from:
        _fetch_and_set_upstream_link(node_copied_from, node_copied_version, temp_xblock, user)

    # Save the XBlock into modulestore. We need to save the block and its parent for this to work:
    new_xblock = store.update_item(temp_xblock, user.id, allow_not_found=True)
    new_xblock.parent = parent_key
    parent_xblock.children.append(new_xblock.location)
    store.update_item(parent_xblock, user.id)

    children_handled = False
    if hasattr(new_xblock, 'studio_post_paste'):
        # Allow an XBlock to do anything fancy it may need to when pasted from the clipboard.
        # These blocks may handle their own children or parenting if needed. Let them return booleans to
        # let us know if we need to handle these or not.
        children_handled = new_xblock.studio_post_paste(store, node)

    if not children_handled:
        for child_node in child_nodes:
            _import_xml_node_to_parent(
                child_node,
                new_xblock,
                store,
                user=user,
                tags=tags,
            )

    # Copy content tags to the new xblock
    if new_xblock.upstream:
        # copy the tags from the upstream as ready-only
        content_tagging_api.copy_tags_as_read_only(
            new_xblock.upstream,
            new_xblock.location,
        )
    elif tags and node_copied_from:
        object_tags = tags.get(node_copied_from)
        if object_tags:
            content_tagging_api.set_all_object_tags(
                content_key=new_xblock.location,
                object_tags=object_tags,
            )

    return new_xblock


def _import_files_into_course(
    course_key: CourseKey,
    staged_content_id: int,
    static_files: list[content_staging_api.StagedContentFileData],
    usage_key: UsageKey,
) -> tuple[StaticFileNotices, dict[str, str]]:
    """
    For the given staged static asset files (which are in "Staged Content" such
    as the user's clipbaord, but which need to end up in the course's Files &
    Uploads page), import them into the destination course, unless they already
    exist.

    This function returns a tuple of StaticFileNotices (assets added, errors,
    conflicts), and static asset path substitutions that should be made in the
    OLX in order to paste this content into this course. The latter is for the
    case in which we're brining content in from a v2 library, which stores
    static assets locally to a Component and needs to go into a subdirectory
    when pasting into a course to avoid overwriting commonly named things, e.g.
    "figure1.png".
    """
    # List of files that were newly added to the destination course
    new_files = []
    # List of files that conflicted with identically named files already in the destination course
    conflicting_files = []
    # List of files that had an error (shouldn't happen unless we have some kind of bug)
    error_files = []

    # Store a mapping of asset URLs that need to be modified for the destination
    # assets. This is necessary when you take something from a library and paste
    # it into a course, because we need to translate Component-local static
    # assets and shove them into the Course's global Files & Uploads space in a
    # nested directory structure.
    substitutions = {}
    for file_data_obj in static_files:
        # At this point, we know this is a "Files & Uploads" asset that we may need to copy into the course:
        try:
            result, substitution_for_file = _import_file_into_course(
                course_key,
                staged_content_id,
                file_data_obj,
                usage_key,
            )
            if result is True:
                new_files.append(file_data_obj.filename)
                substitutions.update(substitution_for_file)
            elif substitution_for_file:
                # substitutions need to be made because OLX references to these files need to be updated
                substitutions.update(substitution_for_file)
            elif result is None:
                pass  # This file already exists; no action needed.
            else:
                conflicting_files.append(file_data_obj.filename)
        except Exception:  # lint-amnesty, pylint: disable=broad-except
            error_files.append(file_data_obj.filename)
            log.exception(f"Failed to import Files & Uploads file {file_data_obj.filename}")

    notices = StaticFileNotices(
        new_files=new_files,
        conflicting_files=conflicting_files,
        error_files=error_files,
    )

    return notices, substitutions


def _import_file_into_course(
    course_key: CourseKey,
    staged_content_id: int,
    file_data_obj: content_staging_api.StagedContentFileData,
    usage_key: UsageKey,
) -> tuple[bool | None, dict]:
    """
    Import a single staged static asset file into the course, unless it already exists.
    Returns True if it was imported, False if there's a conflict, or None if
    the file already existed (no action needed).
    """
    clipboard_file_path = file_data_obj.filename

    # We need to generate an AssetKey to add an asset to a course. The mapping
    # of directories '/' -> '_' is a long-existing contentstore convention that
    # we're not going to attempt to change.
    if clipboard_file_path.startswith('static/'):
        # If it's in this form, it came from a library and assumes component-local assets
        file_path = clipboard_file_path.removeprefix('static/')
        import_path = build_components_import_path(usage_key, file_path)
        filename = pathlib.Path(file_path).name
        new_key = course_key.make_asset_key("asset", import_path.replace("/", "_"))
    else:
        # Otherwise it came from a course...
        file_path = clipboard_file_path
        import_path = None
        filename = pathlib.Path(file_path).name
        new_key = course_key.make_asset_key("asset", file_path.replace("/", "_"))

    try:
        current_file = contentstore().find(new_key)
    except NotFoundError:
        current_file = None
    if not current_file:
        # This static asset should be imported into the new course:
        content_type = guess_type(filename)[0]
        data = content_staging_api.get_staged_content_static_file_data(staged_content_id, clipboard_file_path)
        if data is None:
            raise NotFoundError(file_data_obj.source_key)
        content = StaticContent(
            new_key,
            name=filename,
            content_type=content_type,
            data=data,
            import_path=import_path
        )
        # If it's an image file, also generate the thumbnail:
        thumbnail_content, thumbnail_location = contentstore().generate_thumbnail(content)
        if thumbnail_content is not None:
            content.thumbnail_location = thumbnail_location
        contentstore().save(content)
        return True, {clipboard_file_path: filename if not import_path else f"static/{import_path}"}
    elif current_file.content_digest == file_data_obj.md5_hash:
        # The file already exists and matches exactly, so no action is needed
        return None, {}
    else:
        # There is a conflict with some other file that has the same name.
        return False, {}


def _import_transcripts(
    block: XBlock,
    staged_content_id: int,
    static_files: list[content_staging_api.StagedContentFileData],
):
    """
    Adds transcripts to VAL using the new edx_video_id.
    """
    for file_data_obj in static_files:
        clipboard_file_path = file_data_obj.filename
        data = content_staging_api.get_staged_content_static_file_data(
            staged_content_id,
            clipboard_file_path
        )
        if data is None:
            raise NotFoundError(file_data_obj.source_key)

        if clipboard_file_path.startswith('static/'):
            # If it's in this form, it came from a library and assumes component-local assets
            file_path = clipboard_file_path.removeprefix('static/')
        else:
            # Otherwise it came from a course...
            file_path = clipboard_file_path

        filename = pathlib.Path(file_path).name

        language_code = next((k for k, v in block.transcripts.items() if v == filename), None)
        if language_code:
            sjson_subs = Transcript.convert(
                content=data,
                input_format=Transcript.SRT,
                output_format=Transcript.SJSON
            ).encode()
            create_or_update_video_transcript(
                video_id=block.edx_video_id,
                language_code=language_code,
                metadata={
                    'file_format': Transcript.SJSON,
                    'language_code': language_code
                },
                file_data=ContentFile(sjson_subs),
            )


def is_item_in_course_tree(item):
    """
    Check that the item is in the course tree.

    It's possible that the item is not in the course tree
    if its parent has been deleted and is now an orphan.
    """
    ancestor = item.get_parent()
    while ancestor is not None and ancestor.location.block_type != "course":
        ancestor = ancestor.get_parent()

    return ancestor is not None


def concat_static_file_notices(notices: list[StaticFileNotices]) -> StaticFileNotices:
    """Combines multiple static file notices into a single object

    Args:
        notices: list of StaticFileNotices

    Returns:
        Single StaticFileNotices
    """
    new_files = []
    conflicting_files = []
    error_files = []
    for notice in notices:
        new_files.extend(notice.new_files)
        conflicting_files.extend(notice.conflicting_files)
        error_files.extend(notice.error_files)
    return StaticFileNotices(
        new_files=list(set(new_files)),
        conflicting_files=list(set(conflicting_files)),
        error_files=list(set(error_files)),
    )
