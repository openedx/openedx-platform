"""
Code related to LTI course tab functionality.
"""
from typing import Dict
from urllib.parse import quote

from django.contrib.auth.models import AbstractBaseUser
from django.contrib.sites.shortcuts import get_current_site
from django.http import HttpRequest
from django.utils.translation import get_language, gettext_lazy, to_locale
from lti_consumer.api import get_lti_pii_sharing_state_for_course
from lti_consumer.lti_1p1.contrib.django import lti_embed
from lti_consumer.models import LtiConfiguration
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment

from common.djangoapps.student.models import anonymous_id_for_user
from lms.djangoapps.courseware.access import get_user_role
from lms.djangoapps.courseware.tabs import EnrolledTab
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration
from openedx.core.djangolib.markup import HTML
from xmodule.course_block import CourseBlock  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tabs import TabFragmentViewMixin, key_checker  # lint-amnesty, pylint: disable=wrong-import-order


class LtiCourseLaunchMixin:
    """
    Mixin that encapsulates all LTI-related functionality from the View
    """

    ROLE_MAP = {
        'student': 'Student,Learner',
        'staff': 'Administrator',
        'instructor': 'Instructor',
    }
    DEFAULT_ROLE = 'Student,Learner'

    def _get_pii_lti_parameters(self, course: CourseBlock, request: HttpRequest) -> Dict[str, str]:
        """
        Get LTI parameters that contain PII.

        Args:
            course (CourseBlock): CourseBlock object.
            request (HttpRequest): Request object for view in which LTI will be embedded.

        Returns:
            Dictionary with LTI parameters containing PII.
        """
        pii_sharing_allowed = get_lti_pii_sharing_state_for_course(course.id)
        if not pii_sharing_allowed:
            return {}
        lti_config = self._get_lti_config(course)
        # Currently only LTI 1.1 is supported by the tab
        if lti_config.version != lti_config.LTI_1P1:
            return {}

        pii_config = {}
        if lti_config.pii_share_username:
            pii_config['person_sourcedid'] = request.user.username

        if lti_config.pii_share_email:
            pii_config['person_contact_email_primary'] = request.user.email
        return pii_config

    def _get_additional_lti_parameters(self, course: CourseBlock, request: HttpRequest) -> Dict[str, str]:
        """
        Get additional misc LTI parameters.

        Args:
            course (CourseBlock): CourseBlock object.
            request (HttpRequest): Request object for view in which LTI will be embedded.

        Returns:
            Dictionary with additional LTI parameters.
        """
        lti_config = self._get_lti_config(course)
        additional_config = lti_config.lti_config.get('additional_parameters', {})
        return additional_config

    @staticmethod
    def _get_user_id(user: AbstractBaseUser, course_key: CourseKey):
        return anonymous_id_for_user(user, course_key)

    def _get_lti_roles(self, user: AbstractBaseUser, course_key: CourseKey) -> str:
        return self.ROLE_MAP.get(
            get_user_role(user, course_key),
            self.DEFAULT_ROLE,
        )

    @staticmethod
    def _get_context_id(course_key: CourseKey) -> str:
        return quote(str(course_key))

    @staticmethod
    def _get_resource_link_id(course_key: CourseKey, request: HttpRequest) -> str:
        site = get_current_site(request)
        return '{}-{}'.format(
            site.domain,
            str(course_key.make_usage_key('course', course_key.run)),
        )

    @staticmethod
    def _get_result_sourcedid(context_id: str, resource_link_id: str, user_id: str) -> str:
        return "{context}:{resource_link}:{user_id}".format(
            context=context_id,
            resource_link=resource_link_id,
            user_id=user_id,
        )

    @staticmethod
    def _get_context_title(course: CourseBlock) -> str:
        return "{} - {}".format(
            course.display_name_with_default,
            course.display_org_with_default,
        )

    def _get_lti_config(self, course: CourseBlock) -> LtiConfiguration:
        raise NotImplementedError

    def _get_lti_embed_code(self, course: CourseBlock, request: HttpRequest) -> str:
        """
        Returns the LTI embed code for embedding in the current course context.
        Args:
            course (CourseBlock): CourseBlock object.
            request (HttpRequest): Request object for view in which LTI will be embedded.
        Returns:
            HTML code to embed LTI in course page.
        """
        course_key = course.id
        lti_config = self._get_lti_config(course)
        lti_consumer = lti_config.get_lti_consumer()
        user_id = quote(self._get_user_id(request.user, course_key))
        context_id = quote(self._get_context_id(course_key))
        resource_link_id = quote(self._get_resource_link_id(course_key, request))
        roles = self._get_lti_roles(request.user, course_key)
        context_title = self._get_context_title(course)
        result_sourcedid = quote(self._get_result_sourcedid(context_id, resource_link_id, user_id))
        additional_params = self._get_additional_lti_parameters(course, request)
        pii_params = self._get_pii_lti_parameters(course, request)
        locale = to_locale(get_language())

        return lti_embed(
            html_element_id='lti-tab-launcher',
            lti_consumer=lti_consumer,
            resource_link_id=resource_link_id,
            user_id=user_id,
            roles=roles,
            context_id=context_id,
            context_title=context_title,
            context_label=context_id,
            result_sourcedid=result_sourcedid,
            launch_presentation_locale=locale,
            **pii_params,
            **additional_params,
        )

    # pylint: disable=unused-argument
    def render_to_fragment(self, request: HttpRequest, course: CourseBlock, **kwargs) -> Fragment:
        """
        Returns a fragment view for the LTI launch.
        Args:
            request (HttpRequest): request object
            course (CourseBlock): A course object
        Returns:
            A Fragment that embeds LTI in a course page.
        """
        lti_embed_html = self._get_lti_embed_code(course, request)

        fragment = Fragment(
            HTML(
                """
                <iframe
                    id='lti-tab-embed'
                    srcdoc='{srcdoc}'
                 >
                </iframe>
                """
            ).format(
                srcdoc=lti_embed_html
            )
        )
        fragment.add_css(
            """
            #lti-tab-embed {
                width: 100%;
                min-height: 800px;
                border: none;
            }
            """
        )
        return fragment


