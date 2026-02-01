"""
Test the lms/staticbook views.
"""


import textwrap
from unittest import mock

import pytest
import requests
from django.urls import NoReverseMatch, reverse

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

IMAGE_BOOK = ("An Image Textbook", "http://example.com/the_book/")

PDF_BOOK = {
    "tab_title": "Textbook",
    "title": "A PDF Textbook",
    "chapters": [
        {"title": "Chapter 1 for PDF", "url": "https://somehost.com/the_book/chap1.pdf"},
        {"title": "Chapter 2 for PDF", "url": "https://somehost.com/the_book/chap2.pdf"},
    ],
}

PORTABLE_PDF_BOOK = {
    "tab_title": "Textbook",
    "title": "A PDF Textbook",
    "chapters": [
        {"title": "Chapter 1 for PDF", "url": "/static/chap1.pdf"},
        {"title": "Chapter 2 for PDF", "url": "/static/chap2.pdf"},
    ],
}

HTML_BOOK = {
    "tab_title": "Textbook",
    "title": "An HTML Textbook",
    "chapters": [
        {"title": "Chapter 1 for HTML", "url": "https://somehost.com/the_book/chap1.html"},
        {"title": "Chapter 2 for HTML", "url": "https://somehost.com/the_book/chap2.html"},
    ],
}


