"""
Tests for the LTI outcome service handlers, both in outcomes.py and in tasks.py
"""


from unittest.mock import ANY, MagicMock, patch

from django.test import TestCase
from lxml import etree
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

import lms.djangoapps.lti_provider.outcomes as outcomes
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.lti_provider.models import GradedAssignment, LtiConsumer, OutcomeService
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class StoreOutcomeParametersTest(TestCase):
    """
    Tests for the store_outcome_parameters method in outcomes.py
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.course_key = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )
        self.usage_key = BlockUsageLocator(
            course_key=self.course_key,
            block_type='problem',
            block_id='block_id'
        )
        self.consumer = LtiConsumer(
            consumer_name='consumer',
            consumer_key='consumer_key',
            consumer_secret='secret'
        )
        self.consumer.save()

    def get_valid_request_params(self):
        """
        Returns a dictionary containing a complete set of required LTI
        parameters.
        """
        return {
            'lis_result_sourcedid': 'sourcedid',
            'lis_outcome_service_url': 'http://example.com/service_url',
            'oauth_consumer_key': 'consumer_key',
            'tool_consumer_instance_guid': 'tool_instance_guid',
            'usage_key': self.usage_key,
            'course_key': self.course_key,
        }

    def test_graded_assignment_created(self):
        params = self.get_valid_request_params()
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        assignment = GradedAssignment.objects.get(
            lis_result_sourcedid=params['lis_result_sourcedid']
        )
        assert assignment.course_key == self.course_key
        assert assignment.usage_key == self.usage_key
        assert assignment.user == self.user

    def test_outcome_service_created(self):
        params = self.get_valid_request_params()
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        outcome = OutcomeService.objects.get(
            lti_consumer=self.consumer
        )
        assert outcome.lti_consumer == self.consumer

    def test_graded_assignment_references_outcome_service(self):
        params = self.get_valid_request_params()
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        outcome = OutcomeService.objects.get(
            lti_consumer=self.consumer
        )
        assignment = GradedAssignment.objects.get(
            lis_result_sourcedid=params['lis_result_sourcedid']
        )
        assert assignment.outcome_service == outcome

    def test_no_duplicate_graded_assignments(self):
        params = self.get_valid_request_params()
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        with self.assertNumQueries(2):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        assignments = GradedAssignment.objects.filter(
            lis_result_sourcedid=params['lis_result_sourcedid']
        )
        assert len(assignments) == 1

    def test_no_duplicate_outcome_services(self):
        params = self.get_valid_request_params()
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        with self.assertNumQueries(2):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        outcome_services = OutcomeService.objects.filter(
            lti_consumer=self.consumer
        )
        assert len(outcome_services) == 1

    def test_no_db_update_for_ungraded_assignment(self):
        params = self.get_valid_request_params()
        del params['lis_result_sourcedid']
        with self.assertNumQueries(0):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)

    def test_no_db_update_for_bad_request(self):
        params = self.get_valid_request_params()
        del params['lis_outcome_service_url']
        with self.assertNumQueries(0):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)

    def test_db_record_created_without_consumer_id(self):
        params = self.get_valid_request_params()
        del params['tool_consumer_instance_guid']
        with self.assertNumQueries(8):
            outcomes.store_outcome_parameters(params, self.user, self.consumer)
        assert GradedAssignment.objects.count() == 1
        assert OutcomeService.objects.count() == 1


class SignAndSendReplaceResultTest(TestCase):
    """
    Tests for the sign_and_send_replace_result method in outcomes.py
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator(
            org='some_org',
            course='some_course',
            run='some_run'
        )
        self.usage_key = BlockUsageLocator(
            course_key=self.course_key,
            block_type='problem',
            block_id='block_id'
        )
        self.user = UserFactory.create()
        consumer = LtiConsumer(
            consumer_name='consumer',
            consumer_key='consumer_key',
            consumer_secret='secret'
        )
        consumer.save()
        outcome = OutcomeService(
            lis_outcome_service_url='http://example.com/service_url',
            lti_consumer=consumer,
        )
        outcome.save()
        self.assignment = GradedAssignment(
            user=self.user,
            course_key=self.course_key,
            usage_key=self.usage_key,
            outcome_service=outcome,
            lis_result_sourcedid='sourcedid',
        )
        self.assignment.save()

    @patch('requests.post', return_value='response')
    def test_sign_and_send_replace_result(self, post_mock):
        response = outcomes.sign_and_send_replace_result(self.assignment, 'xml')
        post_mock.assert_called_with(
            'http://example.com/service_url',
            data='xml',
            auth=ANY,
            headers={'content-type': 'application/xml'}
        )
        assert response == 'response'


