"""
Unit tests for cloning a course between the same and different module stores.
"""


import json
from unittest.mock import Mock, patch

from django.conf import settings
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore.tasks import rerun_course
from cms.djangoapps.contentstore.tests.utils import CourseTestCase
from common.djangoapps.course_action_state.managers import CourseRerunUIStateManager
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.student.auth import has_course_author_access
from xmodule.contentstore.content import StaticContent  # pylint: disable=wrong-import-order
from xmodule.contentstore.django import contentstore  # pylint: disable=wrong-import-order
from xmodule.modulestore import EdxJSONEncoder, ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # pylint: disable=wrong-import-order

TEST_DATA_DIR = settings.COMMON_TEST_DATA_ROOT


class CloneCourseTest(CourseTestCase):
    """
    Unit tests for cloning a course
    """
    def test_clone_course(self):
        """
        Tests cloning of a course: Split -> Split
        """

        with self.store.default_store(ModuleStoreEnum.Type.split):
            split_course1_id = CourseFactory().id
            split_course2_id = CourseLocator(
                org="edx4", course="split4", run="2013_Fall"
            )
            self.store.clone_course(split_course1_id, split_course2_id, self.user.id)
            self.assertCoursesEqual(split_course1_id, split_course2_id)

    def test_space_in_asset_name_for_rerun_course(self):
        """
        Tests check the scenario where one course which has an asset with percentage(%) in its
        name, it should re-run successfully.
        """
        org = 'edX'
        course_number = 'CS101'
        course_run = '2015_Q1'
        display_name = 'rerun'
        fields = {'display_name': display_name}
        course_assets = {'subs_Introduction%20To%20New.srt.sjson'}

        # Create a course using split modulestore
        course = CourseFactory.create(
            org=org,
            number=course_number,
            run=course_run,
            display_name=display_name,
            default_store=ModuleStoreEnum.Type.split
        )

        # add an asset
        asset_key = course.id.make_asset_key('asset', 'subs_Introduction%20To%20New.srt.sjson')
        content = StaticContent(
            asset_key, 'Dummy assert', 'application/json', 'dummy data',
        )
        contentstore().save(content)

        # Get & verify all assets of the course
        assets, count = contentstore().get_all_content_for_course(course.id)
        self.assertEqual(count, 1)  # noqa: PT009
        self.assertEqual({asset['asset_key'].block_id for asset in assets}, course_assets)  # pylint: disable=consider-using-set-comprehension  # noqa: PT009

        # rerun from split into split
        split_rerun_id = CourseLocator(org=org, course=course_number, run="2012_Q2")
        CourseRerunState.objects.initiated(course.id, split_rerun_id, self.user, fields['display_name'])
        result = rerun_course.delay(
            str(course.id),
            str(split_rerun_id),
            self.user.id,
            json.dumps(fields, cls=EdxJSONEncoder)
        )

        # Check if re-run was successful
        self.assertEqual(result.get(), "succeeded")  # noqa: PT009
        rerun_state = CourseRerunState.objects.find_first(course_key=split_rerun_id)
        self.assertEqual(rerun_state.state, CourseRerunUIStateManager.State.SUCCEEDED)  # noqa: PT009

    def test_rerun_course(self):
        """
        Unit tests for :meth: `contentstore.tasks.rerun_course`
        """
        org = 'edX'
        course_number = 'CS101'
        course_run = '2015_Q1'
        display_name = 'rerun'
        fields = {'display_name': display_name}

        # Create a course using split modulestore
        split_course = CourseFactory.create(
            org=org,
            number=course_number,
            run=course_run,
            display_name=display_name,
            default_store=ModuleStoreEnum.Type.split
        )

        split_course3_id = CourseLocator(
            org="edx3", course="split3", run="rerun_test"
        )
        # Mark the action as initiated
        fields = {'display_name': 'rerun'}
        CourseRerunState.objects.initiated(split_course.id, split_course3_id, self.user, fields['display_name'])
        result = rerun_course.delay(str(split_course.id), str(split_course3_id), self.user.id,
                                    json.dumps(fields, cls=EdxJSONEncoder))
        self.assertEqual(result.get(), "succeeded")  # noqa: PT009
        self.assertTrue(has_course_author_access(self.user, split_course3_id), "Didn't grant access")  # noqa: PT009
        rerun_state = CourseRerunState.objects.find_first(course_key=split_course3_id)
        self.assertEqual(rerun_state.state, CourseRerunUIStateManager.State.SUCCEEDED)  # noqa: PT009

        # try creating rerunning again to same name and ensure it generates error
        result = rerun_course.delay(str(split_course.id), str(split_course3_id), self.user.id)
        self.assertEqual(result.get(), "duplicate course")  # noqa: PT009
        # the below will raise an exception if the record doesn't exist
        CourseRerunState.objects.find_first(
            course_key=split_course3_id,
            state=CourseRerunUIStateManager.State.FAILED
        )

        # try to hit the generic exception catch
        with patch('xmodule.modulestore.split_mongo.mongo_connection.MongoPersistenceBackend.insert_course_index', Mock(side_effect=Exception)):  # pylint: disable=line-too-long
            split_course4_id = CourseLocator(org="edx3", course="split3", run="rerun_fail")
            fields = {'display_name': 'total failure'}
            CourseRerunState.objects.initiated(split_course3_id, split_course4_id, self.user, fields['display_name'])
            result = rerun_course.delay(str(split_course3_id), str(split_course4_id), self.user.id,
                                        json.dumps(fields, cls=EdxJSONEncoder))
            self.assertIn("exception: ", result.get())  # noqa: PT009
            self.assertIsNone(self.store.get_course(split_course4_id), "Didn't delete course after error")  # noqa: PT009  # pylint: disable=line-too-long
            CourseRerunState.objects.find_first(
                course_key=split_course4_id,
                state=CourseRerunUIStateManager.State.FAILED
            )
