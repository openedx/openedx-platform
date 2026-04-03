"""
Tests for discussions tasks.
"""
from unittest import mock

import ddt
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_events.learning.data import DiscussionTopicContext

from openedx.core.djangoapps.discussions.config.waffle import ENABLE_NEW_STRUCTURE_DISCUSSIONS
from openedx.core.djangoapps.discussions.models import DiscussionsConfiguration, Provider
from openedx.core.djangoapps.discussions.tasks import (
    update_discussions_settings_from_course,
    update_unit_discussion_state_from_discussion_blocks,
)
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class DiscussionConfigUpdateMixin:
    """
    Mixin for common methods used to update course discussion configuration.
    """

    def update_course_field(self, **update):
        """
        Update the test course using provided parameters.
        """
        for key, value in update.items():
            setattr(self.course, key, value)
        self.store.update_item(self.course, self.user.id)

    def update_discussions_settings(self, settings):
        """
        Update course discussion settings based on the provided discussion settings.
        """
        discussion_config = DiscussionsConfiguration.get(self.course.id)
        for key, value in settings.items():
            key = "provider_type" if key == "provider" else key
            if value is not None:
                setattr(discussion_config, key, value)
        discussion_config.save()
        self.course.discussions_settings.update(settings)
        self.store.update_item(self.course, self.user.id)

    def assert_discussion_settings(self, **settings):
        """
        Assert that the provided settings have the provided values in the course's discussion settings.
        """
        course = self.store.get_course(self.course.id)
        discussion_config = DiscussionsConfiguration.get(self.course.id)
        for key, value in settings.items():
            assert course.discussions_settings.get(key, None) == value
            key = "provider_type" if key == "provider" else key
            assert getattr(discussion_config, key) == value


