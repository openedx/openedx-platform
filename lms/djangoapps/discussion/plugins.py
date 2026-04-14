"""
Views handling read (GET) requests for the Discussion tab and inline discussions.
"""


from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_noop

import lms.djangoapps.discussion.django_comment_client.utils as utils
from lms.djangoapps.courseware.tabs import EnrolledTab
from lms.djangoapps.discussion.toggles import ENABLE_DISCUSSIONS_MFE
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.djangoapps.discussions.url_helpers import get_discussions_mfe_url
from openedx.features.lti_course_tab.tab import DiscussionLtiCourseTab
from xmodule.tabs import TabFragmentViewMixin


class DiscussionTab(TabFragmentViewMixin, EnrolledTab):
    """
    A tab for the forums.
    """

    type = 'discussion'
    title = gettext_noop('Discussion')
    priority = 40
    view_name = 'forum_form_discussion'
    fragment_view_name = 'lms.djangoapps.discussion.views.DiscussionBoardFragmentView'
    is_hideable = settings.FEATURES.get('ALLOW_HIDING_DISCUSSION_TAB', False)
    is_default = False
    body_class = 'discussion'
    online_help_token = 'discussions'

    @classmethod
    def is_enabled(cls, course, user=None):
        if not DiscussionsConfiguration.is_enabled(context_key=course.id):
            return False
        if not super().is_enabled(course, user):
            return False
        # Disable the regular discussion tab if LTI-based external Discussion forum is enabled
        if DiscussionLtiCourseTab.is_enabled(course, user):
            return False
        return utils.is_discussion_enabled(course.id)

    @property
    def link_func(self):
        def _link_func(course, reverse_func):
            if ENABLE_DISCUSSIONS_MFE.is_enabled(course.id):
                return get_discussions_mfe_url(course_key=course.id)
            return reverse('forum_form_discussion', args=[str(course.id)])

        return _link_func
