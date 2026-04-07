"""Tests for Show Answer overrides for self-paced courses."""

import ddt
from django.test import RequestFactory
from django.test.utils import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag

from lms.djangoapps.ccx.tests.test_overrides import inject_field_overrides
from lms.djangoapps.courseware.block_render import get_block
from lms.djangoapps.courseware.model_data import FieldDataCache
from openedx.features.course_experience import RELATIVE_DATES_FLAG
from xmodule.capa_block import SHOWANSWER  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@override_settings(
    FIELD_OVERRIDE_PROVIDERS=[
        'openedx.features.personalized_learner_schedules.show_answer.show_answer_field_override.ShowAnswerFieldOverride'
    ],
)
@ddt.ddt
class ShowAnswerFieldOverrideTest(ModuleStoreTestCase):
    """ Tests for Show Answer overrides for self-paced courses. """

    def setup_course(self, **course_kwargs):
        """ Set up a course with provided course attributes. """
        course = CourseFactory.create(**course_kwargs)
        inject_field_overrides((course,), course, self.user)
        return course

    def get_course_block(self, course):
        request = RequestFactory().request()
        field_data_cache = FieldDataCache([], course.id, self.user)
        return get_block(self.user, request, course.location, field_data_cache, course=course)

    @ddt.data(True, False)
    def test_override_enabled_for(self, active):
        with override_waffle_flag(RELATIVE_DATES_FLAG, active=active):
            # Instructor paced course will just have the default value
            ip_course = self.setup_course()
            course_block = self.get_course_block(ip_course)
            assert course_block.showanswer == SHOWANSWER.FINISHED

            # This should be updated to not explicitly add in the showanswer so it can test the
            # default case of never touching showanswer. Reference ticket AA-307 (if that's closed,
            # this can be updated!)
            sp_course = self.setup_course(self_paced=True, showanswer=SHOWANSWER.FINISHED)
            course_block = self.get_course_block(sp_course)
            if active:
                assert course_block.showanswer == SHOWANSWER.AFTER_ALL_ATTEMPTS_OR_CORRECT
            else:
                assert course_block.showanswer == SHOWANSWER.FINISHED

    @ddt.data(
        (SHOWANSWER.ATTEMPTED, SHOWANSWER.ATTEMPTED_NO_PAST_DUE),
        (SHOWANSWER.CLOSED, SHOWANSWER.AFTER_ALL_ATTEMPTS),
        (SHOWANSWER.CORRECT_OR_PAST_DUE, SHOWANSWER.ANSWERED),
        (SHOWANSWER.FINISHED, SHOWANSWER.AFTER_ALL_ATTEMPTS_OR_CORRECT),
        (SHOWANSWER.PAST_DUE, SHOWANSWER.NEVER),
        (SHOWANSWER.NEVER, SHOWANSWER.NEVER),
        (SHOWANSWER.AFTER_SOME_NUMBER_OF_ATTEMPTS, SHOWANSWER.AFTER_SOME_NUMBER_OF_ATTEMPTS),
        (SHOWANSWER.ALWAYS, SHOWANSWER.ALWAYS),
    )
    @ddt.unpack
    @override_waffle_flag(RELATIVE_DATES_FLAG, active=True)
    def test_get(self, initial_value, expected_final_value):
        course = self.setup_course(self_paced=True, showanswer=initial_value)
        course_block = self.get_course_block(course)
        assert course_block.showanswer == expected_final_value