class XmlHandlingTest(TestCase):
    """
    Tests for the generate_replace_result_xml and check_replace_result_response
    methods in outcomes.py
    """

    response_xml = """
        <imsx_POXEnvelopeResponse xmlns = "http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">
          <imsx_POXHeader>
            <imsx_POXResponseHeaderInfo>
              <imsx_version>V1.0</imsx_version>
              <imsx_messageIdentifier>4560</imsx_messageIdentifier>
              <imsx_statusInfo>
                {major_code}
                <imsx_severity>status</imsx_severity>
                <imsx_description>Score for result_id is now 0.25</imsx_description>
                <imsx_messageRefIdentifier>999999123</imsx_messageRefIdentifier>
                <imsx_operationRefIdentifier>replaceResult</imsx_operationRefIdentifier>
              </imsx_statusInfo>
            </imsx_POXResponseHeaderInfo>
          </imsx_POXHeader>
          <imsx_POXBody>
            <replaceResultResponse/>
          </imsx_POXBody>
        </imsx_POXEnvelopeResponse>
    """

    result_id = 'result_id'
    score = 0.25

    @patch('uuid.uuid4', return_value='random_uuid')
    def test_replace_result_message_uuid(self, _uuid_mock):
        # Pylint doesn't recognize members in the LXML module
        xml = outcomes.generate_replace_result_xml(self.result_id, self.score)
        tree = etree.fromstring(xml)
        message_id = tree.xpath(
            '//ns:imsx_messageIdentifier',
            namespaces={'ns': 'http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0'}
        )
        assert len(message_id) == 1
        assert message_id[0].text == 'random_uuid'

    def test_replace_result_sourced_id(self):
        xml = outcomes.generate_replace_result_xml(self.result_id, self.score)
        tree = etree.fromstring(xml)
        sourced_id = tree.xpath(
            '/ns:imsx_POXEnvelopeRequest/ns:imsx_POXBody/ns:replaceResultRequest/'
            'ns:resultRecord/ns:sourcedGUID/ns:sourcedId',
            namespaces={'ns': 'http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0'}
        )
        assert len(sourced_id) == 1
        assert sourced_id[0].text == 'result_id'

    def test_replace_result_score(self):
        xml = outcomes.generate_replace_result_xml(self.result_id, self.score)
        tree = etree.fromstring(xml)
        xml_score = tree.xpath(
            '/ns:imsx_POXEnvelopeRequest/ns:imsx_POXBody/ns:replaceResultRequest/'
            'ns:resultRecord/ns:result/ns:resultScore/ns:textString',
            namespaces={'ns': 'http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0'}
        )
        assert len(xml_score) == 1
        assert xml_score[0].text == '0.25'

    def create_response_object(
            self, status, xml,
            major_code='<imsx_codeMajor>success</imsx_codeMajor>'
    ):
        """
        Returns an XML document containing a successful replace_result response.
        """
        response = MagicMock()
        response.status_code = status
        response.content = xml.format(major_code=major_code).encode('ascii', 'ignore')
        return response

    def test_response_with_correct_xml(self):
        xml = self.response_xml
        response = self.create_response_object(200, xml)
        assert outcomes.check_replace_result_response(response)

    def test_response_with_bad_status_code(self):
        response = self.create_response_object(500, '')
        assert not outcomes.check_replace_result_response(response)

    def test_response_with_invalid_xml(self):
        xml = '<badly>formatted</xml>'
        response = self.create_response_object(200, xml)
        assert not outcomes.check_replace_result_response(response)

    def test_response_with_multiple_status_fields(self):
        response = self.create_response_object(
            200, self.response_xml,
            major_code='<imsx_codeMajor>success</imsx_codeMajor>'
                       '<imsx_codeMajor>failure</imsx_codeMajor>'
        )
        assert not outcomes.check_replace_result_response(response)

    def test_response_with_no_status_field(self):
        response = self.create_response_object(
            200, self.response_xml,
            major_code=''
        )
        assert not outcomes.check_replace_result_response(response)

    def test_response_with_failing_status_field(self):
        response = self.create_response_object(
            200, self.response_xml,
            major_code='<imsx_codeMajor>failure</imsx_codeMajor>'
        )
        assert not outcomes.check_replace_result_response(response)


