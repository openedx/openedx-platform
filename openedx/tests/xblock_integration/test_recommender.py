"""
This test file will run through some XBlock test scenarios regarding the
recommender system
"""


import codecs
import itertools
import unittest
from copy import deepcopy
from io import BytesIO

import simplejson as json
from ddt import data, ddt
from django.conf import settings
from django.urls import reverse

from common.djangoapps.student.tests.factories import GlobalStaffFactory
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase
from openedx.core.lib.url_utils import quote_slashes
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class TestRecommender(SharedModuleStoreTestCase, LoginEnrollmentTestCase):
    """
    Check that Recommender state is saved properly
    """
    STUDENTS = [
        {'email': 'view@test.com', 'password': 'Password1234'},
        {'email': 'view2@test.com', 'password': 'Password1234'}
    ]
    XBLOCK_NAMES = ['recommender', 'recommender_second']

    @classmethod
    def setUpClass(cls):
        # Nose runs setUpClass methods even if a class decorator says to skip
        # the class: https://github.com/nose-devs/nose/issues/946
        # So, skip the test class here if we are not in the LMS.
        if settings.ROOT_URLCONF != 'lms.urls':
            raise unittest.SkipTest('Test only valid in lms')

        super().setUpClass()
        cls.course = CourseFactory.create(
            display_name='Recommender_Test_Course'
        )
        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            cls.chapter = BlockFactory.create(
                parent=cls.course, display_name='Overview'
            )
            cls.section = BlockFactory.create(
                parent=cls.chapter, display_name='Welcome'
            )
            cls.unit = BlockFactory.create(
                parent=cls.section, display_name='New Unit'
            )
            cls.xblock = BlockFactory.create(
                parent=cls.unit,
                category='recommender',
                display_name='recommender'
            )
            cls.xblock2 = BlockFactory.create(
                parent=cls.unit,
                category='recommender',
                display_name='recommender_second'
            )

        cls.course_url = reverse('render_xblock', args=[str(cls.section.location)])

        cls.resource_urls = [
            (
                "https://courses.edx.org/courses/MITx/3.091X/"
                "2013_Fall/courseware/SP13_Week_4/"
                "SP13_Periodic_Trends_and_Bonding/"
            ),
            (
                "https://courses.edx.org/courses/MITx/3.091X/"
                "2013_Fall/courseware/SP13_Week_4/SP13_Covalent_Bonding/"
            )
        ]

        cls.test_recommendations = {
            cls.resource_urls[0]: {
                "title": "Covalent bonding and periodic trends",
                "url": cls.resource_urls[0],
                "description": (
                    "http://people.csail.mit.edu/swli/edx/"
                    "recommendation/img/videopage1.png"
                ),
                "descriptionText": (
                    "short description for Covalent bonding "
                    "and periodic trends"
                )
            },
            cls.resource_urls[1]: {
                "title": "Polar covalent bonds and electronegativity",
                "url": cls.resource_urls[1],
                "description": (
                    "http://people.csail.mit.edu/swli/edx/"
                    "recommendation/img/videopage2.png"
                ),
                "descriptionText": (
                    "short description for Polar covalent "
                    "bonds and electronegativity"
                )
            }
        }

    def setUp(self):
        super().setUp()
        for idx, student in enumerate(self.STUDENTS):
            username = f"u{idx}"
            self.create_account(username, student['email'], student['password'])
            self.activate_user(student['email'])
            self.logout()

        self.staff_user = GlobalStaffFactory()

    def get_handler_url(self, handler, xblock_name=None):
        """
        Get url for the specified xblock handler
        """
        if xblock_name is None:
            xblock_name = TestRecommender.XBLOCK_NAMES[0]
        return reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': quote_slashes(str(self.course.id.make_usage_key('recommender', xblock_name))),
            'handler': handler,
            'suffix': ''
        })

    def enroll_student(self, email, password):
        """
        Student login and enroll for the course
        """
        self.login(email, password)
        self.enroll(self.course, verify=True)

    def enroll_staff(self, staff):
        """
        Staff login and enroll for the course
        """
        email = staff.email
        password = self.TEST_PASSWORD
        self.login(email, password)
        self.enroll(self.course, verify=True)

    def initialize_database_by_id(self, handler, resource_id, times, xblock_name=None):
        """
        Call a ajax event (vote, delete, endorse) on a resource by its id
        several times
        """
        if xblock_name is None:
            xblock_name = TestRecommender.XBLOCK_NAMES[0]
        url = self.get_handler_url(handler, xblock_name)
        for _ in range(times):
            self.client.post(url, json.dumps({'id': resource_id}), '')

    def call_event(self, handler, resource, xblock_name=None):
        """
        Call a ajax event (add, edit, flag, etc.) by specifying the resource
        it takes
        """
        if xblock_name is None:
            xblock_name = TestRecommender.XBLOCK_NAMES[0]
        url = self.get_handler_url(handler, xblock_name)
        return self.client.post(url, json.dumps(resource), '')

    def check_event_response_by_key(self, handler, resource, resp_key, resp_val, xblock_name=None):
        """
        Call the event specified by the handler with the resource, and check
        whether the key (resp_key) in response is as expected (resp_val)
        """
        if xblock_name is None:
            xblock_name = TestRecommender.XBLOCK_NAMES[0]
        resp = json.loads(self.call_event(handler, resource, xblock_name).content)
        assert resp[resp_key] == resp_val
        self.assert_request_status_code(200, self.course_url)

    def check_event_response_by_http_status(self, handler, resource, http_status_code, xblock_name=None):
        """
        Call the event specified by the handler with the resource, and check
        whether the http_status in response is as expected
        """
        if xblock_name is None:
            xblock_name = TestRecommender.XBLOCK_NAMES[0]
        resp = self.call_event(handler, resource, xblock_name)
        assert resp.status_code == http_status_code
        self.assert_request_status_code(200, self.course_url)


