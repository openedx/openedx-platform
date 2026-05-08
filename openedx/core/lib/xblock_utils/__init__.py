"""
Functions that are used to modify XBlock fragments for use in the LMS and Studio
"""


import datetime
import hashlib
import json
import logging
import re
import uuid
from zoneinfo import ZoneInfo

import markupsafe
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user  # noqa: F401
from django.contrib.staticfiles.storage import staticfiles_storage
from django.urls import reverse
from django.utils.html import escape
from edx_django_utils.plugins import pluggable_override
from lxml import etree, html
from opaque_keys.edx.asides import AsideUsageKeyV1, AsideUsageKeyV2
from web_fragments.fragment import Fragment
from xblock.core import XBlock  # noqa: F401
from xblock.exceptions import InvalidScopeError
from xblock.scorable import ScorableXBlockMixin

from common.djangoapps import static_replace  # noqa: F401
from common.djangoapps.edxmako.shortcuts import render_to_string
from xmodule.seq_block import SequenceBlock  # pylint: disable=wrong-import-order
from xmodule.vertical_block import VerticalBlock  # pylint: disable=wrong-import-order
from xmodule.x_module import (  # pylint: disable=wrong-import-order
    PREVIEW_VIEWS,
    STUDENT_VIEW,
    STUDIO_VIEW,
    shim_xmodule_js,  # noqa: F401
)

log = logging.getLogger(__name__)


def wrap_fragment(fragment, new_content):
    """
    Returns a new Fragment that has `new_content` and all
    as its content, and all of the resources from fragment
    """
    wrapper_frag = Fragment(content=new_content)
    wrapper_frag.add_fragment_resources(fragment)
    return wrapper_frag


def request_token(request):
    """
    Return a unique token for the supplied request.
    This token will be the same for all calls to `request_token`
    made on the same request object.
    """
    # pylint: disable=protected-access
    if not hasattr(request, '_xblock_token'):
        request._xblock_token = uuid.uuid1().hex

    return request._xblock_token


def wrap_xblock(
        runtime_class,
        block,
        view,
        frag,
        context,
        usage_id_serializer,
        request_token,                  # pylint: disable=redefined-outer-name
        display_name_only=False,
        extra_data=None
):
    """
    Wraps the results of rendering an XBlock view in a standard <section> with identifying
    data so that the appropriate javascript module can be loaded onto it.

    :param runtime_class: The name of the javascript runtime class to use to load this block
    :param block: An XBlock (that may be an XModule or XModuleDescriptor)
    :param view: The name of the view that rendered the fragment being wrapped
    :param frag: The :class:`Fragment` to be wrapped
    :param context: The context passed to the view being rendered
    :param usage_id_serializer: A function to serialize the block's usage_id for use by the
        front-end Javascript Runtime.
    :param request_token: An identifier that is unique per-request, so that only xblocks
        rendered as part of this request will have their javascript initialized.
    :param display_name_only: If true, don't render the fragment content at all.
        Instead, just render the `display_name` of `block`
    :param extra_data: A dictionary with extra data values to be set on the wrapper
    """
    if extra_data is None:
        extra_data = {}

    # If any mixins have been applied, then use the unmixed class
    class_name = getattr(block, 'unmixed_class', block.__class__).__name__

    data = {}
    data.update(extra_data)

    if context:
        data.update(context.get('wrap_xblock_data', {}))

    css_classes = [
        'xblock',
        f'xblock-{markupsafe.escape(view)}',
        'xblock-{}-{}'.format(  # noqa: UP032
            markupsafe.escape(view),
            markupsafe.escape(block.scope_ids.block_type),
        )
    ]

    if view == STUDENT_VIEW and getattr(block, 'HIDDEN', False):
        css_classes.append('is-hidden')

    # TODO: This special case will be removed when we update the SCSS under
    #       xmodule/assets to use the standard XBlock CSS classes.
    #       See https://github.com/openedx/edx-platform/issues/32617.
    if getattr(block, 'uses_xmodule_styles_setup', False):
        if view in PREVIEW_VIEWS:
            # The block is acting as an XModule
            css_classes.append('xmodule_display')
        elif view == STUDIO_VIEW:
            # The block is acting as an XModuleDescriptor
            css_classes.append('xmodule_edit')

        css_classes.append('xmodule_' + markupsafe.escape(class_name))

    if frag.js_init_fn:
        data['init'] = frag.js_init_fn
        data['runtime-class'] = runtime_class
        data['runtime-version'] = frag.js_init_version

    data['block-type'] = block.scope_ids.block_type
    data['usage-id'] = usage_id_serializer(block.scope_ids.usage_id)
    data['request-token'] = request_token
    data['graded'] = getattr(block, 'graded', False)
    data['has-score'] = getattr(block, 'has_score', False)

    if block.name:
        data['name'] = block.name

    template_context = {
        'content': block.display_name if display_name_only else frag.content,
        'classes': css_classes,
        'display_name': block.display_name_with_default_escaped,  # xss-lint: disable=python-deprecated-display-name
        'data_attributes': ' '.join(f'data-{markupsafe.escape(key)}="{markupsafe.escape(value)}"'
                                    for key, value in data.items()),
    }

    if hasattr(frag, 'json_init_args') and frag.json_init_args is not None:
        template_context['js_init_parameters'] = frag.json_init_args
    else:
        template_context['js_init_parameters'] = ""

    return wrap_fragment(frag, render_to_string('xblock_wrapper.html', template_context))


