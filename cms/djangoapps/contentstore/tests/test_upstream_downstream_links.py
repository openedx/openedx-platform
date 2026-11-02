"""
Tests for upstream downstream tracking links.
"""

from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import LibraryContainerLocator, LibraryUsageLocatorV2
from openedx_events.tests.utils import OpenEdxEventsTestMixin

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, ImmediateOnCommitMixin
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from ..models import ContainerLink, LearningContextLinksStatus, LearningContextLinksStatusChoices, ComponentLink


class BaseUpstreamLinksHelpers(TestCase):
    """
    Base class with helpers to create xblocks.
    """
    def _set_course_data(self, course):
        self.section = BlockFactory.create(parent=course, category="chapter", display_name="Section")  # pylint: disable=attribute-defined-outside-init
        self.sequence = BlockFactory.create(parent=self.section, category="sequential", display_name="Sequence")  # pylint: disable=attribute-defined-outside-init
        self.unit = BlockFactory.create(parent=self.sequence, category="vertical", display_name="Unit")  # pylint: disable=attribute-defined-outside-init

    def _create_block(self, num: int, category="html"):
        """
        Create xblock with random upstream key and version number.
        """
        random_upstream = LibraryUsageLocatorV2.from_string(
            f"lb:OpenedX:CSPROB2:{category}:{uuid4()}"
        )
        return random_upstream, BlockFactory.create(
            parent=self.unit,  # pylint: disable=attribute-defined-outside-init
            category=category,
            display_name=f"An {category} Block - {num}",
            upstream=str(random_upstream),
            upstream_version=num,
        )

    def _create_unit(self, num: int):
        """
        Create xblock with random upstream key and version number.
        """
        random_upstream = LibraryContainerLocator.from_string(
            f"lct:OpenedX:CSPROB2:unit:{uuid4()}"
        )
        return random_upstream, BlockFactory.create(
            parent=self.sequence,  # pylint: disable=attribute-defined-outside-init
            category='vertical',
            display_name=f"An unit Block - {num}",
            upstream=str(random_upstream),
            upstream_version=num,
        )

    def _create_unit_and_expected_container_link(self, course_key: str | CourseKey, num_blocks: int = 3):
        """
        Create unit xblock with random upstream key and version number.
        """
        data = []
        for i in range(num_blocks):
            upstream, block = self._create_unit(i + 1)
            data.append({
                "upstream_container": None,
                "downstream_context_key": course_key,
                "downstream_usage_key": block.usage_key,
                "upstream_container_key": upstream,
                "upstream_context_key": str(upstream.context_key),
                "version_synced": i + 1,
                "version_declined": None,
            })
        return data

    def _create_block_and_expected_links_data(self, course_key: str | CourseKey, num_blocks: int = 3):
        """
        Creates xblocks and its expected links data for given course_key
        """
        data = []
        for i in range(num_blocks):
            upstream, block = self._create_block(i + 1)
            data.append({
                "upstream_block": None,
                "downstream_context_key": course_key,
                "downstream_usage_key": block.usage_key,
                "upstream_usage_key": upstream,
                "upstream_context_key": str(upstream.context_key),
                "version_synced": i + 1,
                "version_declined": None,
            })
        return data

    def _compare_links(self, course_key, expected_component_links, expected_container_links):
        """
        Compares links for given course with passed expected list of dicts.
        """
        links = list(ComponentLink.objects.filter(downstream_context_key=course_key).values(
            'upstream_block',
            'upstream_usage_key',
            'upstream_context_key',
            'downstream_usage_key',
            'downstream_context_key',
            'version_synced',
            'version_declined',
        ))
        self.assertListEqual(links, expected_component_links)
        container_links = list(ContainerLink.objects.filter(downstream_context_key=course_key).values(
            'upstream_container',
            'upstream_container_key',
            'upstream_context_key',
            'downstream_usage_key',
            'downstream_context_key',
            'version_synced',
            'version_declined',
        ))
        self.assertListEqual(container_links, expected_container_links)