class TestRecommenderCreateFromEmpty(TestRecommender):
    """
    Check whether we can add resources to an empty database correctly
    """
    def test_add_resource(self):
        """
        Verify the addition of new resource is handled correctly
        """
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        # Check whether adding new resource is successful
        for resource_id, resource in self.test_recommendations.items():
            for xblock_name in self.XBLOCK_NAMES:
                result = self.call_event('add_resource', resource, xblock_name)

                expected_result = {
                    'upvotes': 0,
                    'downvotes': 0,
                    'id': resource_id
                }
                for field in resource:
                    expected_result[field] = resource[field]

                self.assertDictEqual(json.loads(result.content), expected_result)
                self.assert_request_status_code(200, self.course_url)


class TestRecommenderResourceBase(TestRecommender):
    """Base helper class for tests with resources."""
    def setUp(self):
        super().setUp()
        self.resource_id = self.resource_urls[0]
        self.resource_id_second = self.resource_urls[1]
        self.non_existing_resource_id = 'An non-existing id'
        self.set_up_resources()

    def set_up_resources(self):
        """
        Set up resources and enroll staff
        """
        self.logout()
        self.enroll_staff(self.staff_user)
        # Add resources, assume correct here, tested in test_add_resource
        for resource, xblock_name in itertools.product(list(self.test_recommendations.values()), self.XBLOCK_NAMES):
            self.call_event('add_resource', resource, xblock_name)

    def generate_edit_resource(self, resource_id):
        """
        Based on the given resource (specified by resource_id), this function
        generate a new one for testing 'edit_resource' event
        """
        resource = {"id": resource_id}
        edited_recommendations = {
            key: value + "edited" for key, value in self.test_recommendations[self.resource_id].items()
        }
        resource.update(edited_recommendations)
        return resource


