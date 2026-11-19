"""
Tests for the incremental assessment load: descendant counting, shell eligibility, the
batch endpoint's access boundary, and VerticalBlock's eager-window rendering.
"""
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase, override_settings
from opaque_keys.edx.locator import CourseLocator

from lms.djangoapps.courseware.block_render import (
    _allowed_child_usage_keys,
    _lazy_child_student_view_context,
    _load_unbound_child_descriptors,
    count_renderable_descendants,
    should_use_incremental_load,
)
from xmodule.modulestore.exceptions import ItemNotFoundError

COURSE_KEY = CourseLocator('org', 'course', 'run')


def _block(block_type, children=None, dynamic=False, max_count=None, pool=0):
    """
    Build a stand-in XBlock with just the attributes the helpers under test read.
    """
    block = Mock()
    block.location.block_type = block_type
    block.location.course_key = COURSE_KEY
    block.has_dynamic_children.return_value = dynamic
    block.get_children.return_value = children or []
    if dynamic:
        block.max_count = max_count
        block.children = [Mock() for _ in range(pool)]
    return block


class CountRenderableDescendantsTests(TestCase):
    """
    Tests for count_renderable_descendants.
    """

    def test_counts_problems_in_a_flat_unit(self):
        vertical = _block('vertical', children=[_block('problem'), _block('problem')])
        assert count_renderable_descendants(vertical) == 2

    def test_ignores_cheap_leaf_types(self):
        # A unit full of short HTML renders quickly and should not be split up.
        vertical = _block('vertical', children=[_block('html'), _block('video'), _block('problem')])
        assert count_renderable_descendants(vertical) == 1

    def test_randomized_bank_counts_what_the_learner_sees(self):
        # This is the shape that caused the incident: a large pool, one problem shown.
        bank = _block('library_content', dynamic=True, max_count=1, pool=40)
        vertical = _block('vertical', children=[bank])
        assert count_renderable_descendants(vertical) == 1

    def test_many_single_selection_banks_add_up(self):
        banks = [_block('library_content', dynamic=True, max_count=1, pool=4) for _ in range(90)]
        vertical = _block('vertical', children=banks)
        assert count_renderable_descendants(vertical) == 90

    def test_max_count_of_minus_one_counts_whole_pool(self):
        # -1 means "show all of them", the most expensive configuration there is.
        bank = _block('library_content', dynamic=True, max_count=-1, pool=30)
        assert count_renderable_descendants(bank) == 30

    def test_max_count_above_pool_is_capped_by_pool(self):
        bank = _block('library_content', dynamic=True, max_count=50, pool=6)
        assert count_renderable_descendants(bank) == 6

    def test_depth_limit_stops_runaway_recursion(self):
        deep = _block('problem')
        for _unused in range(10):
            deep = _block('vertical', children=[deep])
        assert count_renderable_descendants(deep) >= 0


class ShouldUseIncrementalLoadTests(TestCase):
    """
    Tests for should_use_incremental_load. All conditions must hold.
    """

    def setUp(self):
        super().setUp()
        self.request = Mock()
        self.large_vertical = _block('vertical', children=[_block('problem') for _ in range(30)])

    def _check(self, block, flag_enabled=True, mobile=False):
        """
        Call should_use_incremental_load with the flag and mobile-app checks mocked.
        """
        with patch(
            'lms.djangoapps.courseware.block_render.incremental_assessment_load_is_enabled',
            return_value=flag_enabled,
        ), patch(
            'lms.djangoapps.courseware.block_render.is_request_from_mobile_app',
            return_value=mobile,
        ):
            return should_use_incremental_load(self.request, block)

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=20)
    def test_large_vertical_with_flag_on(self):
        assert self._check(self.large_vertical) is True

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=20)
    def test_flag_off_is_the_kill_switch(self):
        assert self._check(self.large_vertical, flag_enabled=False) is False

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=20)
    def test_small_vertical_is_untouched_even_when_flagged(self):
        small = _block('vertical', children=[_block('problem') for _ in range(5)])
        assert self._check(small) is False

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=20)
    def test_non_vertical_blocks_never_shell(self):
        assert self._check(_block('sequential', children=[_block('problem')] * 30)) is False
        assert self._check(_block('problem')) is False

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=20)
    def test_mobile_apps_are_excluded(self):
        assert self._check(self.large_vertical, mobile=True) is False

    @override_settings(INCREMENTAL_LOAD_PROBLEM_THRESHOLD=100)
    def test_threshold_is_respected(self):
        assert self._check(self.large_vertical) is False


class AllowedChildUsageKeysTests(TestCase):
    """
    Tests for the batch endpoint's access boundary.
    """

    def test_static_parent_exposes_its_children(self):
        children = [_block('problem'), _block('problem')]
        parent = _block('vertical', children=children)
        assert _allowed_child_usage_keys(parent) == {child.location for child in children}

    def test_static_parent_uses_content_keys_without_instantiating_children(self):
        children = [_block('problem'), _block('problem')]
        parent = _block('vertical')
        parent.children = [child.location for child in children]
        parent.get_children.side_effect = AssertionError('static children must not be instantiated')

        assert _allowed_child_usage_keys(parent) == {child.location for child in children}

    def test_dynamic_parent_exposes_only_the_learner_selection(self):
        # A learner must not be able to pull problems the bank did not assign them.
        selected = _block('problem')
        unselected = _block('problem')
        parent = _block('library_content', children=[selected, unselected], dynamic=True, max_count=1, pool=2)
        parent.get_child_blocks.return_value = [selected]

        allowed = _allowed_child_usage_keys(parent)
        assert allowed == {selected.location}
        assert unselected.location not in allowed

    def test_none_children_are_skipped(self):
        good = _block('problem')
        parent = _block('vertical', children=[good, None])
        assert _allowed_child_usage_keys(parent) == {good.location}


