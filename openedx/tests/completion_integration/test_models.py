"""
Test models, managers, and validators.
"""

import pytest
from completion import models
from completion.test_utils import CompletionWaffleTestMixin, submit_completions_for_testing
from completion.waffle import ENABLE_COMPLETION_TRACKING_SWITCH
from django.core.exceptions import ValidationError
from django.test import TestCase
from edx_toggles.toggles.testutils import override_waffle_switch
from opaque_keys.edx.keys import CourseKey, UsageKey

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms

SELECT = 1
UPDATE = 1
SAVEPOINT = 1
OTHER = 1


@skip_unless_lms
class PercentValidatorTestCase(TestCase):
    """
    Test that validate_percent only allows floats (and ints) between 0.0 and 1.0.
    """
    def test_valid_percents(self):
        for value in [1.0, 0.0, 1, 0, 0.5, 0.333081348071397813987230871]:
            models.validate_percent(value)

    def test_invalid_percent(self):
        for value in [-0.00000000001, 1.0000000001, 47.1, 1000, None, float('inf'), float('nan')]:
            self.assertRaises(ValidationError, models.validate_percent, value)  # noqa: PT027


class CompletionSetUpMixin(CompletionWaffleTestMixin):
    """
    Mixin that provides helper to create test BlockCompletion object.
    """
    def set_up_completion(self):
        self.user = UserFactory()
        self.block_key = UsageKey.from_string('block-v1:edx+test+run+type@video+block@doggos')
        self.completion = models.BlockCompletion.objects.create(
            user=self.user,
            context_key=self.block_key.context_key,
            block_type=self.block_key.block_type,
            block_key=self.block_key,
            completion=0.5,
        )


@skip_unless_lms
class SubmitCompletionTestCase(CompletionSetUpMixin, TestCase):
    """
    Test that BlockCompletion.objects.submit_completion has the desired
    semantics.
    """
    def setUp(self):
        super().setUp()
        self.override_waffle_switch(True)
        self.set_up_completion()

    def test_changed_value(self):
        with self.assertNumQueries(2 * SELECT + UPDATE + 2 * SAVEPOINT + 2 * OTHER):
            # OTHER = user exists, completion exists
            completion, isnew = models.BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=self.block_key,
                completion=0.9,
            )
        completion.refresh_from_db()
        assert completion.completion == 0.9
        assert not isnew
        assert models.BlockCompletion.objects.count() == 1

    def test_unchanged_value(self):
        with self.assertNumQueries(2 * SELECT + 2 * SAVEPOINT):
            completion, isnew = models.BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=self.block_key,
                completion=0.5,
            )
        completion.refresh_from_db()
        assert completion.completion == 0.5
        assert not isnew
        assert models.BlockCompletion.objects.count() == 1

    def test_new_user(self):
        newuser = UserFactory()
        with self.assertNumQueries(SELECT + UPDATE + 4 * SAVEPOINT):
            _, isnew = models.BlockCompletion.objects.submit_completion(
                user=newuser,
                block_key=self.block_key,
                completion=0.0,
            )
        assert isnew
        assert models.BlockCompletion.objects.count() == 2

    def test_new_block(self):
        newblock = UsageKey.from_string('block-v1:edx+test+run+type@video+block@puppers')
        with self.assertNumQueries(SELECT + UPDATE + 4 * SAVEPOINT):
            _, isnew = models.BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=newblock,
                completion=1.0,
            )
        assert isnew
        assert models.BlockCompletion.objects.count() == 2

    def test_invalid_completion(self):
        with pytest.raises(ValidationError):
            models.BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=self.block_key,
                completion=1.2
            )
        completion = models.BlockCompletion.objects.get(user=self.user, block_key=self.block_key)
        assert completion.completion == 0.5
        assert models.BlockCompletion.objects.count() == 1


@skip_unless_lms
class CompletionDisabledTestCase(CompletionSetUpMixin, TestCase):
    """
    Tests that completion API is not called when the feature is disabled.
    """
    def setUp(self):
        super().setUp()
        # insert one completion record...
        self.set_up_completion()
        # ...then disable the feature.
        self.override_waffle_switch(False)

    def test_cannot_call_submit_completion(self):
        assert models.BlockCompletion.objects.count() == 1
        with pytest.raises(RuntimeError):
            models.BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=self.block_key,
                completion=0.9,
            )
        assert models.BlockCompletion.objects.count() == 1