class TestRecommenderWithResources(TestRecommenderResourceBase):
    """
    Check whether we can add/edit/flag/export resources correctly
    """
    def test_add_redundant_resource(self):
        """
        Verify the addition of a redundant resource (url) is rejected
        """
        for suffix in ['', '#IAmSuffix', '%23IAmSuffix']:
            resource = deepcopy(self.test_recommendations[self.resource_id])
            resource['url'] += suffix
            self.check_event_response_by_http_status('add_resource', resource, 409)

    def test_add_removed_resource(self):
        """
        Verify the addition of a removed resource (url) is rejected
        """
        self.call_event('remove_resource', {"id": self.resource_id, 'reason': ''})
        for suffix in ['', '#IAmSuffix', '%23IAmSuffix']:
            resource = deepcopy(self.test_recommendations[self.resource_id])
            resource['url'] += suffix
            self.check_event_response_by_http_status('add_resource', resource, 405)

    def test_edit_resource_non_existing(self):
        """
        Edit a non-existing resource
        """
        self.check_event_response_by_http_status(
            'edit_resource',
            self.generate_edit_resource(self.non_existing_resource_id),
            400
        )

    def test_edit_redundant_resource(self):
        """
        Check whether changing the url to the one of 'another' resource is
        rejected
        """
        for suffix in ['', '#IAmSuffix', '%23IAmSuffix']:
            resource = self.generate_edit_resource(self.resource_id)
            resource['url'] = self.resource_id_second + suffix
            self.check_event_response_by_http_status('edit_resource', resource, 409)

    def test_edit_removed_resource(self):
        """
        Check whether changing the url to the one of a removed resource is
        rejected
        """
        self.call_event('remove_resource', {"id": self.resource_id_second, 'reason': ''})
        for suffix in ['', '#IAmSuffix', '%23IAmSuffix']:
            resource = self.generate_edit_resource(self.resource_id)
            resource['url'] = self.resource_id_second + suffix
            self.check_event_response_by_http_status('edit_resource', resource, 405)

    def test_edit_resource(self):
        """
        Check whether changing the content of resource is successful
        """
        self.check_event_response_by_http_status(
            'edit_resource',
            self.generate_edit_resource(self.resource_id),
            200
        )

    def test_edit_resource_same_url(self):
        """
        Check whether changing the content (except for url) of resource is successful
        """
        resource = self.generate_edit_resource(self.resource_id)
        for suffix in ['', '#IAmSuffix', '%23IAmSuffix']:
            resource['url'] = self.resource_id + suffix
            self.check_event_response_by_http_status('edit_resource', resource, 200)

    def test_edit_then_add_resource(self):
        """
        Check whether we can add back an edited resource
        """
        self.call_event('edit_resource', self.generate_edit_resource(self.resource_id))
        # Test
        self.check_event_response_by_key(
            'add_resource',
            self.test_recommendations[self.resource_id],
            'id',
            self.resource_id
        )

    def test_edit_resources_in_different_xblocks(self):
        """
        Check whether changing the content of resource is successful in two
        different xblocks
        """
        resource = self.generate_edit_resource(self.resource_id)
        for xblock_name in self.XBLOCK_NAMES:
            self.check_event_response_by_http_status('edit_resource', resource, 200, xblock_name)

    def test_flag_resource_wo_reason(self):
        """
        Flag a resource as problematic, without providing the reason
        """
        resource = {'id': self.resource_id, 'isProblematic': True, 'reason': ''}
        # Test
        self.check_event_response_by_key('flag_resource', resource, 'reason', '')

    def test_flag_resource_w_reason(self):
        """
        Flag a resource as problematic, with providing the reason
        """
        resource = {'id': self.resource_id, 'isProblematic': True, 'reason': 'reason 0'}
        # Test
        self.check_event_response_by_key('flag_resource', resource, 'reason', 'reason 0')

    def test_flag_resource_change_reason(self):
        """
        Flag a resource as problematic twice, with different reasons
        """
        resource = {'id': self.resource_id, 'isProblematic': True, 'reason': 'reason 0'}
        self.call_event('flag_resource', resource)
        # Test
        resource['reason'] = 'reason 1'
        resp = json.loads(self.call_event('flag_resource', resource).content)
        assert resp['oldReason'] == 'reason 0'
        assert resp['reason'] == 'reason 1'
        self.assert_request_status_code(200, self.course_url)

    def test_flag_resources_in_different_xblocks(self):
        """
        Flag resources as problematic in two different xblocks
        """
        resource = {'id': self.resource_id, 'isProblematic': True, 'reason': 'reason 0'}
        # Test
        for xblock_name in self.XBLOCK_NAMES:
            self.check_event_response_by_key('flag_resource', resource, 'reason', 'reason 0', xblock_name)

    def test_flag_resources_by_different_users(self):
        """
        Different users can't see the flag result of each other
        """
        resource = {'id': self.resource_id, 'isProblematic': True, 'reason': 'reason 0'}
        self.call_event('flag_resource', resource)
        self.logout()
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        # Test
        resp = json.loads(self.call_event('flag_resource', resource).content)
        # The second user won't see the reason provided by the first user
        assert 'oldReason' not in resp
        assert resp['reason'] == 'reason 0'
        self.assert_request_status_code(200, self.course_url)

    def test_export_resources(self):
        """
        Test the function for exporting all resources from the Recommender.
        """
        self.call_event('remove_resource', {"id": self.resource_id, 'reason': ''})
        self.call_event('endorse_resource', {"id": self.resource_id_second, 'reason': ''})
        # Test
        resp = json.loads(self.call_event('export_resources', {}).content)

        assert self.resource_id_second in resp['export']['recommendations']
        assert self.resource_id not in resp['export']['recommendations']
        assert self.resource_id_second in resp['export']['endorsed_recommendation_ids']
        assert self.resource_id in resp['export']['removed_recommendations']
        self.assert_request_status_code(200, self.course_url)


