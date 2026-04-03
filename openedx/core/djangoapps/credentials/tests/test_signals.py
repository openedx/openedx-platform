"""Tests covering Credentials signals."""


from unittest import mock

from django.conf import settings  # lint-amnesty, pylint: disable=unused-import
from django.test import TestCase, override_settings  # lint-amnesty, pylint: disable=unused-import

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
from lms.djangoapps.grades.course_grade_factory import CourseGradeFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (
    CourseFactory as XModuleCourseFactory,  # lint-amnesty, pylint: disable=wrong-import-order
)

SIGNALS_MODULE = 'openedx.core.djangoapps.credentials.signals'


@skip_unless_lms
@mock.patch(SIGNALS_MODULE + '.send_grade_if_interesting')
class TestCredentialsSignalsEmissions(ModuleStoreTestCase):
    """ Tests for whether we are receiving signal emissions correctly. """

    def test_cert_changed(self, mock_send_grade_if_interesting):
        user = UserFactory()

        assert not mock_send_grade_if_interesting.called
        GeneratedCertificateFactory(user=user)
        assert mock_send_grade_if_interesting.called

    def test_grade_changed(self, mock_send_grade_if_interesting):
        user = UserFactory()
        course = XModuleCourseFactory()

        assert not mock_send_grade_if_interesting.called
        CourseGradeFactory().update(user, course=course)
        assert mock_send_grade_if_interesting.called