class StaticBookTest(ModuleStoreTestCase):
    """
    Helpers for the static book tests.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = None

    def make_course(self, **kwargs):
        """
        Make a course with an enrolled logged-in student.
        """
        self.course = CourseFactory.create(**kwargs)
        user = UserFactory.create()
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id)
        self.client.login(username=user.username, password=self.TEST_PASSWORD)

    def make_url(self, url_name, **kwargs):
        """
        Make a URL for a `url_name` using keyword args for url slots.

        Automatically provides the course id.

        """
        kwargs['course_id'] = str(self.course.id)
        url = reverse(url_name, kwargs=kwargs)
        return url


class StaticImageBookTest(StaticBookTest):
    """
    Test the image-based static book view.
    """

    def test_book(self):
        # We can access a book.
        with mock.patch.object(requests, 'get') as mock_get:
            mock_get.return_value.text = textwrap.dedent('''\
                <?xml version="1.0"?>
                <table_of_contents>
                <entry page="9" page_label="ix" name="Contents!?"/>
                <entry page="1" page_label="i" name="Preamble">
                    <entry page="4" page_label="iv" name="About the Elephants"/>
                </entry>
                </table_of_contents>
                ''')

            self.make_course(textbooks=[IMAGE_BOOK])
            url = self.make_url('book', book_index=0)
            response = self.client.get(url)

        self.assertContains(response, "Contents!?")
        self.assertContains(response, "About the Elephants")

    def test_bad_book_id(self):
        # A bad book id will be a 404.
        self.make_course(textbooks=[IMAGE_BOOK])
        with pytest.raises(NoReverseMatch):
            self.make_url('book', book_index='fooey')

    def test_out_of_range_book_id(self):
        self.make_course()
        url = self.make_url('book', book_index=0)
        response = self.client.get(url)
        assert response.status_code == 404

    def test_bad_page_id(self):
        # A bad page id will cause a 404.
        self.make_course(textbooks=[IMAGE_BOOK])
        with pytest.raises(NoReverseMatch):
            self.make_url('book', book_index=0, page='xyzzy')


class StaticPdfBookTest(StaticBookTest):
    """
    Test the PDF static book view.
    """

    def test_book(self):
        # We can access a book.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        url = self.make_url('pdf_book', book_index=0)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 1 for PDF")
        # Verify file parameter is not present (security fix)
        self.assertNotContains(response, "file=")
        # Verify postMessage infrastructure is in place
        self.assertContains(response, "request_pdf_url")
        self.assertContains(response, "pdf_url_response")

    def test_book_chapter(self):
        # We can access a book at a particular chapter.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        url = self.make_url('pdf_book', book_index=0, chapter=2)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 2 for PDF")
        # Verify file parameter is not present anywhere (security fix)
        self.assertNotContains(response, "file=")
        # Verify postMessage infrastructure is in place
        self.assertContains(response, "request_pdf_url")

    def test_book_page(self):
        # We can access a book at a particular page.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        url = self.make_url('pdf_book', book_index=0, page=17)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 1 for PDF")
        # Verify file parameter is not present (security fix)
        self.assertNotContains(response, "file=")
        # Page parameter is still used in viewer_params
        self.assertContains(response, "page=17")

    def test_book_chapter_page(self):
        # We can access a book at a particular chapter and page.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        url = self.make_url('pdf_book', book_index=0, chapter=2, page=17)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 2 for PDF")
        # Verify file parameter is not present (security fix)
        self.assertNotContains(response, "file=")
        # Page parameter is still used in viewer_params
        self.assertContains(response, "page=17")

    def test_bad_book_id(self):
        # If the book id isn't an int, we'll get a 404.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        with pytest.raises(NoReverseMatch):
            self.make_url('pdf_book', book_index='fooey', chapter=1)

    def test_out_of_range_book_id(self):
        # If we have one book, asking for the second book will fail with a 404.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        url = self.make_url('pdf_book', book_index=1, chapter=1)
        response = self.client.get(url)
        assert response.status_code == 404

    def test_no_book(self):
        # If we have no books, asking for the first book will fail with a 404.
        self.make_course()
        url = self.make_url('pdf_book', book_index=0, chapter=1)
        response = self.client.get(url)
        assert response.status_code == 404

    def test_chapter_xss(self):
        # The chapter in the URL used to go right on the page.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        # It's no longer possible to use a non-integer chapter.
        with pytest.raises(NoReverseMatch):
            self.make_url('pdf_book', book_index=0, chapter='xyzzy')

    def test_page_xss(self):
        # The page in the URL used to go right on the page.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        # It's no longer possible to use a non-integer page.
        with pytest.raises(NoReverseMatch):
            self.make_url('pdf_book', book_index=0, page='xyzzy')

    def test_chapter_page_xss(self):
        # The page in the URL used to go right on the page.
        self.make_course(pdf_textbooks=[PDF_BOOK])
        # It's no longer possible to use a non-integer page and a non-integer chapter.
        with pytest.raises(NoReverseMatch):
            self.make_url('pdf_book', book_index=0, chapter='fooey', page='xyzzy')

    def test_static_url_map_contentstore(self):
        """
        This ensure static URL mapping is happening properly for
        a course that uses the contentstore.
        URLs are remapped in backend but not exposed via file parameter (security fix).
        """
        self.make_course(pdf_textbooks=[PORTABLE_PDF_BOOK])
        url = self.make_url('pdf_book', book_index=0, chapter=1)
        response = self.client.get(url)
        # Verify file parameter is not present in response (security fix)
        self.assertNotContains(response, 'file=')
        # Verify the chapter URL is in the sidebar for postMessage communication
        self.assertContains(response, '/asset-v1:{0.org}+{0.course}+{0.run}+type@asset+block/{1}'.format(
            self.course.location,
            PORTABLE_PDF_BOOK['chapters'][0]['url'].replace('/static/', '')))

    def test_static_url_map_static_asset_path(self):
        """
        Like above, but used when the course has set a static_asset_path.
        URLs are remapped in backend but not exposed via file parameter (security fix).
        """
        self.make_course(pdf_textbooks=[PORTABLE_PDF_BOOK], static_asset_path='awesomesauce')
        url = self.make_url('pdf_book', book_index=0, chapter=1)
        response = self.client.get(url)
        # Verify file parameter is not present anywhere (security fix)
        self.assertNotContains(response, 'file=')
        # Verify the remapped URL is in the sidebar for postMessage communication
        self.assertContains(response, '/static/awesomesauce/{}'.format(
            PORTABLE_PDF_BOOK['chapters'][0]['url'].replace('/static/', '')))

    def test_invalid_chapter_id(self):
        """
        Test that 1st chapter is displayed to the user when an invalid chapter id is provided
        """
        self.make_course(pdf_textbooks=[PDF_BOOK])
        invalid_chapter = len(PDF_BOOK['chapters']) + 1
        url = self.make_url('pdf_book', book_index=0, chapter=invalid_chapter)
        response = self.client.get(url)
        assert response.status_code == 200
        self.assertContains(response, "Chapter 1 for PDF")


class StaticHtmlBookTest(StaticBookTest):
    """
    Test the HTML static book view.
    """

    def test_book(self):
        # We can access a book.
        self.make_course(html_textbooks=[HTML_BOOK])
        url = self.make_url('html_book', book_index=0)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 1 for HTML")
        self.assertNotContains(response, "options.chapterNum =")

    def test_book_chapter(self):
        # We can access a book at a particular chapter.
        self.make_course(html_textbooks=[HTML_BOOK])
        url = self.make_url('html_book', book_index=0, chapter=2)
        response = self.client.get(url)
        self.assertContains(response, "Chapter 2 for HTML")
        self.assertContains(response, "options.chapterNum = 2;")

    def test_bad_book_id(self):
        # If we have one book, asking for the second book will fail with a 404.
        self.make_course(html_textbooks=[HTML_BOOK])
        url = self.make_url('html_book', book_index=1, chapter=1)
        response = self.client.get(url)
        assert response.status_code == 404

    def test_no_book(self):
        # If we have no books, asking for the first book will fail with a 404.
        self.make_course()
        url = self.make_url('html_book', book_index=0, chapter=1)
        response = self.client.get(url)
        assert response.status_code == 404

    def test_chapter_xss(self):
        # The chapter in the URL used to go right on the page.
        self.make_course(pdf_textbooks=[HTML_BOOK])
        # It's no longer possible to use a non-integer chapter.
        with pytest.raises(NoReverseMatch):
            self.make_url('html_book', book_index=0, chapter='xyzzy')