@ddt
class TestRecommenderVoteWithResources(TestRecommenderResourceBase):
    """
    Check whether we can vote resources correctly
    """
    @data(
        {'event': 'recommender_upvote'},
        {'event': 'recommender_downvote'}
    )
    def test_vote_resource_non_existing(self, test_case):
        """
        Vote a non-existing resource
        """
        resource = {"id": self.non_existing_resource_id, 'event': test_case['event']}
        self.check_event_response_by_http_status('handle_vote', resource, 400)

    @data(
        {'event': 'recommender_upvote', 'new_votes': 1},
        {'event': 'recommender_downvote', 'new_votes': -1}
    )
    def test_vote_resource_once(self, test_case):
        """
        Vote a resource
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])

    @data(
        {'event': 'recommender_upvote', 'new_votes': 0},
        {'event': 'recommender_downvote', 'new_votes': 0}
    )
    def test_vote_resource_twice(self, test_case):
        """
        Vote a resource twice
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.call_event('handle_vote', resource)
        # Test
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])

    @data(
        {'event': 'recommender_upvote', 'new_votes': 1},
        {'event': 'recommender_downvote', 'new_votes': -1}
    )
    def test_vote_resource_thrice(self, test_case):
        """
        Vote a resource thrice
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        for _ in range(2):
            self.call_event('handle_vote', resource)
        # Test
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])

    @data(
        {'event': 'recommender_upvote', 'event_second': 'recommender_downvote', 'new_votes': -1},
        {'event': 'recommender_downvote', 'event_second': 'recommender_upvote', 'new_votes': 1}
    )
    def test_switch_vote_resource(self, test_case):
        """
        Switch the vote of a resource
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.call_event('handle_vote', resource)
        # Test
        resource['event'] = test_case['event_second']
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])

    @data(
        {'event': 'recommender_upvote', 'new_votes': 1},
        {'event': 'recommender_downvote', 'new_votes': -1}
    )
    def test_vote_different_resources(self, test_case):
        """
        Vote two different resources
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.call_event('handle_vote', resource)
        # Test
        resource['id'] = self.resource_id_second
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])

    @data(
        {'event': 'recommender_upvote', 'new_votes': 1},
        {'event': 'recommender_downvote', 'new_votes': -1}
    )
    def test_vote_resources_in_different_xblocks(self, test_case):
        """
        Vote two resources in two different xblocks
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.call_event('handle_vote', resource)
        # Test
        self.check_event_response_by_key(
            'handle_vote', resource, 'newVotes', test_case['new_votes'], self.XBLOCK_NAMES[1]
        )

    @data(
        {'event': 'recommender_upvote', 'new_votes': 2},
        {'event': 'recommender_downvote', 'new_votes': -2}
    )
    def test_vote_resource_by_different_users(self, test_case):
        """
        Vote resource by two different users
        """
        resource = {"id": self.resource_id, 'event': test_case['event']}
        self.call_event('handle_vote', resource)
        self.logout()
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        # Test
        self.check_event_response_by_key('handle_vote', resource, 'newVotes', test_case['new_votes'])


