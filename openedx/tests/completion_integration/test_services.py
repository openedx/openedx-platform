"""
Tests of completion xblock runtime services
"""


import ddt
from completion.models import BlockCompletion
from completion.services import CompletionService
from completion.test_utils import CompletionWaffleTestMixin
from django.conf import settings
from django.test import override_settings
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.library_tools import LegacyLibraryToolsService
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory
from xmodule.tests import prepare_block_runtime


@ddt.ddt
@skip_unless_lms
class CompletionServiceTestCase(CompletionWaffleTestMixin, SharedModuleStoreTestCase):
    """
    Test the data returned by the CompletionService.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create()
        with cls.store.bulk_operations(cls.course.id):
            cls.chapter = BlockFactory.create(
                parent=cls.course,
                category="chapter",
                publish_item=False,
            )
            cls.sequence = BlockFactory.create(
                parent=cls.chapter,
                category='sequential',
                publish_item=False,
            )
            cls.vertical = BlockFactory.create(
                parent=cls.sequence,
                category='vertical',
                publish_item=False,
            )
            cls.html = BlockFactory.create(
                parent=cls.vertical,
                category='html',
                publish_item=False,
            )
            cls.problem = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                publish_item=False,
            )
            cls.problem2 = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                publish_item=False,
            )
            cls.problem3 = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                publish_item=False,
            )
            cls.problem4 = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                publish_item=False,
            )
            cls.problem5 = BlockFactory.create(
                parent=cls.vertical,
                category="problem",
                publish_item=False,
            )
            cls.store.update_item(cls.course, UserFactory().id)
        cls.problems = [cls.problem, cls.problem2, cls.problem3, cls.problem4, cls.problem5]

    def setUp(self):
        super().setUp()
        self.override_waffle_switch(True)
        self.user = UserFactory.create()
        self.other_user = UserFactory.create()
        self.course_key = self.course.id
        self.other_course_key = CourseKey.from_string("course-v1:ReedX+Hum110+1904")
        self.block_keys = [problem.location for problem in self.problems]
        self.completion_service = CompletionService(self.user, self.course_key)

        # Proper completions for the given runtime
        BlockCompletion.objects.submit_completion(
            user=self.user,
            block_key=self.html.location,
            completion=1.0,
        )

        for idx, block_key in enumerate(self.block_keys[0:3]):
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=block_key,
                completion=1.0 - (0.2 * idx),
            )

        # Wrong user
        for idx, block_key in enumerate(self.block_keys[2:]):
            BlockCompletion.objects.submit_completion(
                user=self.other_user,
                block_key=block_key,
                completion=0.9 - (0.2 * idx),
            )

        # Wrong course
        BlockCompletion.objects.submit_completion(
            user=self.user,
            block_key=self.other_course_key.make_usage_key('problem', 'other'),
            completion=0.75,
        )

    def _bind_course_block(self, block):
        """
        Bind a block (part of self.course) so we can access student-specific data.
        """
        prepare_block_runtime(block.runtime, course_id=block.location.course_key)
        block.runtime._services.update({'library_tools': LegacyLibraryToolsService(self.store, self.user.id)})  # pylint: disable=protected-access

        def get_block(descriptor):
            """Mocks module_system get_block_for_descriptor function"""
            prepare_block_runtime(descriptor.runtime, course_id=block.location.course_key)
            descriptor.runtime.get_block_for_descriptor = get_block
            descriptor.bind_for_student(self.user.id)
            return descriptor

        block.runtime.get_block_for_descriptor = get_block

    def test_completion_service(self):
        # Only the completions for the user and course specified for the CompletionService
        # are returned.  Values are returned for all keys provided.
        assert self.completion_service.get_completions(self.block_keys) == {
            self.block_keys[0]: 1.0, self.block_keys[1]: 0.8,
            self.block_keys[2]: 0.6, self.block_keys[3]: 0.0,
            self.block_keys[4]: 0.0
        }

    @ddt.data(True, False)
    def test_enabled_honors_waffle_switch(self, enabled):
        self.override_waffle_switch(enabled)
        assert self.completion_service.completion_tracking_enabled() == enabled

    def test_vertical_completion(self):
        assert self.completion_service.vertical_is_complete(self.vertical) is False

        for block_key in self.block_keys:
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=block_key,
                completion=1.0
            )

        assert self.completion_service.vertical_is_complete(self.vertical) is True

    def test_vertical_partial_completion(self):
        block_keys_count = len(self.block_keys)
        for i in range(block_keys_count - 1):
            # Mark all the child blocks completed except the last one
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=self.block_keys[i],
                completion=1.0
            )

        assert self.completion_service.vertical_is_complete(self.vertical) is False

    def test_can_mark_block_complete_on_view(self):

        assert self.completion_service.can_mark_block_complete_on_view(self.course) is False
        assert self.completion_service.can_mark_block_complete_on_view(self.chapter) is False
        assert self.completion_service.can_mark_block_complete_on_view(self.sequence) is False
        assert self.completion_service.can_mark_block_complete_on_view(self.vertical) is False
        assert self.completion_service.can_mark_block_complete_on_view(self.html) is True
        assert self.completion_service.can_mark_block_complete_on_view(self.problem) is False

    @override_settings(FEATURES={**settings.FEATURES, 'MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW': True})
    def test_can_mark_library_content_complete_on_view(self):
        library = LibraryFactory.create(modulestore=self.store)
        lib_vertical = BlockFactory.create(parent=self.sequence, category='vertical', publish_item=False)
        library_content_block = BlockFactory.create(
            parent=lib_vertical,
            category='library_content',
            max_count=1,
            source_library_id=str(library.location.library_key),
            user_id=self.user.id,
        )
        self.assertTrue(self.completion_service.can_mark_block_complete_on_view(library_content_block))  # noqa: PT009

    def test_vertical_completion_with_library_content(self):
        library = LibraryFactory.create(modulestore=self.store)
        BlockFactory.create(parent=library, category='problem', publish_item=False, user_id=self.user.id)
        BlockFactory.create(parent=library, category='problem', publish_item=False, user_id=self.user.id)
        BlockFactory.create(parent=library, category='problem', publish_item=False, user_id=self.user.id)
        # Create a new vertical to hold the library content block
        # It is very important that we use parent_location=self.sequence.location (and not parent=self.sequence), since
        # sequence is a class attribute and passing it by value will update its .children=[] which will then leak into
        # other tests and cause errors if the children no longer exist.
        lib_vertical = BlockFactory.create(
            parent_location=self.sequence.location,
            category='vertical',
            publish_item=False,
        )
        library_content_block = BlockFactory.create(
            parent=lib_vertical,
            category='library_content',
            max_count=1,
            source_library_id=str(library.location.library_key),
            user_id=self.user.id,
        )
        # Library Content Block needs its children to be completed.
        self.assertFalse(self.completion_service.can_mark_block_complete_on_view(library_content_block))  # noqa: PT009

        # Dirty hack:
        # sync_from_library isn't *supposed* to work inside LMS, but this test case was written
        # before we made that rule. So, we need to trick this part of  test case into thinking that it's
        # running inside CMS instead of LMS. Please don't copy-paste this trick to any other LMS tests :)
        # Long-term solution: https://github.com/openedx/edx-platform/issues/33545
        with override_settings(ROOT_URLCONF="cms.urls"):
            library_content_block.sync_from_library()
            lib_vertical = self.store.get_item(lib_vertical.location)

        self._bind_course_block(lib_vertical)
        # We need to refetch the library_content_block to retrieve the
        # fresh version from the call to get_item for lib_vertical
        library_content_block = [child for child in lib_vertical.get_children()
                                 if child.scope_ids.block_type == 'library_content'][0]

        ## Ensure the library_content_block is properly set up
        # This is needed so we can call get_child_blocks
        self._bind_course_block(library_content_block)
        # Make sure the runtime knows that the block's children vary per-user:
        assert library_content_block.has_dynamic_children()
        assert len(library_content_block.children) == 3
        # Check how many children each user will see:
        assert len(library_content_block.get_child_blocks()) == 1

        # No problems are complete yet
        assert not self.completion_service.vertical_is_complete(lib_vertical)

        for block_key in self.block_keys:
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=block_key,
                completion=1.0
            )
        # Library content problems aren't complete yet
        assert not self.completion_service.vertical_is_complete(lib_vertical)

        for child in library_content_block.get_child_blocks():
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=child.scope_ids.usage_id,
                completion=1.0
            )
        assert self.completion_service.vertical_is_complete(lib_vertical)

    def test_vertical_completion_with_nested_children(self):
        # Create a new vertical.
        # It is very important that we use parent_location=self.sequence.location (and not parent=self.sequence), since
        # sequence is a class attribute and passing it by value will update its .children=[] which will then leak into
        # other tests and cause errors if the children no longer exist.
        parent_vertical = BlockFactory(parent_location=self.sequence.location, category='vertical')
        extra_vertical = BlockFactory(parent=parent_vertical, category='vertical')
        problem = BlockFactory(parent=extra_vertical, category='problem')
        parent_vertical = self.store.get_item(parent_vertical.location)

        # Nothing is complete
        assert not self.completion_service.vertical_is_complete(parent_vertical)

        for block_key in self.block_keys:
            BlockCompletion.objects.submit_completion(
                user=self.user,
                block_key=block_key,
                completion=1.0
            )
        # The nested child isn't complete yet
        assert not self.completion_service.vertical_is_complete(parent_vertical)

        BlockCompletion.objects.submit_completion(
            user=self.user,
            block_key=problem.location,
            completion=1.0
        )
        assert self.completion_service.vertical_is_complete(parent_vertical)