class LtiCourseTab(LtiCourseLaunchMixin, EnrolledTab):
    """
    A tab to add custom LTI components to a course in a tab.
    """
    type = 'lti_tab'
    priority = 120
    is_default = False
    allow_multiple = True

    def _get_lti_config(self, course: CourseBlock) -> LtiConfiguration:
        return LtiConfiguration.objects.get(config_id=self.lti_config_id)

    def __init__(self, tab_dict=None, name=None, lti_config_id=None):
        def link_func(course, reverse_func):
            """ Returns a function that returns the lti tab's URL. """
            return reverse_func('lti_course_tab', args=[str(course.id), self.lti_config_id])

        self.lti_config_id = tab_dict.get('lti_config_id') if tab_dict else lti_config_id

        if tab_dict is None:
            tab_dict = {}

        if name is not None:
            tab_dict['name'] = name

        tab_dict['link_func'] = link_func
        tab_dict['tab_id'] = f'lti_tab_{self.lti_config_id}'

        super().__init__(tab_dict)

    @classmethod
    def validate(cls, tab_dict, raise_error=True):
        """
        Ensures that the specified tab_dict is valid.
        """
        return (
            super().validate(tab_dict, raise_error)
            and key_checker(['name', 'lti_config_id'])(tab_dict, raise_error)
        )

    def __getitem__(self, key):
        if key == 'lti_config_id':
            return self.lti_config_id
        else:
            return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key == 'lti_config_id':
            self.lti_config_id = value
        else:
            super().__setitem__(key, value)

    def to_json(self):
        """
        Return a dictionary representation of this tab.
        """
        to_json_val = super().to_json()
        to_json_val.update({'lti_config_id': self.lti_config_id})
        return to_json_val

    def __eq__(self, other):
        if not super().__eq__(other):
            return False
        return self.lti_config_id == other.get('lti_config_id')

    def __hash__(self):
        """
        Return a hash representation of Tab Object.
        """
        return hash(repr(self))


class DiscussionLtiCourseTab(LtiCourseLaunchMixin, TabFragmentViewMixin, EnrolledTab):
    """
    Course tab that loads the associated LTI-based discussion provider in a tab.
    """
    type = 'lti_discussion'
    priority = 41
    allow_multiple = False
    is_dynamic = True
    title = gettext_lazy("Discussion")

    def _get_lti_config(self, course: CourseBlock) -> LtiConfiguration:
        config = DiscussionsConfiguration.get(course.id)
        return config.lti_configuration

    @classmethod
    def is_enabled(cls, course, user=None):
        """Check if the tab is enabled."""
        if super().is_enabled(course, user):
            return DiscussionsConfiguration.lti_discussion_enabled(course.id)
        return False