@skip_unless_cms
class TestRecreateUpstreamLinks(ModuleStoreTestCase, OpenEdxEventsTestMixin, BaseUpstreamLinksHelpers):
    """
    Test recreate_upstream_links management command.
    """

    ENABLED_SIGNALS = ['course_deleted', 'course_published']
    ENABLED_OPENEDX_EVENTS = []

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course_1 = course_1 = CourseFactory.create(emit_signals=True)
        self.course_key_1 = course_key_1 = self.course_1.id
        with self.store.bulk_operations(course_key_1):
            self._set_course_data(course_1)
            self.expected_links_1 = self._create_block_and_expected_links_data(course_key_1)
            self.expected_container_links_1 = self._create_unit_and_expected_container_link(course_key_1)
        self.course_2 = course_2 = CourseFactory.create(emit_signals=True)
        self.course_key_2 = course_key_2 = self.course_2.id
        with self.store.bulk_operations(course_key_2):
            self._set_course_data(course_2)
            self.expected_links_2 = self._create_block_and_expected_links_data(course_key_2)
            self.expected_container_links_2 = self._create_unit_and_expected_container_link(course_key_2)
        self.course_3 = course_3 = CourseFactory.create(emit_signals=True)
        self.course_key_3 = course_key_3 = self.course_3.id
        with self.store.bulk_operations(course_key_3):
            self._set_course_data(course_3)
            self.expected_links_3 = self._create_block_and_expected_links_data(course_key_3)
            self.expected_container_links_3 = self._create_unit_and_expected_container_link(course_key_3)

    def call_command(self, *args, **kwargs):
        """
        call command with pass args.
        """
        out = StringIO()
        kwargs['stdout'] = out
        err = StringIO()
        kwargs['stderr'] = err
        call_command('recreate_upstream_links', *args, **kwargs)
        return out, err

    def test_call_with_invalid_args(self):
        """
        Test command with invalid args.
        """
        with self.assertRaisesRegex(CommandError, 'Either --course or --all argument'):
            self.call_command()
        with self.assertRaisesRegex(CommandError, 'Only one of --course or --all argument'):
            self.call_command('--all', '--course', str(self.course_key_1))

    def test_call_for_single_course(self):
        """
        Test command with single course argument
        """
        # Pre-checks
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_1)).exists()
        assert not ComponentLink.objects.filter(downstream_context_key=self.course_key_1).exists()
        # Run command
        self.call_command('--course', str(self.course_key_1))
        # Post verfication
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_1)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        self._compare_links(self.course_key_1, self.expected_links_1, self.expected_container_links_1)

    def test_call_for_multiple_course(self):
        """
        Test command with multiple course arguments
        """
        # Pre-checks
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_2)).exists()
        assert not ComponentLink.objects.filter(downstream_context_key=self.course_key_2).exists()
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_3)).exists()
        assert not ComponentLink.objects.filter(downstream_context_key=self.course_key_3).exists()

        # Run command
        self.call_command('--course', str(self.course_key_2), '--course', str(self.course_key_3))

        # Post verfication
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_2)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_3)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        self._compare_links(self.course_key_2, self.expected_links_2, self.expected_container_links_2)
        self._compare_links(self.course_key_3, self.expected_links_3, self.expected_container_links_3)

    def test_call_for_all_courses(self):
        """
        Test command with multiple course arguments
        """
        # Delete all links and status just to make sure --all option works
        LearningContextLinksStatus.objects.all().delete()
        ComponentLink.objects.all().delete()
        # Pre-checks
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_1)).exists()
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_2)).exists()
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_3)).exists()

        # Run command
        self.call_command('--all')

        # Post verfication
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_1)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_2)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        assert LearningContextLinksStatus.objects.filter(
            context_key=str(self.course_key_3)
        ).first().status == LearningContextLinksStatusChoices.COMPLETED
        self._compare_links(self.course_key_1, self.expected_links_1, self.expected_container_links_1)
        self._compare_links(self.course_key_2, self.expected_links_2, self.expected_container_links_2)
        self._compare_links(self.course_key_3, self.expected_links_3, self.expected_container_links_3)

    def test_call_for_invalid_course(self):
        """
        Test recreate_upstream_links with nonexistent course
        """
        course_key = "invalid-course"
        with self.assertLogs(level="ERROR") as ctx:
            self.call_command('--course', course_key)
            self.assertEqual(
                f'Invalid course key: {course_key}, skipping..',
                ctx.records[0].getMessage()
            )

    def test_call_for_nonexistent_course(self):
        """
        Test recreate_upstream_links with nonexistent course
        """
        course_key = "course-v1:unix+ux1+2024_T2"
        with self.assertLogs(level="ERROR") as ctx:
            self.call_command('--course', course_key)
            self.assertIn(
                f'Could not find items for given course: {course_key}',
                ctx.records[0].getMessage()
            )


