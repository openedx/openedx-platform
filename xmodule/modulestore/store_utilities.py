# pylint: disable=missing-module-docstring
import logging
import re
import uuid
from collections import namedtuple

from xblock.core import XBlock

DETACHED_XBLOCK_TYPES = {name for name, __ in XBlock.load_tagged_classes("detached")}


def _prefix_only_url_replace_regex(pattern):
    """
    Match urls in quotes pulling out the fields from pattern
    """
    return re.compile("""
        (?P<quote>\\\\?['"])      # the opening quotes
        {}
        (?P=quote)                # the first matching closing quote
        """.format(pattern),  # noqa: UP032
        flags=re.VERBOSE)


def rewrite_nonportable_content_links(source_course_id, dest_course_id, text):
    """
    rewrite any non-portable links to (->) relative links:
         /c4x/<org>/<course>/asset/<name> -> /static/<name>
         /jump_to/i4x://<org>/<course>/<category>/<name> -> /jump_to_id/<id>
    """

    def portable_asset_link_subtitution(match):
        quote = match.group('quote')
        block_id = match.group('block_id')
        return quote + '/static/' + block_id + quote

    def portable_jump_to_link_substitution(match):
        quote = match.group('quote')
        rest = match.group('block_id')
        return quote + '/jump_to_id/' + rest + quote

    # if something blows up, log the error and continue

    # create a serialized template for what the id will look like in the source_course but with
    # the block_id as a regex pattern
    placeholder_id = uuid.uuid4().hex
    asset_block_pattern = str(source_course_id.make_asset_key('asset', placeholder_id))
    asset_block_pattern = asset_block_pattern.replace(placeholder_id, r'(?P<block_id>.*?)')
    try:
        text = _prefix_only_url_replace_regex(asset_block_pattern).sub(portable_asset_link_subtitution, text)
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("Error producing regex substitution %r for text = %r.\n\nError msg = %s", asset_block_pattern, text, str(exc))  # pylint: disable=line-too-long

    placeholder_category = f'cat_{uuid.uuid4().hex}'
    usage_block_pattern = str(source_course_id.make_usage_key(placeholder_category, placeholder_id))
    usage_block_pattern = usage_block_pattern.replace(placeholder_category, r'(?P<category>[^/+@]+)')
    usage_block_pattern = usage_block_pattern.replace(placeholder_id, r'(?P<block_id>.*?)')
    jump_to_link_base = '/courses/{course_key_string}/jump_to/{usage_key_string}'.format(  # noqa: UP032
        course_key_string=str(source_course_id), usage_key_string=usage_block_pattern
    )
    try:
        text = _prefix_only_url_replace_regex(jump_to_link_base).sub(portable_jump_to_link_substitution, text)
    except Exception as exc:  # pylint: disable=broad-except
        logging.warning("Error producing regex substitution %r for text = %r.\n\nError msg = %s", jump_to_link_base, text, str(exc))  # pylint: disable=line-too-long

    # Also, there commonly is a set of link URL's used in the format:
    # /courses/<org>/<course>/<name> which will be broken if migrated to a different course_id
    # so let's rewrite those, but the target will also be non-portable,
    #
    # Note: we only need to do this if we are changing course-id's
    #
    if source_course_id != dest_course_id:
        try:
            generic_courseware_link_base = f'/courses/{str(source_course_id)}/'
            text = re.sub(_prefix_only_url_replace_regex(generic_courseware_link_base), portable_asset_link_subtitution, text)  # pylint: disable=line-too-long
        except Exception as exc:  # pylint: disable=broad-except
            logging.warning("Error producing regex substitution %r for text = %r.\n\nError msg = %s", source_course_id, text, str(exc))  # pylint: disable=line-too-long

    return text


def draft_node_constructor(block, url, parent_url, location=None, parent_location=None, index=None):
    """
    Contructs a draft_node namedtuple with defaults.
    """
    draft_node = namedtuple('draft_node', ['module', 'location', 'url', 'parent_location', 'parent_url', 'index'])
    return draft_node(block, location, url, parent_location, parent_url, index)


def get_draft_subtree_roots(draft_nodes):
    """
    Takes a list of draft_nodes, which are namedtuples, each of which identify
    itself and its parent.

    If a draft_node is in `draft_nodes`, then we expect for all its children
    should be in `draft_nodes` as well. Since `_import_draft` is recursive,
    we only want to import the roots of any draft subtrees contained in
    `draft_nodes`.

    This generator yields those roots.
    """
    urls = [draft_node.url for draft_node in draft_nodes]

    for draft_node in draft_nodes:
        if draft_node.parent_url not in urls:
            yield draft_node
