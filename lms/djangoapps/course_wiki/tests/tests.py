"""
Tests for course wiki
"""


from unittest.mock import patch

from django.urls import reverse

from lms.djangoapps.courseware.tests.tests import LoginEnrollmentTestCase
from openedx.features.course_experience.url_helpers import make_learning_mfe_courseware_url
from openedx.features.enterprise_support.tests.mixins.enterprise import EnterpriseTestConsentRequired
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order


class WikiRedirectTestCase(EnterpriseTestConsentRequired, LoginEnrollmentTestCase, ModuleStoreTestCase):
    """
    Tests for wiki course redirection.
    """

    def setUp(self):
        super().setUp()
        self.toy = CourseFactory.create(org='edX', course='toy', display_name='2012_Fall')

        # Create two accounts
        self.student = 'view@test.com'
        self.instructor = 'view2@test.com'
        self.password = self.TEST_PASSWORD
        for username, email in [('u1', self.student), ('u2', self.instructor)]:
            self.create_account(username, email, self.password)
            self.activate_user(email)
            self.logout()

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    def test_wiki_redirect(self):
        """
        Test that requesting wiki URLs redirect properly to or out of classes.

        An enrolled in student going from /courses/edX/toy/2012_Fall/progress
        to /wiki/some/fake/wiki/page/ will redirect to
        /courses/edX/toy/2012_Fall/wiki/some/fake/wiki/page/

        An unenrolled student going to /courses/edX/toy/2012_Fall/wiki/some/fake/wiki/page/
        will be redirected to /wiki/some/fake/wiki/page/

        """
        self.login(self.student, self.password)

        self.enroll(self.toy)

        referer = reverse("progress", kwargs={'course_id': str(self.toy.id)})
        destination = reverse("wiki:get", kwargs={'path': 'some/fake/wiki/page/'})

        redirected_to = referer.replace("progress", "wiki/some/fake/wiki/page/")

        resp = self.client.get(destination, HTTP_REFERER=referer)
        assert resp.status_code == 302

        assert resp['Location'] == redirected_to

        # Now we test that the student will be redirected away from that page if the course doesn't exist
        # We do this in the same test because we want to make sure the redirected_to is constructed correctly
        # This is a location like /courses/*/wiki/* , but with an invalid course ID
        bad_course_wiki_page = redirected_to.replace(self.toy.location.course, "bad_course")

        resp = self.client.get(bad_course_wiki_page, HTTP_REFERER=referer)
        assert resp.status_code == 302
        assert resp['Location'] == destination

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': False})
    def test_wiki_no_root_access(self):
        """
        Test to verify that normally Wiki's cannot be browsed from the /wiki/xxxx/yyy/zz URLs

        """
        self.login(self.student, self.password)

        self.enroll(self.toy)

        referer = reverse("progress", kwargs={'course_id': str(self.toy.id)})
        destination = reverse("wiki:get", kwargs={'path': 'some/fake/wiki/page/'})

        resp = self.client.get(destination, HTTP_REFERER=referer)
        assert resp.status_code == 403

    def create_course_page(self, course):
        """
        Test that loading the course wiki page creates the wiki page.
        The user must be enrolled in the course to see the page.
        """

        course_wiki_home = reverse('course_wiki', kwargs={'course_id': str(course.id)})
        referer = reverse("progress", kwargs={'course_id': str(course.id)})

        resp = self.client.get(course_wiki_home, follow=True, HTTP_REFERER=referer)

        course_wiki_page = referer.replace('progress', 'wiki/' + course.wiki_slug + "/")

        ending_location = resp.redirect_chain[-1][0]

        assert ending_location == course_wiki_page
        assert resp.status_code == 200

        self.has_course_navigator(resp)
        self.assertContains(resp, f'<h3 class="entry-title">{course.display_name_with_default}</h3>')

    def has_course_navigator(self, resp):
        """
        Ensure that the response has the course navigator.
        """
        self.assertContains(resp, "Home")
        self.assertContains(resp, "Course")

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    def test_course_navigator(self):
        """"
        Test that going from a course page to a wiki page contains the course navigator.
        """

        self.login(self.student, self.password)
        self.enroll(self.toy)
        self.create_course_page(self.toy)

        course_wiki_page = reverse('wiki:get', kwargs={'path': self.toy.wiki_slug + '/'})
        referer = make_learning_mfe_courseware_url(self.toy.id)

        resp = self.client.get(course_wiki_page, follow=True, HTTP_REFERER=referer)

        self.has_course_navigator(resp)

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    def test_wiki_not_accessible_when_not_enrolled(self):
        """
        Test that going from a course page to a wiki page when not enrolled
        redirects a user to the course about page
        """

        self.login(self.instructor, self.password)
        self.enroll(self.toy)
        self.create_course_page(self.toy)
        self.logout()

        self.login(self.student, self.password)
        course_wiki_page = reverse('wiki:get', kwargs={'path': self.toy.wiki_slug + '/'})
        referer = make_learning_mfe_courseware_url(self.toy.id)

        # When not enrolled, we should get a 302
        resp = self.client.get(course_wiki_page, follow=False, HTTP_REFERER=referer)
        assert resp.status_code == 302

        # and end up at the course about page
        resp = self.client.get(course_wiki_page, follow=True, HTTP_REFERER=referer)
        target_url, __ = resp.redirect_chain[-1]
        assert target_url.endswith(reverse('about_course', args=[str(self.toy.id)]))

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    def test_redirect_when_not_logged_in(self):
        """
        Test that attempting to reach a course wiki page when not logged in
        redirects the user to the login page
        """
        self.logout()
        course_wiki_page = reverse('wiki:get', kwargs={'path': self.toy.wiki_slug + '/'})

        # When not logged in, we should get a 302
        resp = self.client.get(course_wiki_page, follow=False)
        assert resp.status_code == 302

        # and end up at the login page
        resp = self.client.get(course_wiki_page, follow=True)
        target_url, __ = resp.redirect_chain[-1]
        assert reverse('signin_user') in target_url

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    def test_create_wiki_with_long_course_id(self):
        """
        Tests that the wiki is successfully created for courses that have
        very long course ids.
        """
        # Combined course key length is currently capped at 65 characters (see MAX_SUM_KEY_LENGTH
        # in /common/static/common/js/components/utils/view_utils.js).
        # The below key components combined are exactly 65 characters long.
        org = 'a-very-long-org-name'
        course = 'a-very-long-course-name'
        display_name = 'very-long-display-name'
        # This is how wiki_slug is generated in cms/djangoapps/contentstore/views/course.py.
        wiki_slug = f"{org}.{course}.{display_name}"

        assert len((org + course) + display_name) == 65
        # sanity check

        course = CourseFactory.create(org=org, course=course, display_name=display_name, wiki_slug=wiki_slug)

        self.login(self.student, self.password)
        self.enroll(course)
        self.create_course_page(course)

        course_wiki_page = reverse('wiki:get', kwargs={'path': course.wiki_slug + '/'})
        referer = make_learning_mfe_courseware_url(self.toy.id)

        resp = self.client.get(course_wiki_page, follow=True, HTTP_REFERER=referer)
        assert resp.status_code == 200

    @patch.dict("django.conf.settings.FEATURES", {'ALLOW_WIKI_ROOT_ACCESS': True})
    @patch('openedx.features.enterprise_support.api.enterprise_customer_for_request')
    def test_consent_required(self, mock_enterprise_customer_for_request):
        """
        Test that enterprise data sharing consent is required when enabled for the various courseware views.
        """
        # ENT-924: Temporary solution to replace sensitive SSO usernames.
        mock_enterprise_customer_for_request.return_value = None

        # Public wikis can be accessed by non-enrolled users, and so direct access is not gated by the consent page
        course = CourseFactory.create()
        course.allow_public_wiki_access = False
        course.save()

        # However, for private wikis, enrolled users must pass through the consent gate
        # (Unenrolled users are redirected to course/about)
        course_id = str(course.id)
        self.login(self.student, self.password)
        self.enroll(course)

        for (url, status_code) in (
                (reverse('course_wiki', kwargs={'course_id': course_id}), 302),
                (f'/courses/{course_id}/wiki/', 200),
        ):
            self.verify_consent_required(self.client, url, status_code=status_code)  # pylint: disable=no-value-for-parameter