@ddt
class TestRecommenderStaffFeedbackWithResources(TestRecommenderResourceBase):
    """
    Check whether we can remove/endorse resources correctly
    """
    @data('remove_resource', 'endorse_resource')
    def test_remove_or_endorse_resource_non_existing(self, test_case):
        """
        Remove/endorse a non-existing resource
        """
        resource = {"id": self.non_existing_resource_id, 'reason': ''}
        self.check_event_response_by_http_status(test_case, resource, 400)

    @data(
        {'times': 1, 'key': 'status', 'val': 'endorsement'},
        {'times': 2, 'key': 'status', 'val': 'undo endorsement'},
        {'times': 3, 'key': 'status', 'val': 'endorsement'}
    )
    def test_endorse_resource_multiple_times(self, test_case):
        """
        Endorse a resource once/twice/thrice
        """
        resource = {"id": self.resource_id, 'reason': ''}
        for _ in range(test_case['times'] - 1):
            self.call_event('endorse_resource', resource)
        # Test
        self.check_event_response_by_key('endorse_resource', resource, test_case['key'], test_case['val'])

    @data(
        {'times': 1, 'status': 200},
        {'times': 2, 'status': 400},
        {'times': 3, 'status': 400}
    )
    def test_remove_resource_multiple_times(self, test_case):
        """
        Remove a resource once/twice/thrice
        """
        resource = {"id": self.resource_id, 'reason': ''}
        for _ in range(test_case['times'] - 1):
            self.call_event('remove_resource', resource)
        # Test
        self.check_event_response_by_http_status('remove_resource', resource, test_case['status'])

    @data(
        {'handler': 'remove_resource', 'status': 200},
        {'handler': 'endorse_resource', 'key': 'status', 'val': 'endorsement'}
    )
    def test_remove_or_endorse_different_resources(self, test_case):
        """
        Remove/endorse two different resources
        """
        self.call_event(test_case['handler'], {"id": self.resource_id, 'reason': ''})
        # Test
        resource = {"id": self.resource_id_second, 'reason': ''}
        if test_case['handler'] == 'remove_resource':
            self.check_event_response_by_http_status(test_case['handler'], resource, test_case['status'])
        else:
            self.check_event_response_by_key(test_case['handler'], resource, test_case['key'], test_case['val'])

    @data(
        {'handler': 'remove_resource', 'status': 200},
        {'handler': 'endorse_resource', 'key': 'status', 'val': 'endorsement'}
    )
    def test_remove_or_endorse_resources_in_different_xblocks(self, test_case):
        """
        Remove/endorse two resources in two different xblocks
        """
        self.call_event(test_case['handler'], {"id": self.resource_id, 'reason': ''})
        # Test
        resource = {"id": self.resource_id, 'reason': ''}
        if test_case['handler'] == 'remove_resource':
            self.check_event_response_by_http_status(
                test_case['handler'], resource, test_case['status'], self.XBLOCK_NAMES[1]
            )
        else:
            self.check_event_response_by_key(
                test_case['handler'], resource, test_case['key'], test_case['val'], self.XBLOCK_NAMES[1]
            )

    @data(
        {'handler': 'remove_resource', 'status': 400},
        {'handler': 'endorse_resource', 'status': 400}
    )
    def test_remove_or_endorse_resource_by_student(self, test_case):
        """
        Remove/endorse resource by a student
        """
        self.logout()
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        # Test
        resource = {"id": self.resource_id, 'reason': ''}
        self.check_event_response_by_http_status(test_case['handler'], resource, test_case['status'])


