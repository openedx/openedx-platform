""" Test cases for the textbook index page. """


import json
from unittest import TestCase

from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_course_url

from ..course import TextbookValidationError, validate_textbook_json, validate_textbooks_json


class TextbookIndexTestCase(CourseTestCase):
    "Test cases for the textbook index page"
    def setUp(self):
        "Set the URL for tests"
        super().setUp()
        self.url = reverse_course_url('textbooks_list_handler', self.course.id)

    def test_view_index(self):
        "Basic check that the textbook index page responds correctly"
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)  # noqa: PT009

    def test_view_index_xhr(self):
        "Check that we get a JSON response when requested via AJAX"
        resp = self.client.get(
            self.url,
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        obj = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(self.course.pdf_textbooks, obj)  # noqa: PT009

    def test_view_index_xhr_content(self):
        "Check that the response maps to the content of the modulestore"
        content = [
            {
                "tab_title": "my textbook",
                "url": "/abc.pdf",
                "id": "992"
            }, {
                "tab_title": "pineapple",
                "id": "0pineapple",
                "chapters": [
                    {
                        "title": "The Fruit",
                        "url": "/a/b/fruit.pdf",
                    }, {
                        "title": "The Legend",
                        "url": "/b/c/legend.pdf",
                    }
                ]
            }
        ]
        self.course.pdf_textbooks = content
        self.save_course()

        resp = self.client.get(
            self.url,
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        obj = json.loads(resp.content.decode('utf-8'))

        self.assertEqual(content, obj)  # noqa: PT009

    def test_view_index_xhr_put(self):
        "Check that you can save information to the server"
        textbooks = [
            {"tab_title": "Hi, mom!"},
            {"tab_title": "Textbook 2"},
        ]
        resp = self.client.put(
            self.url,
            data=json.dumps(textbooks),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)  # noqa: PT009

        # should be the same, except for added ID
        no_ids = []
        self.reload_course()
        for textbook in self.course.pdf_textbooks:
            del textbook["id"]
            no_ids.append(textbook)
        self.assertEqual(no_ids, textbooks)  # noqa: PT009

    def test_view_index_xhr_put_invalid(self):
        "Check that you can't save invalid JSON"
        resp = self.client.put(
            self.url,
            data="invalid",
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        obj = json.loads(resp.content.decode('utf-8'))
        self.assertIn("error", obj)  # noqa: PT009


class TextbookCreateTestCase(CourseTestCase):
    "Test cases for creating a new PDF textbook"

    def setUp(self):
        "Set up a url and some textbook content for tests"
        super().setUp()
        self.url = reverse_course_url('textbooks_list_handler', self.course.id)

        self.textbook = {
            "tab_title": "Economics",
            "chapters": {
                "title": "Chapter 1",
                "url": "/a/b/c/ch1.pdf",
            }
        }

    def test_happy_path(self):
        "Test that you can create a textbook"
        resp = self.client.post(
            self.url,
            data=json.dumps(self.textbook),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 201)  # noqa: PT009
        self.assertIn("Location", resp)  # noqa: PT009
        textbook = json.loads(resp.content.decode('utf-8'))
        self.assertIn("id", textbook)  # noqa: PT009
        del textbook["id"]
        self.assertEqual(self.textbook, textbook)  # noqa: PT009

    def test_valid_id(self):
        "Textbook IDs must begin with a number; try a valid one"
        self.textbook["id"] = "7x5"
        resp = self.client.post(
            self.url,
            data=json.dumps(self.textbook),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 201)  # noqa: PT009
        textbook = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(self.textbook, textbook)  # noqa: PT009

    def test_invalid_id(self):
        "Textbook IDs must begin with a number; try an invalid one"
        self.textbook["id"] = "xxx"
        resp = self.client.post(
            self.url,
            data=json.dumps(self.textbook),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)  # noqa: PT009
        self.assertNotIn("Location", resp)  # noqa: PT009


class TextbookDetailTestCase(CourseTestCase):
    "Test cases for the `textbook_detail_handler` view"

    def setUp(self):
        "Set some useful content and URLs for tests"
        super().setUp()
        self.textbook1 = {
            "tab_title": "Economics",
            "id": 1,
            "chapters": {
                "title": "Chapter 1",
                "url": "/a/b/c/ch1.pdf",
            }
        }
        self.url1 = self.get_details_url("1")

        self.textbook2 = {
            "tab_title": "Algebra",
            "id": 2,
            "chapters": {
                "title": "Chapter 11",
                "url": "/a/b/ch11.pdf",
            }
        }
        self.url2 = self.get_details_url("2")
        self.course.pdf_textbooks = [self.textbook1, self.textbook2]
        # Save the data that we've just changed to the underlying
        # MongoKeyValueStore before we update the mongo datastore.
        self.save_course()
        self.url_nonexist = self.get_details_url("1=20")

    def get_details_url(self, textbook_id):
        """
        Returns the URL for textbook detail handler.
        """
        return reverse_course_url(
            'textbooks_detail_handler',
            self.course.id,
            kwargs={'textbook_id': textbook_id}
        )

    def test_get_1(self):
        "Get the first textbook"
        resp = self.client.get(self.url1)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        compare = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(compare, self.textbook1)  # noqa: PT009

    def test_get_2(self):
        "Get the second textbook"
        resp = self.client.get(self.url2)
        self.assertEqual(resp.status_code, 200)  # noqa: PT009
        compare = json.loads(resp.content.decode('utf-8'))
        self.assertEqual(compare, self.textbook2)  # noqa: PT009

    def test_get_nonexistant(self):
        "Get a nonexistent textbook"
        resp = self.client.get(self.url_nonexist)
        self.assertEqual(resp.status_code, 404)  # noqa: PT009

    def test_delete(self):
        "Delete a textbook by ID"
        resp = self.client.delete(self.url1)
        self.assertEqual(resp.status_code, 204)  # noqa: PT009
        self.reload_course()
        self.assertEqual(self.course.pdf_textbooks, [self.textbook2])  # noqa: PT009

    def test_delete_nonexistant(self):
        "Delete a textbook by ID, when the ID doesn't match an existing textbook"
        resp = self.client.delete(self.url_nonexist)
        self.assertEqual(resp.status_code, 404)  # noqa: PT009
        self.reload_course()
        self.assertEqual(self.course.pdf_textbooks, [self.textbook1, self.textbook2])  # noqa: PT009

    def test_create_new_by_id(self):
        "Create a textbook by ID"
        textbook = {
            "tab_title": "a new textbook",
            "url": "supercool.pdf",
            "id": "1supercool",
        }
        url = self.get_details_url("1supercool")
        resp = self.client.post(
            url,
            data=json.dumps(textbook),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)  # noqa: PT009
        resp2 = self.client.get(url)
        self.assertEqual(resp2.status_code, 200)  # noqa: PT009
        compare = json.loads(resp2.content.decode('utf-8'))
        self.assertEqual(compare, textbook)  # noqa: PT009
        self.reload_course()
        self.assertEqual(  # noqa: PT009
            self.course.pdf_textbooks,
            [self.textbook1, self.textbook2, textbook]
        )

    def test_replace_by_id(self):
        "Create a textbook by ID, overwriting an existing textbook ID"
        replacement = {
            "tab_title": "You've been replaced!",
            "url": "supercool.pdf",
            "id": "2",
        }
        resp = self.client.post(
            self.url2,
            data=json.dumps(replacement),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)  # noqa: PT009
        resp2 = self.client.get(self.url2)
        self.assertEqual(resp2.status_code, 200)  # noqa: PT009
        compare = json.loads(resp2.content.decode('utf-8'))
        self.assertEqual(compare, replacement)  # noqa: PT009
        course = self.store.get_item(self.course.location)
        self.assertEqual(  # noqa: PT009
            course.pdf_textbooks,
            [self.textbook1, replacement]
        )


class TextbookValidationTestCase(TestCase):
    "Tests for the code to validate the structure of a PDF textbook"

    def setUp(self):
        "Set some useful content for tests"
        super().setUp()

        self.tb1 = {
            "tab_title": "Hi, mom!",
            "url": "/mom.pdf"
        }
        self.tb2 = {
            "tab_title": "Hi, dad!",
            "chapters": [
                {
                    "title": "Baseball",
                    "url": "baseball.pdf",
                }, {
                    "title": "Basketball",
                    "url": "crazypants.pdf",
                }
            ]
        }
        self.textbooks = [self.tb1, self.tb2]

    def test_happy_path_plural(self):
        "Test that the plural validator works properly"
        result = validate_textbooks_json(json.dumps(self.textbooks))
        self.assertEqual(self.textbooks, result)  # noqa: PT009

    def test_happy_path_singular_1(self):
        "Test that the singular validator works properly"
        result = validate_textbook_json(json.dumps(self.tb1))
        self.assertEqual(self.tb1, result)  # noqa: PT009

    def test_happy_path_singular_2(self):
        "Test that the singular validator works properly, with different data"
        result = validate_textbook_json(json.dumps(self.tb2))
        self.assertEqual(self.tb2, result)  # noqa: PT009

    def test_valid_id(self):
        "Test that a valid ID doesn't trip the validator, and comes out unchanged"
        self.tb1["id"] = 1
        result = validate_textbook_json(json.dumps(self.tb1))
        self.assertEqual(self.tb1, result)  # noqa: PT009

    def test_invalid_id(self):
        "Test that an invalid ID trips the validator"
        self.tb1["id"] = "abc"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbook_json(json.dumps(self.tb1))

    def test_invalid_json_plural(self):
        "Test that invalid JSON trips the plural validator"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbooks_json("[{'abc'}]")

    def test_invalid_json_singular(self):
        "Test that invalid JSON trips the singluar validator"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbook_json("[{1]}")

    def test_wrong_json_plural(self):
        "Test that a JSON object trips the plural validators (requires a list)"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbooks_json('{"tab_title": "Hi, mom!"}')

    def test_wrong_json_singular(self):
        "Test that a JSON list trips the plural validators (requires an object)"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbook_json('[{"tab_title": "Hi, mom!"}, {"tab_title": "Hi, dad!"}]')

    def test_no_tab_title_plural(self):
        "Test that `tab_title` is required for the plural validator"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbooks_json('[{"url": "/textbook.pdf"}]')

    def test_no_tab_title_singular(self):
        "Test that `tab_title` is required for the singular validator"
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbook_json('{"url": "/textbook.pdf"}')

    def test_duplicate_ids(self):
        "Test that duplicate IDs in the plural validator trips the validator"
        textbooks = [{
            "tab_title": "name one",
            "url": "one.pdf",
            "id": 1,
        }, {
            "tab_title": "name two",
            "url": "two.pdf",
            "id": 1,
        }]
        with self.assertRaises(TextbookValidationError):  # noqa: PT027
            validate_textbooks_json(json.dumps(textbooks))
