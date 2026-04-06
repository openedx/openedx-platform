"""
Tests for course certificate tasks.
"""


from textwrap import dedent
from unittest import mock
from unittest.mock import patch

import ddt
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.tasks import generate_certificate, get_changed_cert_templates
from lms.djangoapps.certificates.tests.factories import CertificateTemplateFactory


@ddt.ddt
class GenerateUserCertificateTest(TestCase):
    """
    Tests for course certificate tasks
    """

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.course_key = "course-v1:edX+DemoX+Demo_Course"

    @ddt.data("student", "course_key", "enrollment_mode")
    def test_missing_args(self, missing_arg):
        kwargs = {
            "student": self.user.id,
            "course_key": self.course_key,
            "other_arg": "shiny",
            "enrollment_mode": CourseMode.MASTERS,
        }
        del kwargs[missing_arg]

        with patch("lms.djangoapps.certificates.tasks.User.objects.get"):
            with self.assertRaisesRegex(KeyError, missing_arg):
                generate_certificate.apply_async(kwargs=kwargs).get()

    def test_generation(self):
        """
        Verify the task handles certificate generation
        """
        enrollment_mode = CourseMode.VERIFIED

        with mock.patch(
            "lms.djangoapps.certificates.tasks.generate_course_certificate",
            return_value=None,
        ) as mock_generate_cert:
            kwargs = {
                "student": self.user.id,
                "course_key": self.course_key,
                "enrollment_mode": enrollment_mode,
            }

            generate_certificate.apply_async(kwargs=kwargs)
            mock_generate_cert.assert_called_with(
                user=self.user,
                course_key=CourseKey.from_string(self.course_key),
                status=CertificateStatuses.downloadable,
                enrollment_mode=enrollment_mode,
                course_grade="",
                generation_mode="batch",
            )

    def test_generation_custom(self):
        """
        Verify the task handles certificate generation custom params
        """
        gen_mode = "self"
        status = CertificateStatuses.notpassing
        enrollment_mode = CourseMode.AUDIT
        course_grade = "0.89"

        with mock.patch(
            "lms.djangoapps.certificates.tasks.generate_course_certificate",
            return_value=None,
        ) as mock_generate_cert:
            kwargs = {
                "status": status,
                "student": self.user.id,
                "course_key": self.course_key,
                "course_grade": course_grade,
                "enrollment_mode": enrollment_mode,
                "generation_mode": gen_mode,
                "what_about": "dinosaurs",
            }

            generate_certificate.apply_async(kwargs=kwargs)
            mock_generate_cert.assert_called_with(
                user=self.user,
                course_key=CourseKey.from_string(self.course_key),
                status=status,
                enrollment_mode=enrollment_mode,
                course_grade=course_grade,
                generation_mode=gen_mode,
            )


class ModifyCertTemplateTests(TestCase):
    """Tests for get_changed_cert_templates"""

    def test_command_changes_called_templates(self):
        """Verify command changes for all and only those templates for which it is called."""
        template1 = CertificateTemplateFactory.create(
            template="fiddledee-doo fiddledee-dah"
        )
        template2 = CertificateTemplateFactory.create(
            template="violadee-doo violadee-dah"
        )
        template3 = CertificateTemplateFactory.create(
            template="fiddledee-doo fiddledee-dah"
        )
        template1.save()
        template2.save()
        template3.save()
        expected1 = "fiddleeep-doo fiddledee-dah"
        expected2 = "violaeep-doo violadee-dah"
        options = {
            "old_text": "dee",
            "new_text": "eep",
            "templates": [1, 2],
        }
        new_templates = get_changed_cert_templates(options)
        assert len(new_templates) == 2
        assert new_templates[0].template == expected1
        assert new_templates[1].template == expected2

    def test_dry_run(self):
        """Verify command doesn't change anything on dry-run."""
        template1 = CertificateTemplateFactory.create(
            template="fiddledee-doo fiddledee-dah"
        )
        template2 = CertificateTemplateFactory.create(
            template="violadee-doo violadee-dah"
        )
        template1.save()
        template2.save()
        options = {
            "old_text": "dee",
            "new_text": "eep",
            "templates": [1, 2],
            "dry_run": True,
        }
        new_templates = get_changed_cert_templates(options)
        assert not new_templates

    def test_multiline_change(self):
        """Verify template change works with a multiline change string."""
        template1 = CertificateTemplateFactory.create(
            template="fiddledee-doo fiddledee-dah"
        )
        template1.save()
        new_text = """
        there's something happening here
        what it is ain't exactly clear
        """
        expected = f"fiddle{dedent(new_text)}-doo fiddledee-dah"
        options = {
            "old_text": "dee",
            "new_text": dedent(new_text),
            "templates": [1],
        }
        new_templates = get_changed_cert_templates(options)
        assert len(new_templates) == 1
        assert new_templates[0].template == expected
