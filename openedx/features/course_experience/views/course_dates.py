"""
Fragment for rendering the course dates sidebar.
"""


from django.template.loader import render_to_string
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment

from lms.djangoapps.courseware.courses import get_course_date_blocks, get_course_with_access
from lms.djangoapps.courseware.tabs import DatesTab
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from openedx.features.course_experience.url_helpers import get_learning_mfe_home_url


class CourseDatesFragmentView(EdxFragmentView):
    """
    A fragment to important dates within a course.
    """
    template_name = 'course_experience/course-dates-fragment.html'

    def render_to_fragment(self, request, course_id=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Render the course dates fragment.
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=False)
        course_date_blocks = get_course_date_blocks(course, request.user, request, num_assignments=1)

        dates_tab_enabled = DatesTab.is_enabled(course, request.user)
        dates_tab_link = get_learning_mfe_home_url(course_key=course.id, url_fragment='dates')

        context = {
            'course_date_blocks': [block for block in course_date_blocks if block.title != 'current_datetime'],
            'dates_tab_link': dates_tab_link,
            'dates_tab_enabled': dates_tab_enabled,
        }
        html = render_to_string(self.template_name, context)
        dates_fragment = Fragment(html)
        self.add_fragment_resource_urls(dates_fragment)

        return dates_fragment
