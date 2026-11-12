"""Tests for the Tagging models"""
import io
import os
import tempfile
from unittest.mock import patch

import ddt
import pytest
from django.test.testcases import TestCase
from fs.osfs import OSFS
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import CourseLocator, LibraryCollectionLocator, LibraryContainerLocator, LibraryLocatorV2
from openedx_catalog.api import create_course_run_for_modulestore_course_with, get_course_run
from openedx_catalog.models_api import CourseRun
from openedx_learning.api import associate_competency_criterion, create_competency_taxonomy
from openedx_learning.models_api import CompetencyCriteriaGroup, CompetencyCriterion, RuleType
from openedx_tagging.models import ObjectTag, Tag, Taxonomy
from organizations.models import Organization

from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory

from .. import api
from ..utils import rules_cache
from .test_objecttag_export_helpers import TaggedCourseMixin, TestGetAllObjectTagsMixin


class TestTaxonomyMixin:
    """
    Sets up data for testing Content Taxonomies.
    """

    def setUp(self):
        super().setUp()
        self.org1 = Organization.objects.create(name="OpenedX", short_name="OeX")
        self.org2 = Organization.objects.create(name="Axim", short_name="Ax")
        # Taxonomies
        self.taxonomy_disabled = api.create_taxonomy(
            name="Learning Objectives",
            # We will disable this taxonomy below, after we have used it to tag some objects.
            # Note: "disabled" taxonomies are not a supported nor user-exposed feature at the moment, so it's not
            # actually that important to test them.
        )
        api.set_taxonomy_orgs(self.taxonomy_disabled, orgs=[self.org1, self.org2])

        self.taxonomy_all_orgs = api.create_taxonomy(name="Content Types")
        api.set_taxonomy_orgs(self.taxonomy_all_orgs, all_orgs=True)

        self.taxonomy_both_orgs = api.create_taxonomy(name="OpenedX/Axim Content Types")
        api.set_taxonomy_orgs(self.taxonomy_both_orgs, orgs=[self.org1, self.org2])

        self.taxonomy_one_org = api.create_taxonomy(name="OpenedX Content Types")
        api.set_taxonomy_orgs(self.taxonomy_one_org, orgs=[self.org1])

        self.taxonomy_no_orgs = api.create_taxonomy(name="No orgs")

        # Tags
        self.tag_disabled = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_disabled,
            tag="learning",
        )
        self.tag_all_orgs = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_all_orgs,
            tag="learning",
        )
        self.tag_both_orgs = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_both_orgs,
            tag="learning",
        )
        self.tag_one_org = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_one_org,
            tag="learning",
        )
        self.tag_no_orgs = api.add_tag_to_taxonomy(
            taxonomy=self.taxonomy_no_orgs,
            tag="learning",
        )
        # ObjectTags
        api.tag_object(
            object_id="course-v1:OeX+DemoX+Demo_Course",
            taxonomy=self.taxonomy_all_orgs,
            tags=[self.tag_all_orgs.value],
        )
        self.all_orgs_course_tag = api.get_object_tags(
            object_id="course-v1:OeX+DemoX+Demo_Course",
        )[0]
        api.tag_object(
            object_id="block-v1:Ax+DemoX+Demo_Course+type@vertical+block@abcde",
            taxonomy=self.taxonomy_all_orgs,
            tags=[self.tag_all_orgs.value],
        )
        self.all_orgs_block_tag = api.get_object_tags(
            object_id="block-v1:Ax+DemoX+Demo_Course+type@vertical+block@abcde",
        )[0]

        # Force apply these tags: Ax and OeX are not an allowed org for these taxonomies
        api.oel_tagging.tag_object(
            object_id="course-v1:Ax+DemoX+Demo_Course",
            taxonomy=self.taxonomy_both_orgs,
            tags=[self.tag_both_orgs.value],
        )
        self.both_orgs_course_tag = api.get_object_tags(
            object_id="course-v1:Ax+DemoX+Demo_Course",
        )[0]
        api.oel_tagging.tag_object(
            object_id="block-v1:OeX+DemoX+Demo_Course+type@video+block@abcde",
            taxonomy=self.taxonomy_both_orgs,
            tags=[self.tag_both_orgs.value],
        )
        self.both_orgs_block_tag = api.get_object_tags(
            object_id="block-v1:OeX+DemoX+Demo_Course+type@video+block@abcde",
        )[0]
        api.oel_tagging.tag_object(
            object_id="block-v1:OeX+DemoX+Demo_Course+type@html+block@abcde",
            taxonomy=self.taxonomy_one_org,
            tags=[self.tag_one_org.value],
        )
        self.one_org_block_tag = api.get_object_tags(
            object_id="block-v1:OeX+DemoX+Demo_Course+type@html+block@abcde",
        )[0]
        api.oel_tagging.tag_object(
            object_id="course-v1:Ax+DemoX+Demo_Course",
            taxonomy=self.taxonomy_disabled,
            tags=[self.tag_disabled.value],
        )
        self.disabled_course_tag = api.get_object_tags(
            object_id="course-v1:Ax+DemoX+Demo_Course",
        )[0]
        self.taxonomy_disabled.enabled = False
        self.taxonomy_disabled.save()
        self.disabled_course_tag.refresh_from_db()  # Update its cached .taxonomy

        # Clear the rules cache in between test runs
        rules_cache.clear()


