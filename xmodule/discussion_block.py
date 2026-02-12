"""
Discussion XBlock
"""

import logging
import urllib
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import get_language_bidi
from web_fragments.fragment import Fragment
from xblock.completable import XBlockCompletionMode
from xblock.core import XBlock
from xblock.fields import UNIQUE_ID, Scope, String
from xblock.utils.resources import ResourceLoader
from xblock.utils.studio_editable import StudioEditableXBlockMixin
from xblocks_contrib.discussion import DiscussionXBlock as _ExtractedDiscussionXBlock

from openedx.core.djangolib.markup import HTML, Text
from openedx.core.lib.xblock_utils import get_css_dependencies, get_js_dependencies
from xmodule.xml_block import XmlMixin

log = logging.getLogger(__name__)
loader = ResourceLoader("lms")  # pylint: disable=invalid-name


def _(text):
    """
    A noop underscore function that marks strings for extraction.
    """
    return text


@XBlock.needs('user')  # pylint: disable=abstract-method
@XBlock.needs('i18n')
@XBlock.needs('mako')
@XBlock.wants('discussion_config_service')
class _BuiltInDiscussionXBlock(XBlock, StudioEditableXBlockMixin,
                               XmlMixin):  # lint-amnesty, pylint: disable=abstract-method
    """
    Provides a discussion forum that is inline with other content in the courseware.
    """
    is_extracted = False
    completion_mode = XBlockCompletionMode.EXCLUDED

    discussion_id = String(scope=Scope.settings, default=UNIQUE_ID)
    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        default="Discussion",
        scope=Scope.settings
    )
    discussion_category = String(
        display_name=_("Category"),
        default=_("Week 1"),
        help=_(
            "A category name for the discussion. "
            "This name appears in the left pane of the discussion forum for the course."
        ),
        scope=Scope.settings
    )
    discussion_target = String(
        display_name=_("Subcategory"),
        default="Topic-Level Student-Visible Label",
        help=_(
            "A subcategory name for the discussion. "
            "This name appears in the left pane of the discussion forum for the course."
        ),
        scope=Scope.settings
    )
    sort_key = String(scope=Scope.settings)

    editable_fields = ["display_name", "discussion_category", "discussion_target"]

    has_author_view = True  # Tells Studio to use author_view

    @property
    def discussion_config_service(self):
        """
        Returns discussion configuration service.
        """
        return self.runtime.service(self, 'discussion_config_service')

    @property
    def course_key(self):
        return getattr(self.scope_ids.usage_id, 'course_key', None)

    @property
    def is_visible(self):
        """
        Discussion Xblock does not support new OPEN_EDX provider
        """
        if self.discussion_config_service:
            return self.discussion_config_service.is_discussion_visible(self.course_key)
        return False

    @property
    def is_discussion_enabled(self):
        """
        Returns True if discussions are enabled; else False
        """
        if self.discussion_config_service:
            return self.discussion_config_service.is_discussion_enabled()
        return False

    @property
    def django_user(self):
        """
        Returns django user associated with user currently interacting
        with the XBlock.
        """
        user_service = self.runtime.service(self, 'user')
        if not user_service:
            return None
        return user_service._django_user  # pylint: disable=protected-access

    @staticmethod
    def vendor_js_dependencies():
        """
        Returns list of vendor JS files that this XBlock depends on.

        The helper function that it uses to obtain the list of vendor JS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        return get_js_dependencies('discussion_vendor')

    @staticmethod
    def js_dependencies():
        """
        Returns list of JS files that this XBlock depends on.

        The helper function that it uses to obtain the list of JS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        return get_js_dependencies('discussion')

    @staticmethod
    def css_dependencies():
        """
        Returns list of CSS files that this XBlock depends on.

        The helper function that it uses to obtain the list of CSS files
        works in conjunction with the Django pipeline to ensure that in development mode
        the files are loaded individually, but in production just the single bundle is loaded.
        """
        if get_language_bidi():
            return get_css_dependencies('style-inline-discussion-rtl')
        else:
            return get_css_dependencies('style-inline-discussion')

    def add_resource_urls(self, fragment):
        """
        Adds URLs for JS and CSS resources that this XBlock depends on to `fragment`.
        """
        # Head dependencies
        for vendor_js_file in self.vendor_js_dependencies():
            fragment.add_resource_url(staticfiles_storage.url(vendor_js_file), "application/javascript", "head")

        for css_file in self.css_dependencies():
            fragment.add_css_url(staticfiles_storage.url(css_file))

        # Body dependencies
        for js_file in self.js_dependencies():
            fragment.add_javascript_url(staticfiles_storage.url(js_file))

    def has_permission(self, permission):
        """
        Encapsulates lms specific functionality, as `has_permission` is not
        importable outside of lms context, namely in tests.

        :param user:
        :param str permission: Permission
        :rtype: bool
        """
        if self.discussion_config_service:
            return self.discussion_config_service.has_permission(self.django_user, permission, self.course_key)
        return False

    def student_view(self, context=None):
        """
        Renders student view for LMS.
        """
        fragment = Fragment()

        if not self.is_visible:
            return fragment

        self.add_resource_urls(fragment)
        login_msg = ''

        if not self.django_user.is_authenticated:
            qs = urllib.parse.urlencode({
                'course_id': self.course_key,
                'enrollment_action': 'enroll',
                'email_opt_in': False,
            })
            login_msg = Text(_("You are not signed in. To view the discussion content, {sign_in_link} or "
                               "{register_link}, and enroll in this course.")).format(
                sign_in_link=HTML('<a href="{url}">{sign_in_label}</a>').format(
                    sign_in_label=_('sign in'),
                    url='{}?{}'.format(reverse('signin_user'), qs),
                ),
                register_link=HTML('<a href="/{url}">{register_label}</a>').format(
                    register_label=_('register'),
                    url='{}?{}'.format(reverse('register_user'), qs),
                ),
            )
        if self.is_discussion_enabled:
            context = {
                'discussion_id': self.discussion_id,
                'display_name': self.display_name if self.display_name else _("Discussion"),
                'user': self.django_user,
                'course_id': self.course_key,
                'discussion_category': self.discussion_category,
                'discussion_target': self.discussion_target,
                'can_create_thread': self.has_permission("create_thread"),
                'can_create_comment': self.has_permission("create_comment"),
                'can_create_subcomment': self.has_permission("create_sub_comment"),
                'login_msg': login_msg,
                'PLATFORM_NAME': settings.PLATFORM_NAME,
                'enable_discussion_home_panel': settings.FEATURES.get("ENABLE_DISCUSSION_HOME_PANEL", False),
            }
            fragment.add_content(
                render_to_string('discussion/_discussion_inline.html', context)
            )

        fragment.initialize_js('DiscussionInlineBlock')

        return fragment

    def author_view(self, context=None):  # pylint: disable=unused-argument
        """
        Renders author view for Studio.
        """
        fragment = Fragment()
        # For historic reasons, this template is in the LMS templates folder:
        context = {
            'discussion_id': self.discussion_id,
            'is_visible': self.is_visible,
        }
        fragment.add_content(
            loader.render_django_template('templates/discussion/_discussion_inline_studio.html', context)
        )
        return fragment

    def student_view_data(self):
        """
        Returns a JSON representation of the student_view of this XBlock.
        """
        return {'topic_id': self.discussion_id}

    @classmethod
    def parse_xml(cls, node, runtime, keys):
        """
        Parses OLX into XBlock.

        This method is overridden here to allow parsing legacy OLX, coming from discussion XModule.
        XBlock stores all the associated data, fields and children in a XML element inlined into vertical XML file
        XModule stored only minimal data on the element included into vertical XML and used a dedicated "discussion"
        folder in OLX to store fields and children. Also, some info was put into "policy.json" file.

        If no external data sources are found (file in "discussion" folder), it is exactly equivalent to base method
        XBlock.parse_xml. Otherwise this method parses file in "discussion" folder (known as definition_xml), applies
        policy.json and updates fields accordingly.
        """
        block = super().parse_xml(node, runtime, keys)

        cls._apply_metadata_and_policy(block, node, runtime)

        return block

    @classmethod
    def _apply_metadata_and_policy(cls, block, node, runtime):
        """
        Attempt to load definition XML from "discussion" folder in OLX, than parse it and update block fields
        """
        if node.get('url_name') is None:
            return  # Newer/XBlock XML format - no need to load an additional file.
        try:
            definition_xml, _ = cls.load_definition_xml(node, runtime, block.scope_ids.def_id)
        except Exception as err:  # pylint: disable=broad-except
            log.info(
                "Exception %s when trying to load definition xml for block %s - assuming XBlock export format",
                err,
                block
            )
            return

        metadata = cls.load_metadata(definition_xml)
        cls.apply_policy(metadata, runtime.get_policy(block.scope_ids.usage_id))

        for field_name, value in metadata.items():
            if field_name in block.fields:
                setattr(block, field_name, value)


DiscussionXBlock = None


def reset_class():
    """Reset class as per django settings flag"""
    global DiscussionXBlock
    DiscussionXBlock = (
        _ExtractedDiscussionXBlock if settings.USE_EXTRACTED_DISCUSSION_BLOCK
        else _BuiltInDiscussionXBlock
    )
    return DiscussionXBlock

reset_class()
DiscussionXBlock.__name__ = "DiscussionXBlock"