class TestAssignmentsForProblem(ModuleStoreTestCase):
    """
    Test cases for the assignments_for_problem method in outcomes.py
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.user_id = self.user.id
        self.outcome_service = self.create_outcome_service('outcomes')
        self.course = CourseFactory.create()
        with self.store.bulk_operations(self.course.id, emit_signals=False):
            self.chapter = BlockFactory.create(parent=self.course, category="chapter")
            self.vertical = BlockFactory.create(parent=self.chapter, category="vertical")
            self.unit = BlockFactory.create(parent=self.vertical, category="unit")

    def create_outcome_service(self, id_suffix):
        """
        Create and save a new OutcomeService model in the test database. The
        OutcomeService model requires an LtiConsumer model, so we create one of
        those as well. The method takes an ID string that is used to ensure that
        unique fields do not conflict.
        """
        lti_consumer = LtiConsumer(
            consumer_name='lti_consumer_name' + id_suffix,
            consumer_key='lti_consumer_key' + id_suffix,
            consumer_secret='lti_consumer_secret' + id_suffix,
            instance_guid='lti_instance_guid' + id_suffix
        )
        lti_consumer.save()
        outcome_service = OutcomeService(
            lis_outcome_service_url='https://example.com/outcomes/' + id_suffix,
            lti_consumer=lti_consumer
        )
        outcome_service.save()
        return outcome_service

    def create_graded_assignment(self, desc, result_id, outcome_service):
        """
        Create and save a new GradedAssignment model in the test database.
        """
        assignment = GradedAssignment(
            user=self.user,
            course_key=self.course.id,
            usage_key=desc.location,
            outcome_service=outcome_service,
            lis_result_sourcedid=result_id,
            version_number=0
        )
        assignment.save()
        return assignment

    def test_create_two_lti_consumers_with_empty_instance_guid(self):
        """
        Test ability to create two or more LTI consumers through the Django admin
        with empty instance_guid field.
        A blank guid field is required when a customer enables a new secret/key combination for
        LTI integration with their LMS.
        """
        lti_consumer_first = LtiConsumer(
            consumer_name='lti_consumer_name_second',
            consumer_key='lti_consumer_key_second',
            consumer_secret='lti_consumer_secret_second',
            instance_guid=''
        )
        lti_consumer_first.save()
        lti_consumer_second = LtiConsumer(
            consumer_name='lti_consumer_name_third',
            consumer_key='lti_consumer_key_third',
            consumer_secret='lti_consumer_secret_third',
            instance_guid=''
        )
        lti_consumer_second.save()
        count = LtiConsumer.objects.count()
        assert count == 3

    def test_with_no_graded_assignments(self):
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 0

    def test_with_graded_unit(self):
        self.create_graded_assignment(self.unit, 'graded_unit', self.outcome_service)
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 1
        assert assignments[0].lis_result_sourcedid == 'graded_unit'

    def test_with_graded_vertical(self):
        self.create_graded_assignment(self.vertical, 'graded_vertical', self.outcome_service)
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 0

    def test_with_graded_unit_and_vertical(self):
        self.create_graded_assignment(self.unit, 'graded_unit', self.outcome_service)
        self.create_graded_assignment(self.vertical, 'graded_vertical', self.outcome_service)
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 1
        assert assignments[0].lis_result_sourcedid == 'graded_unit'

    def test_with_unit_used_twice(self):
        self.create_graded_assignment(self.unit, 'graded_unit', self.outcome_service)
        self.create_graded_assignment(self.unit, 'graded_unit2', self.outcome_service)
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 2
        assert assignments[0].lis_result_sourcedid == 'graded_unit'
        assert assignments[1].lis_result_sourcedid == 'graded_unit2'

    def test_with_unit_graded_for_different_user(self):
        self.create_graded_assignment(self.unit, 'graded_unit', self.outcome_service)
        other_user = UserFactory.create()
        assignments = outcomes.get_assignments_for_problem(
            self.unit, other_user.id, self.course.id
        )
        assert len(assignments) == 0

    def test_with_unit_graded_for_multiple_consumers(self):
        other_outcome_service = self.create_outcome_service('second_consumer')
        self.create_graded_assignment(self.unit, 'graded_unit', self.outcome_service)
        self.create_graded_assignment(self.unit, 'graded_unit2', other_outcome_service)
        assignments = outcomes.get_assignments_for_problem(
            self.unit, self.user_id, self.course.id
        )
        assert len(assignments) == 2
        assert assignments[0].lis_result_sourcedid == 'graded_unit'
        assert assignments[1].lis_result_sourcedid == 'graded_unit2'
        assert assignments[0].outcome_service == self.outcome_service
        assert assignments[1].outcome_service == other_outcome_service