@ddt.ddt
class TestAPITaxonomy(TestTaxonomyMixin, TestCase):
    """
    Tests the Content Taxonomy APIs.
    """

    def test_get_taxonomies_enabled_subclasses(self):
        with self.assertNumQueries(1):
            taxonomies = list(api.get_taxonomies())
        assert taxonomies == [
            self.taxonomy_all_orgs,
            self.taxonomy_no_orgs,
            self.taxonomy_one_org,
            self.taxonomy_both_orgs,
        ]

    @ddt.data(
        # All orgs
        (None, True, ["taxonomy_all_orgs"]),
        (None, False, []),
        (None, None, ["taxonomy_all_orgs"]),
        # Org 1
        ("org1", True, ["taxonomy_all_orgs", "taxonomy_one_org", "taxonomy_both_orgs"]),
        ("org1", False, ["taxonomy_disabled"]),
        (
            "org1",
            None,
            [
                "taxonomy_all_orgs",
                "taxonomy_disabled",
                "taxonomy_one_org",
                "taxonomy_both_orgs",
            ],
        ),
        # Org 2
        ("org2", True, ["taxonomy_all_orgs", "taxonomy_both_orgs"]),
        ("org2", False, ["taxonomy_disabled"]),
        (
            "org2",
            None,
            ["taxonomy_all_orgs", "taxonomy_disabled", "taxonomy_both_orgs"],
        ),
    )
    @ddt.unpack
    def test_get_taxonomies_for_org(self, org_attr, enabled, expected):
        org_owner = getattr(self, org_attr).short_name if org_attr else None
        taxonomies = list(
            api.get_taxonomies_for_org(org_short_name=org_owner, enabled=enabled)
        )
        assert taxonomies == [
            getattr(self, taxonomy_attr) for taxonomy_attr in expected
        ]

    def test_get_unassigned_taxonomies(self):
        expected = ["taxonomy_no_orgs"]
        taxonomies = list(api.get_unassigned_taxonomies())
        assert taxonomies == [
            getattr(self, taxonomy_attr) for taxonomy_attr in expected
        ]

    @ddt.data(
        ("taxonomy_all_orgs", "all_orgs_course_tag"),
        ("taxonomy_all_orgs", "all_orgs_block_tag"),
        ("taxonomy_both_orgs", "both_orgs_course_tag"),
        ("taxonomy_both_orgs", "both_orgs_block_tag"),
        ("taxonomy_one_org", "one_org_block_tag"),
    )
    @ddt.unpack
    def test_get_content_tags_valid_for_org(
        self,
        taxonomy_attr,
        object_tag_attr,
    ):
        taxonomy_id = getattr(self, taxonomy_attr).id
        object_tag = getattr(self, object_tag_attr)
        with self.assertNumQueries(1):
            valid_tags = list(
                api.get_object_tags(
                    object_id=object_tag.object_id,
                    taxonomy_id=taxonomy_id,
                )
            )
        assert len(valid_tags) == 1
        assert valid_tags[0].id == object_tag.id

    @ddt.data(
        ("taxonomy_all_orgs", "all_orgs_course_tag"),
        ("taxonomy_all_orgs", "all_orgs_block_tag"),
        ("taxonomy_both_orgs", "both_orgs_course_tag"),
        ("taxonomy_both_orgs", "both_orgs_block_tag"),
        ("taxonomy_one_org", "one_org_block_tag"),
    )
    @ddt.unpack
    def test_get_content_tags(
        self,
        taxonomy_attr,
        object_tag_attr,
    ):
        taxonomy_id = getattr(self, taxonomy_attr).id
        object_tag = getattr(self, object_tag_attr)
        with self.assertNumQueries(1):
            valid_tags = list(
                api.get_object_tags(
                    object_id=object_tag.object_id,
                    taxonomy_id=taxonomy_id,
                )
            )
        assert len(valid_tags) == 1
        assert valid_tags[0].id == object_tag.id

    def test_get_tags(self):
        result = list(api.get_tags(self.taxonomy_all_orgs))
        assert len(result) == 1
        assert result[0]["value"] == self.tag_all_orgs.value
        assert result[0]["_id"] == self.tag_all_orgs.id
        assert result[0]["parent_value"] is None
        assert result[0]["depth"] == 0