@ddt.ddt
class UpdateDiscussionsSettingsFromCourseTestCase(ModuleStoreTestCase, DiscussionConfigUpdateMixin):
    """
    Tests for the discussions settings update tasks
    """

    def setUp(self):
        super().setUp()
        self.course = course = CourseFactory.create(
            discussions_settings={
                "provider": Provider.OPEN_EDX
            }
        )
        self.course_key = course_key = self.course.id
        with self.store.bulk_operations(course_key):
            self.section = BlockFactory.create(parent=course, category="chapter", display_name="Section")
            self.sequence = BlockFactory.create(parent=self.section, category="sequential", display_name="Sequence")
            self.unit = BlockFactory.create(parent=self.sequence, category="vertical", display_name="Unit")
            BlockFactory.create(
                parent=self.sequence,
                category="vertical",
                display_name="Discussable Unit",
                discussion_enabled=True,
            )
            BlockFactory.create(
                parent=self.sequence,
                category="vertical",
                display_name="Non-Discussable Unit",
                discussion_enabled=False,
            )
            BlockFactory.create(parent=self.unit, category="html", display_name="An HTML Block")
            graded_sequence = BlockFactory.create(
                parent=self.section,
                category="sequential",
                display_name="Graded Sequence",
                graded=True,
            )
            graded_unit = BlockFactory.create(
                parent=graded_sequence,
                category="vertical",
                display_name="Graded Unit",
            )
            BlockFactory.create(
                parent=graded_sequence,
                category="vertical",
                display_name="Discussable Graded Unit",
                discussion_enabled=True,
            )
            BlockFactory.create(
                parent=graded_sequence,
                category="vertical",
                display_name="Non-Discussable Graded Unit",
                discussion_enabled=False,
            )
            BlockFactory.create(
                parent=graded_unit,
                category="html",
                display_name="Graded HTML Block",
            )
        discussion_config = DiscussionsConfiguration.get(course_key)
        discussion_config.provider_type = Provider.OPEN_EDX
        discussion_config.save()

    def test_default(self):
        """
        Test that the course defaults.
        """
        config_data = update_discussions_settings_from_course(self.course.id)
        assert config_data.course_key == self.course.id
        assert config_data.enable_graded_units is False
        assert config_data.unit_level_visibility is True
        assert config_data.provider_type is not None
        assert config_data.plugin_configuration == {}
        assert {context.title for context in config_data.contexts} == {"General", "Unit", "Discussable Unit"}

    def test_topics_contexts(self):
        """
        Test the handling of topics.
        """
        self.update_course_field(discussion_topics={
            "General": {"id": "general-topic"},
            "Test Topic": {"id": "test-topic"},
        })
        config_data = update_discussions_settings_from_course(self.course.id)
        assert len(config_data.contexts) == 4
        assert DiscussionTopicContext(
            title="General",
            external_id="general-topic",
            ordering=0,
        ) in config_data.contexts
        assert DiscussionTopicContext(
            title="Test Topic",
            external_id="test-topic",
            ordering=1,
        ) in config_data.contexts
        assert DiscussionTopicContext(
            title='Unit',
            usage_key=self.unit.location,
            group_id=None,
            external_id=None,
            ordering=100,
            context={'section': 'Section', 'subsection': 'Sequence', 'unit': 'Unit'}
        ) in config_data.contexts

    @ddt.data(
        ({}, 3, {"Unit", "Discussable Unit"},
         {"Graded Unit", "Non-Discussable Unit", "Discussable Graded Unit", "Non-Discussable Graded Unit"}),
        ({"enable_in_context": False}, 1, set(), {"Unit", "Graded Unit"}),
        ({"unit_level_visibility": False, "enable_graded_units": False}, 3,
         {"Unit", "Discussable Unit"},
         {"Graded Unit"}),
        ({"unit_level_visibility": False, "enable_graded_units": True}, 5,
         {"Unit", "Graded Unit", "Discussable Graded Unit"}, set()),
        ({"enable_graded_units": True}, 5,
         {"Discussable Unit", "Discussable Graded Unit", "Graded Unit"},
         {"Non-Discussable Unit", "Non-Discussable Graded Unit"}),
    )
    @ddt.unpack
    def test_custom_discussion_settings(self, settings, context_count, present_units, missing_units):
        """
        Test different combinations of settings and their impact on the units that are returned.
        """
        self.update_discussions_settings(settings)
        config_data = update_discussions_settings_from_course(self.course.id)
        assert len(config_data.contexts) == context_count
        units_in_config = {context.title for context in config_data.contexts}
        assert present_units <= units_in_config
        assert not missing_units & units_in_config

    @ddt.data(
        # (tab_is_hidden, expected_enabled)
        (True, False),   # import with hidden tab → disabled
        (False, True),   # import with visible tab → enabled
    )
    @ddt.unpack
    def test_config_data_enabled_from_discussion_tab(self, tab_is_hidden, expected_enabled):
        """
        update_discussions_settings_from_course should set config_data.enabled
        based on the discussion tab's is_hidden state.
        """
        course = self.store.get_course(self.course.id)
        for tab in course.tabs:
            if getattr(tab, 'tab_id', None) == 'discussion':
                tab['is_hidden'] = tab_is_hidden
                break
        self.store.update_item(course, self.user.id)

        config_data = update_discussions_settings_from_course(self.course.id)

        assert config_data.enabled is expected_enabled