def wrap_xblock_aside(
        runtime_class,
        aside,
        view,
        frag,
        context,                        # pylint: disable=unused-argument
        usage_id_serializer,
        request_token,                   # pylint: disable=redefined-outer-name
        extra_data=None,
        extra_classes=None
):
    """
    Wraps the results of rendering an XBlockAside view in a standard <section> with identifying
    data so that the appropriate javascript module can be loaded onto it.

    :param runtime_class: The name of the javascript runtime class to use to load this block
    :param aside: An XBlockAside
    :param view: The name of the view that rendered the fragment being wrapped
    :param frag: The :class:`Fragment` to be wrapped
    :param context: The context passed to the view being rendered
    :param usage_id_serializer: A function to serialize the block's usage_id for use by the
        front-end Javascript Runtime.
    :param request_token: An identifier that is unique per-request, so that only xblocks
        rendered as part of this request will have their javascript initialized.
    :param extra_data: A dictionary with extra data values to be set on the wrapper
    :param extra_classes: A list with extra classes to be set on the wrapper element
    """

    if extra_data is None:
        extra_data = {}

    data = {}
    data.update(extra_data)

    css_classes = [
        f'xblock-{markupsafe.escape(view)}',
        'xblock-{}-{}'.format(  # noqa: UP032
            markupsafe.escape(view),
            markupsafe.escape(aside.scope_ids.block_type),
        ),
        'xblock_asides-v1'
    ]
    if extra_classes:
        css_classes.extend(extra_classes)

    if frag.js_init_fn:
        data['init'] = frag.js_init_fn
        data['runtime-class'] = runtime_class
        data['runtime-version'] = frag.js_init_version

    data['block-type'] = aside.scope_ids.block_type
    data['usage-id'] = usage_id_serializer(aside.scope_ids.usage_id)
    data['request-token'] = request_token

    template_context = {
        'content': frag.content,
        'classes': css_classes,
        'data_attributes': ' '.join(f'data-{markupsafe.escape(key)}="{markupsafe.escape(value)}"'
                                    for key, value in data.items()),
    }

    if hasattr(frag, 'json_init_args') and frag.json_init_args is not None:
        template_context['js_init_parameters'] = frag.json_init_args
    else:
        template_context['js_init_parameters'] = ""

    return wrap_fragment(frag, render_to_string('xblock_wrapper.html', template_context))


