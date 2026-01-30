"""
Test module for Entrance Exams AJAX callback handler workflows
"""


import json

from django.conf import settings
from django.test import override_settings
from django.test.client import RequestFactory
from milestones.tests.utils import MilestonesTestCaseMixin
from opaque_keys.edx.keys import UsageKey

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient, CourseTestCase
from cms.djangoapps.contentstore.utils import reverse_url
from cms.djangoapps.models.settings.course_grading import CourseGradingModel
from cms.djangoapps.models.settings.course_metadata import CourseMetadata
from common.djangoapps.student.tests.factories import UserFactory
from common.djangoapps.util import milestones_helpers
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order

from ..entrance_exam import (
    add_entrance_exam_milestone,
    create_entrance_exam,
    delete_entrance_exam,
    remove_entrance_exam_milestone_reference,
    update_entrance_exam
)
from cms.djangoapps.contentstore.helpers import GRADER_TYPES
from cms.djangoapps.contentstore.xblock_storage_handlers.create_xblock import create_xblock


@override_settings(ENTRANCE_EXAMS=True)
class EntranceExamHandlerTests(CourseTestCase, MilestonesTestCaseMixin):
    """
    Base test class for create, save, and delete
    """

    def setUp(self):
        """
        Shared scaffolding for individual test runs
        """
        super().setUp()
        self.course_key = self.course.id
        self.usage_key = self.course.location
        self.course_url = f'/course/{str(self.course.id)}'
        self.exam_url = f'/course/{str(self.course.id)}/entrance_exam/'
        self.milestone_relationship_types = milestones_helpers.get_milestone_relationship_types()

    def test_entrance_exam_milestone_addition(self):
        """
        Unit Test: test addition of entrance exam milestone content
        """
        parent_locator = str(self.course.location)
        created_block = create_xblock(
            parent_locator=parent_locator,
            user=self.user,
            category='chapter',
            display_name=('Entrance Exam'),
            is_entrance_exam=True
        )
        add_entrance_exam_milestone(self.course.id, created_block)
        content_milestones = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            str(created_block.location),
            self.milestone_relationship_types['FULFILLS']
        )
        self.assertTrue(len(content_milestones))
        self.assertEqual(len(milestones_helpers.get_course_milestones(self.course.id)), 1)

    def test_entrance_exam_milestone_removal(self):
        """
        Unit Test: test removal of entrance exam milestone content
        """
        parent_locator = str(self.course.location)
        created_block = create_xblock(
            parent_locator=parent_locator,
            user=self.user,
            category='chapter',
            display_name=('Entrance Exam'),
            is_entrance_exam=True
        )
        add_entrance_exam_milestone(self.course.id, created_block)
        content_milestones = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            str(created_block.location),
            self.milestone_relationship_types['FULFILLS']
        )
        self.assertEqual(len(content_milestones), 1)
        user = UserFactory()
        request = RequestFactory().request()
        request.user = user
        remove_entrance_exam_milestone_reference(request, self.course.id)
        content_milestones = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            str(created_block.location),
            self.milestone_relationship_types['FULFILLS']
        )
        self.assertEqual(len(content_milestones), 0)

    def test_contentstore_views_entrance_exam_post(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_post
        """
        resp = self.client.post(self.exam_url, {}, http_accept='application/json')
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)

        # Reload the test course now that the exam block has been added
        self.course = modulestore().get_course(self.course.id)
        metadata = CourseMetadata.fetch_all(self.course)
        self.assertTrue(metadata['entrance_exam_enabled'])
        self.assertIsNotNone(metadata['entrance_exam_minimum_score_pct'])
        self.assertIsNotNone(metadata['entrance_exam_id']['value'])
        self.assertTrue(len(milestones_helpers.get_course_milestones(str(self.course.id))))
        content_milestones = milestones_helpers.get_course_content_milestones(
            str(self.course.id),
            metadata['entrance_exam_id']['value'],
            self.milestone_relationship_types['FULFILLS']
        )
        self.assertTrue(len(content_milestones))

    def test_contentstore_views_entrance_exam_post_new_sequential_confirm_grader(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_post
        """
        resp = self.client.post(self.exam_url, {}, http_accept='application/json')
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)

        # Reload the test course now that the exam block has been added
        self.course = modulestore().get_course(self.course.id)

        # Add a new child sequential to the exam block
        # Confirm that the grader type is 'Entrance Exam'
        chapter_locator_string = json.loads(resp.content.decode('utf-8')).get('locator')
        # chapter_locator = UsageKey.from_string(chapter_locator_string)
        seq_data = {
            'category': "sequential",
            'display_name': "Entrance Exam Subsection",
            'parent_locator': chapter_locator_string,
        }
        resp = self.client.ajax_post(reverse_url('xblock_handler'), seq_data)
        seq_locator_string = json.loads(resp.content.decode('utf-8')).get('locator')
        seq_locator = UsageKey.from_string(seq_locator_string)
        section_grader_type = CourseGradingModel.get_section_grader_type(seq_locator)
        self.assertEqual(GRADER_TYPES['ENTRANCE_EXAM'], section_grader_type['graderType'])

    def test_contentstore_views_entrance_exam_get(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_get
        """
        resp = self.client.post(
            self.exam_url,
            {'entrance_exam_minimum_score_pct': settings.ENTRANCE_EXAM_MIN_SCORE_PCT},
            http_accept='application/json'
        )
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)

    def test_contentstore_views_entrance_exam_delete(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_delete
        """
        resp = self.client.post(self.exam_url, {}, http_accept='application/json')
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.delete(self.exam_url)
        self.assertEqual(resp.status_code, 204)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 404)

        user = UserFactory.create(
            username='test_user',
            email='test_user@edx.org',
            is_active=True,
        )
        user.set_password('test')
        user.save()
        milestones = milestones_helpers.get_course_milestones(str(self.course_key))
        self.assertEqual(len(milestones), 1)
        milestone_key = '{}.{}'.format(milestones[0]['namespace'], milestones[0]['name'])
        paths = milestones_helpers.get_course_milestones_fulfillment_paths(
            str(self.course_key),
            milestones_helpers.serialize_user(user)
        )

        # What we have now is a course milestone requirement and no valid fulfillment
        # paths for the specified user.  The LMS is going to have to ignore this situation,
        # because we can't confidently prevent it from occuring at some point in the future.
        # milestone_key_1 =
        self.assertEqual(len(paths[milestone_key]), 0)

        # Re-adding an entrance exam to the course should fix the missing link
        # It wipes out any old entrance exam artifacts and inserts a new exam course chapter/block
        resp = self.client.post(self.exam_url, {}, http_accept='application/json')
        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)

        # Confirm that we have only one Entrance Exam grader after re-adding the exam (validates SOL-475)
        graders = CourseGradingModel.fetch(self.course_key).graders
        count = 0
        for grader in graders:
            if grader['type'] == GRADER_TYPES['ENTRANCE_EXAM']:
                count += 1
        self.assertEqual(count, 1)

    def test_contentstore_views_entrance_exam_delete_bogus_course(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_delete_bogus_course
        """
        resp = self.client.delete('/course/bad/course/key/entrance_exam')
        self.assertEqual(resp.status_code, 400)

    def test_contentstore_views_entrance_exam_get_bogus_course(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_get_bogus_course
        """
        resp = self.client.get('/course/bad/course/key/entrance_exam')
        self.assertEqual(resp.status_code, 400)

    def test_contentstore_views_entrance_exam_get_bogus_exam(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_get_bogus_exam
        """
        resp = self.client.post(
            self.exam_url,
            {'entrance_exam_minimum_score_pct': '50'},
            http_accept='application/json'
        )

        self.assertEqual(resp.status_code, 201)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 200)
        self.course = modulestore().get_course(self.course.id)
        # Should raise an ItemNotFoundError and return a 404
        updated_metadata = {'entrance_exam_id': 'i4x://org.4/course_4/chapter/ed7c4c6a4d68409998e2c8554c4629d1'}

        CourseMetadata.update_from_dict(
            updated_metadata,
            self.course,
            self.user,
        )
        self.course = modulestore().get_course(self.course.id)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 404)

        # Should raise an InvalidKeyError and return a 404
        updated_metadata = {'entrance_exam_id': '123afsdfsad90f87'}

        CourseMetadata.update_from_dict(
            updated_metadata,
            self.course,
            self.user,
        )
        self.course = modulestore().get_course(self.course.id)
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 404)

    def test_contentstore_views_entrance_exam_post_bogus_course(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_post_bogus_course
        """
        resp = self.client.post(
            '/course/bad/course/key/entrance_exam',
            {},
            http_accept='application/json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_contentstore_views_entrance_exam_post_invalid_http_accept(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_post_invalid_http_accept
        """
        resp = self.client.post(
            '/course/bad/course/key/entrance_exam',
            {},
            http_accept='text/html'
        )
        self.assertEqual(resp.status_code, 400)

    def test_contentstore_views_entrance_exam_get_invalid_user(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_get_invalid_user
        """
        user = UserFactory.create(
            username='test_user',
            email='test_user@edx.org',
            is_active=True,
        )
        user.set_password('test')
        user.save()
        self.client = AjaxEnabledTestClient()
        self.client.login(username='test_user', password='test')
        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 403)

    def test_contentstore_views_entrance_exam_unsupported_method(self):
        """
        Unit Test: test_contentstore_views_entrance_exam_unsupported_method
        """
        resp = self.client.put(self.exam_url)
        self.assertEqual(resp.status_code, 405)

    def test_entrance_exam_view_direct_missing_score_setting(self):
        """
        Unit Test: test_entrance_exam_view_direct_missing_score_setting
        """
        user = UserFactory()
        user.is_staff = True
        request = RequestFactory()
        request.user = user

        resp = create_entrance_exam(request, self.course.id, None)
        self.assertEqual(resp.status_code, 201)

    @override_settings(ENTRANCE_EXAMS=False)
    def test_entrance_exam_feature_flag_gating(self):
        user = UserFactory()
        user.is_staff = True
        request = RequestFactory()
        request.user = user

        resp = self.client.get(self.exam_url)
        self.assertEqual(resp.status_code, 400)

        resp = create_entrance_exam(request, self.course.id, None)
        self.assertEqual(resp.status_code, 400)

        resp = delete_entrance_exam(request, self.course.id)
        self.assertEqual(resp.status_code, 400)

        # No return, so we'll just ensure no exception is thrown
        update_entrance_exam(request, self.course.id, {})
