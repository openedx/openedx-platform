"""
Test for auto-tagging content
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import override_settings, LiveServerTestCase
from django.http import HttpRequest
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_tagging.models import Tag, Taxonomy, ObjectTag
from organizations.models import Organization

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE,
    ModuleStoreTestCase,
    ImmediateOnCommitMixin,
)
from openedx.core.djangoapps.content_libraries.api import (
    create_library, create_library_block, delete_library_block, restore_library_block
)

from .. import api
from ..models.base import TaxonomyOrg
from ..toggles import CONTENT_TAGGING_AUTO
from ..types import ContentKey

LANGUAGE_TAXONOMY_ID = -1


class LanguageTaxonomyTestMixin:
    """
    Mixin for test cases that expect the Language System Taxonomy to exist.
    """

    def setUp(self):
        """
        When pytest runs, it creates the database by inspecting models, not by
        running migrations. So data created by our migrations is not present.
        In particular, the Language Taxonomy is not present. So this mixin will
        create the taxonomy, simulating the effect of the following migrations:
            1. openedx_tagging.migrations.0012_language_taxonomy
            2. content_tagging.migrations.0007_system_defined_org_2
            3. openedx_tagging.migrations.0015_taxonomy_export_id
        """
        super().setUp()
        Taxonomy.objects.get_or_create(id=-1, defaults={
            "name": "Languages",
            "description": "Languages that are enabled on this system.",
            "enabled": True,
            "allow_multiple": False,
            "allow_free_text": False,
            "visible_to_authors": True,
            "export_id": "-1_languages",
            "_taxonomy_class": "openedx_tagging.models.system_defined.LanguageTaxonomy",
        })
        TaxonomyOrg.objects.get_or_create(taxonomy_id=-1, defaults={"org": None})


@skip_unless_cms  # Auto-tagging is only available in the CMS
@override_waffle_flag(CONTENT_TAGGING_AUTO, active=True)
class TestAutoTagging(  # type: ignore[misc]
    LanguageTaxonomyTestMixin,
    ImmediateOnCommitMixin,
    ModuleStoreTestCase,
    LiveServerTestCase
):
    """
    Test if the Course and XBlock tags are automatically created
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _check_tag(self, object_key: ContentKey, taxonomy_id: int, value: str | None):
        """
        Check if the ObjectTag exists for the given object_id and taxonomy_id

        If value is None, check if the ObjectTag does not exists
        """
        object_tags = list(api.get_object_tags(str(object_key), taxonomy_id=taxonomy_id))
        object_tag = object_tags[0] if len(object_tags) == 1 else None
        if len(object_tags) > 1:
            raise ValueError("Found too many object tags")
        if value is None:
            assert not object_tag, f"Expected no tag for taxonomy_id={taxonomy_id}, " \
                f"but one found with value={object_tag.value}"
        else:
            assert object_tag, f"Tag for taxonomy_id={taxonomy_id} with value={value} with expected, but none found"
            assert object_tag.value == value, f"Tag value mismatch {object_tag.value} != {value}"

        return True

    def setUp(self):
        super().setUp()
        # Create user
        self.user = UserFactory.create()
        self.user_id = self.user.id

        self.orgA = Organization.objects.create(name="Organization A", short_name="orgA")
        self.patcher = patch("openedx.core.djangoapps.content_tagging.tasks.modulestore", return_value=self.store)
        self.addCleanup(self.patcher.stop)
        self.patcher.start()

    def test_create_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pl"},
        )

        # Check if the tags are created in the Course
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Polski")

    def test_only_tag_course_id(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pl"},
        )
        object_id = str(course.id).replace('course-v1:', '')

        # Check that only one object tag is created for the course
        tags = ObjectTag.objects.filter(object_id__contains=object_id)
        assert len(tags) == 1
        assert tags[0].value == "Polski"
        assert tags[0].object_id == str(course.id)

    @override_settings(LANGUAGE_CODE='pt-br')
    def test_create_course_invalid_language(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "11"},
        )

        # Check if the tags are created in the Course is the system default
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Português (Brasil)")

    @override_settings(LANGUAGES=[('pt', 'Portuguese')], LANGUAGE_DICT={'pt': 'Portuguese'}, LANGUAGE_CODE='pt')
    def test_create_course_unsuported_language(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "en"},
        )

        # Check if the tags are created in the Course is the system default
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Portuguese")

    @override_settings(LANGUAGE_CODE='pt')
    def test_create_course_no_tag_default_language(self):
        # Remove Portuguese tag
        Tag.objects.filter(taxonomy_id=LANGUAGE_TAXONOMY_ID, value="Portuguese").delete()
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "11"},
        )

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

    def test_update_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt-br"},
        )

        # Simulates user manually changing a tag
        lang_taxonomy = Taxonomy.objects.get(pk=LANGUAGE_TAXONOMY_ID)
        api.tag_object(
            object_id=str(course.id),
            taxonomy=lang_taxonomy,
            tags=["Español (España)"]
        )

        # Update course language
        course.language = "en"
        self.store.update_item(course, self.user_id)

        # Does not automatically update the tag
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Español (España)")

    def test_create_delete_xblock(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt-br"},
        )

        # Create XBlocks
        sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")

        usage_key_str = str(vertical.location)

        # Check if the tags are created in the XBlock
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, "Português (Brasil)")

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        # Check if the tags are deleted
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)

    @override_waffle_flag(CONTENT_TAGGING_AUTO, active=False)
    def test_waffle_disabled_create_update_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt"},
        )

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

        # Update course language
        course.language = "en"
        self.store.update_item(course, self.user_id)

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

    @override_waffle_flag(CONTENT_TAGGING_AUTO, active=False)
    def test_waffle_disabled_create_delete_xblock(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt"},
        )

        # Create XBlocks
        sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")

        usage_key_str = str(vertical.location)

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        # Still no tags
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)

    def test_create_delete_restore_library_block(self):
        # Create library
        library = create_library(
            org=self.orgA,
            slug="lib_a",
            title="Library Org A",
            description="This is a library from Org A",
        )

        fake_request = HttpRequest()
        fake_request.LANGUAGE_CODE = "pt-br"
        with patch('crum.get_current_request', return_value=fake_request):
            # Create Library Block
            library_block = create_library_block(library.key, "problem", "Problem1")

        usage_key_str = str(library_block.usage_key)

        # Check if the tags are created in the Library Block with the user's preferred language
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, 'Português (Brasil)')

        # Soft delete the XBlock
        delete_library_block(library_block.usage_key)

        # Check that the tags are not deleted
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, 'Português (Brasil)')

        # Restore the XBlock
        restore_library_block(library_block.usage_key)

        # Check if the tags are still present in the Library Block with the user's preferred language
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, 'Português (Brasil)')

    @override_waffle_flag(CONTENT_TAGGING_AUTO, active=False)
    def test_waffle_disabled_create_delete_library_block(self):
        # Create library
        library = create_library(
            org=self.orgA,
            slug="lib_a2",
            title="Library Org A 2",
            description="This is a library from Org A 2",
        )

        fake_request = HttpRequest()
        fake_request.LANGUAGE_CODE = "pt-br"
        with patch('crum.get_current_request', return_value=fake_request):
            # Create Library Block
            library_block = create_library_block(library.key, "problem", "Problem2")

        usage_key_str = str(library_block.usage_key)

        # No tags created
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)

        # Delete the XBlock
        delete_library_block(library_block.usage_key)

        # Still no tags
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)

        # Restore the XBlock
        with patch('crum.get_current_request', return_value=fake_request):
            restore_library_block(library_block.usage_key)

        # Still no tags
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)