@skip_unless_lms
class SubmitBatchCompletionTestCase(CompletionWaffleTestMixin, TestCase):
    """
    Test that BlockCompletion.objects.submit_batch_completion has the desired
    semantics.
    """
    def setUp(self):
        super().setUp()
        self.override_waffle_switch(True)

        self.block_key = UsageKey.from_string('block-v1:edx+test+run+type@video+block@doggos')
        self.course_key_obj = CourseKey.from_string('course-v1:edx+test+run')
        self.user = UserFactory()
        CourseEnrollmentFactory.create(user=self.user, course_id=str(self.course_key_obj))

    def test_submit_batch_completion(self):
        blocks = [(self.block_key, 1.0)]
        models.BlockCompletion.objects.submit_batch_completion(self.user, blocks)
        assert models.BlockCompletion.objects.count() == 1
        assert models.BlockCompletion.objects.last().completion == 1.0

    def test_submit_batch_completion_without_waffle(self):
        with override_waffle_switch(ENABLE_COMPLETION_TRACKING_SWITCH, False):
            with pytest.raises(RuntimeError):  # noqa: PT012
                blocks = [(self.block_key, 1.0)]
                models.BlockCompletion.objects.submit_batch_completion(self.user, blocks)

    def test_submit_batch_completion_with_same_block_new_completion_value(self):
        blocks = [(self.block_key, 0.0)]
        assert models.BlockCompletion.objects.count() == 0
        models.BlockCompletion.objects.submit_batch_completion(self.user, blocks)
        assert models.BlockCompletion.objects.count() == 1
        model = models.BlockCompletion.objects.first()
        assert model.completion == 0.0
        blocks = [
            (UsageKey.from_string('block-v1:edx+test+run+type@video+block@doggos'), 1.0),
        ]
        models.BlockCompletion.objects.submit_batch_completion(self.user, blocks)
        assert models.BlockCompletion.objects.count() == 1
        model = models.BlockCompletion.objects.first()
        assert model.completion == 1.0


@skip_unless_lms
class BatchCompletionMethodTests(CompletionWaffleTestMixin, TestCase):
    """
    Tests for the classmethods that retrieve course/block completion data.
    """
    def setUp(self):
        super().setUp()
        self.override_waffle_switch(True)

        self.user = UserFactory.create()
        self.other_user = UserFactory.create()
        self.course_key = CourseKey.from_string("edX/MOOC101/2049_T2")
        self.other_course_key = CourseKey.from_string("course-v1:ReedX+Hum110+1904")
        self.block_keys = [UsageKey.from_string(f"i4x://edX/MOOC101/video/{number}") for number in range(5)]
        self.block_keys_with_runs = [key.replace(course_key=self.course_key) for key in self.block_keys]
        self.other_course_block_keys = [self.other_course_key.make_usage_key('html', '1')]

        # Submit completions for the main course:
        submit_completions_for_testing(self.user, self.block_keys_with_runs[:3])
        # Different user:
        submit_completions_for_testing(self.other_user, self.block_keys_with_runs[2:])
        # Different course:
        submit_completions_for_testing(self.user, self.other_course_block_keys)

    def test_get_learning_context_completions_missing_runs(self):
        actual_completions = models.BlockCompletion.get_learning_context_completions(self.user, self.course_key)
        expected_block_keys = self.block_keys_with_runs[:3]
        expected_completions = dict(list(zip(expected_block_keys, [1.0, 0.8, 0.6])))  # noqa: B905
        assert expected_completions == actual_completions

    def test_get_learning_context_completions_empty_result_set(self):
        assert models.BlockCompletion.get_learning_context_completions(self.other_user, self.other_course_key) == {}

    def test_get_latest_block_completed(self):
        assert models.BlockCompletion.get_latest_block_completed(self.user, self.course_key).block_key == \
               self.block_keys[2]

    def test_get_latest_completed_none_exist(self):
        assert models.BlockCompletion.get_latest_block_completed(self.other_user, self.other_course_key) is None
