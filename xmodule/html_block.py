# pylint: disable=missing-module-docstring

import textwrap
from datetime import datetime

from xblock.core import XBlock
from xblock.fields import Boolean, List, Scope, String
from xblocks_contrib.html import HtmlBlockMixin

from xmodule.x_module import ResourceTemplates

# Make '_' a no-op so we can scrape strings. Using lambda instead of
#  `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


class AboutFields:  # pylint: disable=missing-class-docstring
    display_name = String(
        help=_("The display name for this component."),
        scope=Scope.settings,
        default="overview",
    )
    data = String(
        help=_("Html contents to display for this block"),
        default="",
        scope=Scope.content
    )


@XBlock.tag("detached")
# ResourceTemplates is required on the LMS side to load template resources for this AboutBlock.
# On the CMS side, it is already included via XBLOCK_MIXINS.
class AboutBlock(AboutFields, ResourceTemplates, HtmlBlockMixin):  # pylint: disable=abstract-method
    """
    These pieces of course content are treated as HtmlBlocks but we need to overload where the templates are located
    in order to be able to create new ones
    """
    template_dir_name = "about"


class StaticTabFields:
    """
    The overrides for Static Tabs
    """
    display_name = String(
        display_name=_("Display Name"),
        help=_("The display name for this component."),
        scope=Scope.settings,
        default="Empty",
    )
    course_staff_only = Boolean(
        display_name=_("Hide Page From Learners"),
        help=_("If you select this option, only course team members with"
               " the Staff or Admin role see this page."),
        default=False,
        scope=Scope.settings
    )
    data = String(
        default=textwrap.dedent("""\
            <p>Add the content you want students to see on this page.</p>
        """),
        scope=Scope.content,
        help=_("HTML for the additional pages")
    )


@XBlock.tag("detached")
class StaticTabBlock(StaticTabFields, HtmlBlockMixin):  # pylint: disable=abstract-method
    """
    These pieces of course content are treated as HtmlBlocks but we need to overload where the templates are located
    in order to be able to create new ones
    """
    template_dir_name = None


class CourseInfoFields:
    """
    Field overrides
    """
    items = List(
        help=_("List of course update items"),
        default=[],
        scope=Scope.content
    )
    data = String(
        help=_("Html contents to display for this block"),
        default="<ol></ol>",
        scope=Scope.content
    )


@XBlock.tag("detached")
@XBlock.needs('replace_urls')
@XBlock.needs('mako')
class CourseInfoBlock(CourseInfoFields, HtmlBlockMixin):  # pylint: disable=abstract-method
    """
    These pieces of course content are treated as HtmlBlock but we need to overload where the templates are located
    in order to be able to create new ones
    """
    # statuses
    STATUS_VISIBLE = 'visible'
    STATUS_DELETED = 'deleted'
    TEMPLATE_DIR = 'courseware'

    template_dir_name = None

    def get_html(self):
        """ Returns html required for rendering XModule. """

        # When we switch this to an XBlock, we can merge this with student_view,
        # but for now the XModule mixin requires that this method be defined.
        data = super().get_html()
        if data != "":
            return data
        else:
            # This should no longer be called on production now that we are using a separate updates page
            # and using a fragment HTML file - it will be called in tests until those are removed.
            course_updates = self.order_updates(self.items)
            context = {
                'visible_updates': course_updates[:3],
                'hidden_updates': course_updates[3:],
            }
            return self.runtime.service(self, 'mako').render_lms_template(
                f"{self.TEMPLATE_DIR}/course_updates.html",
                context,
            )

    @classmethod
    def order_updates(self, updates):  # pylint: disable=bad-classmethod-argument
        """
        Returns any course updates in reverse chronological order.
        """
        sorted_updates = [update for update in updates if update.get('status') == self.STATUS_VISIBLE]
        sorted_updates.sort(
            key=lambda item: (self.safe_parse_date(item['date']), item['id']),
            reverse=True
        )
        return sorted_updates

    @staticmethod
    def safe_parse_date(date):
        """
        Since this is used solely for ordering purposes, use today's date as a default
        """
        try:
            return datetime.strptime(date, '%B %d, %Y')
        except ValueError:  # occurs for ill-formatted date values
            return datetime.today()
