"""
A mixin that provides functionality and default attributes for all XBlocks in
the new XBlock runtime.
"""


from django.utils.translation import gettext as _
from web_fragments.fragment import Fragment
from xblock.core import XBlock, XBlockMixin
from xblock.exceptions import JsonHandlerError

from openedx.core.djangolib.markup import HTML


@XBlock.wants('completion')
class LmsBlockMixin(XBlockMixin):
    """
    A mixin that provides functionality and default attributes for all XBlocks
    in the new XBlock runtime.

    These are not standard XBlock attributes but are used by the LMS (and
    possibly Studio).
    """

    # This indicates whether the XBlock has a score (e.g. it's a problem, not
    # static content). If it does, it should set this and provide scoring
    # functionality by inheriting xblock.scorable.ScorableXBlockMixin
    has_score = False

    @XBlock.json_handler
    def publish_completion(self, data, suffix=''):  # pylint: disable=unused-argument
        """
        Allow the frontend app that's rendering this XBlock to mark it as
        completed when the user views it, if appropriate.

        Copied from lms.djangoapps.lms_xblock.mixin.LmsBlockMixin
        """
        completion_service = self.runtime.service(self, 'completion')
        if completion_service is None:  # lint-amnesty, pylint: disable=no-else-raise
            raise JsonHandlerError(500, "No completion service found")
        elif not completion_service.completion_tracking_enabled():
            raise JsonHandlerError(404, "Completion tracking is not enabled and API calls are unexpected")
        if not completion_service.can_mark_block_complete_on_view(self):
            raise JsonHandlerError(400, "Block not configured for completion on view.")
        self.runtime.publish(self, "completion", data)
        return {'result': 'ok'}

    def public_view(self, _context):
        """
        Default message for blocks that don't implement public_view

        public_view is shown when users aren't logged in and/or are not enrolled
        in a particular course.
        """
        alert_html = HTML(
            '<div class="page-banner"><div class="alert alert-warning">'
            '<span class="icon icon-alert fa fa fa-warning" aria-hidden="true"></span>'
            '<div class="message-content">{}</div></div></div>'
        )

        # Determine if the user is seeing public_view because they're not logged in or because they're not enrolled.
        # (Note: 'self.runtime.user' is not part of the XBlock API and some runtimes don't provide it, but this mixin is
        # part of the runtime so it's OK to access it that way.)
        if self.runtime.user is None or self.runtime.user.is_anonymous:
            display_text = _('This content is only accessible to registered learners. Sign in or register to view it.')
        else:
            # This is a registered user but they're still seeing public_view
            # so they must be excluded because of enrollment status.
            display_text = _('This content is only accessible to enrolled learners. ')

        return Fragment(alert_html.format(display_text))
