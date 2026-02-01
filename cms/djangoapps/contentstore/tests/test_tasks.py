"""
Unit tests for course import and export Celery tasks
"""
import copy
import json
import logging
from unittest import mock
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from celery import Task

import ddt
import pytest
from django.conf import settings
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from organizations.models import OrganizationCourse
from organizations.tests.factories import OrganizationFactory
from user_tasks.models import UserTaskArtifact, UserTaskStatus

from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.course_apps.toggles import EXAMS_IDA
from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.embargo.models import Country, CountryAccessRule, RestrictedCourse
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from ..tasks import (
    LinkState,
    export_olx,
    update_special_exams_and_publish,
    rerun_course,
    sync_discussion_settings,
    _validate_urls_access_in_batches,
    _filter_by_status,
    _check_broken_links,
    _is_studio_url,
    _scan_course_for_links,
    _convert_to_standard_url,
    extract_content_URLs_from_course,
)

logging = logging.getLogger(__name__)

TEST_DATA_CONTENTSTORE = copy.deepcopy(settings.CONTENTSTORE)
TEST_DATA_CONTENTSTORE['DOC_STORE_CONFIG']['db'] = 'test_xcontent_%s' % uuid4().hex


def side_effect_exception(*args, **kwargs):
    """
    Side effect for mocking which raises an exception
    """
    raise Exception('Boom!')


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ExportCourseTestCase(CourseTestCase):
    """
    Tests of the export_olx task applied to courses
    """

    def test_success(self):
        """
        Verify that a routine course export task succeeds
        """
        key = str(self.course.location.course_key)
        result = export_olx.delay(self.user.id, key, 'en')
        status = UserTaskStatus.objects.get(task_id=result.id)
        self.assertEqual(status.state, UserTaskStatus.SUCCEEDED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        output = artifacts[0]
        self.assertEqual(output.name, 'Output')

    @mock.patch('cms.djangoapps.contentstore.tasks.export_course_to_xml', side_effect=side_effect_exception)
    def test_exception(self, mock_export):  # pylint: disable=unused-argument
        """
        The export task should fail gracefully if an exception is thrown
        """
        key = str(self.course.location.course_key)
        result = export_olx.delay(self.user.id, key, 'en')
        self._assert_failed(result, json.dumps({'raw_error_msg': 'Boom!'}))

    @mock.patch('cms.djangoapps.contentstore.tasks.User.objects.get', side_effect=User.DoesNotExist)
    def test_invalid_user_id(self, mock_raise_exc):  # pylint: disable=unused-argument
        """
        Verify that attempts to export a course as an invalid user fail
        """
        user = UserFactory(id=User.objects.order_by('-id').first().pk + 100)
        key = str(self.course.location.course_key)
        result = export_olx.delay(user.id, key, 'en')
        self._assert_failed(result, f'Unknown User ID: {user.id}')

    def test_non_course_author(self):
        """
        Verify that users who aren't authors of the course are unable to export it
        """
        _, nonstaff_user = self.create_non_staff_authed_user_client()
        key = str(self.course.location.course_key)
        result = export_olx.delay(nonstaff_user.id, key, 'en')
        self._assert_failed(result, 'Permission denied')

    def _assert_failed(self, task_result, error_message):
        """
        Verify that a task failed with the specified error message
        """
        status = UserTaskStatus.objects.get(task_id=task_result.id)
        self.assertEqual(status.state, UserTaskStatus.FAILED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        error = artifacts[0]
        self.assertEqual(error.name, 'Error')
        self.assertEqual(error.text, error_message)


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class ExportLibraryTestCase(LibraryTestCase):
    """
    Tests of the export_olx task applied to libraries
    """

    def test_success(self):
        """
        Verify that a routine library export task succeeds
        """
        key = str(self.lib_key)
        result = export_olx.delay(self.user.id, key, 'en')
        status = UserTaskStatus.objects.get(task_id=result.id)
        self.assertEqual(status.state, UserTaskStatus.SUCCEEDED)
        artifacts = UserTaskArtifact.objects.filter(status=status)
        self.assertEqual(len(artifacts), 1)
        output = artifacts[0]
        self.assertEqual(output.name, 'Output')


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class RerunCourseTaskTestCase(CourseTestCase):  # lint-amnesty, pylint: disable=missing-class-docstring

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _rerun_course(self, old_course_key, new_course_key):
        CourseRerunState.objects.initiated(old_course_key, new_course_key, self.user, 'Test Re-run')
        rerun_course(str(old_course_key), str(new_course_key), self.user.id)

    def test_success(self):
        """ The task should clone the OrganizationCourse and RestrictedCourse data. """
        old_course_key = self.course.id
        new_course_key = CourseLocator(org=old_course_key.org, course=old_course_key.course, run='rerun')

        old_course_id = str(old_course_key)
        new_course_id = str(new_course_key)

        organization = OrganizationFactory(short_name=old_course_key.org)
        OrganizationCourse.objects.create(course_id=old_course_id, organization=organization)

        restricted_course = RestrictedCourse.objects.create(course_key=self.course.id)
        restricted_country = Country.objects.create(country='US')

        CountryAccessRule.objects.create(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=restricted_course,
            country=restricted_country
        )

        # Run the task!
        self._rerun_course(old_course_key, new_course_key)

        # Verify the new course run exists
        course = modulestore().get_course(new_course_key)
        self.assertIsNotNone(course)

        # Verify the OrganizationCourse is cloned
        self.assertEqual(OrganizationCourse.objects.count(), 2)
        # This will raise an error if the OrganizationCourse object was not cloned
        OrganizationCourse.objects.get(course_id=new_course_id, organization=organization)

        # Verify the RestrictedCourse and related objects are cloned
        self.assertEqual(RestrictedCourse.objects.count(), 2)
        restricted_course = RestrictedCourse.objects.get(course_key=new_course_key)

        self.assertEqual(CountryAccessRule.objects.count(), 2)
        CountryAccessRule.objects.get(
            rule_type=CountryAccessRule.BLACKLIST_RULE,
            restricted_course=restricted_course,
            country=restricted_country
        )

    def test_success_different_org(self):
        """
        The task should clone the OrganizationCourse with a different org.
        """
        old_course_key = self.course.id
        new_course_key = CourseLocator(org='neworg', course=old_course_key.course, run='rerun')

        old_course_id = str(old_course_key)
        new_course_id = str(new_course_key)

        organization = OrganizationFactory(short_name=old_course_key.org)
        OrganizationCourse.objects.create(course_id=old_course_id, organization=organization)

        # Run the task!
        self._rerun_course(old_course_key, new_course_key)

        # Verify the OrganizationCourse is cloned with a different org
        self.assertEqual(OrganizationCourse.objects.count(), 2)
        OrganizationCourse.objects.get(course_id=new_course_id, organization__short_name='neworg')


@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class RegisterExamsTaskTestCase(CourseTestCase):  # pylint: disable=missing-class-docstring

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    def test_exam_service_not_enabled_success(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ edx-proctoring interface is called if exam service is not enabled """
        update_special_exams_and_publish(str(self.course.id))
        _mock_register_exams_proctoring.assert_called_once_with(self.course.id)
        _mock_register_exams_service.assert_not_called()

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    @override_waffle_flag(EXAMS_IDA, active=True)
    def test_exam_service_enabled_success(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ exams service interface is called if exam service is enabled """
        update_special_exams_and_publish(str(self.course.id))
        _mock_register_exams_proctoring.assert_not_called()
        _mock_register_exams_service.assert_called_once_with(self.course.id)

    @mock.patch('cms.djangoapps.contentstore.exams.register_exams')
    @mock.patch('cms.djangoapps.contentstore.proctoring.register_special_exams')
    def test_register_exams_failure(self, _mock_register_exams_proctoring, _mock_register_exams_service):
        """ credit requirements update signal fires even if exam registration fails """
        with mock.patch('openedx.core.djangoapps.credit.signals.handlers.on_course_publish') as course_publish:
            _mock_register_exams_proctoring.side_effect = Exception('boom!')
            update_special_exams_and_publish(str(self.course.id))
            course_publish.assert_called()


class MockCourseLinkCheckTask(Task):
    def __init__(self):
        self.status = mock.Mock()


############## Course Optimizer tests ##############


class CheckBrokenLinksTaskTest(ModuleStoreTestCase):
    """Tests for CheckBrokenLinksTask"""
    def setUp(self):
        super().setUp()
        self.store = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.mongo)  # lint-amnesty, pylint: disable=protected-access
        self.test_course = CourseFactory.create(
            org="test", course="course1", display_name="run1"
        )
        self.mock_urls = [
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@1", "http://example.com/valid"],
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@2", "http://example.com/invalid"],
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@3", f'http://{settings.CMS_BASE}/locked'],
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@3", 'https://outsider.com/about'],
        ]
        self.expected_file_contents = [
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@2", "http://example.com/invalid", LinkState.BROKEN],
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@3",
             f"http://{settings.CMS_BASE}/locked",
             LinkState.LOCKED
             ],
            ["block-v1:edX+DemoX+Demo_Course+type@vertical+block@3",
             'https://outsider.com/about',
             LinkState.EXTERNAL_FORBIDDEN
             ],
        ]

    @mock.patch('cms.djangoapps.contentstore.tasks.UserTaskArtifact', autospec=True)
    @mock.patch('cms.djangoapps.contentstore.tasks._scan_course_for_links')
    @mock.patch('cms.djangoapps.contentstore.tasks._save_broken_links_file', autospec=True)
    @mock.patch('cms.djangoapps.contentstore.tasks._write_broken_links_to_file', autospec=True)
    @mock.patch('cms.djangoapps.contentstore.tasks._validate_urls_access_in_batches', autospec=True)
    def test_check_broken_links_stores_broken_locked_and_forbidden_urls(
        self,
        mock_validate_urls,
        mock_write_broken_links_to_file,
        mock_save_broken_links_file,
        mock_scan_course_for_links,
        mock_user_task_artifact
    ):
        '''
        The test verifies that the check_broken_links task correctly
        stores broken or locked URLs in the course.
        The expected behavior is that the after scanning the course,
        validating the URLs, and filtering the results, the task stores the results in a
        JSON file.
        Note that this test mocks all validation functions and therefore
        does not test link validation or any of its support functions.
        '''
        mock_user = UserFactory.create(username='student', password='password')
        mock_course_key_string = "course-v1:edX+DemoX+Demo_Course"
        mock_task = MockCourseLinkCheckTask()
        mock_scan_course_for_links.return_value = self.mock_urls
        mock_validate_urls.return_value = [
            {
                "block_id": "block-v1:edX+DemoX+Demo_Course+type@vertical+block@1",
                "url": "http://example.com/valid",
                "status": 200,
            },
            {
                "block_id": "block-v1:edX+DemoX+Demo_Course+type@vertical+block@2",
                "url": "http://example.com/invalid",
                "status": 400,
            },
            {
                "block_id": "block-v1:edX+DemoX+Demo_Course+type@vertical+block@3",
                "url": f"http://{settings.CMS_BASE}/locked",
                "status": 403,
            },
            {
                "block_id": "block-v1:edX+DemoX+Demo_Course+type@vertical+block@3",
                "url": "https://outsider.com/about",
                "status": 403,
            }
        ]

        _check_broken_links(mock_task, mock_user.id, mock_course_key_string, 'en')  # pylint: disable=no-value-for-parameter

        # Check that UserTaskArtifact was called with the correct arguments
        mock_user_task_artifact.assert_called_once_with(status=mock.ANY, name='BrokenLinks')

        # Check that the correct links are written to the file
        mock_write_broken_links_to_file.assert_called_once_with(self.expected_file_contents, mock.ANY)

        # Check that _save_broken_links_file was called with the correct arguments
        mock_save_broken_links_file.assert_called_once_with(mock_user_task_artifact.return_value, mock.ANY)

    def test_hash_tags_stripped_from_url_lists(self):
        NUM_HASH_TAG_LINES = 2
        url_list = '''
        href='#'                        # 1 of 2 lines that will be stripped
        href='http://google.com'
        src='#'                         # 2 of 2 lines that will be stripped
        href='https://microsoft.com'
        src="/static/resource_name"
        '''

        # Correct for the two carriage returns surrounding the ''' marks
        original_lines = len(url_list.splitlines()) - 2

        processed_url_list = extract_content_URLs_from_course(url_list)
        processed_lines = len(processed_url_list)

        assert processed_lines == original_lines - NUM_HASH_TAG_LINES, \
            f'Processed URL list lines = {processed_lines}; expected {original_lines - 2}'

    def test_http_url_not_recognized_as_studio_url_scheme(self):
        self.assertFalse(_is_studio_url('http://www.google.com'))

    def test_https_url_not_recognized_as_studio_url_scheme(self):
        self.assertFalse(_is_studio_url('https://www.google.com'))

    def test_http_with_studio_base_url_recognized_as_studio_url_scheme(self):
        self.assertTrue(_is_studio_url(f'http://{settings.CMS_BASE}/testurl'))

    def test_https_with_studio_base_url_recognized_as_studio_url_scheme(self):
        self.assertTrue(_is_studio_url(f'https://{settings.CMS_BASE}/testurl'))

    def test_container_url_without_url_base_is_recognized_as_studio_url_scheme(self):
        self.assertTrue(_is_studio_url('container/test'))

    def test_slash_url_without_url_base_is_recognized_as_studio_url_scheme(self):
        self.assertTrue(_is_studio_url('/static/test'))

    @mock.patch('cms.djangoapps.contentstore.tasks.ModuleStoreEnum', autospec=True)
    @mock.patch('cms.djangoapps.contentstore.tasks.modulestore', autospec=True)
    def test_course_scan_occurs_on_published_version(self, mock_modulestore, mock_module_store_enum):
        """_scan_course_for_links should only scan published courses"""
        mock_modulestore_instance = mock.Mock()
        mock_modulestore.return_value = mock_modulestore_instance
        mock_modulestore_instance.get_items.return_value = []

        mock_course_key_string = CourseKey.from_string("course-v1:edX+DemoX+Demo_Course")
        mock_module_store_enum.RevisionOption.published_only = "mock_published_only"

        _scan_course_for_links(mock_course_key_string)

        mock_modulestore_instance.get_items.assert_called_once_with(
            mock_course_key_string,
            qualifiers={'category': 'vertical'},
            revision=mock_module_store_enum.RevisionOption.published_only
        )

    @mock.patch('cms.djangoapps.contentstore.tasks.extract_content_URLs_from_course', autospec=True)
    def test_number_of_scanned_blocks_equals_blocks_in_course(self, mockextract_content_URLs_from_course):
        """
        _scan_course_for_links should call extract_content_URLs_from_course once per block in course.
        """
        expected_blocks = self.store.get_items(self.test_course.id)

        _scan_course_for_links(self.test_course.id)
        self.assertEqual(len(expected_blocks), mockextract_content_URLs_from_course.call_count)

    @mock.patch('cms.djangoapps.contentstore.tasks.get_block_info', autospec=True)
    @mock.patch('cms.djangoapps.contentstore.tasks.modulestore', autospec=True)
    def test_scan_course_excludes_drag_and_drop(self, mock_modulestore, mock_get_block_info):
        """
        Test that `_scan_course_for_links` excludes blocks of category 'drag-and-drop-v2'.
        """
        vertical = BlockFactory.create(
            category='vertical',
            parent_location=self.test_course.location
        )
        drag_and_drop_block = BlockFactory.create(
            category='drag-and-drop-v2',
            parent_location=vertical.location,
        )
        text_block = BlockFactory.create(
            category='html',
            parent_location=vertical.location,
            data='Test Link -> <a href="http://example.com">Example.com</a>'
        )

        mock_modulestore_instance = mock.Mock()
        mock_modulestore.return_value = mock_modulestore_instance
        mock_modulestore_instance.get_items.return_value = [vertical]
        vertical.get_children = mock.Mock(return_value=[drag_and_drop_block, text_block])

        def get_block_side_effect(block):
            block_data = getattr(block, 'data', '')
            if isinstance(block_data, str):
                return {'data': block_data}
            raise TypeError("expected string or bytes-like object, got 'dict'")
        mock_get_block_info.side_effect = get_block_side_effect

        urls = _scan_course_for_links(self.test_course.id)
        # The drag-and-drop block should not appear in the results
        self.assertFalse(
            any(block_id == str(drag_and_drop_block.usage_key) for block_id, _ in urls),
            "Drag and Drop blocks should be excluded"
        )
        self.assertTrue(
            any(block_id == str(text_block.usage_key) for block_id, _ in urls),
            "Text block should be included"
        )

    @pytest.mark.asyncio
    async def test_every_detected_link_is_validated(self):
        '''
        The call to _validate_urls_access_in_batches() should call _validate_batch() three times, once for each
        of the three batches of length 2 in url_list. The lambda function supplied for _validate_batch will
        simply return the set of urls fed to _validate_batch(), and _validate_urls_access_in_batches() will
        aggregate these into a list identical to the original url_list.

        What this shows is that each url submitted to _validate_urls_access_in_batches() is ending up as an argument
        to one of the generated _validate_batch() calls, and that no input URL is left unprocessed.
        '''
        url_list = ['1', '2', '3', '4', '5']
        course_key = 'course-v1:edX+DemoX+Demo_Course'
        batch_size = 2
        with patch("cms.djangoapps.contentstore.tasks._validate_batch", new_callable=AsyncMock) as mock_validate_batch:
            mock_validate_batch.side_effect = lambda x, y: x
            validated_urls = await _validate_urls_access_in_batches(url_list, course_key, batch_size)
            mock_validate_batch.assert_called()
            assert mock_validate_batch.call_count == 3  # two full batches and one partial batch
            assert validated_urls == url_list, \
                f"List of validated urls {validated_urls} is not identical to sourced urls {url_list}"

    @pytest.mark.asyncio
    async def test_all_links_are_validated_with_batch_validation(self):
        '''
        Here the focus is not on batching, but rather that when validation occurs it does so on the intended
        URL strings
        '''
        with patch("cms.djangoapps.contentstore.tasks._validate_url_access", new_callable=AsyncMock) as mock_validate:
            mock_validate.return_value = {"status": 200}

            url_list = ['1', '2', '3', '4', '5']
            course_key = 'course-v1:edX+DemoX+Demo_Course'
            batch_size = 2
            await _validate_urls_access_in_batches(url_list, course_key, batch_size)
            args_list = mock_validate.call_args_list
            urls = [call_args.args[1] for call_args in args_list]   # The middle argument in each of the function calls
            for i in range(1, len(url_list) + 1):
                assert str(i) in urls, f'{i} not supplied as a url for validation in batches function'

    def test_no_retries_on_403_access_denied_links(self):
        '''
        No mocking required here. Will populate "filtering_input" with simulated results for link checks where
        some links time out, some links receive 403 errors, and some receive 200 success. This test then
        ensures that "_filter_by_status()" tallies the three categories as expected, and formats the result
        as expected.
        '''
        url_list = ['1', '2', '3', '4', '5']
        filtering_input = []
        for i in range(1, len(url_list) + 1):     # Notch out one of the URLs, having it return a '403' status code
            filtering_input.append({
                'block_id': f'block_{i}',
                'url': str(i),
                'status': 200
            })
        filtering_input[2]['status'] = 403
        filtering_input[3]['status'] = 500
        filtering_input[4]['status'] = None

        broken_or_locked_urls, retry_list = _filter_by_status(filtering_input)
        assert len(broken_or_locked_urls) == 2  # The inputs with status = 403 and 500
        assert len(retry_list) == 1             # The input with status = None
        assert retry_list[0][1] == '5'      # The only URL fit for a retry operation (status == None)

    def test_filter_by_status(self):
        """
        Test the _filter_by_status function to ensure it correctly categorize links
        based on the given status codes and returns appropriate lists of filtered
        results and retry attempts.
        """
        # Test data
        results = [
            {'status': 200, 'block_id': 'block1', 'url': 'https://example.com'},
            {'status': None, 'block_id': 'block2', 'url': 'https://retry.com'},
            {'status': 403, 'block_id': 'block3', 'url': 'https://' + settings.CMS_BASE},
            {'status': None, 'block_id': 'block3', 'url': 'https://' + settings.CMS_BASE},
            {'status': 403, 'block_id': 'block4', 'url': 'https://external.com'},
            {'status': 404, 'block_id': 'block5', 'url': 'https://broken.com'}
        ]

        expected_filtered_results = [
            ['block2', 'https://retry.com', LinkState.EXTERNAL_FORBIDDEN],
            ['block3', 'https://' + settings.CMS_BASE, LinkState.LOCKED],
            ['block4', 'https://external.com', LinkState.EXTERNAL_FORBIDDEN],
            ['block5', 'https://broken.com', LinkState.BROKEN],
        ]

        expected_retry_list = [
            ['block3', 'https://' + settings.CMS_BASE]
        ]

        filtered_results, retry_list = _filter_by_status(results)

        self.assertEqual(filtered_results, expected_filtered_results)
        self.assertEqual(retry_list, expected_retry_list)

    @patch("cms.djangoapps.contentstore.tasks._validate_user", return_value=MagicMock())
    @patch("cms.djangoapps.contentstore.tasks._scan_course_for_links", return_value=["url1", "url2"])
    @patch(
        "cms.djangoapps.contentstore.tasks._validate_urls_access_in_batches",
        return_value=[{"url": "url1", "status": "ok"}]
    )
    @patch(
        "cms.djangoapps.contentstore.tasks._filter_by_status",
        return_value=(["block_1", "url1", True], ["block_2", "url2"])
    )
    @patch("cms.djangoapps.contentstore.tasks._retry_validation", return_value=['block_2', 'url2'])
    def test_check_broken_links_calls_expected_support_functions(
        self,
        mock_retry_validation,
        mock_filter,
        mock_validate_urls,
        mock_scan_course,
        mock_validate_user
    ):
        # Parameters for the function
        user_id = 1234
        language = "en"
        course_key_string = "course-v1:edX+DemoX+2025"

        # Mocking self and status attributes for the test
        class MockStatus:
            """Mock for status attributes"""
            def __init__(self):
                self.state = "READY"

            def set_state(self, state):
                self.state = state

            def increment_completed_steps(self):
                pass

            def fail(self, error_details):
                self.state = "FAILED"

        class MockSelf:
            def __init__(self):
                self.status = MockStatus()

        mock_self = MockSelf()

        _check_broken_links(mock_self, user_id, course_key_string, language)

        # Prepare expected results based on mock settings
        url_list = mock_scan_course.return_value
        validated_url_list = mock_validate_urls.return_value
        broken_or_locked_urls, retry_list = mock_filter.return_value
        course_key = CourseKey.from_string(course_key_string)

        if retry_list:
            retry_results = mock_retry_validation.return_value
            broken_or_locked_urls.extend(retry_results)

        # Perform verifications
        try:
            mock_self.status.increment_completed_steps()
            mock_retry_validation.assert_called_once_with(
                mock_filter.return_value[1], course_key, retry_count=3
            )
        except Exception as e:  # pylint: disable=broad-except
            logging.exception("Error checking links for course %s", course_key_string, exc_info=True)
            if mock_self.status.state != "FAILED":
                mock_self.status.fail({"raw_error_msg": str(e)})
            assert False, "Exception should not occur"

        # Assertions to confirm patched calls were invoked
        mock_validate_user.assert_called_once_with(mock_self, user_id, language)
        mock_scan_course.assert_called_once_with(course_key)
        mock_validate_urls.assert_called_once_with(url_list, course_key, batch_size=100)
        mock_filter.assert_called_once_with(validated_url_list)
        if retry_list:
            mock_retry_validation.assert_called_once_with(retry_list, course_key, retry_count=3)

    def test_convert_to_standard_url(self):
        """Test _convert_to_standard_url function with expected URLs."""
        course_key = CourseKey.from_string("course-v1:test+course1+run1")
        test_cases = [
            (
                "/static/getting-started_x250.png",
                f"https://{settings.CMS_BASE}/asset-v1:test+course1+run1+type@asset+block/getting-started_x250.png",
            ),
            (
                "/jump_to_id/123abc",
                f"https://{settings.LMS_BASE}/courses/{course_key}/jump_to_id/123abc",
            ),
            (
                "/container/block-v1:test+course1+type@vertical+block@123",
                f"https://{settings.CMS_BASE}/container/block-v1:test+course1+type@vertical+block@123",
            ),
            ("/unknown/path", f"https://{settings.CMS_BASE}/unknown/path"),
            ("https://external.com/some/path", "https://external.com/some/path"),
            ("studio-url", "https://localhost:8001/container/studio-url"),
        ]

        for url, expected in test_cases:
            self.assertEqual(
                _convert_to_standard_url(url, course_key),
                expected,
                f"Failed for URL: {url}",
            )

    def test_extract_content_URLs_from_course(self):
        """Test extract_content_URLs_from_course function for correct URL extraction."""

        content = '''
            <a href="https://example.com">Link</a>
            <img src="https://images.com/pic.jpg">
            <link href="https://fonts.googleapis.com/css?family=Roboto">
            <a href="#">Home</a>
            <a href="https://validsite.com">Valid</a>
            <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...">
            <a href="data:application/pdf;base64,JVBERi0xLjQK...">
            <a href="https://another-valid.com">Another</a>
            <p>No links here!</p>
            <img alt="Just an image without src">
        '''

        expected = [
            "https://example.com",
            "https://images.com/pic.jpg",
            "https://fonts.googleapis.com/css?family=Roboto",
            "https://validsite.com",
            "https://another-valid.com"
        ]
        self.assertEqual(extract_content_URLs_from_course(content), set(expected))


@ddt.ddt
@override_settings(CONTENTSTORE=TEST_DATA_CONTENTSTORE)
class SyncDiscussionSettingsTaskTestCase(CourseTestCase):
    """Tests for the `sync_discussion_settings` task."""

    def setUp(self):
        super().setUp()
        self.discussion_config = DiscussionsConfiguration.objects.create(context_key=self.course.id)

    def _update_discussion_settings(self, discussions_settings: dict):
        """Helper method to set discussion settings in the course."""
        self.course.discussions_settings = discussions_settings
        modulestore().update_item(self.course, self.user.id)

    def test_sync_settings(self):
        """Test syncing discussion settings to DiscussionsConfiguration."""
        self._update_discussion_settings(
            {
                "enable_graded_units": True,
                "unit_level_visibility": False,
                "enable_in_context": True,
                "posting_restrictions": "enabled",
            }
        )

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.enable_graded_units is True
        assert self.discussion_config.unit_level_visibility is False
        assert self.discussion_config.enable_in_context is True
        assert self.discussion_config.posting_restrictions == "enabled"
        assert self.discussion_config.provider_type == Provider.LEGACY

    def test_sync_plugin_configuration(self):
        """Test syncing plugin configuration from provider settings."""
        # Set up course discussion settings with provider-specific config
        provider_config = {"test_key": "test_value", "test_key_2": "test_value_2"}
        self._update_discussion_settings({self.discussion_config.provider_type: provider_config})

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.plugin_configuration == provider_config

    @ddt.data(
        (False, True),  # When the tab is visible, the discussion should be enabled.
        (True, False),  # When the tab is hidden, the discussion should be disabled.
    )
    @ddt.unpack
    def test_sync_discussion_tab_visibility(self, is_hidden: bool, expected_enabled: bool):
        """Test syncing discussion enabled status based on tab visibility."""
        for tab in self.course.tabs:
            if tab.tab_id == "discussion":
                tab.is_hidden = is_hidden
                break
        modulestore().update_item(self.course, self.user.id)

        sync_discussion_settings(self.course.id, self.user)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.enabled is expected_enabled

    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=True)
    def test_auto_migrate_to_new_structure(self):
        """Test automatic migration to the `OPEN_EDX` provider when new structure is enabled."""
        with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
            sync_discussion_settings(self.course.id, self.user)

            migration_log = f"New structure is enabled, also updating {self.course.id} to use new provider"
            assert any(migration_log in log for log in logs.output)

        self.discussion_config.refresh_from_db()
        assert self.discussion_config.provider_type == Provider.OPEN_EDX

        course = modulestore().get_course(self.course.id)
        assert course.discussions_settings.get("provider_type") == Provider.OPEN_EDX

    @ddt.data(
        {"provider_type": Provider.OPEN_EDX},  # Using the `provider_type` field.
        {"provider": Provider.OPEN_EDX},  # Using the `provider` field as fallback.
    )
    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=True)
    def test_no_provider_migration_when_already_openedx(self, provider_settings: dict):
        """Test no migration occurs when provider is already `OPEN_EDX`."""
        self._update_discussion_settings(provider_settings)

        with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
            sync_discussion_settings(self.course.id, self.user)

            migration_log = f"New structure is enabled, also updating {self.course.id} to use new provider"
            assert not any(migration_log in log for log in logs.output)

    def test_handling_exceptions(self):
        """Test that exceptions are caught and logged properly."""
        test_error_message = "Test error"

        with mock.patch.object(DiscussionsConfiguration.objects, "get", side_effect=Exception(test_error_message)):
            with self.assertLogs("cms.djangoapps.contentstore.tasks", level="INFO") as logs:
                sync_discussion_settings(self.course.id, self.user)

                expected_log = (
                    f"Course import {self.course.id}: DiscussionsConfiguration sync failed: {test_error_message}"
                )
                assert any(expected_log in log for log in logs.output)
