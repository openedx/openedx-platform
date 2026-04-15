"""
Unittest for generate a test course in an given modulestore
"""


import json
from unittest import mock

import ddt
from django.core.management import CommandError, call_command

from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase


@ddt.ddt
class TestGenerateCourses(ModuleStoreTestCase):
    """
    Unit tests for creating a course in split store via command line
    """

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_generate_course_in_stores(self, mock_logger):
        """
        Test that a course is created successfully
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course", "announcement": "2010-04-20T20:08:21.634121"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        key = modulestore().make_course_key("test-course-generator", "1", "1")
        self.assertTrue(modulestore().has_course(key))  # noqa: PT009
        mock_logger.info.assert_any_call("Created course-v1:test-course-generator+1+1")
        mock_logger.info.assert_any_call("announcement has been set to 2010-04-20T20:08:21.634121")
        mock_logger.info.assert_any_call("display_name has been set to test-course")

    def test_invalid_json(self):
        """
        Test that providing an invalid JSON object will result in the appropriate command error
        """
        with self.assertRaisesRegex(CommandError, "Invalid JSON object"):  # noqa: PT027
            arg = "invalid_json"
            call_command("generate_courses", arg)

    def test_missing_courses_list(self):
        """
        Test that a missing list of courses in json will result in the appropriate command error
        """
        with self.assertRaisesRegex(CommandError, "JSON object is missing courses list"):  # noqa: PT027
            settings = {}
            arg = json.dumps(settings)
            call_command("generate_courses", arg)

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    @ddt.data("organization", "number", "run", "fields")
    def test_missing_course_settings(self, setting, mock_logger):
        """
        Test that missing required settings in JSON object will result in the appropriate error message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course"}
        }]}
        del settings["courses"][0][setting]
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call("Course json is missing " + setting)

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_invalid_user(self, mock_logger):
        """
        Test that providing an invalid user in the course JSON will result in the appropriate error message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": "invalid_user",
            "fields": {"display_name": "test-course"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call("invalid_user user does not exist")

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_missing_display_name(self, mock_logger):
        """
        Test that missing required display_name in JSON object will result in the appropriate error message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.warning.assert_any_call("Fields json is missing display_name")

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_invalid_course_field(self, mock_logger):
        """
        Test that an invalid course field will result in the appropriate message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course", "invalid_field": "invalid_value"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.info.assert_any_call(('invalid_field') + "is not a valid CourseField")

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_invalid_date_setting(self, mock_logger):
        """
        Test that an invalid date json will result in the appropriate message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course", "announcement": "invalid_date"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.info.assert_any_call("The date string could not be parsed for announcement")

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    def test_invalid_course_tab_list_setting(self, mock_logger):
        """
        Test that an invalid course tab list json will result in the appropriate message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course", "tabs": "invalid_tabs"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.info.assert_any_call("The course tab list string could not be parsed for tabs")

    @mock.patch('cms.djangoapps.contentstore.management.commands.generate_courses.logger')
    @ddt.data("mobile_available", "enable_proctored_exams")
    def test_missing_course_fields(self, field, mock_logger):
        """
        Test that missing course fields in fields json will result in the appropriate message
        """
        settings = {"courses": [{
            "organization": "test-course-generator",
            "number": "1",
            "run": "1",
            "user": str(self.user.email),
            "fields": {"display_name": "test-course"}
        }]}
        arg = json.dumps(settings)
        call_command("generate_courses", arg)
        mock_logger.info.assert_any_call(field + " has not been set")
