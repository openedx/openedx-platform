"""Tests for the Tagging models"""
import io
import os
import shutil
import tempfile
from unittest.mock import patch

import ddt
import pytest
from ccx_keys.locator import CCXLocator
from django.db.models import ProtectedError, Q, QuerySet
from django.test.testcases import TestCase
from fs.osfs import OSFS
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryCollectionLocator, LibraryContainerLocator, LibraryLocatorV2
from openedx_tagging.models import ObjectTag
from organizations.models import Organization

from xmodule.modulestore.xml_importer import CourseImportManager

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

    def _course_object_tags(self):
        """
        Returns every ObjectTag of the course and of its blocks, keyed by (object id, taxonomy id, value).

        Rows without a taxonomy are the fixture's "deleted tag" rows. The export never lists them, so they
        are left out of the comparisons.
        """
        block_prefix = "block-v1:orgA+test_course+test_run+type@"
        object_tags = ObjectTag.objects.filter(
            Q(object_id=str(self.course.id)) | Q(object_id__startswith=block_prefix),
            taxonomy__isnull=False,
        )
        return {(ot.object_id, ot.taxonomy_id, ot.value): ot.pk for ot in object_tags}

    def test_reimporting_own_export_keeps_object_tag_rows(self) -> None:
        csv_path = self._create_csv_file(self.expected_csv)
        before = self._course_object_tags()
        assert before  # The course fixture is tagged.

        api.import_course_tags_from_csv(csv_path, self.course.id)

        assert self._course_object_tags() == before

    def test_import_removes_a_dropped_tag_value(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        before = self._course_object_tags()
        kept_key = (sequential_id, self.taxonomy_1.id, "Tag 1.1")
        dropped_key = (sequential_id, self.taxonomy_1.id, "Tag 1.2")
        assert kept_key in before
        assert dropped_key in before

        csv_content = self.expected_csv.replace('"Tag 1.1; Tag 1.2","Tag 2.1"', '"Tag 1.1","Tag 2.1"')
        assert csv_content != self.expected_csv
        api.import_course_tags_from_csv(self._create_csv_file(csv_content), self.course.id)

        after = self._course_object_tags()
        assert dropped_key not in after
        assert after[kept_key] == before[kept_key]

    def test_import_removes_a_dropped_taxonomy(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        before = self._course_object_tags()
        kept_key = (sequential_id, self.taxonomy_1.id, "Tag 1.1")
        dropped_key = (sequential_id, self.taxonomy_2.id, "Tag 2.1")
        assert dropped_key in before

        csv_content = self.expected_csv.replace('"Tag 1.1; Tag 1.2","Tag 2.1"', '"Tag 1.1; Tag 1.2",""')
        assert csv_content != self.expected_csv
        api.import_course_tags_from_csv(self._create_csv_file(csv_content), self.course.id)

        after = self._course_object_tags()
        assert not [key for key in after if key[0] == sequential_id and key[1] == self.taxonomy_2.id]
        assert after[kept_key] == before[kept_key]

    def test_import_sweeps_blocks_absent_from_the_file(self) -> None:
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.1",""\r\n'
        )
        before = self._course_object_tags()
        course_key = (str(self.course.id), self.taxonomy_1.id, "Tag 1.1")
        assert len({key[0] for key in before}) > 1  # Blocks other than the course are tagged.

        api.import_course_tags_from_csv(csv_path, self.course.id)

        after = self._course_object_tags()
        assert {key[0] for key in after} == {str(self.course.id)}
        assert after[course_key] == before[course_key]

    def test_import_removes_a_taxonomy_less_row_the_file_does_not_list(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        ObjectTag.objects.create(
            object_id=sequential_id, taxonomy=None, tag=None, _value="orphan", _export_id="9-unlisted-taxonomy",
        )

        api.import_course_tags_from_csv(self._create_csv_file(self.expected_csv), self.course.id)

        assert not ObjectTag.objects.filter(object_id=sequential_id, _export_id="9-unlisted-taxonomy").exists()

    def test_import_removes_a_taxonomy_less_row_sharing_the_export_id_of_a_listed_taxonomy(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        before = self._course_object_tags()
        orphan = ObjectTag.objects.create(
            object_id=sequential_id, taxonomy=None, tag=None, _value="orphan", _export_id=self.taxonomy_1.export_id,
        )

        api.import_course_tags_from_csv(self._create_csv_file(self.expected_csv), self.course.id)

        assert not ObjectTag.objects.filter(pk=orphan.pk).exists()
        # The rows of the listed taxonomy that share the export id are untouched.
        assert self._course_object_tags() == before

    def test_import_removes_the_rows_of_a_disabled_taxonomy_dropped_from_a_listed_block(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        self.taxonomy_2.enabled = False
        self.taxonomy_2.save()
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_2).exists()

        csv_content = self.expected_csv.replace('"Tag 1.1; Tag 1.2","Tag 2.1"', '"Tag 1.1; Tag 1.2",""')
        assert csv_content != self.expected_csv
        api.import_course_tags_from_csv(self._create_csv_file(csv_content), self.course.id)

        assert not ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_2).exists()

    def test_import_removes_every_row_of_a_block_absent_from_the_file(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy__isnull=True).exists()
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            '"Test Course","course","course-v1:orgA+test_course+test_run","Tag 1.1",""\r\n'
        )

        api.import_course_tags_from_csv(csv_path, self.course.id)

        assert not ObjectTag.objects.filter(object_id=sequential_id).exists()

    def test_import_moves_tags_between_taxonomies_within_the_object_tag_limit(self) -> None:
        # The source taxonomy is closed because an import leaves free-text taxonomies alone.
        taxonomy_a = api.create_taxonomy(name="Moved From", orgs=[self.orgA])
        taxonomy_b = api.create_taxonomy(name="Moved To", allow_free_text=True, orgs=[self.orgA])
        course_id = str(self.course.id)
        for i in range(60):
            api.oel_tagging.add_tag_to_taxonomy(taxonomy_a, f"old {i}")
        api.tag_object(object_id=course_id, taxonomy=taxonomy_a, tags=[f"old {i}" for i in range(60)])
        new_values = "; ".join(f"new {i}" for i in range(60))
        csv_path = self._create_csv_file(
            f'"Name","Type","ID","{taxonomy_b.export_id}"\r\n'
            f'"Test Course","course","{course_id}","{new_values}"\r\n'
        )

        api.import_course_tags_from_csv(csv_path, self.course.id)

        assert ObjectTag.objects.filter(object_id=course_id, taxonomy=taxonomy_b).count() == 60
        assert not ObjectTag.objects.filter(object_id=course_id, taxonomy=taxonomy_a).exists()

    def test_import_with_no_tags_csv_changes_nothing(self) -> None:
        before = set(ObjectTag.objects.values_list("id", "object_id"))
        assert before
        missing_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, missing_dir, ignore_errors=True)

        # `import_tags` raises before any ObjectTag is touched. `run_imports` is where the error is
        # swallowed so the import carries on; this test does not run that layer.
        with pytest.raises(FileNotFoundError):
            CourseImportManager.import_tags(None, missing_dir, self.course.id)

        assert set(ObjectTag.objects.values_list("id", "object_id")) == before

    def test_import_sweep_of_a_non_course_v1_key_covers_only_the_exact_key(self) -> None:
        ccx_key = CCXLocator.from_string("ccx-v1:orgA+test_course+test_run+ccx@1")
        exact = ObjectTag.objects.create(object_id=str(ccx_key), taxonomy=None, tag=None, _value="a", _export_id="9-x")
        # A prefix built from the key string would match this id.
        prefixed = ObjectTag.objects.create(
            object_id=f"{ccx_key}+type@sequential+block@s", taxonomy=None, tag=None, _value="b", _export_id="9-x",
        )

        # The file needs a data row, or the import does nothing. This row names a block that has no tags yet.
        csv_path = self._create_csv_file(
            '"Name","Type","ID"\r\n'
            '"Unit","vertical","unit"\r\n'
        )

        api.import_course_tags_from_csv(csv_path, ccx_key)

        assert not ObjectTag.objects.filter(pk=exact.pk).exists()
        assert ObjectTag.objects.filter(pk=prefixed.pk).exists()

    def test_import_sweeps_the_tags_of_the_course_key_itself(self) -> None:
        course_id = str(self.course.id)
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        api.tag_object(object_id=course_id, taxonomy=self.taxonomy_1, tags=["Tag 1.1"])
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1"\r\n'
            '"Sequential","sequential","test_sequential","Tag 1.1"\r\n'
        )

        api.import_course_tags_from_csv(csv_path, self.course.id)

        assert not ObjectTag.objects.filter(object_id=course_id).exists()
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_1).exists()

    def test_import_of_a_file_without_data_rows_changes_nothing(self) -> None:
        before = set(ObjectTag.objects.values_list("id", "object_id"))
        assert before

        api.import_course_tags_from_csv(
            self._create_csv_file('"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'), self.course.id
        )

        assert set(ObjectTag.objects.values_list("id", "object_id")) == before

    def _free_text_setup(self):
        """
        Tags the fixture's sequential with a free-text taxonomy value. The export omits free-text values,
        so the returned csv content is the course export.
        """
        free_text = api.create_taxonomy(name="Free Text", allow_free_text=True, orgs=[self.orgA])
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        api.tag_object(object_id=sequential_id, taxonomy=free_text, tags=["anything"])
        free_text_row = ObjectTag.objects.get(object_id=sequential_id, taxonomy=free_text)
        buffer = io.StringIO()
        list(api.generate_csv_rows(str(self.course.id), buffer))
        return sequential_id, free_text_row, buffer.getvalue()

    def test_reimporting_an_export_keeps_free_text_tags(self) -> None:
        sequential_id, free_text_row, csv_content = self._free_text_setup()
        assert "anything" not in csv_content

        api.import_course_tags_from_csv(self._create_csv_file(csv_content), self.course.id)

        assert ObjectTag.objects.get(object_id=sequential_id, taxonomy=free_text_row.taxonomy).pk == free_text_row.pk

    def test_import_dropping_a_closed_value_keeps_free_text_tags(self) -> None:
        sequential_id, free_text_row, csv_content = self._free_text_setup()
        csv_content = csv_content.replace('"Tag 1.1; Tag 1.2","Tag 2.1"', '"Tag 1.1; Tag 1.2",""')

        api.import_course_tags_from_csv(self._create_csv_file(csv_content), self.course.id)

        assert ObjectTag.objects.get(object_id=sequential_id, taxonomy=free_text_row.taxonomy).pk == free_text_row.pk
        assert not ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_2).exists()

    def test_import_continues_when_clearing_a_taxonomy_is_protected(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        course_id = str(self.course.id)
        real_tag_object = api.oel_tagging.tag_object

        def tag_object(*args, **kwargs):
            is_clearing = kwargs.get("tags") == []
            if is_clearing and kwargs["taxonomy"] == self.taxonomy_2 and kwargs["object_id"] == sequential_id:
                raise ProtectedError("protected", set())
            return real_tag_object(*args, **kwargs)

        # The sequential row drops taxonomy 2 (protected) and the course row drops taxonomy 1 (clearable).
        # Block rows other than these two are absent, so the sweep removes them.
        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1","2-taxonomy-2"\r\n'
            f'"Test Course","course","{course_id}","",""\r\n'
            '"Sequential","sequential","test_sequential","Tag 1.1",""\r\n'
        )
        with patch.object(api.oel_tagging, "tag_object", side_effect=tag_object):
            with self.assertLogs(api.log, level="WARNING") as logs:
                api.import_course_tags_from_csv(csv_path, self.course.id)

        assert any("other data protects its tags" in line for line in logs.output)
        # The protected rows stay, the other stale rows of the same object are cleared, the listed tags
        # are applied, and the sweep ran.
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_2).exists()
        assert not ObjectTag.objects.filter(object_id=sequential_id, _value="Tag 1.2").exists()
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_1, _value="Tag 1.1").exists()
        assert not ObjectTag.objects.filter(object_id=course_id).exists()
        assert not ObjectTag.objects.filter(
            object_id__startswith="block-v1:orgA+test_course+test_run+type@", taxonomy__isnull=False
        ).exclude(object_id=sequential_id).exists()

    def test_import_continues_when_removing_a_taxonomy_less_row_is_protected(self) -> None:
        sequential_id = "block-v1:orgA+test_course+test_run+type@sequential+block@test_sequential"
        orphan = ObjectTag.objects.create(
            object_id=sequential_id, taxonomy=None, tag=None, _value="orphan", _export_id="9-unlisted-taxonomy",
        )
        real_delete = QuerySet.delete

        def delete(queryset):
            if queryset.model is ObjectTag and queryset.filter(pk=orphan.pk).exists():
                raise ProtectedError("protected", set())
            return real_delete(queryset)

        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1"\r\n'
            '"Sequential","sequential","test_sequential","Tag 1.1"\r\n'
        )
        with patch.object(QuerySet, "delete", autospec=True, side_effect=delete):
            with self.assertLogs(api.log, level="WARNING") as logs:
                api.import_course_tags_from_csv(csv_path, self.course.id)

        assert any("taxonomy-less tags" in line and sequential_id in line for line in logs.output)
        # The protected row stays, the listed tags are applied, and the sweep ran.
        assert ObjectTag.objects.filter(pk=orphan.pk).exists()
        assert ObjectTag.objects.filter(object_id=sequential_id, taxonomy=self.taxonomy_1, _value="Tag 1.1").exists()
        assert not ObjectTag.objects.filter(object_id=str(self.course.id)).exists()

    def test_import_sweep_continues_when_deleting_an_object_is_protected(self) -> None:
        course_id = str(self.course.id)
        real_delete_object_tags = api.oel_tagging.delete_object_tags
        protected = []

        def delete_object_tags(object_id):
            if not protected:
                protected.append(object_id)
            if object_id == protected[0]:
                raise ProtectedError("protected", set())
            real_delete_object_tags(object_id)

        csv_path = self._create_csv_file(
            '"Name","Type","ID","1-taxonomy-1"\r\n'
            f'"Test Course","course","{course_id}","Tag 1.1"\r\n'
        )
        with patch.object(api.oel_tagging, "delete_object_tags", side_effect=delete_object_tags):
            with self.assertLogs(api.log, level="WARNING") as logs:
                api.import_course_tags_from_csv(csv_path, self.course.id)

        assert any("other data protects them" in line and protected[0] in line for line in logs.output)
        remaining = {
            object_id
            for object_id in ObjectTag.objects.values_list("object_id", flat=True)
            if object_id != course_id and object_id.startswith("block-v1:orgA+test_course+test_run+type@")
        }
        assert remaining == {protected[0]}
