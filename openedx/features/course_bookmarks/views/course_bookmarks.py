"""
Views to show a course's bookmarks.
"""


from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.template.context_processors import csrf
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import View
from opaque_keys.edx.keys import CourseKey
from web_fragments.fragment import Fragment

from common.djangoapps.util.views import ensure_valid_course_key
from lms.djangoapps.courseware.courses import get_course_with_access
from openedx.core.djangoapps.plugin_api.views import EdxFragmentView
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.features.course_experience import default_course_url


class CourseBookmarksView(View):
    """
    View showing the user's bookmarks for a course.
    """
    @method_decorator(login_required)
    @method_decorator(ensure_csrf_cookie)
    @method_decorator(cache_control(no_cache=True, no_store=True, must_revalidate=True))
    @method_decorator(ensure_valid_course_key)
    def get(self, request, course_id):
        """
        Displays the user's bookmarks for the specified course.

        Arguments:
            request: HTTP request
            course_id (unicode): course id
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)
        course_url = default_course_url(course.id)

        # Render the bookmarks list as a fragment
        bookmarks_fragment = CourseBookmarksFragmentView().render_to_fragment(request, course_id=course_id)

        # Render the course bookmarks page
        context = {
            'csrf': csrf(request)['csrf_token'],
            'course': course,
            'supports_preview_menu': True,
            'course_url': course_url,
            'bookmarks_fragment': bookmarks_fragment,
            'disable_courseware_js': True,
            'uses_bootstrap': True,
        }
        return render(request, 'course_bookmarks/course-bookmarks.html', context)


class CourseBookmarksFragmentView(EdxFragmentView):
    """
    Fragment view that shows a user's bookmarks for a course.
    """

    def render_to_fragment(self, request, course_id=None, **kwargs):  # lint-amnesty, pylint: disable=arguments-differ
        """
        Renders the user's course bookmarks as a fragment.
        """
        course_key = CourseKey.from_string(course_id)
        course = get_course_with_access(request.user, 'load', course_key, check_if_enrolled=True)

        language = UserPreference.get_value(request.user, 'pref-lang', default='en')

        context = {
            'csrf': csrf(request)['csrf_token'],
            'course': course,
            'bookmarks_api_url': reverse('bookmarks'),
            'language_preference': language,
        }
        html = render_to_string('course_bookmarks/course-bookmarks-fragment.html', context)
        inline_js = render_to_string('course_bookmarks/course_bookmarks_js.template', context)
        fragment = Fragment(html)
        self.add_fragment_resource_urls(fragment)
        fragment.add_javascript(inline_js)
        return fragment

    def standalone_page_title(self, request, fragment, **kwargs):
        """
        Returns the standalone page title.
        """
        return _('Bookmarks')