class TestAPIObjectTags(TestGetAllObjectTagsMixin, TestCase):
    """
    Tests object tag API functions.
    """

    def test_get_course_object_tags(self):
        """
        Test the get_all_object_tags function using a course
        """
        with self.assertNumQueries(1):
            object_tags, taxonomies = api.get_all_object_tags(
                CourseKey.from_string("course-v1:orgA+test_course+test_run")
            )

        assert object_tags == self.expected_course_objecttags
        assert taxonomies == {
            self.taxonomy_1.id: self.taxonomy_1,
            self.taxonomy_2.id: self.taxonomy_2,
        }

    def test_get_course_object_tags_with_add_tags(self):
        """
        This test checks for an issue in get_all_object_tags:
        If new tags are added to those already added previously,
        the previous tags are lost.
        This happens because the new tags will overwrite the old ones
        in the result.
        """
        # Tag in a new taxonomy
        ObjectTag.objects.create(
            object_id="block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            taxonomy=self.taxonomy_1,
            tag=self.tag_1_1,
        )
        # Tag in a already tagged taxonomy
        ObjectTag.objects.create(
            object_id="block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            taxonomy=self.taxonomy_2,
            tag=self.tag_2_1,
        )

        with self.assertNumQueries(1):
            object_tags, taxonomies = api.get_all_object_tags(
                CourseKey.from_string("course-v1:orgA+test_course+test_run")
            )

        vertical1_tags = api.get_object_tags(
            "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            taxonomy_id=self.taxonomy_1.id,
        )
        vertical2_tags = api.get_object_tags(
            "block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1",
            taxonomy_id=self.taxonomy_2.id,
        )

        # Add new object tags to the expected result
        self.expected_course_objecttags["block-v1:orgA+test_course+test_run+type@vertical+block@test_vertical1"] = {
            self.taxonomy_1.id: [tag.value for tag in vertical1_tags],
            self.taxonomy_2.id: [tag.value for tag in vertical2_tags],
        }

        assert object_tags == self.expected_course_objecttags
        assert taxonomies == {
            self.taxonomy_1.id: self.taxonomy_1,
            self.taxonomy_2.id: self.taxonomy_2,
        }

    def test_get_library_object_tags(self):
        """
        Test the get_all_object_tags function using a library
        """
        with self.assertNumQueries(1):
            object_tags, taxonomies = api.get_all_object_tags(
                LibraryLocatorV2.from_string(f"lib:orgA:lib_{self.block_suffix}")
            )

        assert object_tags == self.expected_library_objecttags
        assert taxonomies == {
            self.taxonomy_1.id: self.taxonomy_1,
            self.taxonomy_2.id: self.taxonomy_2,
        }

    def _test_copy_object_tags(self, src_key, dst_key, expected_tags):
        """
        Test copying object tags to a new object.
        """
        # Destination block doesn't have any tags yet
        with self.assertNumQueries(1):
            assert not list(api.get_object_tags(object_id=str(dst_key)))

        # Copy tags from the source block
        api.copy_object_tags(src_key, dst_key)

        with self.assertNumQueries(1):
            dst_tags = list(api.get_object_tags(object_id=str(dst_key)))

        # Check that the destination tags match the expected list (name + value only; object_id will differ)
        with self.assertNumQueries(0):
            assert len(dst_tags) == len(expected_tags)
            for idx, src_tag in enumerate(expected_tags):
                dst_tag = dst_tags[idx]
                assert src_tag.export_id == dst_tag.export_id
                assert src_tag.value == dst_tag.value

    def test_copy_object_tags(self):
        """
        Test copying object tags to a new object.
        """
        src_key = UsageKey.from_string("block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential")
        dst_key = UsageKey.from_string("block-v1:orgB+test_course+test_run+type@sequential+block@test_sequential")
        expected_tags = list(self.sequential_tags1) + list(self.sequential_tags2)
        with self.assertNumQueries(30):  # TODO why so high?
            self._test_copy_object_tags(src_key, dst_key, expected_tags)

    def test_copy_cross_org_tags(self):
        """
        Test copying object tags to a new object in a different org.
        Ensure only the permitted tags are copied.
        """
        src_key = UsageKey.from_string("block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential")
        dst_key = UsageKey.from_string("block-v1:orgB+test_course+test_run+type@sequential+block@test_sequential")

        # Add another tag from an orgA-specific taxonomy
        api.tag_object(
            object_id=str(src_key),
            taxonomy=self.taxonomy_3,
            tags=["Tag 3.1"],
        )

        # Destination block should have all of the source block's tags, except for the orgA-specific one.
        expected_tags = list(self.sequential_tags1) + list(self.sequential_tags2)
        with self.assertNumQueries(31):  # TODO why so high?
            self._test_copy_object_tags(src_key, dst_key, expected_tags)

    def test_tag_collection(self) -> None:
        collection_key = LibraryCollectionLocator.from_string("lib-collection:orgA:libX:1")

        api.tag_object(
            object_id=str(collection_key),
            taxonomy=self.taxonomy_3,
            tags=["Tag 3.1"],
        )

        with self.assertNumQueries(1):
            object_tags, taxonomies = api.get_all_object_tags(collection_key)

        assert object_tags == {'lib-collection:orgA:libX:1': {3: ['Tag 3.1']}}
        assert taxonomies == {
            self.taxonomy_3.id: self.taxonomy_3,
        }

    def test_tag_container(self) -> None:
        unit_key = LibraryContainerLocator.from_string('lct:orgA:libX:unit:unit1')

        api.tag_object(
            object_id=str(unit_key),
            taxonomy=self.taxonomy_3,
            tags=["Tag 3.1"],
        )

        with self.assertNumQueries(1):
            object_tags, taxonomies = api.get_all_object_tags(unit_key)

        assert object_tags == {'lct:orgA:libX:unit:unit1': {3: ['Tag 3.1']}}
        assert taxonomies == {
            self.taxonomy_3.id: self.taxonomy_3,
        }

    def test_tag_container_invalid_org(self) -> None:
        """
        Test what happens when we try to use a taxonomy that's not enabled for
        the content's organization
        """
        unit_key_org_a = LibraryContainerLocator.from_string('lct:orgA:libX:unit:unit1')
        unit_key_org_b = LibraryContainerLocator.from_string('lct:orgB:libX:unit:unit1')
        tag = "Tag 3.1"

        api.tag_object(object_id=str(unit_key_org_a), taxonomy=self.taxonomy_3, tags=[tag])

        with pytest.raises(
            api.InvalidOrgException,
            match='Taxonomy "Taxonomy 3" is not configured for use with organization "orgB".',
        ):
            api.tag_object(object_id=str(unit_key_org_b), taxonomy=self.taxonomy_3, tags=[tag])


