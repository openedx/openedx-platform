"""
THIS MODULE IS DEPRECATED IN FAVOR OF https://github.com/openedx/xblock-lti-consumer

Learning Tools Interoperability (LTI) module.


Resources
---------

Theoretical background and detailed specifications of LTI can be found on:

    http://www.imsglobal.org/LTI/v1p1p1/ltiIMGv1p1p1.html

This module is based on the version 1.1.1 of the LTI specifications by the
IMS Global authority. For authentication, it uses OAuth1.

When responding back to the LTI tool provider, we must issue a correct
response. Types of responses and their message payload is available at:

    Table A1.2 Interpretation of the 'CodeMajor/severity' matrix.
    http://www.imsglobal.org/gws/gwsv1p0/imsgws_wsdlBindv1p0.html

A resource to test the LTI protocol (PHP realization):

    http://www.imsglobal.org/developers/LTI/test/v1p1/lms.php

We have also begun to add support for LTI 1.2/2.0.  We will keep this
docstring in synch with what support is available.  The first LTI 2.0
feature to be supported is the REST API results service, see specification
at
http://www.imsglobal.org/lti/ltiv2p0/uml/purl.imsglobal.org/vocab/lis/v2/outcomes/Result/service.html

What is supported:
------------------

1.) Display of simple LTI in iframe or a new window.
2.) Multiple LTI components on a single page.
3.) The use of multiple LTI providers per course.
4.) Use of advanced LTI component that provides back a grade.
    A) LTI 1.1.1 XML endpoint
        a.) The LTI provider sends back a grade to a specified URL.
        b.) Currently only action "update" is supported. "Read", and "delete"
            actions initially weren't required.
    B) LTI 2.0 Result Service JSON REST endpoint
       (http://www.imsglobal.org/lti/ltiv2p0/uml/purl.imsglobal.org/vocab/lis/v2/outcomes/Result/service.html)
        a.) Discovery of all such LTI http endpoints for a course.  External tools GET from this discovery
            endpoint and receive URLs for interacting with individual grading units.
            (see lms/djangoapps/courseware/views/views.py:get_course_lti_endpoints)
        b.) GET, PUT and DELETE in LTI Result JSON binding
            (http://www.imsglobal.org/lti/ltiv2p0/mediatype/application/vnd/ims/lis/v2/result+json/index.html)
            for a provider to synchronize grades into edx-platform.  Reading, Setting, and Deleteing
            Numeric grades between 0 and 1 and text + basic HTML feedback comments are supported, via
            GET / PUT / DELETE HTTP methods respectively
"""


import base64
import datetime
import hashlib
import logging
import textwrap
from unittest import mock
from urllib import parse
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import nh3
import oauthlib.oauth1
from django.conf import settings
from lxml import etree
from oauthlib.oauth1.rfc5849 import signature
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment
from webob import Response
from xblock.core import List, Scope, String, XBlock
from xblock.fields import Boolean, Float
from xblocks_contrib.lti import LTIBlock as _ExtractedLTIBlock

from common.djangoapps.xblock_django.constants import (
    ATTR_KEY_ANONYMOUS_USER_ID,
    ATTR_KEY_USER_ROLE,
)
from openedx.core.djangolib.markup import HTML, Text
from xmodule.editing_block import EditingMixin
from xmodule.lti_2_util import LTI20BlockMixin, LTIError
from xmodule.mako_block import MakoTemplateBlockBase
from xmodule.raw_block import EmptyDataRawMixin
from xmodule.util.builtin_assets import add_webpack_js_to_fragment, add_css_to_fragment
from xmodule.x_module import (
    ResourceTemplates,
    shim_xmodule_js,
    XModuleMixin,
    XModuleToXBlockMixin,
)
from xmodule.xml_block import XmlMixin

log = logging.getLogger(__name__)

DOCS_ANCHOR_TAG_OPEN = (
    "<a rel='noopener' target='_blank' "
    "href='https://docs.openedx.org/en/latest/educators/navigation/components_activities.html#lti-component'>"
)
BREAK_TAG = '<br />'

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