def grade_histogram(block_id):
    '''
    Print out a histogram of grades on a given problem in staff member debug info.

    Warning: If a student has just looked at an xblock and not attempted
    it, their grade is None. Since there will always be at least one such student
    this function almost always returns [].
    '''
    from django.db import connection
    cursor = connection.cursor()

    query = """\
        SELECT courseware_studentmodule.grade,
        COUNT(courseware_studentmodule.student_id)
        FROM courseware_studentmodule
        WHERE courseware_studentmodule.module_id=%s
        GROUP BY courseware_studentmodule.grade"""
    # Passing block_id this way prevents sql-injection.
    cursor.execute(query, [str(block_id)])

    grades = list(cursor.fetchall())
    grades.sort(key=lambda x: x[0])  # Add ORDER BY to sql query?
    if len(grades) >= 1 and grades[0][0] is None:
        return []
    return grades


def sanitize_html_id(html_id):
    """
    Template uses element_id in js function names, so can't allow dashes and colons.
    """
    sanitized_html_id = re.sub(r'[:-]', '_', html_id)
    return sanitized_html_id


def add_staff_markup(user, disable_staff_debug_info, block, view, frag, context):  # pylint: disable=unused-argument
    """
    Updates the supplied block with a new get_html function that wraps
    the output of the old get_html function with additional information
    for admin users only, including a histogram of student answers, the
    definition of the xblock, and a link to view the block in Studio
    if it is a Studio edited, mongo stored course.

    Does nothing if block is a SequenceBlock.
    """
    if context and context.get('hide_staff_markup', False):
        # If hide_staff_markup is passed, don't add the markup
        return frag
    # TODO: make this more general, eg use an XModule attribute instead
    if isinstance(block, VerticalBlock) and (not context or not context.get('child_of_vertical', False)):
        return frag

    if isinstance(block, SequenceBlock) or getattr(block, 'HIDDEN', False):
        return frag

    block_id = block.location
    if block.has_score and settings.FEATURES.get('DISPLAY_HISTOGRAMS_TO_STAFF'):
        histogram = grade_histogram(block_id)
        render_histogram = len(histogram) > 0
    else:
        histogram = None
        render_histogram = False

    if settings.FEATURES.get('ENABLE_LMS_MIGRATION') and hasattr(block.runtime, 'resources_fs'):
        [filepath, filename] = getattr(block, 'xml_attributes', {}).get('filename', ['', None])
        osfs = block.runtime.resources_fs
        if filename is not None and osfs.exists(filename):
            # if original, unmangled filename exists then use it (github
            # doesn't like symlinks)
            filepath = filename
        data_dir = block.static_asset_path or osfs.root_path.rsplit('/')[-1]
        giturl = block.giturl or 'https://github.com/MITx'
        edit_link = f"{giturl}/{data_dir}/tree/master/{filepath}"
    else:
        edit_link = False
        # Need to define all the variables that are about to be used
        giturl = ""
        data_dir = ""

    source_file = block.source_file  # source used to generate the problem XML, eg latex or word

    # Useful to indicate to staff if problem has been released or not.
    # TODO (ichuang): use _has_access_block.can_load in lms.courseware.access,
    # instead of now>mstart comparison here.
    now = datetime.datetime.now(ZoneInfo("UTC"))
    is_released = "unknown"
    mstart = block.start

    if mstart is not None:
        is_released = "<font color='red'>Yes!</font>" if (now > mstart) else "<font color='green'>Not yet</font>"

    field_contents = []
    for name, field in block.fields.items():
        try:
            field_contents.append((name, field.read_from(block)))
        except InvalidScopeError:
            log.warning("Unable to read field in Staff Debug information", exc_info=True)
            field_contents.append((name, "WARNING: Unable to read field"))

    staff_context = {
        'fields': field_contents,
        'xml_attributes': getattr(block, 'xml_attributes', {}),
        'tags': block._class_tags,  # pylint: disable=protected-access
        'location': block.location,
        'xqa_key': block.xqa_key,
        'source_file': source_file,
        'source_url': f'{giturl}/{data_dir}/tree/master/{source_file}',
        'category': str(block.__class__.__name__),
        'element_id': sanitize_html_id(block.location.html_id()),
        'edit_link': edit_link,
        'user': user,
        'xqa_server': settings.FEATURES.get('XQA_SERVER', "http://your_xqa_server.com"),
        'histogram': json.dumps(histogram),
        'render_histogram': render_histogram,
        'block_content': frag.content,
        'is_released': is_released,
        'can_reset_attempts': 'attempts' in block.fields,
        'can_rescore_problem': hasattr(block, 'rescore'),
        'can_override_problem_score': isinstance(block, ScorableXBlockMixin),
        'disable_staff_debug_info': disable_staff_debug_info,
    }
    if isinstance(block, ScorableXBlockMixin):
        staff_context['max_problem_score'] = block.max_score()

    return wrap_fragment(frag, render_to_string("staff_problem_info.html", staff_context))


