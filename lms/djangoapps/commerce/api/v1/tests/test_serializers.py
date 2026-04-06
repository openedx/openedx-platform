""" Commerce API v1 serializer tests. """


from django.test import TestCase

from ..serializers import serializers, validate_course_id


class CourseValidatorTests(TestCase):
    """ Tests for Course Validator method. """

    def test_validate_course_id_with_non_existent_course(self):
        """ Verify a validator checking non-existent courses."""
        course_key = 'non/existing/keyone'

        error_msg = f"Course {course_key} does not exist."
        with self.assertRaisesRegex(serializers.ValidationError, error_msg):  # noqa: PT027
            validate_course_id(course_key)

    def test_validate_course_id_with_invalid_key(self):
        """ Verify a validator checking invalid course keys."""
        course_key = 'invalidkey'

        error_msg = f"{course_key} is not a valid course key."
        with self.assertRaisesRegex(serializers.ValidationError, error_msg):  # noqa: PT027
            validate_course_id(course_key)