class TestExportImportTags(TaggedCourseMixin):
    """
    Tests for export/import functions
    """
    def _create_csv_file(self, content):
        """
        Create a csv file and returns the path and name
        """
        file_dir_name = tempfile.mkdtemp()
        file_name = f'{file_dir_name}/tags.csv'
        with open(file_name, 'w') as csv_file:
            csv_file.write(content)
        return file_name

    def test_generate_csv_rows(self) -> None:
        buffer = io.StringIO()
        list(api.generate_csv_rows(str(self.course.id), buffer))
        buffer.seek(0)
        csv_content = buffer.getvalue()

        assert csv_content == self.expected_csv

    def test_export_tags_in_csv_file(self) -> None:
        file_dir_name = tempfile.mkdtemp()
        file_dir = OSFS(file_dir_name)
        file_name = 'tags.csv'

        api.export_tags_in_csv_file(str(self.course.id), file_dir, file_name)

        file_path = os.path.join(file_dir_name, file_name)

        self.assertTrue(os.path.exists(file_path))  # noqa: PT009

        with open(file_path, 'r') as f:  # noqa: UP015
            content = f.read()

        cleaned_content = content.replace('\r\n', '\n')
        cleaned_expected_csv = self.expected_csv.replace('\r\n', '\n')
        self.assertEqual(cleaned_content, cleaned_expected_csv)  # noqa: PT009

    def test_import_tags_invalid_format(self) -> None:
        csv_path = self._create_csv_file('invalid format, Invalid\r\ntest1, test2')
        with self.assertRaises(ValueError) as exc:  # noqa: PT027
            api.import_course_tags_from_csv(csv_path, self.course.id)
            assert "Invalid format of csv in" in str(exc.exception)

    def test_import_tags_valid_taxonomy_and_tags(self) -> None:
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.1",""\r\n'
        )
        api.import_course_tags_from_csv(csv_path, self.course.id)
        object_tags = list(api.get_object_tags(self.course.id))
        assert len(object_tags) == 1

        object_tag = object_tags[0]
        assert object_tag.tag == self.tag_1_1
        assert object_tag.taxonomy == self.taxonomy_1

    def test_import_tags_invalid_tag(self) -> None:
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.11",""\r\n'
        )
        api.import_course_tags_from_csv(csv_path, self.course.id)
        object_tags = list(api.get_object_tags(self.course.id))
        assert len(object_tags) == 0

        object_tags = list(api.get_object_tags(
            self.course.id,
            include_deleted=True,
        ))
        assert len(object_tags) == 1

        object_tag = object_tags[0]
        assert object_tag.tag is None
        assert object_tag.value == 'Tag 1.11'
        assert object_tag.taxonomy == self.taxonomy_1

    def test_import_tags_invalid_taxonomy(self) -> None:
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1-1"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.11"\r\n'
        )
        api.import_course_tags_from_csv(csv_path, self.course.id)
        object_tags = list(api.get_object_tags(self.course.id))
        assert len(object_tags) == 0

        object_tags = list(api.get_object_tags(
            self.course.id,
            include_deleted=True,
        ))
        assert len(object_tags) == 1

        object_tag = object_tags[0]
        assert object_tag.tag is None
        assert object_tag.value == 'Tag 1.11'
        assert object_tag.taxonomy is None
        assert object_tag.export_id == '1-taxonomy-1-1'