def get_course_update_items(course_updates, provided_index=0):
    """
    Returns list of course_updates data dictionaries either from new format if available or
    from old. This function don't modify old data to new data (in db), instead returns data
    in common old dictionary format.
    New Format: {"items" : [{"id": computed_id, "date": date, "content": html-string}],
                 "data": "<ol>[<li><h2>date</h2>content</li>]</ol>"}
    Old Format: {"data": "<ol>[<li><h2>date</h2>content</li>]</ol>"}
    """
    def _course_info_content(html_parsed):
        """
        Constructs the HTML for the course info update, not including the header.
        """
        if len(html_parsed) == 1:
            # could enforce that update[0].tag == 'h2'
            content = html_parsed[0].tail
        else:
            content = html_parsed[0].tail if html_parsed[0].tail is not None else ""
            content += "\n".join([html.tostring(ele).decode('utf-8') for ele in html_parsed[1:]])
        return content

    if course_updates and getattr(course_updates, "items", None):
        if provided_index and 0 < provided_index <= len(course_updates.items):
            return course_updates.items[provided_index - 1]
        else:
            # return list in reversed order (old format: [4,3,2,1]) for compatibility
            return list(reversed(course_updates.items))

    course_update_items = []
    if course_updates:
        # old method to get course updates
        # purely to handle free formed updates not done via editor. Actually kills them, but at least doesn't break.
        try:
            course_html_parsed = html.fromstring(course_updates.data)
        except (etree.XMLSyntaxError, etree.ParserError):
            log.error("Cannot parse: " + course_updates.data)  # pylint: disable=logging-not-lazy
            escaped = escape(course_updates.data)
            # xss-lint: disable=python-concat-html
            course_html_parsed = html.fromstring("<ol><li>" + escaped + "</li></ol>")

        # confirm that root is <ol>, iterate over <li>, pull out <h2> subs and then rest of val
        if course_html_parsed.tag == 'ol':
            # 0 is the newest
            for index, update in enumerate(course_html_parsed):
                if len(update) > 0:
                    content = _course_info_content(update)
                    # make the id on the client be 1..len w/ 1 being the oldest and len being the newest
                    computed_id = len(course_html_parsed) - index
                    payload = {
                        "id": computed_id,
                        "date": update.findtext("h2"),
                        "content": content
                    }
                    if provided_index == 0:
                        course_update_items.append(payload)
                    elif provided_index == computed_id:
                        return payload

    return course_update_items


def xblock_local_resource_url(block, uri):
    """
    Returns the URL for an XBlock's local resource.

    Note: when running with the full Django pipeline, the file will be accessed
    as a static asset which will use a CDN in production.
    """
    xblock_class = getattr(block.__class__, 'unmixed_class', block.__class__)
    if settings.PIPELINE['PIPELINE_ENABLED'] or not settings.REQUIRE_DEBUG:
        return staticfiles_storage.url('xblock/resources/{package_name}/{path}'.format(  # noqa: UP032
            package_name=xblock_resource_pkg(xblock_class),
            path=uri
        ))
    else:
        return reverse('xblock_resource_url', kwargs={
            'block_type': block.scope_ids.block_type,
            'uri': uri,
        })