@ddt
class TestRecommenderFileUploading(TestRecommender):
    """
    Check whether we can handle file uploading correctly
    """
    def setUp(self):
        super().setUp()
        self.initial_configuration = {
            'flagged_accum_resources': {},
            'endorsed_recommendation_reasons': [],
            'endorsed_recommendation_ids': [],
            'removed_recommendations': {},
            'recommendations': self.test_recommendations[self.resource_urls[0]]
        }

    def attempt_upload_file_and_verify_result(self, test_case, event_name, content=None):
        """
        Running on a test case, creating a temp file, uploading it by
        calling the corresponding ajax event, and verifying that upload
        happens or is rejected as expected.
        """
        if 'magic_number' in test_case:
            f_handler = BytesIO(codecs.decode(test_case['magic_number'], 'hex_codec'))
        elif content is not None:
            f_handler = BytesIO(
                json.dumps(content, sort_keys=True).encode('utf-8'))
        else:
            f_handler = BytesIO(b'')

        f_handler.content_type = test_case['mimetypes']
        f_handler.name = 'file' + test_case['suffixes']
        url = self.get_handler_url(event_name)
        resp = self.client.post(url, {'file': f_handler})
        assert resp.status_code == test_case['status']

    @data(
        {
            'suffixes': '.csv',
            'magic_number': 'ffff',
            'mimetypes': 'text/plain',
            'status': 415
        },  # Upload file with wrong extension name
        {
            'suffixes': '.gif',
            'magic_number': '89504e470d0a1a0a',
            'mimetypes': 'image/gif',
            'status': 415
        },  # Upload file with wrong magic number
        {
            'suffixes': '.jpg',
            'magic_number': '89504e470d0a1a0a',
            'mimetypes': 'image/jpeg',
            'status': 415
        },  # Upload file with wrong magic number
        {
            'suffixes': '.png',
            'magic_number': '474946383761',
            'mimetypes': 'image/png',
            'status': 415
        },  # Upload file with wrong magic number
        {
            'suffixes': '.jpg',
            'magic_number': '474946383761',
            'mimetypes': 'image/jpeg',
            'status': 415
        },  # Upload file with wrong magic number
        {
            'suffixes': '.png',
            'magic_number': 'ffd8ffd9',
            'mimetypes': 'image/png',
            'status': 415
        },  # Upload file with wrong magic number
        {
            'suffixes': '.gif',
            'magic_number': 'ffd8ffd9',
            'mimetypes': 'image/gif',
            'status': 415
        }
    )
    def test_upload_screenshot_wrong_file_type(self, test_case):
        """
        Verify the file uploading fails correctly when file with wrong type
        (extension/magic number) is provided
        """
        self.enroll_staff(self.staff_user)
        # Upload file with wrong extension name or magic number
        self.attempt_upload_file_and_verify_result(test_case, 'upload_screenshot')

    @data(
        {
            'suffixes': '.png',
            'magic_number': '89504e470d0a1a0a',
            'mimetypes': 'image/png',
            'status': 200
        },
        {
            'suffixes': '.gif',
            'magic_number': '474946383961',
            'mimetypes': 'image/gif',
            'status': 200
        },
        {
            'suffixes': '.gif',
            'magic_number': '474946383761',
            'mimetypes': 'image/gif',
            'status': 200
        },
        {
            'suffixes': '.jpg',
            'magic_number': 'ffd8ffd9',
            'mimetypes': 'image/jpeg',
            'status': 200
        }
    )
    def test_upload_screenshot_correct_file_type(self, test_case):
        """
        Verify the file type checking in the file uploading method is
        successful.
        """
        self.enroll_staff(self.staff_user)
        # Upload file with correct extension name and magic number
        self.attempt_upload_file_and_verify_result(test_case, 'upload_screenshot')

    @data(
        {
            'suffixes': '.json',
            'mimetypes': 'application/json',
            'status': 403
        }
    )
    def test_import_resources_by_student(self, test_case):
        """
        Test the function for importing all resources into the Recommender
        by a student.
        """
        self.enroll_student(self.STUDENTS[0]['email'], self.STUDENTS[0]['password'])
        self.attempt_upload_file_and_verify_result(test_case, 'import_resources', self.initial_configuration)

    @data(
        {
            'suffixes': '.csv',
            'mimetypes': 'application/json',
            'status': 415
        },  # Upload file with wrong extension name
        {
            'suffixes': '.json',
            'mimetypes': 'application/json',
            'status': 200
        }
    )
    def test_import_resources(self, test_case):
        """
        Test the function for importing all resources into the Recommender.
        """
        self.enroll_staff(self.staff_user)
        self.attempt_upload_file_and_verify_result(test_case, 'import_resources', self.initial_configuration)

    @data(
        {
            'suffixes': '.json',
            'mimetypes': 'application/json',
            'status': 415
        }
    )
    def test_import_resources_wrong_format(self, test_case):
        """
        Test the function for importing empty dictionary into the Recommender.
        This should fire an error.
        """
        self.enroll_staff(self.staff_user)
        self.attempt_upload_file_and_verify_result(test_case, 'import_resources', {})
