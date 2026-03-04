"""
Tests for discussions signal handlers
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import ddt
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from openedx_events.learning.data import CourseDiscussionConfigurationData, DiscussionTopicContext
from openedx.core.djangoapps.discussions.handlers import update_course_discussion_config
from openedx.core.djangoapps.discussions.models import DiscussionTopicLink, DiscussionsConfiguration


@ddt.ddt
@patch("openedx.core.djangoapps.discussions.handlers.modulestore")
class UpdateCourseDiscussionsConfigTestCase(TestCase):
    """
    Tests for the discussion config update handler.
    """

    def setUp(self) -> None:
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:test+test+test")
        self.discussion_config = DiscussionsConfiguration.objects.create(
            context_key=self.course_key,
            provider_type="openedx",
        )

    def create_contexts(self, general=0, unit=0):
        """
        Create context data for topics
        """
        for idx in range(general):
            yield DiscussionTopicContext(
                title=f"General topic {idx}",
                external_id=f"general-topic-{idx}",
            )
        for idx in range(unit):
            yield DiscussionTopicContext(
                title=f"Unit {idx}",
                usage_key=self.course_key.make_usage_key("vertical", f"unit-{idx}"),
                context={
                    "section": f"Section {idx}",
                    "subsection": f"Subsection {idx}",
                    "unit": f"Unit {idx}",
                },
            )

    def test_configuration_for_new_course(self, mock_modulestore):
        """
        Test that a new course gets a new discussion configuration object
        """
        new_key = CourseKey.from_string("course-v1:test+test+test2")
        config_data = CourseDiscussionConfigurationData(
            course_key=new_key,
            provider_type="openedx",
        )
        assert not DiscussionsConfiguration.objects.filter(context_key=new_key).exists()
        update_course_discussion_config(config_data)
        assert DiscussionsConfiguration.objects.filter(context_key=new_key).exists()
        db_config = DiscussionsConfiguration.objects.get(context_key=new_key)
        assert db_config.provider_type == "openedx"

    def test_creating_new_links(self, mock_modulestore):
        """
        Test that new links are created in the db when they are added in the config.
        """
        contexts = list(self.create_contexts(general=2, unit=3))
        config_data = CourseDiscussionConfigurationData(
            course_key=self.course_key,
            provider_type="openedx",
            contexts=contexts,
        )
        update_course_discussion_config(config_data)
        topic_links = DiscussionTopicLink.objects.filter(context_key=self.course_key)
        assert topic_links.count() == len(contexts)  # 2 general + 3 units

    def test_updating_existing_links(self, mock_modulestore):
        """
        Test that updating existing links works as expected.
        """
        contexts = list(self.create_contexts(general=2, unit=3))
        config_data = CourseDiscussionConfigurationData(
            course_key=self.course_key,
            provider_type="openedx",
            contexts=contexts,
        )
        existing_external_id = uuid4()
        existing_topic_link = DiscussionTopicLink.objects.create(
            context_key=self.course_key,
            usage_key=self.course_key.make_usage_key("vertical", "unit-2"),
            title="Old title",
            provider_id="openedx",
            external_id=existing_external_id,
            enabled_in_context=True,
        )
        update_course_discussion_config(config_data)
        existing_topic_link.refresh_from_db()
        # Make sure that the title changes, but nothing else
        assert existing_topic_link.title == "Unit 2"
        assert existing_topic_link.provider_id == "openedx"
        assert existing_topic_link.external_id == str(existing_external_id)
        assert existing_topic_link.enabled_in_context
        assert existing_topic_link.context == {
            "section": "Section 2",
            "subsection": "Subsection 2",
            "unit": "Unit 2",
        }

    @patch.dict(
        "openedx.core.djangoapps.discussions.models.AVAILABLE_PROVIDER_MAP",
        {"test": {"supports_in_context_discussions": True}},
    )
    def test_provider_change(self, mock_modulestore):
        """
        Test that changing providers creates new links, and doesn't update existing ones.
        """
        contexts = list(self.create_contexts(general=2, unit=3))
        config_data = CourseDiscussionConfigurationData(
            course_key=self.course_key,
            provider_type="test",
            contexts=contexts,
        )
        existing_external_id = uuid4()
        existing_usage_key = self.course_key.make_usage_key("vertical", "unit-2")
        existing_topic_link = DiscussionTopicLink.objects.create(
            context_key=self.course_key,
            usage_key=existing_usage_key,
            title="Old title",
            provider_id="openedx",
            external_id=existing_external_id,
            enabled_in_context=True,
        )
        update_course_discussion_config(config_data)
        existing_topic_link.refresh_from_db()
        # If the provider has changed, new links should be created, the existing on remains the same
        assert existing_topic_link.title == "Old title"
        assert existing_topic_link.provider_id == "openedx"
        assert existing_topic_link.external_id == str(existing_external_id)
        assert existing_topic_link.enabled_in_context
        new_link = DiscussionTopicLink.objects.get(
            context_key=self.course_key,
            provider_id="test",
            usage_key=existing_usage_key,
        )
        assert new_link.title == "Unit 2"
        # The new link will get a new id
        assert new_link.external_id != str(existing_external_id)

    def test_enabled_units_change(self, mock_modulestore):
        """
        Test that when enabled units change, old unit links are disabled in context.
        """
        contexts = list(self.create_contexts(general=2, unit=3))
        config_data = CourseDiscussionConfigurationData(
            course_key=self.course_key,
            provider_type="openedx",
            contexts=contexts,
        )
        existing_external_id = uuid4()
        existing_usage_key = self.course_key.make_usage_key("vertical", "unit-10")
        existing_topic_link = DiscussionTopicLink.objects.create(
            context_key=self.course_key,
            usage_key=existing_usage_key,
            title="Unit 10",
            provider_id="openedx",
            external_id=existing_external_id,
            enabled_in_context=True,
            context={
                "section": "Section 10",
                "subsection": "Subsection 10",
                "unit": "Unit 10",
            },
        )
        existing_topic_link_2 = DiscussionTopicLink.objects.create(
            context_key=self.course_key,
            usage_key=existing_usage_key,
            title="Unit 11",
            provider_id="openedx",
            external_id=existing_external_id,
            enabled_in_context=True,
        )
        update_course_discussion_config(config_data)
        existing_topic_link.refresh_from_db()
        existing_topic_link_2.refresh_from_db()
        # If the unit has an existing link but is disabled or removed
        assert not existing_topic_link.enabled_in_context
        assert not existing_topic_link_2.enabled_in_context
        # If a unit has been removed, its title will be updated to clarify where it used to be in the course.
        assert existing_topic_link.title == "Section 10|Subsection 10|Unit 10"
        # If there is no stored context, then continue using the Unit name.
        assert existing_topic_link_2.title == "Unit 11"


def _make_mock_tab(tab_id, is_hidden=False):
    """Helper to create a mock course tab."""
    tab = MagicMock()
    tab.tab_id = tab_id
    tab.is_hidden = is_hidden
    return tab


def _mock_modulestore_with_tabs(course_key, tabs):
    """
    Return a mock modulestore whose get_course returns a course with the given tabs.
    The mock supports the branch_setting context-manager protocol.
    """
    mock_course = MagicMock()
    mock_course.tabs = tabs

    store = MagicMock()
    store.get_course.return_value = mock_course
    store.branch_setting.return_value.__enter__ = MagicMock(return_value=store)
    store.branch_setting.return_value.__exit__ = MagicMock(return_value=False)
    return store


@patch("openedx.core.djangoapps.discussions.handlers.modulestore")
class TabStateSyncTestCase(TestCase):
    """
    Tests that DiscussionsConfiguration.enabled is synced from the discussion
    tab's is_hidden state in the modulestore.
    """

    def setUp(self):
        super().setUp()
        self.course_key = CourseKey.from_string("course-v1:test+test+test")

    def _build_config_data(self, course_key=None):
        return CourseDiscussionConfigurationData(
            course_key=course_key or self.course_key,
            provider_type="openedx",
        )

    # -- New configuration creation (no existing DiscussionsConfiguration) --

    def test_new_config_enabled_when_tab_visible(self, mock_modulestore):
        """
        When creating a DiscussionsConfiguration for a brand-new course whose
        discussion tab is visible (is_hidden=False), enabled should be True.
        """
        new_key = CourseKey.from_string("course-v1:test+test+new_visible")
        tabs = [
            _make_mock_tab("courseware"),
            _make_mock_tab("discussion", is_hidden=False),
        ]
        mock_modulestore.return_value = _mock_modulestore_with_tabs(new_key, tabs)

        update_course_discussion_config(self._build_config_data(new_key))

        config = DiscussionsConfiguration.objects.get(context_key=new_key)
        assert config.enabled is True

    def test_new_config_disabled_when_tab_hidden(self, mock_modulestore):
        """
        When creating a DiscussionsConfiguration for a brand-new course whose
        discussion tab is hidden (is_hidden=True), enabled should be False.
        """
        new_key = CourseKey.from_string("course-v1:test+test+new_hidden")
        tabs = [
            _make_mock_tab("courseware"),
            _make_mock_tab("discussion", is_hidden=True),
        ]
        mock_modulestore.return_value = _mock_modulestore_with_tabs(new_key, tabs)

        update_course_discussion_config(self._build_config_data(new_key))

        config = DiscussionsConfiguration.objects.get(context_key=new_key)
        assert config.enabled is False

    # -- Existing configuration update (import / rerun scenario) --

    def test_existing_config_updated_to_disabled_on_import(self, mock_modulestore):
        """
        Simulates importing a course with a hidden discussion tab into a course
        that already has DiscussionsConfiguration(enabled=True). After publish,
        enabled should become False to match the imported tab state.
        """
        DiscussionsConfiguration.objects.create(
            context_key=self.course_key,
            provider_type="openedx",
            enabled=True,
        )
        tabs = [
            _make_mock_tab("courseware"),
            _make_mock_tab("discussion", is_hidden=True),
        ]
        mock_modulestore.return_value = _mock_modulestore_with_tabs(self.course_key, tabs)

        update_course_discussion_config(self._build_config_data())

        config = DiscussionsConfiguration.objects.get(context_key=self.course_key)
        assert config.enabled is False

    def test_existing_config_updated_to_enabled_on_import(self, mock_modulestore):
        """
        If an existing config has enabled=False and the imported course has a
        visible discussion tab (is_hidden=False), enabled should be set to True.
        """
        DiscussionsConfiguration.objects.create(
            context_key=self.course_key,
            provider_type="openedx",
            enabled=False,
        )
        tabs = [
            _make_mock_tab("courseware"),
            _make_mock_tab("discussion", is_hidden=False),
        ]
        mock_modulestore.return_value = _mock_modulestore_with_tabs(self.course_key, tabs)

        update_course_discussion_config(self._build_config_data())

        config = DiscussionsConfiguration.objects.get(context_key=self.course_key)
        assert config.enabled is True

    def test_modulestore_failure_defaults_to_enabled(self, mock_modulestore):
        """
        If the modulestore read throws an exception, enabled should default to True
        so discussion isn't accidentally disabled.
        """
        new_key = CourseKey.from_string("course-v1:test+test+ms_fail")
        mock_modulestore.return_value.branch_setting.side_effect = Exception("boom")

        update_course_discussion_config(self._build_config_data(new_key))

        config = DiscussionsConfiguration.objects.get(context_key=new_key)
        assert config.enabled is True