@skip_unless_cms
class TestUpstreamLinksEvents(
    ImmediateOnCommitMixin,
    ModuleStoreTestCase,
    OpenEdxEventsTestMixin,
    BaseUpstreamLinksHelpers,
):
    """
    Test signals related to managing upstream->downstream links.
    """

    ENABLED_SIGNALS = ['course_deleted', 'course_published']
    ENABLED_OPENEDX_EVENTS = [
        "org.openedx.content_authoring.xblock.created.v1",
        "org.openedx.content_authoring.xblock.updated.v1",
        "org.openedx.content_authoring.xblock.deleted.v1",
    ]

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.course_1 = course_1 = CourseFactory.create(emit_signals=True)
        self.course_key_1 = course_key_1 = self.course_1.id
        with self.store.bulk_operations(course_key_1):
            self._set_course_data(course_1)
            self.expected_links_1 = self._create_block_and_expected_links_data(course_key_1)
            self.expected_container_links_1 = self._create_unit_and_expected_container_link(course_key_1)
        self.course_2 = course_2 = CourseFactory.create(emit_signals=True)
        self.course_key_2 = course_key_2 = self.course_2.id
        with self.store.bulk_operations(course_key_2):
            self._set_course_data(course_2)
            self.expected_links_2 = self._create_block_and_expected_links_data(course_key_2)
            self.expected_container_links_2 = self._create_unit_and_expected_container_link(course_key_2)
        self.course_3 = course_3 = CourseFactory.create(emit_signals=True)
        self.course_key_3 = course_key_3 = self.course_3.id
        with self.store.bulk_operations(course_key_3):
            self._set_course_data(course_3)
            self.expected_links_3 = self._create_block_and_expected_links_data(course_key_3)
            self.expected_container_links_3 = self._create_unit_and_expected_container_link(course_key_3)

    def test_create_or_update_events(self):
        """
        Test task create_or_update_upstream_links for a course
        """
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_1)).exists()
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_2)).exists()
        assert not LearningContextLinksStatus.objects.filter(context_key=str(self.course_key_3)).exists()
        assert ComponentLink.objects.filter(downstream_context_key=self.course_key_1).count() == 3
        assert ComponentLink.objects.filter(downstream_context_key=self.course_key_2).count() == 3
        assert ComponentLink.objects.filter(downstream_context_key=self.course_key_3).count() == 3
        self._compare_links(self.course_key_1, self.expected_links_1, self.expected_container_links_1)
        self._compare_links(self.course_key_2, self.expected_links_2, self.expected_container_links_2)
        self._compare_links(self.course_key_3, self.expected_links_3, self.expected_container_links_3)

    def test_delete_handler(self):
        """
        Test whether links are deleted on deletion of xblock.
        """
        usage_key = self.expected_links_1[0]["downstream_usage_key"]
        assert ComponentLink.objects.filter(downstream_usage_key=usage_key).exists()
        self.store.delete_item(usage_key, self.user.id)
        assert not ComponentLink.objects.filter(downstream_usage_key=usage_key).exists()

        usage_key = self.expected_container_links_1[0]["downstream_usage_key"]
        assert ContainerLink.objects.filter(downstream_usage_key=usage_key).exists()
        self.store.delete_item(usage_key, self.user.id)
        assert not ContainerLink.objects.filter(downstream_usage_key=usage_key).exists()