class LTIFields:
    """
    Fields to define and obtain LTI tool from provider are set here,
    except credentials, which should be set in course settings::

    `lti_id` is id to connect tool with credentials in course settings. It should not contain :: (double semicolon)
    `launch_url` is launch URL of tool.
    `custom_parameters` are additional parameters to navigate to proper book and book page.

    For example, for Vitalsource provider, `launch_url` should be
    *https://bc-staging.vitalsource.com/books/book*,
    and to get to proper book and book page, you should set custom parameters as::

        vbid=put_book_id_here
        book_location=page/put_page_number_here

    Default non-empty URL for `launch_url` is needed due to oauthlib demand (URL scheme should be presented)::

    https://github.com/idan/oauthlib/blob/master/oauthlib/oauth1/rfc5849/signature.py#L136
    """
    display_name = String(
        display_name=_("Display Name"),
        help=_(
            "The display name for this component. "
            "Analytics reports may also use the display name to identify this component."
        ),
        scope=Scope.settings,
        default="LTI",
    )
    lti_id = String(
        display_name=_("LTI ID"),
        help=Text(_(
            "Enter the LTI ID for the external LTI provider.  "
            "This value must be the same LTI ID that you entered in the "
            "LTI Passports setting on the Advanced Settings page."
            "{break_tag}See {docs_anchor_open}the edX LTI documentation{anchor_close} for more details on this setting."
        )).format(
            break_tag=HTML(BREAK_TAG),
            docs_anchor_open=HTML(DOCS_ANCHOR_TAG_OPEN),
            anchor_close=HTML("</a>")
        ),
        default='',
        scope=Scope.settings
    )
    launch_url = String(
        display_name=_("LTI URL"),
        help=Text(_(
            "Enter the URL of the external tool that this component launches. "
            "This setting is only used when Hide External Tool is set to False."
            "{break_tag}See {docs_anchor_open}the edX LTI documentation{anchor_close} for more details on this setting."
        )).format(
            break_tag=HTML(BREAK_TAG),
            docs_anchor_open=HTML(DOCS_ANCHOR_TAG_OPEN),
            anchor_close=HTML("</a>")
        ),
        default='http://www.example.com',
        scope=Scope.settings)
    custom_parameters = List(
        display_name=_("Custom Parameters"),
        help=Text(_(
            "Add the key/value pair for any custom parameters, such as the page your e-book should open to or "
            "the background color for this component."
            "{break_tag}See {docs_anchor_open}the edX LTI documentation{anchor_close} for more details on this setting."
        )).format(
            break_tag=HTML(BREAK_TAG),
            docs_anchor_open=HTML(DOCS_ANCHOR_TAG_OPEN),
            anchor_close=HTML("</a>")
        ),
        scope=Scope.settings)
    open_in_a_new_page = Boolean(
        display_name=_("Open in New Page"),
        help=_(
            "Select True if you want students to click a link that opens the LTI tool in a new window. "
            "Select False if you want the LTI content to open in an IFrame in the current page. "
            "This setting is only used when Hide External Tool is set to False.  "
        ),
        default=True,
        scope=Scope.settings
    )
    has_score = Boolean(
        display_name=_("Scored"),
        help=_(
            "Select True if this component will receive a numerical score from the external LTI system."
        ),
        default=False,
        scope=Scope.settings
    )
    weight = Float(
        display_name=_("Weight"),
        help=_(
            "Enter the number of points possible for this component.  "
            "The default value is 1.0.  "
            "This setting is only used when Scored is set to True."
        ),
        default=1.0,
        scope=Scope.settings,
        values={"min": 0},
    )
    module_score = Float(
        help=_("The score kept in the xblock KVS -- duplicate of the published score in django DB"),
        default=None,
        scope=Scope.user_state
    )
    score_comment = String(
        help=_("Comment as returned from grader, LTI2.0 spec"),
        default="",
        scope=Scope.user_state
    )
    hide_launch = Boolean(
        display_name=_("Hide External Tool"),
        help=_(
            "Select True if you want to use this component as a placeholder for syncing with an external grading  "
            "system rather than launch an external tool.  "
            "This setting hides the Launch button and any IFrames for this component."
        ),
        default=False,
        scope=Scope.settings
    )

    # Users will be presented with a message indicating that their e-mail/username would be sent to a third
    # party application. When "Open in New Page" is not selected, the tool automatically appears without any user action.  # lint-amnesty, pylint: disable=line-too-long
    ask_to_send_username = Boolean(
        display_name=_("Request user's username"),
        # Translators: This is used to request the user's username for a third party service.
        help=_("Select True to request the user's username."),
        default=False,
        scope=Scope.settings
    )
    ask_to_send_email = Boolean(
        display_name=_("Request user's email"),
        # Translators: This is used to request the user's email for a third party service.
        help=_("Select True to request the user's email address."),
        default=False,
        scope=Scope.settings
    )

    description = String(
        display_name=_("LTI Application Information"),
        help=_(
            "Enter a description of the third party application. If requesting username and/or email, use this text box to inform users "  # lint-amnesty, pylint: disable=line-too-long
            "why their username and/or email will be forwarded to a third party application."
        ),
        default="",
        scope=Scope.settings
    )

    button_text = String(
        display_name=_("Button Text"),
        help=_(
            "Enter the text on the button used to launch the third party application."
        ),
        default="",
        scope=Scope.settings
    )

    accept_grades_past_due = Boolean(
        display_name=_("Accept grades past deadline"),
        help=_("Select True to allow third party systems to post grades past the deadline."),
        default=True,
        scope=Scope.settings
    )


