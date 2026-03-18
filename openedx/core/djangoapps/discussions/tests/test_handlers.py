"""
Tests for discussions signal handlers
"""
from unittest.mock import patch
from uuid import uuid4

import ddt
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey

from openedx_events.learning.data import CourseDiscussionConfigurationData, DiscussionTopicContext
from openedx.core.djangoapps.discussions.handlers import update_course_discussion_config
from openedx.core.djangoapps.discussions.models import DiscussionTopicLink, DiscussionsConfiguration


@ddt.ddt
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

    def test_configuration_for_new_course(self):
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

    def test_creating_new_links(self):
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

    def test_updating_existing_links(self):
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
    def test_provider_change(self):
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

    def test_enabled_units_change(self):
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