def xblock_resource_pkg(block):
    """
    Return the module name needed to find an XBlock's shared static assets.

    This method will return the full module name that is one level higher than
    the one the block is in. For instance, problem_builder.answer.AnswerBlock
    has a __module__ value of 'problem_builder.answer'. This method will return
    'problem_builder' instead. However, for edx-ora2's
    openassessment.xblock.openassessmentblock.OpenAssessmentBlock, the value
    returned is 'openassessment.xblock'.

    Built-in edx-platform XBlocks (defined under ./xmodule/) are special cases.
    They currently use two different mechanisms to load assets:
    1. The `builtin_assets` utilities, which let the blocks add JS and CSS
       compiled completely outside of the XBlock pipeline. Used by HtmlBlock,
       ProblemBlock, and most other built-in blocks currently. Handling for these
       assets does not interact with this function.
    2. The (preferred) standard XBlock runtime resource loading system, used by
       LegacyLibraryContentBlock. Handling for these assets *does* interact with this
       function.

    We hope to migrate to (2) eventually, tracked by:
    https://github.com/openedx/edx-platform/issues/32618.
    """
    module_name = block.__module__
    # Special handling for case (2) of the built-in XBlocks because they map to different
    # dirs for sub-modules.
    if module_name.startswith('xmodule.'):
        return module_name

    return module_name.rsplit('.', 1)[0]


def is_xblock_aside(usage_key):
    """
    Returns True if the given usage key is for an XBlock aside

    Args:
        usage_key (opaque_keys.edx.keys.UsageKey): A usage key

    Returns:
        bool: Whether or not the usage key is an aside key type
    """
    return isinstance(usage_key, (AsideUsageKeyV1, AsideUsageKeyV2))


def get_aside_from_xblock(xblock, aside_type):
    """
    Gets an instance of an XBlock aside from the XBlock that it's decorating. This also
    configures the aside instance with the runtime and fields of the given XBlock.

    Args:
        xblock (xblock.core.XBlock): The XBlock that the desired aside is decorating
        aside_type (str): The aside type

    Returns:
        xblock.core.XBlockAside: Instance of an xblock aside
    """
    return xblock.runtime.get_aside_of_type(xblock, aside_type)


def hash_resource(resource):
    """
    Hash a :class:`web_fragments.fragment.FragmentResource
    Those hash values are used to avoid loading the resources
    multiple times.
    """
    md5 = hashlib.md5()
    for data in resource:
        if isinstance(data, bytes):
            md5.update(data)
        elif isinstance(data, str):
            md5.update(data.encode('utf-8'))
        else:
            md5.update(repr(data).encode('utf-8'))
    return md5.hexdigest()


@pluggable_override('OVERRIDE_GET_UNIT_ICON')
def get_icon(block):
    """
    A function that returns the CSS class representing an icon to use for this particular
    XBlock (in the courseware navigation bar). Mostly used for Vertical/Unit XBlocks.
    It can be overridden by setting `OVERRIDE_GET_UNIT_ICON` to an alternative implementation.
    """
    return block.get_icon_class()


def get_css_dependencies(group):
    """
    Returns list of CSS dependencies belonging to `group` in settings.PIPELINE['STYLESHEETS'].

    Respects `PIPELINE['PIPELINE_ENABLED']` setting.
    """
    if settings.PIPELINE['PIPELINE_ENABLED']:
        return [settings.PIPELINE['STYLESHEETS'][group]['output_filename']]
    else:
        return settings.PIPELINE['STYLESHEETS'][group]['source_filenames']


def get_js_dependencies(group):
    """
    Returns list of JS dependencies belonging to `group` in settings.PIPELINE['JAVASCRIPT'].

    Respects `PIPELINE['PIPELINE_ENABLED']` setting.
    """
    if settings.PIPELINE['PIPELINE_ENABLED']:
        return [settings.PIPELINE['JAVASCRIPT'][group]['output_filename']]
    else:
        return settings.PIPELINE['JAVASCRIPT'][group]['source_filenames']
