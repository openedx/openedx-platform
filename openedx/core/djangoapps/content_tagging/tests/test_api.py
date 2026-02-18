"""Tests for the Tagging models"""
import io
import os
import tempfile
import ddt
from django.test.testcases import TestCase
from fs.osfs import OSFS
from opaque_keys.edx.keys import CourseKey, UsageKey
from opaque_keys.edx.locator import LibraryLocatorV2, LibraryCollectionLocator, LibraryContainerLocator
from openedx_tagging.models import ObjectTag
from organizations.models import Organization
from .test_objecttag_export_helpers import TestGetAllObjectTagsMixin, TaggedCourseMixin

from .. import api
from ..utils import rules_cache


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
            taxonomies = list(taxonomy.cast() for taxonomy in api.get_taxonomies())
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
            taxonomy.cast()
            for taxonomy in api.get_taxonomies_for_org(
                org_short_name=org_owner, enabled=enabled
            )
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

    def test_tag_collection(self):
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

    def test_tag_container(self):
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

        self.assertTrue(os.path.exists(file_path))

        with open(file_path, 'r') as f:
            content = f.read()

        cleaned_content = content.replace('\r\n', '\n')
        cleaned_expected_csv = self.expected_csv.replace('\r\n', '\n')
        self.assertEqual(cleaned_content, cleaned_expected_csv)

    def test_import_tags_invalid_format(self) -> None:
        csv_path = self._create_csv_file('invalid format, Invalid\r\ntest1, test2')
        with self.assertRaises(ValueError) as exc:
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
