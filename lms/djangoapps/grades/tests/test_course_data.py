"""
Tests for CourseData utility class.
"""
from unittest.mock import patch

import pytest

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.course_blocks.api import get_course_blocks
from openedx.core.djangoapps.content.block_structure.api import get_course_in_cache
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

from ..course_data import CourseData


class CourseDataTest(ModuleStoreTestCase):
    """
    Simple tests to ensure CourseData works as advertised.
    """

    def setUp(self):
        super().setUp()
        with self.store.default_store(ModuleStoreEnum.Type.split):
            self.course = CourseFactory.create()
            # need to re-retrieve the course since the version on the original course isn't accurate.
            self.course = self.store.get_course(self.course.id)
        self.user = UserFactory.create()
        self.collected_structure = get_course_in_cache(self.course.id)
        self.one_true_structure = get_course_blocks(
            self.user, self.course.location, collected_block_structure=self.collected_structure,
        )
        self.expected_results = {
            'course': self.course,
            'collected_block_structure': self.collected_structure,
            'structure': self.one_true_structure,
            'course_key': self.course.id,
            'location': self.course.location,
        }

    @patch('lms.djangoapps.grades.course_data.get_course_blocks')
    def test_fill_course_data(self, mock_get_blocks):
        """
        Tests to ensure that course data is fully filled with just a single input.
        """
        mock_get_blocks.return_value = self.one_true_structure
        for kwarg in self.expected_results:  # We iterate instead of ddt due to dependence on 'self'
            if kwarg == 'location':
                continue  # This property is purely output; it's never able to be used as input
            kwargs = {kwarg: self.expected_results[kwarg]}
            course_data = CourseData(self.user, **kwargs)
            for arg in self.expected_results:
                # No point validating the data we used as input, and c_b_s is input-only
                if arg != kwarg and arg != "collected_block_structure":  # lint-amnesty, pylint: disable=consider-using-in
                    expected = self.expected_results[arg]
                    actual = getattr(course_data, arg)
                    if arg == 'course':
                        assert expected.location == actual.location
                    else:
                        assert expected == actual

    def test_properties(self):
        expected_edited_on = getattr(  # lint-amnesty, pylint: disable=literal-used-as-attribute
            self.one_true_structure[self.one_true_structure.root_block_usage_key],
            'subtree_edited_on',
        )

        for kwargs in [
            dict(course=self.course),
            dict(collected_block_structure=self.one_true_structure),
            dict(structure=self.one_true_structure),
            dict(course_key=self.course.id),
        ]:
            course_data = CourseData(self.user, **kwargs)
            assert course_data.course_key == self.course.id
            assert course_data.location == self.course.location
            assert course_data.structure.root_block_usage_key == self.one_true_structure.root_block_usage_key
            assert course_data.course.id == self.course.id
            assert course_data.version == self.course.course_version
            assert course_data.edited_on == expected_edited_on
            assert 'Course: course_key' in str(course_data)
            assert 'Course: course_key' in course_data.full_string()

    def test_no_data(self):
        with pytest.raises(ValueError):
            _ = CourseData(self.user)

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_full_string(self):
        empty_structure = get_course_blocks(self.user, self.course.location)
        assert not empty_structure

        # full_string retrieves value from collected_structure when structure is empty.
        course_data = CourseData(
            self.user, structure=empty_structure, collected_block_structure=self.collected_structure,
        )
        assert f'Course: course_key: {self.course.id}, version:' in course_data.full_string()

        # full_string returns minimal value when structures aren't readily available.
        course_data = CourseData(self.user, course_key=self.course.id)
        assert 'empty course structure' in course_data.full_string()