@ddt.ddt
class MigrateUnitDiscussionStateFromXBlockTestCase(ModuleStoreTestCase, DiscussionConfigUpdateMixin):
    """
    Tests for the discussions settings update tasks
    """

    def setUp(self):
        super().setUp()
        self.course = course = CourseFactory.create()
        self.course_key = course_key = self.course.id
        with self.store.bulk_operations(course_key):
            section = BlockFactory.create(
                parent=course, category="chapter", display_name="Section"
            )
            sequence = BlockFactory.create(
                parent=section, category="sequential", display_name="Sequence"
            )
            self.unit_discussible = unit_discussible = BlockFactory.create(
                parent=sequence,
                category="vertical",
                display_name="Discussable Unit",
            )
            unit_non_discussible = BlockFactory.create(
                parent=sequence,
                category="vertical",
                display_name="Non-Discussable Unit",
                discussion_enabled=False,
            )
            graded_sequence = BlockFactory.create(
                parent=section,
                category="sequential",
                display_name="Graded Sequence",
                graded=True,
            )
            self.graded_unit_discussible = graded_unit_discussible = BlockFactory.create(
                parent=graded_sequence,
                category="vertical",
                display_name="Discussable Graded Unit",
            )
            graded_unit_non_discussible = BlockFactory.create(
                parent=graded_sequence,
                category="vertical",
                display_name="Non-Discussable Graded Unit",
            )
        self.discussible = {unit_discussible.display_name, graded_unit_discussible.display_name}
        self.non_discussible = {
            unit_non_discussible.display_name,
            graded_unit_non_discussible.display_name,
        }
        self.graded = {
            graded_unit_discussible.display_name,
            graded_unit_non_discussible.display_name,
        }

    def add_discussion_block(self, units):
        """
        Add a discussion block to the specified units.
        """
        for unit in units:
            BlockFactory.create(
                parent=unit,
                category='discussion',
                discussion_id=f'id-{unit.location}',
                discussion_target=f'Target {unit.display_name}',
                discussion_category=f'Category {unit.display_name}',
            )

    @mock.patch('openedx.core.djangoapps.discussions.tasks.get_accessible_discussion_xblocks_by_course_id')
    def test_course_not_using_legacy(self, mock_get_discussion_blocks):
        self.update_discussions_settings({'provider': 'non-legacy'})
        update_unit_discussion_state_from_discussion_blocks(self.course.id, self.user.id)
        mock_get_discussion_blocks.assert_not_called()

    @mock.patch('openedx.core.djangoapps.discussions.tasks.get_accessible_discussion_xblocks_by_course_id')
    @ddt.data(None, 'legacy')
    def test_course_using_legacy(self, provider, mock_get_discussion_blocks):
        self.update_discussions_settings({'provider': provider})
        update_unit_discussion_state_from_discussion_blocks(self.course.id, self.user.id)
        mock_get_discussion_blocks.assert_called()

    def get_discussible_and_non_discussible_blocks(self):
        """
        Get a set of display names of the discussible and non-discussible blocks in a course.
        """
        discussible = {
            item.display_name
            for item in self.store.get_items(
                self.course.id,
                qualifiers={'block_type': 'vertical'},
                settings={'discussion_enabled': True},
            )
        }
        non_discussible = {
            item.display_name
            for item in self.store.get_items(
                self.course.id,
                qualifiers={'block_type': 'vertical'},
                settings={'discussion_enabled': False},
            )
        }
        return discussible, non_discussible

    def test_without_graded(self):
        self.add_discussion_block([self.unit_discussible])
        update_unit_discussion_state_from_discussion_blocks(self.course.id, self.user.id)
        discussible, non_discussible = self.get_discussible_and_non_discussible_blocks()
        # A discussion block was not added to a graded unit, so it shouldn't be in the set of discussible blocks
        assert discussible == (self.discussible - self.graded)
        assert non_discussible == (self.non_discussible | self.graded)
        self.assert_discussion_settings(enable_graded_units=False)

    @ddt.data(True, False)
    def test_with_graded(self, new_structure_enabled):
        self.add_discussion_block([self.unit_discussible, self.graded_unit_discussible])
        with override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=new_structure_enabled):
            update_unit_discussion_state_from_discussion_blocks(self.course.id, self.user.id)
            discussible, non_discussible = self.get_discussible_and_non_discussible_blocks()
            # A discussion block was not added to a graded unit, so it shouldn't be in the set of discussible blocks
            assert discussible == self.discussible
            assert non_discussible == self.non_discussible
            self.assert_discussion_settings(enable_graded_units=new_structure_enabled)

    @override_waffle_flag(ENABLE_NEW_STRUCTURE_DISCUSSIONS, active=True)
    def test_with_new_structure(self):
        update_unit_discussion_state_from_discussion_blocks(self.course.id, self.user.id)
        self.assert_discussion_settings(provider=Provider.OPEN_EDX)
        self.assert_discussion_settings(unit_level_visibility=True)