@XBlock.needs("i18n")
@XBlock.needs("mako")
@XBlock.needs("user")
@XBlock.needs("rebind_user")
class _BuiltInLTIBlock(
    LTIFields,
    LTI20BlockMixin,
    EmptyDataRawMixin,
    XmlMixin,
    EditingMixin,
    MakoTemplateBlockBase,
    XModuleToXBlockMixin,
    ResourceTemplates,
    XModuleMixin,
):  # pylint: disable=abstract-method
    """
    THIS MODULE IS DEPRECATED IN FAVOR OF https://github.com/openedx/xblock-lti-consumer

    Module provides LTI integration to course.

    Except usual Xmodule structure it proceeds with OAuth signing.
    How it works::

    1. Get credentials from course settings.

    2.  There is minimal set of parameters need to be signed (presented for Vitalsource)::

            user_id
            oauth_callback
            lis_outcome_service_url
            lis_result_sourcedid
            launch_presentation_return_url
            lti_message_type
            lti_version
            roles
            *+ all custom parameters*

        These parameters should be encoded and signed by *OAuth1* together with
        `launch_url` and *POST* request type.

    3. Signing proceeds with client key/secret pair obtained from course settings.
        That pair should be obtained from LTI provider and set into course settings by course author.
        After that signature and other OAuth data are generated.

        OAuth data which is generated after signing is usual::

            oauth_callback
            oauth_nonce
            oauth_consumer_key
            oauth_signature_method
            oauth_timestamp
            oauth_version


    4. All that data is passed to form and sent to LTI provider server by browser via
        autosubmit via JavaScript.

        Form example::

            <form
                action="${launch_url}"
                name="ltiLaunchForm-${element_id}"
                class="ltiLaunchForm"
                method="post"
                target="ltiLaunchFrame-${element_id}"
                encType="application/x-www-form-urlencoded"
            >
                <input name="launch_presentation_return_url" value="" />
                <input name="lis_outcome_service_url" value="" />
                <input name="lis_result_sourcedid" value="" />
                <input name="lti_message_type" value="basic-lti-launch-request" />
                <input name="lti_version" value="LTI-1p0" />
                <input name="oauth_callback" value="about:blank" />
                <input name="oauth_consumer_key" value="${oauth_consumer_key}" />
                <input name="oauth_nonce" value="${oauth_nonce}" />
                <input name="oauth_signature_method" value="HMAC-SHA1" />
                <input name="oauth_timestamp" value="${oauth_timestamp}" />
                <input name="oauth_version" value="1.0" />
                <input name="user_id" value="${user_id}" />
                <input name="role" value="student" />
                <input name="oauth_signature" value="${oauth_signature}" />

                <input name="custom_1" value="${custom_param_1_value}" />
                <input name="custom_2" value="${custom_param_2_value}" />
                <input name="custom_..." value="${custom_param_..._value}" />

                <input type="submit" value="Press to Launch" />
            </form>

    5. LTI provider has same secret key and it signs data string via *OAuth1* and compares signatures.

        If signatures are correct, LTI provider redirects iframe source to LTI tool web page,
        and LTI tool is rendered to iframe inside course.

        Otherwise error message from LTI provider is generated.
    """
    is_extracted = False
    resources_dir = None
    uses_xmodule_styles_setup = True

    mako_template = 'widgets/metadata-only-edit.html'

    studio_js_module_name = 'MetadataOnlyEditingDescriptor'

    def studio_view(self, _context):
        """
        Return the studio view.
        """
        context = MakoTemplateBlockBase.get_context(self)
        # Add our specific template information (the raw data body)
        context.update({'data': self.data})
        fragment = Fragment(
            self.runtime.service(self, 'mako').render_cms_template(self.mako_template, context)
        )
        add_webpack_js_to_fragment(fragment, 'LTIBlockEditor')
        shim_xmodule_js(fragment, self.studio_js_module_name)
        return fragment

    def max_score(self):
        return self.weight if self.has_score else None

    def get_input_fields(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        # LTI provides a list of default parameters that might be passed as
        # part of the POST data. These parameters should not be prefixed.
        # Likewise, The creator of an LTI link can add custom key/value parameters
        # to a launch which are to be included with the launch of the LTI link.
        # In this case, we will automatically add `custom_` prefix before this parameters.
        # See http://www.imsglobal.org/LTI/v1p1p1/ltiIMGv1p1p1.html#_Toc316828520
        PARAMETERS = [
            "lti_message_type",
            "lti_version",
            "resource_link_title",
            "resource_link_description",
            "user_image",
            "lis_person_name_given",
            "lis_person_name_family",
            "lis_person_name_full",
            "lis_person_contact_email_primary",
            "lis_person_sourcedid",
            "role_scope_mentor",
            "context_type",
            "context_title",
            "context_label",
            "launch_presentation_locale",
            "launch_presentation_document_target",
            "launch_presentation_css_url",
            "launch_presentation_width",
            "launch_presentation_height",
            "launch_presentation_return_url",
            "tool_consumer_info_product_family_code",
            "tool_consumer_info_version",
            "tool_consumer_instance_guid",
            "tool_consumer_instance_name",
            "tool_consumer_instance_description",
            "tool_consumer_instance_url",
            "tool_consumer_instance_contact_email",
        ]

        client_key, client_secret = self.get_client_key_secret()

        # parsing custom parameters to dict
        custom_parameters = {}

        for custom_parameter in self.custom_parameters:
            try:
                param_name, param_value = [p.strip() for p in custom_parameter.split('=', 1)]
            except ValueError:
                _ = self.runtime.service(self, "i18n").ugettext
                msg = _('Could not parse custom parameter: {custom_parameter}. Should be "x=y" string.').format(
                    custom_parameter=f"{custom_parameter!r}"
                )
                raise LTIError(msg)  # lint-amnesty, pylint: disable=raise-missing-from

            # LTI specs: 'custom_' should be prepended before each custom parameter, as pointed in link above.
            if param_name not in PARAMETERS:
                param_name = 'custom_' + param_name

            custom_parameters[str(param_name)] = str(param_value)

        return self.oauth_params(
            custom_parameters,
            client_key,
            client_secret,
        )

    def get_context(self):
        """
        Returns a context.
        """
        # nh3 defaults for
        # ALLOWED_TAGS are
        # {
        #   'a', 'abbr', 'acronym', 'area', 'article', 'aside', 'b', 'bdi', 'bdo',
        #   'blockquote', 'br', 'caption', 'center', 'cite', 'code', 'col', 'colgroup',
        #   'data', 'dd', 'del', 'details', 'dfn', 'div', 'dl', 'dt', 'em', 'figcaption',
        #   'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup',
        #   'hr', 'i', 'img', 'ins', 'kbd', 'li', 'map', 'mark', 'nav', 'ol', 'p', 'pre',
        #   'q', 'rp', 'rt', 'rtc', 'ruby', 's', 'samp', 'small', 'span', 'strike',
        #   'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'th', 'thead',
        #   'time', 'tr', 'tt', 'u', 'ul', 'var', 'wbr'
        # }
        #
        # ALLOWED_ATTRIBUTES are
        # {
        #   'a': {'href', 'hreflang'},
        #   'bdo': {'dir'},
        #   'blockquote': {'cite'},
        #   'col': {'charoff', 'char', 'align', 'span'},
        #   'colgroup': {'align', 'char', 'charoff', 'span'},
        #   'del': {'datetime', 'cite'},
        #   'hr': {'width', 'align', 'size'},
        #   'img': {'height', 'src', 'width', 'alt', 'align'},
        #   'ins': {'datetime', 'cite'},
        #   'ol': {'start'},
        #   'q': {'cite'},
        #   'table': {'align', 'char', 'charoff', 'summary'},
        #   'tbody': {'align', 'char', 'charoff'},
        #   'td': {'rowspan', 'headers', 'charoff', 'colspan', 'char', 'align'},
        #   'tfoot': {'align', 'char', 'charoff'},
        #   'th': {'rowspan', 'headers', 'charoff', 'colspan', 'scope', 'char', 'align'},
        #   'thead': {'charoff', 'char', 'align'},
        #   'tr': {'align', 'char', 'charoff'}
        # }
        #
        # This lets all plaintext through.
        sanitized_comment = nh3.clean(self.score_comment)

        return {
            'input_fields': self.get_input_fields(),

            # These parameters do not participate in OAuth signing.
            'launch_url': self.launch_url.strip(),
            'element_id': self.location.html_id(),
            'element_class': self.category,
            'open_in_a_new_page': self.open_in_a_new_page,
            'display_name': self.display_name,
            'form_url': self.runtime.handler_url(self, 'preview_handler').rstrip('/?'),
            'hide_launch': self.hide_launch,
            'has_score': self.has_score,
            'weight': self.weight,
            'module_score': self.module_score,
            'comment': sanitized_comment,
            'description': self.description,
            'ask_to_send_username': self.ask_to_send_username,
            'ask_to_send_email': self.ask_to_send_email,
            'button_text': self.button_text,
            'accept_grades_past_due': self.accept_grades_past_due,
        }

    def student_view(self, _context):
        """
        Return the student view.
        """
        fragment = Fragment()
        fragment.add_content(self.runtime.service(self, 'mako').render_lms_template('lti.html', self.get_context()))
        add_css_to_fragment(fragment, 'LTIBlockDisplay.css')
        add_webpack_js_to_fragment(fragment, 'LTIBlockDisplay')
        shim_xmodule_js(fragment, 'LTI')
        return fragment

    @XBlock.handler
    def preview_handler(self, _, __):
        """
        This is called to get context with new oauth params to iframe.
        """
        template = self.runtime.service(self, 'mako').render_lms_template('lti_form.html', self.get_context())
        return Response(template, content_type='text/html')

    def get_user_id(self):
        """
        Returns the current user ID, URL-escaped so it is safe to use as a URL component.
        """
        user_id = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_ANONYMOUS_USER_ID)
        assert user_id is not None
        return str(parse.quote(user_id))

    def get_outcome_service_url(self, service_name="grade_handler"):
        """
        Return URL for storing grades.

        To test LTI on sandbox we must use http scheme.

        While testing locally and on Jenkins, mock_lti_server use http.referer
        to obtain scheme, so it is ok to have http(s) anyway.

        The scheme logic is handled in lms/lib/xblock/runtime.py
        """
        return self.runtime.handler_url(self, service_name, thirdparty=True).rstrip('/?')

    def get_resource_link_id(self):
        """
        This is an opaque unique identifier that the TC guarantees will be unique
        within the TC for every placement of the link.

        If the tool / activity is placed multiple times in the same context,
        each of those placements will be distinct.

        This value will also change if the item is exported from one system or
        context and imported into another system or context.

        This parameter is required.

        Example:  u'edx.org-i4x-2-3-lti-31de800015cf4afb973356dbe81496df'

        Hostname, edx.org,
        makes resource_link_id change on import to another system.

        Last part of location, location.name - 31de800015cf4afb973356dbe81496df,
        is random hash, updated by course_id,
        this makes resource_link_id unique inside single course.

        First part of location is tag-org-course-category, i4x-2-3-lti.

        Location.name itself does not change on import to another course,
        but org and course_id change.

        So together with org and course_id in a form of
        i4x-2-3-lti-31de800015cf4afb973356dbe81496df this part of resource_link_id:
        makes resource_link_id to be unique among courses inside same system.
        """
        return str(parse.quote(f"{settings.LMS_BASE}-{self.location.html_id()}"))

    def get_lis_result_sourcedid(self):
        """
        This field contains an identifier that indicates the LIS Result Identifier (if any)
        associated with this launch.  This field identifies a unique row and column within the
        TC gradebook.  This field is unique for every combination of context_id / resource_link_id / user_id.
        This value may change for a particular resource_link_id / user_id  from one launch to the next.
        The TP should only retain the most recent value for this field for a particular resource_link_id / user_id.
        This field is generally optional, but is required for grading.
        """
        return "{context}:{resource_link}:{user_id}".format(
            context=parse.quote(self.context_id),
            resource_link=self.get_resource_link_id(),
            user_id=self.get_user_id()
        )

    def get_course(self):
        """
        Return course by course id.

        Returns None if the current block is not part of a course (i.e part of a library).
        """
        if isinstance(self.course_id, CourseKey):
            return self.runtime.modulestore.get_course(self.course_id)
        return None

    @property
    def context_id(self):
        """
        Return context_id.

        context_id is an opaque identifier that uniquely identifies the context (e.g., a course)
        that contains the link being launched.
        """
        return str(self.course_id)

    @property
    def role(self):
        """
        Get system user role and convert it to LTI role.
        """
        roles = {
            'student': 'Student',
            'staff': 'Administrator',
            'instructor': 'Instructor',
        }
        user_role = self.runtime.service(self, 'user').get_current_user().opt_attrs.get(ATTR_KEY_USER_ROLE)
        return roles.get(user_role, 'Student')

    def get_icon_class(self):
        """ Returns the icon class """
        if self.graded and self.has_score:  # pylint: disable=no-member
            return 'problem'
        return 'other'

    def oauth_params(self, custom_parameters, client_key, client_secret):
        """
        Signs request and returns signature and OAuth parameters.

        `custom_paramters` is dict of parsed `custom_parameter` field
        `client_key` and `client_secret` are LTI tool credentials.

        Also *anonymous student id* is passed to template and therefore to LTI provider.
        """

        client = oauthlib.oauth1.Client(
            client_key=str(client_key),
            client_secret=str(client_secret)
        )

        # Must have parameters for correct signing from LTI:
        body = {
            'user_id': self.get_user_id(),
            'oauth_callback': 'about:blank',
            'launch_presentation_return_url': '',
            'lti_message_type': 'basic-lti-launch-request',
            'lti_version': 'LTI-1p0',
            'roles': self.role,

            # Parameters required for grading:
            'resource_link_id': self.get_resource_link_id(),
            'lis_result_sourcedid': self.get_lis_result_sourcedid(),

            'context_id': self.context_id,
        }

        if self.has_score:
            body.update({
                'lis_outcome_service_url': self.get_outcome_service_url()
            })

        self.user_email = ""  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.user_username = ""  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        # Username and email can't be sent in studio mode, because the user object is not defined.
        # To test functionality test in LMS

        real_user_object = self.runtime.service(self, 'user').get_user_by_anonymous_id()
        try:
            self.user_email = real_user_object.email  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        except AttributeError:
            self.user_email = ""  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        try:
            self.user_username = real_user_object.username  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        except AttributeError:
            self.user_username = ""  # lint-amnesty, pylint: disable=attribute-defined-outside-init

        if self.ask_to_send_username and self.user_username:
            body["lis_person_sourcedid"] = self.user_username
        if self.ask_to_send_email and self.user_email:
            body["lis_person_contact_email_primary"] = self.user_email

        # Appending custom parameter for signing.
        body.update(custom_parameters)

        headers = {
            # This is needed for body encoding:
            'Content-Type': 'application/x-www-form-urlencoded',
        }

        try:
            __, headers, __ = client.sign(
                str(self.launch_url.strip()),
                http_method='POST',
                body=body,
                headers=headers)
        except ValueError:  # Scheme not in url.
            # https://github.com/idan/oauthlib/blob/master/oauthlib/oauth1/rfc5849/signature.py#L136
            # Stubbing headers for now:
            log.info(
                "LTI block %s in course %s does not have oauth parameters correctly configured.",
                self.location,
                self.location.course_key,
            )
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Authorization': 'OAuth oauth_nonce="80966668944732164491378916897", \
oauth_timestamp="1378916897", oauth_version="1.0", oauth_signature_method="HMAC-SHA1", \
oauth_consumer_key="", oauth_signature="frVp4JuvT1mVXlxktiAUjQ7%2F1cw%3D"'}

        params = headers['Authorization']
        # Parse headers to pass to template as part of context:
        params = dict([param.strip().replace('"', '').split('=') for param in params.split(',')])

        params['oauth_nonce'] = params['OAuth oauth_nonce']
        del params['OAuth oauth_nonce']

        # oauthlib encodes signature with
        # 'Content-Type': 'application/x-www-form-urlencoded'
        # so '='' becomes '%3D'.
        # We send form via browser, so browser will encode it again,
        # So we need to decode signature back:
        params['oauth_signature'] = parse.unquote(params['oauth_signature']).encode('utf-8').decode('utf8')  # lint-amnesty, pylint: disable=line-too-long

        # Add LTI parameters to OAuth parameters for sending in form.
        params.update(body)
        return params

    @XBlock.handler
    def grade_handler(self, request, suffix):  # lint-amnesty, pylint: disable=unused-argument
        """
        This is called by courseware.block_render, to handle an AJAX call.

        Used only for grading. Returns XML response.

        Example of request body from LTI provider::

        <?xml version = "1.0" encoding = "UTF-8"?>
            <imsx_POXEnvelopeRequest xmlns = "some_link (may be not required)">
              <imsx_POXHeader>
                <imsx_POXRequestHeaderInfo>
                  <imsx_version>V1.0</imsx_version>
                  <imsx_messageIdentifier>528243ba5241b</imsx_messageIdentifier>
                </imsx_POXRequestHeaderInfo>
              </imsx_POXHeader>
              <imsx_POXBody>
                <replaceResultRequest>
                  <resultRecord>
                    <sourcedGUID>
                      <sourcedId>feb-123-456-2929::28883</sourcedId>
                    </sourcedGUID>
                    <result>
                      <resultScore>
                        <language>en-us</language>
                        <textString>0.4</textString>
                      </resultScore>
                    </result>
                  </resultRecord>
                </replaceResultRequest>
              </imsx_POXBody>
            </imsx_POXEnvelopeRequest>

        Example of correct/incorrect answer XML body:: see response_xml_template.
        """
        response_xml_template = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <imsx_POXEnvelopeResponse xmlns = "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">
                <imsx_POXHeader>
                    <imsx_POXResponseHeaderInfo>
                        <imsx_version>V1.0</imsx_version>
                        <imsx_messageIdentifier>{imsx_messageIdentifier}</imsx_messageIdentifier>
                        <imsx_statusInfo>
                            <imsx_codeMajor>{imsx_codeMajor}</imsx_codeMajor>
                            <imsx_severity>status</imsx_severity>
                            <imsx_description>{imsx_description}</imsx_description>
                            <imsx_messageRefIdentifier>
                            </imsx_messageRefIdentifier>
                        </imsx_statusInfo>
                    </imsx_POXResponseHeaderInfo>
                </imsx_POXHeader>
                <imsx_POXBody>{response}</imsx_POXBody>
            </imsx_POXEnvelopeResponse>
        """)
        # Returns when `action` is unsupported.
        # Supported actions:
        #   - replaceResultRequest.
        unsupported_values = {
            'imsx_codeMajor': 'unsupported',
            'imsx_description': 'Target does not support the requested operation.',
            'imsx_messageIdentifier': 'unknown',
            'response': ''
        }
        # Returns if:
        #   - past due grades are not accepted and grade is past due
        #   - score is out of range
        #   - can't parse response from TP;
        #   - can't verify OAuth signing or OAuth signing is incorrect.
        failure_values = {
            'imsx_codeMajor': 'failure',
            'imsx_description': 'The request has failed.',
            'imsx_messageIdentifier': 'unknown',
            'response': ''
        }

        if not self.accept_grades_past_due and self.is_past_due():
            failure_values['imsx_description'] = "Grade is past due"
            return Response(response_xml_template.format(**failure_values), content_type="application/xml")

        try:
            imsx_messageIdentifier, sourcedId, score, action = self.parse_grade_xml_body(request.body)
        except Exception as e:  # lint-amnesty, pylint: disable=broad-except
            error_message = "Request body XML parsing error: " + escape(str(e))
            log.debug("[LTI]: " + error_message)  # lint-amnesty, pylint: disable=logging-not-lazy
            failure_values['imsx_description'] = error_message
            return Response(response_xml_template.format(**failure_values), content_type="application/xml")

        # Verify OAuth signing.
        try:
            self.verify_oauth_body_sign(request)
        except (ValueError, LTIError) as e:
            failure_values['imsx_messageIdentifier'] = escape(imsx_messageIdentifier)
            error_message = "OAuth verification error: " + escape(str(e))
            failure_values['imsx_description'] = error_message
            log.debug("[LTI]: " + error_message)  # lint-amnesty, pylint: disable=logging-not-lazy
            return Response(response_xml_template.format(**failure_values), content_type="application/xml")

        real_user = self.runtime.service(self, 'user').get_user_by_anonymous_id(parse.unquote(sourcedId.split(':')[-1]))
        if not real_user:  # that means we can't save to database, as we do not have real user id.
            failure_values['imsx_messageIdentifier'] = escape(imsx_messageIdentifier)
            failure_values['imsx_description'] = "User not found."
            return Response(response_xml_template.format(**failure_values), content_type="application/xml")

        if action == 'replaceResultRequest':
            self.set_user_module_score(real_user, score, self.max_score())

            values = {
                'imsx_codeMajor': 'success',
                'imsx_description': f'Score for {sourcedId} is now {score}',
                'imsx_messageIdentifier': escape(imsx_messageIdentifier),
                'response': '<replaceResultResponse/>'
            }
            log.debug("[LTI]: Grade is saved.")
            return Response(response_xml_template.format(**values), content_type="application/xml")

        unsupported_values['imsx_messageIdentifier'] = escape(imsx_messageIdentifier)
        log.debug("[LTI]: Incorrect action.")
        return Response(response_xml_template.format(**unsupported_values), content_type='application/xml')

    @classmethod
    def parse_grade_xml_body(cls, body):
        """
        Parses XML from request.body and returns parsed data

        XML body should contain nsmap with namespace, that is specified in LTI specs.

        Returns tuple: imsx_messageIdentifier, sourcedId, score, action

        Raises Exception if can't parse.
        """
        lti_spec_namespace = "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0"
        namespaces = {'def': lti_spec_namespace}

        data = body.strip()
        parser = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')
        root = etree.fromstring(data, parser=parser)

        imsx_messageIdentifier = root.xpath("//def:imsx_messageIdentifier", namespaces=namespaces)[0].text or ''
        sourcedId = root.xpath("//def:sourcedId", namespaces=namespaces)[0].text
        score = root.xpath("//def:textString", namespaces=namespaces)[0].text
        action = root.xpath("//def:imsx_POXBody", namespaces=namespaces)[0].getchildren()[0].tag.replace('{' + lti_spec_namespace + '}', '')  # lint-amnesty, pylint: disable=line-too-long
        # Raise exception if score is not float or not in range 0.0-1.0 regarding spec.
        score = float(score)
        if not 0 <= score <= 1:
            raise LTIError('score value outside the permitted range of 0-1.')

        return imsx_messageIdentifier, sourcedId, score, action

    def verify_oauth_body_sign(self, request, content_type='application/x-www-form-urlencoded'):
        """
        Verify grade request from LTI provider using OAuth body signing.

        Uses http://oauth.googlecode.com/svn/spec/ext/body_hash/1.0/oauth-bodyhash.html::

            This specification extends the OAuth signature to include integrity checks on HTTP request bodies
            with content types other than application/x-www-form-urlencoded.

        Arguments:
            request: DjangoWebobRequest.

        Raises:
            LTIError if request is incorrect.
        """

        client_key, client_secret = self.get_client_key_secret()  # lint-amnesty, pylint: disable=unused-variable
        headers = {
            'Authorization': str(request.headers.get('Authorization')),
            'Content-Type': content_type,
        }

        sha1 = hashlib.sha1()
        sha1.update(request.body)
        oauth_body_hash = base64.b64encode(sha1.digest()).decode('utf-8')
        oauth_params = signature.collect_parameters(headers=headers, exclude_oauth_signature=False)
        oauth_headers = dict(oauth_params)
        oauth_signature = oauth_headers.pop('oauth_signature')
        mock_request_lti_1 = mock.Mock(
            uri=str(parse.unquote(self.get_outcome_service_url())),
            http_method=str(request.method),
            params=list(oauth_headers.items()),
            signature=oauth_signature
        )
        mock_request_lti_2 = mock.Mock(
            uri=str(parse.unquote(request.url)),
            http_method=str(request.method),
            params=list(oauth_headers.items()),
            signature=oauth_signature
        )
        if oauth_body_hash != oauth_headers.get('oauth_body_hash'):
            log.error(
                "OAuth body hash verification failed, provided: {}, "
                "calculated: {}, for url: {}, body is: {}".format(
                    oauth_headers.get('oauth_body_hash'),
                    oauth_body_hash,
                    self.get_outcome_service_url(),
                    request.body
                )
            )
            raise LTIError("OAuth body hash verification is failed.")

        if (not signature.verify_hmac_sha1(mock_request_lti_1, client_secret) and not
                signature.verify_hmac_sha1(mock_request_lti_2, client_secret)):
            log.error("OAuth signature verification failed, for "
                      "headers:{} url:{} method:{}".format(
                          oauth_headers,
                          self.get_outcome_service_url(),
                          str(request.method)
                      ))
            raise LTIError("OAuth signature verification has failed.")

    def get_client_key_secret(self):
        """
        Obtains client_key and client_secret credentials from current course.
        """
        course = self.get_course()
        lti_passports = course.lti_passports if course else []
        for lti_passport in lti_passports:
            try:
                lti_id, key, secret = [i.strip() for i in lti_passport.split(':')]
            except ValueError:
                _ = self.runtime.service(self, "i18n").ugettext
                msg = _('Could not parse LTI passport: {lti_passport}. Should be "id:key:secret" string.').format(
                    lti_passport=f'{lti_passport!r}'
                )
                raise LTIError(msg)  # lint-amnesty, pylint: disable=raise-missing-from

            if lti_id == self.lti_id.strip():
                return key, secret
        return '', ''

    def is_past_due(self):
        """
        Is it now past this problem's due date, including grace period?
        """
        due_date = self.due  # pylint: disable=no-member
        if self.graceperiod is not None and due_date:  # pylint: disable=no-member
            close_date = due_date + self.graceperiod  # pylint: disable=no-member
        else:
            close_date = due_date
        return close_date is not None and datetime.datetime.now(ZoneInfo("UTC")) > close_date


LTIBlock = None


def reset_class():
    """Reset class as per django settings flag"""
    global LTIBlock
    LTIBlock = (
        _ExtractedLTIBlock if settings.USE_EXTRACTED_LTI_BLOCK
        else _BuiltInLTIBlock
    )
    return LTIBlock

reset_class()
LTIBlock.__name__ = "LTIBlock"
