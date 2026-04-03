"""Middleware for course_wiki"""


from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.utils.deprecation import MiddlewareMixin
from wiki.models import reverse

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.courses import get_course_overview_with_access, get_course_with_access
from openedx.core.lib.request_utils import course_id_from_url
from openedx.features.enterprise_support.api import get_enterprise_consent_url
from xmodule.modulestore.django import modulestore


class WikiAccessMiddleware(MiddlewareMixin):
    """
    This middleware wraps calls to django-wiki in order to handle authentication and redirection
    between the root wiki and the course wikis.

    TODO: removing the "root wiki" would obviate the need for this middleware; it could be replaced
          with a wrapper function around the wiki views. This is currently difficult or impossible to do
          because there are two sets of wiki urls loaded in urls.py
    """
    def _redirect_from_referrer(self, request, wiki_path):
        """
        redirect to course wiki url if the referrer is from a course page
        """
        course_id = course_id_from_url(request.META.get('HTTP_REFERER'))
        if course_id:
            # See if we are able to view the course. If we are, redirect to it
            try:
                get_course_overview_with_access(request.user, 'load', course_id)
                return redirect(f"/courses/{str(course_id)}/wiki/{wiki_path}")  # lint-amnesty, pylint: disable=line-too-long
            except Http404:
                # Even though we came from the course, we can't see it. So don't worry about it.
                pass

    def process_view(self, request, view_func, view_args, view_kwargs):  # lint-amnesty, pylint: disable=unused-argument
        """
        This function handles authentication logic for wiki urls and redirects from
        the "root wiki" to the "course wiki" if the user accesses the wiki from a course url
        """
        # we care only about requests to wiki urls
        if not view_func.__module__.startswith('wiki.'):
            return

        # wiki pages are login required
        if not request.user.is_authenticated:
            return redirect(reverse('signin_user'), next=request.path)

        course_id = course_id_from_url(request.path)
        wiki_path = request.path.partition('/wiki/')[2]

        # if no wiki_path, can't get wiki_slug, so no point trying to look up
        # course_id by wiki_slug
        if not course_id and wiki_path:
            # wiki path always starts with wiki_slug
            wiki_slug = wiki_path.split('/')[0]

            course_ids = modulestore().get_courses_for_wiki(wiki_slug)
            # the above can return multiple courses, and to avoid ambiguity and
            # avoid pointing to wrong courses, we only set course_id if we've
            # got an exact match, i.e. only one course was returned for a
            # wiki_slug
            if len(course_ids) == 1:
                course_id = course_ids[0]

        if course_id:
            # This is a /courses/org/name/run/wiki request
            course_path = f"/courses/{str(course_id)}"
            # HACK: django-wiki monkeypatches the reverse function to enable
            # urls to be rewritten
            reverse._transform_url = lambda url: course_path + url  # pylint: disable=protected-access
            # Authorization Check
            # Let's see if user is enrolled or the course allows for public access
            try:
                course = get_course_with_access(request.user, 'load', course_id)
            except Http404:
                # course does not exist. redirect to root wiki.
                # clearing the referrer will cause process_response not to redirect
                # back to a non-existent course
                request.META['HTTP_REFERER'] = ''
                return redirect(f'/wiki/{wiki_path}')

            if not course.allow_public_wiki_access:
                is_enrolled = CourseEnrollment.is_enrolled(request.user, course.id)
                is_staff = has_access(request.user, 'staff', course)
                if not (is_enrolled or is_staff):
                    # if a user is logged in, but not authorized to see a page,
                    # we'll redirect them to the course about page
                    return redirect('about_course', str(course_id))

                # If we need enterprise data sharing consent for this course, then redirect to the form.
                consent_url = get_enterprise_consent_url(request, str(course_id), source='WikiAccessMiddleware')
                if consent_url:
                    return redirect(consent_url)

            # set the course onto here so that the wiki template can show the course navigation
            request.course = course
        else:
            # this is a request for /wiki/...

            # Check to see if we don't allow top-level access to the wiki via the /wiki/xxxx/yyy/zzz URLs
            # this will help prevent people from writing pell-mell to the Wiki in an unstructured way
            if not settings.FEATURES.get('ALLOW_WIKI_ROOT_ACCESS', False):
                raise PermissionDenied()

            return self._redirect_from_referrer(request, wiki_path)

    def process_response(self, request, response):
        """
        Modify the redirect from /wiki/123 to /course/foo/bar/wiki/123/
        if the referrer comes from a course page
        """
        if response.status_code == 302 and response['Location'].startswith('/wiki/'):
            wiki_path = urlparse(response['Location']).path.split('/wiki/', 1)[1]

            response = self._redirect_from_referrer(request, wiki_path) or response

        # END HACK: _transform_url must be set to a no-op function after it's done its work
        reverse._transform_url = lambda url: url  # pylint: disable=protected-access
        return response