@ddt.ddt
class TestCopyCourseTagsAndCriteria(ModuleStoreTestCase):
    """
    Tests for copy_course_tags_and_criteria().
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.org_a = Organization.objects.create(name="Organization A", short_name="orgA")
        Organization.objects.create(name="Organization B", short_name="orgB")

        self.taxonomy = api.create_taxonomy(name="Plain")
        api.set_taxonomy_orgs(self.taxonomy, all_orgs=True)
        api.add_tag_to_taxonomy(taxonomy=self.taxonomy, tag="Alpha")
        api.add_tag_to_taxonomy(taxonomy=self.taxonomy, tag="Beta")

        self.org_a_taxonomy = api.create_taxonomy(name="Org A only", orgs=[self.org_a])
        api.add_tag_to_taxonomy(taxonomy=self.org_a_taxonomy, tag="Gamma")

        self.competency_taxonomy = create_competency_taxonomy(name="Competencies")
        api.set_taxonomy_orgs(self.competency_taxonomy, all_orgs=True)
        self.competency = api.add_tag_to_taxonomy(taxonomy=self.competency_taxonomy, tag="Writing")

        self.course = CourseFactory.create(org="orgA", number="course", run="run1", display_name="Source Course")
        sequential = BlockFactory.create(parent=self.course, category="sequential")
        vertical = BlockFactory.create(parent=sequential, category="vertical")
        problem = BlockFactory.create(parent=vertical, category="problem")
        self.source_key = self.course.id
        self.sequential_key = self.source_key.make_usage_key("sequential", sequential.location.block_id)
        self.problem_key = self.source_key.make_usage_key("problem", problem.location.block_id)
        self.destination_key = CourseLocator(org="orgA", course="course", run="run2")

        rules_cache.clear()

    def _clone(self, destination_key, display_name="Rerun Course", **fields):
        """Clone the source course to `destination_key`, as a rerun does."""
        self.store.clone_course(
            self.source_key, destination_key, self.user.id, fields={"display_name": display_name, **fields},
        )

    def _tag(self, object_key, taxonomy, values):
        """Tag `object_key` with `values` from `taxonomy`."""
        api.tag_object(object_id=str(object_key), taxonomy=taxonomy, tags=values)

    def _values(self, object_key, taxonomy):
        """Return the tag values `object_key` has from `taxonomy`."""
        return [tag.value for tag in api.get_object_tags(str(object_key), taxonomy_id=taxonomy.id)]

    def _in_destination(self, usage_key, destination_key=None):
        """Return `usage_key` mapped into the destination course."""
        return usage_key.map_into_course(destination_key or self.destination_key)

    def _add_criterion(self):
        """Give the source run a catalog row and a criterion for the problem, which tags it with the competency."""
        create_course_run_for_modulestore_course_with(self.source_key, title="Source Course")
        associate_competency_criterion(
            self.competency.id,
            str(self.problem_key),
            rule_type_override=RuleType.GRADE,
            rule_payload_override={"op": "gte", "value": 0.8, "scale": "percent"},
        )

    @ddt.data(
        ("competency_taxonomy", ["Writing"]),
        ("taxonomy", ["Alpha", "Beta"]),
    )
    @ddt.unpack
    def test_copies_course_and_block_tags(self, taxonomy_name, block_values):
        taxonomy = getattr(self, taxonomy_name)
        self._tag(self.source_key, taxonomy, block_values[:1])
        self._tag(self.sequential_key, taxonomy, block_values)
        self._clone(self.destination_key)
        tag_count, taxonomy_count = Tag.objects.count(), Taxonomy.objects.count()

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert self._values(self.destination_key, taxonomy) == block_values[:1]
        assert self._values(self._in_destination(self.sequential_key), taxonomy) == block_values
        assert (Tag.objects.count(), Taxonomy.objects.count()) == (tag_count, taxonomy_count)

    def test_copied_tags_are_editable(self):
        self._tag(self.source_key, self.taxonomy, ["Alpha"])
        self._tag(self.sequential_key, self.taxonomy, ["Beta"])
        self._clone(self.destination_key)

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        copied = ObjectTag.objects.filter(
            object_id__in=[str(self.destination_key), str(self._in_destination(self.sequential_key))],
        )
        assert copied.count() == 2
        assert not copied.filter(is_copied=True).exists()

        destination_sequential_key = self._in_destination(self.sequential_key)
        self._tag(destination_sequential_key, self.taxonomy, ["Alpha"])
        self._tag(self.destination_key, self.taxonomy, [])
        assert self._values(self.sequential_key, self.taxonomy) == ["Beta"]
        assert self._values(self.source_key, self.taxonomy) == ["Alpha"]

        self._tag(self.sequential_key, self.taxonomy, [])
        assert self._values(destination_sequential_key, self.taxonomy) == ["Alpha"]

    def test_source_tags_unchanged(self):
        self._tag(self.source_key, self.taxonomy, ["Alpha"])
        self._tag(self.sequential_key, self.taxonomy, ["Alpha", "Beta"])
        self._add_criterion()
        source_tags = list(ObjectTag.objects.order_by("id").values())
        self._clone(self.destination_key)

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert list(ObjectTag.objects.filter(id__in=[tag["id"] for tag in source_tags]).order_by("id").values()) == (
            source_tags
        )

    def test_ignores_tags_of_course_with_prefixed_key(self):
        # "run10" starts with "run1", so get_all_object_tags() also returns this course's block tags.
        prefixed_key = CourseLocator(org="orgA", course="course", run="run10")
        other_block_key = prefixed_key.make_usage_key("problem", "other_problem")
        self._tag(other_block_key, self.taxonomy, ["Alpha"])
        self._clone(self.destination_key)

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert not api.get_all_object_tags(self.destination_key)[0]

    def test_skips_taxonomy_not_enabled_for_destination_org(self):
        destination_key = CourseLocator(org="orgB", course="course", run="run2")
        self._tag(self.source_key, self.org_a_taxonomy, ["Gamma"])
        self._tag(self.sequential_key, self.org_a_taxonomy, ["Gamma"])
        self._tag(self.sequential_key, self.taxonomy, ["Alpha"])
        self._add_criterion()
        api.set_taxonomy_orgs(self.competency_taxonomy, orgs=[self.org_a])
        rules_cache.clear()
        self._clone(destination_key)

        with self.assertLogs(api.log, level="WARNING") as logs:
            api.copy_course_tags_and_criteria(self.source_key, destination_key)

        assert len(logs.records) == 2
        for taxonomy in (self.org_a_taxonomy, self.competency_taxonomy):
            assert sum(
                taxonomy.name in message and str(self.source_key) in message and str(destination_key) in message
                for message in logs.output
            ) == 1
        assert self._values(destination_key, self.org_a_taxonomy) == []
        assert self._values(self._in_destination(self.sequential_key, destination_key), self.org_a_taxonomy) == []
        assert self._values(self._in_destination(self.problem_key, destination_key), self.competency_taxonomy) == []
        assert self._values(self._in_destination(self.sequential_key, destination_key), self.taxonomy) == ["Alpha"]
        assert not CompetencyCriteriaGroup.objects.filter(course__course_key=destination_key).exists()

    def test_copies_criteria_onto_destination_run(self):
        self._add_criterion()
        self._clone(self.destination_key)

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        course_level_group = CompetencyCriteriaGroup.objects.get(course__course_key=self.destination_key)
        assert course_level_group.tag == self.competency
        criterion = CompetencyCriterion.objects.get(group__parent=course_level_group)
        assert criterion.object_tag.object_id == str(self._in_destination(self.problem_key))
        assert self._values(self._in_destination(self.problem_key), self.competency_taxonomy) == ["Writing"]

    def test_creates_destination_course_run_when_missing(self):
        create_course_run_for_modulestore_course_with(self.source_key, title="Source Course")
        self._clone(self.destination_key, display_name="Rerun Course")
        assert not CourseRun.objects.filter(course_key=self.destination_key).exists()

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert get_course_run(self.destination_key).title == "Rerun Course"

    def test_destination_course_run_title_is_cut_to_the_column_length(self):
        create_course_run_for_modulestore_course_with(self.source_key, title="Source Course")
        self._clone(self.destination_key, display_name="x" * 300)

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert get_course_run(self.destination_key).title == "x" * 255

    @ddt.data(("fr", "fr"), ("not-a-language", "en"))
    @ddt.unpack
    def test_destination_catalog_course_language(self, course_language, expected_language):
        # A new course number, so the copy also creates the catalog course, which is what stores the language.
        destination_key = CourseLocator(org="orgA", course="course2", run="run1")
        create_course_run_for_modulestore_course_with(self.source_key, title="Source Course")
        self._clone(destination_key, language=course_language)

        api.copy_course_tags_and_criteria(self.source_key, destination_key)

        assert get_course_run(destination_key).catalog_course.language_short == expected_language

    def test_course_without_tags_or_criteria_succeeds(self):
        self._clone(self.destination_key)
        assert not CourseRun.objects.filter(course_key=self.source_key).exists()

        api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert not api.get_all_object_tags(self.destination_key)[0]
        assert not CompetencyCriteriaGroup.objects.filter(course__course_key=self.destination_key).exists()
        assert not CourseRun.objects.filter(course_key=self.destination_key).exists()

    def test_criteria_failure_rolls_back_tags(self):
        self._tag(self.source_key, self.taxonomy, ["Alpha"])
        self._tag(self.sequential_key, self.taxonomy, ["Beta"])
        self._add_criterion()
        self._clone(self.destination_key)

        with patch.object(api, "copy_competency_criteria_for_course_rerun", side_effect=ObjectTag.DoesNotExist):
            with pytest.raises(ObjectTag.DoesNotExist):
                api.copy_course_tags_and_criteria(self.source_key, self.destination_key)

        assert not api.get_all_object_tags(self.destination_key)[0]
        assert not CourseRun.objects.filter(course_key=self.destination_key).exists()