class UnboundChildDescriptorTests(TestCase):
    """Tests for resolving lazy children independently of a parent instance cache."""

    def test_resolves_requested_keys_without_using_parent_children(self):
        first_key = COURSE_KEY.make_usage_key('problem', 'first')
        second_key = COURSE_KEY.make_usage_key('problem', 'second')
        first_descriptor = Mock()
        second_descriptor = Mock()
        store = Mock()
        store.get_item.side_effect = [first_descriptor, second_descriptor]

        with patch('lms.djangoapps.courseware.block_render.modulestore', return_value=store):
            descriptors, missing = _load_unbound_child_descriptors([first_key, second_key])

        assert list(descriptors) == [first_key, second_key]
        assert descriptors[first_key] is first_descriptor
        assert descriptors[second_key] is second_descriptor
        assert not missing

    def test_reports_missing_descriptor_without_aborting_the_batch(self):
        existing_key = COURSE_KEY.make_usage_key('problem', 'existing')
        missing_key = COURSE_KEY.make_usage_key('problem', 'missing')
        existing_descriptor = Mock()
        store = Mock()
        store.get_item.side_effect = [existing_descriptor, ItemNotFoundError()]

        with patch('lms.djangoapps.courseware.block_render.modulestore', return_value=store):
            descriptors, missing = _load_unbound_child_descriptors([existing_key, missing_key])

        assert descriptors == {existing_key: existing_descriptor}
        assert missing == [missing_key]


class LazyChildStudentViewContextTests(TestCase):
    """
    Tests for the child render context used by incremental batch rendering.
    """

    def test_context_matches_unit_child_render_flags(self):
        request = RequestFactory().get(
            '/',
            {
                'recheck_access': '1',
                'show_bookmark_button': '0',
                'show_title': '0',
                'view': 'student_view',
            }
        )

        context = _lazy_child_student_view_context(request)

        assert context['child_of_vertical'] is True
        assert context['show_bookmark_button'] is False
        assert context['show_title'] is False
        assert context['is_mobile_app'] is False
        assert context['view'] == 'student_view'


class VerticalEagerWindowRenderTests(TestCase):
    """
    Tests that VerticalBlock renders only the eager window and reports the rest as
    placeholders.
    """

    def _render_context(self, eager_count, child_count=8):
        """
        Run VerticalBlock._student_or_public_view with stubbed children and return the
        fragment context it handed to the template.
        """
        from xmodule.vertical_block import VerticalBlock

        children = []
        for index in range(child_count):
            child = Mock()
            child.location = f'block-v1:org+course+run+type@problem+block@p{index}'
            rendered = Mock()
            rendered.content = f'<div>problem {index}</div>'
            rendered.resources = []
            child.render.return_value = rendered
            children.append(child)

        # Not Mock(spec=VerticalBlock): `runtime` is set dynamically per-instance by the
        # XBlock runtime binding, not declared on the class, so spec= would reject it.
        block = Mock()
        block.get_children.return_value = children
        block.is_block_complete_for_assignments.return_value = None
        block.due = None
        block.location = 'block-v1:org+course+run+type@vertical+block@v1'
        block.display_name_with_default = 'Unit'

        captured = {}

        def capture_render(_template, fragment_context):
            captured.update(fragment_context)
            return ''

        mako = Mock()
        mako.render_lms_template.side_effect = capture_render
        completion = Mock()
        completion.completion_tracking_enabled.return_value = False
        user_service = Mock()
        user_service.get_current_user.return_value.opt_attrs = {'edx-platform.username': 'learner'}

        def service(_self, name):
            return {
                'mako': mako,
                'completion': completion,
                'bookmarks': Mock(is_bookmarked=Mock(return_value=False)),
                'user': user_service,
                'call_to_action': None,
            }.get(name)

        block.runtime.service.side_effect = service

        context = {'incremental_load_eager_count': eager_count} if eager_count is not None else {}
        with patch('xmodule.vertical_block.add_webpack_js_to_fragment'):
            VerticalBlock._student_or_public_view(  # pylint: disable=protected-access
                block, context, 'student_view'
            )
        return captured, children

    def test_renders_only_the_eager_window(self):
        captured, children = self._render_context(eager_count=5, child_count=8)

        assert len(captured['items']) == 5
        assert len(captured['lazy_children']) == 3
        for child in children[:5]:
            child.render.assert_called_once()
        for child in children[5:]:
            child.render.assert_not_called()

    def test_placeholders_are_reported_in_order(self):
        captured, children = self._render_context(eager_count=2, child_count=6)
        assert captured['lazy_children'] == [str(child.location) for child in children[2:]]
        assert captured['parent_usage_key'] is not None

    def test_without_eager_count_every_child_renders(self):
        # The default path: flag off, no shell, behavior identical to before this feature.
        captured, children = self._render_context(eager_count=None, child_count=6)
        assert len(captured['items']) == 6
        assert captured['lazy_children'] == []
        assert captured['parent_usage_key'] is None
        for child in children:
            child.render.assert_called_once()

    def test_eager_count_at_or_above_child_count_renders_everything(self):
        captured, children = self._render_context(eager_count=10, child_count=6)
        assert len(captured['items']) == 6
        assert captured['lazy_children'] == []
