"""
Tests for the Agreements API
"""
import logging
from datetime import datetime, timedelta

from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from testfixtures import LogCapture

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from ..api import (
    create_integrity_signature,
    create_lti_pii_signature,
    create_user_agreement_record,
    get_integrity_signature,
    get_integrity_signatures_for_course,
    get_lti_pii_signature,
    get_pii_receiving_lti_tools,
    get_latest_user_agreement_record,
    get_user_agreement_records
)
from ..models import LTIPIITool

LOGGER_NAME = "openedx.core.djangoapps.agreements.api"


@skip_unless_lms
class TestIntegritySignatureApi(SharedModuleStoreTestCase):
    """
    Tests for the integrity signature API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()
        cls.course = CourseFactory()
        cls.course_id = str(cls.course.id)

    def test_create_integrity_signature(self):
        """
        Test to create an integrity signature
        """
        signature = create_integrity_signature(self.user.username, self.course_id)
        self._assert_integrity_signature(signature)

    def test_create_duplicate_integrity_signature(self):
        """
        Test that duplicate integrity signatures cannot be created
        """
        with LogCapture(LOGGER_NAME, level=logging.WARNING) as logger:
            create_integrity_signature(self.user.username, self.course_id)
            create_integrity_signature(self.user.username, self.course_id)
            signature = get_integrity_signature(self.user.username, self.course_id)
            self._assert_integrity_signature(signature)
            logger.check((
                LOGGER_NAME,
                'WARNING',
                (
                    'Integrity signature already exists for user_id={user_id} and '
                    'course_id={course_id}'.format(
                        user_id=self.user.id, course_id=str(self.course_id)
                    )
                )
            ))

    def test_get_integrity_signature(self):
        """
        Test to get an integrity signature
        """
        create_integrity_signature(self.user.username, self.course_id)
        signature = get_integrity_signature(self.user.username, self.course_id)
        self._assert_integrity_signature(signature)

    def test_get_nonexistent_integrity_signature(self):
        """
        Test that None is returned if an integrity signature does not exist
        """
        signature = get_integrity_signature(self.user.username, self.course_id)
        self.assertIsNone(signature)

    def test_get_integrity_signatures_for_course(self):
        """
        Test to get all integrity signatures for a course
        """
        create_integrity_signature(self.user.username, self.course_id)
        second_user = UserFactory()
        create_integrity_signature(second_user.username, self.course_id)
        signatures = get_integrity_signatures_for_course(self.course_id)
        self._assert_integrity_signature(signatures[0])
        self.assertEqual(signatures[1].user, second_user)
        self.assertEqual(signatures[1].course_key, self.course.id)

    def test_get_integrity_signatures_for_course_empty(self):
        """
        Test that a course with no integrity signatures returns an empty queryset
        """
        signatures = get_integrity_signatures_for_course(self.course_id)
        self.assertEqual(len(signatures), 0)

    def _assert_integrity_signature(self, signature):
        """
        Helper function to assert the returned integrity signature has the correct
        user and course key
        """
        self.assertEqual(signature.user, self.user)
        self.assertEqual(signature.course_key, self.course.id)


@skip_unless_lms
class TestLTIPIISignatureApi(SharedModuleStoreTestCase):
    """
    Tests for the lti pii signature API
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = UserFactory()
        cls.course = CourseFactory()
        cls.course_id = str(cls.course.id)
        cls.lti_tools = {"first_lti_tool": "This is the first tool",
                         "second_lti_tool": "This is the second tool", }
        cls.lti_tools_2 = {"first_lti_tool": "This is the first lti tool",
                           "second_lti_tool": "This is the second tool",
                           "third_lti_tool": "This is the third tool", }
        LTIPIITool.objects.create(
            course_key=CourseKey.from_string(cls.course_id),
            lti_tools=cls.lti_tools,
            lti_tools_hash=11111,
        )

    def test_create_lti_pii_signature(self):
        """
        Test to check if an lti pii signature is created from a course and its lti tools.
        """
        signature = create_lti_pii_signature(self.user.username, self.course_id, self.lti_tools)
        self._assert_lti_pii_signature(signature)

    def test_create_multiple_lti_pii_signature(self):
        """
        Test that lti pii signatures are either created or updated
        """

        create_lti_pii_signature(self.user.username, self.course_id, self.lti_tools)  # first signature
        s1 = get_lti_pii_signature(self.user.username, self.course_id)  # retrieve the database entry
        create_lti_pii_signature(self.user.username, self.course_id, self.lti_tools_2)  # signature with updated tools
        s2 = get_lti_pii_signature(self.user.username, self.course_id)  # retrieve the updated database entry
        self.assertNotEqual(s1, s2)  # the signatue retrieved from the database should be the updated version

    def _assert_lti_pii_signature(self, signature):
        """
        Helper function to assert the returned lti pii signature has the correct
        user and course key
        """
        self.assertEqual(signature.user, self.user)
        self.assertEqual(signature.course_key, self.course.id)


@skip_unless_lms
class TestLTIPIIToolsApi(SharedModuleStoreTestCase):
    """
    Tests for the lti pii tool sharing API. To make sure the list of LTI tools can be retreived from the Model.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory()
        cls.course_id = str(cls.course.id)
        cls.lti_tools = {"first_lti_tool": "This is the first tool",
                         "second_lti_tool": "This is the second tool", }
        LTIPIITool.objects.create(
            course_key=CourseKey.from_string(cls.course_id),
            lti_tools=cls.lti_tools,
            lti_tools_hash=11111,
        )

    def test_get_pii_receiving_lti_tools(self):
        """
        Test to check if a course's lti pii tools can be retrieved.
        """
        data = get_pii_receiving_lti_tools(self.course_id)
        self._assert_ltitools(data.lii_tools_receiving_pii)

    def _assert_ltitools(self, lti_list):
        """
        Helper function to assert the returned list has the correct tools
        """
        self.assertEqual(self.lti_tools, lti_list)


@skip_unless_lms
class UserAgreementsTests(TestCase):
    """
    Tests for the python APIs related to user agreements.
    """
    def setUp(self):
        self.user = UserFactory()

    def test_get_user_agreements(self, ):
        result = list(get_user_agreement_records(self.user))
        assert len(result) == 0

        record = create_user_agreement_record(self.user, 'test_type')
        result = list(get_user_agreement_records(self.user))

        assert len(result) == 1
        assert result[0].agreement_type == 'test_type'
        assert result[0].username == self.user.username
        assert result[0].accepted_at == record.accepted_at

    def test_get_user_agreement_record(self):
        record = create_user_agreement_record(self.user, 'test_type')
        result = get_latest_user_agreement_record(self.user, 'test_type')

        assert result == record

        result = get_latest_user_agreement_record(self.user, 'test_type', datetime.now() + timedelta(days=1))

        assert result is None

    def tearDown(self):
        self.user.delete()
