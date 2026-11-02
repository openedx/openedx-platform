"""
Unit and integration tests to ensure that syncing content from libraries to
courses is working.
"""
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import ddt
from opaque_keys.edx.keys import UsageKey
from freezegun import freeze_time

from openedx.core.djangoapps.content_libraries.tests import ContentLibrariesRestApiTest
from cms.djangoapps.contentstore.xblock_storage_handlers.xblock_helpers import get_block_key_string
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, ImmediateOnCommitMixin
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


@ddt.ddt
class CourseToLibraryTestCase(ContentLibrariesRestApiTest, ImmediateOnCommitMixin, ModuleStoreTestCase):
    """
    Tests that involve syncing content from libraries to courses.
    """
    maxDiff = None  # Necessary for debugging OLX differences

    def setUp(self):
        super().setUp()
        self.now = datetime.now(timezone.utc)
        freezer = freeze_time(self.now)
        self.addCleanup(freezer.stop)
        freezer.start()
        # self.user is set up by ContentLibrariesRestApiTest

        # The source library (contains the upstreams):
        self.library_title = "Upstream Library"
        self.library = self._create_library(slug="testlib", title=self.library_title)
        self.library_id = self.library["id"]  # the library ID as a string
        self.upstream_problem1 = self._add_block_to_library(self.library_id, "problem", "prob1", can_stand_alone=True)
        self._set_library_block_olx(
            self.upstream_problem1["id"],
            '<problem display_name="Problem 1 Display Name" weight="1" markdown="MD 1">multiple choice...</problem>'
        )
        self.upstream_problem2 = self._add_block_to_library(self.library_id, "problem", "prob2", can_stand_alone=True)
        self._set_library_block_olx(
            self.upstream_problem2["id"],
            '<problem display_name="Problem 2 Display Name" max_attempts="22">multi select...</problem>'
        )
        self.upstream_html1 = self._add_block_to_library(self.library_id, "html", "html1", can_stand_alone=False)
        self._set_library_block_olx(
            self.upstream_html1["id"],
            '<html display_name="Text Content">This is the HTML.</html>'
        )
        self.upstream_unit = self._create_container(self.library_id, "unit", slug="u1", display_name="Unit 1 Title")
        self._add_container_children(self.upstream_unit["id"], [
            self.upstream_html1["id"],
            self.upstream_problem1["id"],
            self.upstream_problem2["id"],
        ])
        self._commit_library_changes(self.library_id)  # publish everything

        # The destination course:
        self.course = CourseFactory.create()
        self.course_section = BlockFactory.create(category='chapter', parent=self.course)
        self.course_subsection = BlockFactory.create(category='sequential', parent=self.course_section)
        self.course_unit = BlockFactory.create(category='vertical', parent=self.course_subsection)

    def _get_sync_status(self, usage_key: str):
        return self._api('get', f"/api/contentstore/v2/downstreams/{usage_key}", {}, expect_response=200)

    def _sync_downstream(self, usage_key: str):
        return self._api('post', f"/api/contentstore/v2/downstreams/{usage_key}/sync", {}, expect_response=200)

    def _decline_sync_downstream(self, usage_key: str):
        return self._api('delete', f"/api/contentstore/v2/downstreams/{usage_key}/sync", {}, expect_response=204)

    def _get_course_block_olx(self, usage_key: str):
        data = self._api('get', f'/api/olx-export/v1/xblock/{usage_key}/', {}, expect_response=200)
        return data["blocks"][data["root_block_id"]]["olx"]

    def _copy_course_block(self, usage_key: str):
        """
        Copy a course block to the clipboard
        """
        data = self._api(
            'post',
            "/api/content-staging/v1/clipboard/",
            {"usage_key": usage_key},
            expect_response=200
        )
        return data

    def _paste_course_block(self, parent_usage_key: str):
        """
        Paste a course block from the clipboard
        """
        return self._api(
            'post',
            '/xblock/',
            {"parent_locator": parent_usage_key, "staged_content": "clipboard"},
            expect_response=200
        )

    # def _get_course_block_fields(self, usage_key: str):
    #     return self._api('get', f'/xblock/{usage_key}', {}, expect_response=200)

    def _get_course_block_children(self, usage_key: str) -> list[str]:
        """ Get the IDs of the child XBlocks of the given XBlock """
        # TODO: is there really no REST API to get the children of an XBlock in Studio?
        # Maybe this one: /api/contentstore/v1/container/vertical/{usage_key_string}/children
        return [str(k) for k in modulestore().get_item(UsageKey.from_string(usage_key), depth=0).children]

    def _create_block_from_upstream(
        self,
        block_category: str,
        parent_usage_key: str,
        upstream_key: str,
        expect_response: int = 200,
    ):
        """
        Call the CMS API for inserting an XBlock that's cloned from a library
        item. i.e. copy a *published* library block into a course, and create an
        upstream link.
        """
        return self._api('post', "/xblock/", {
            "category": block_category,
            "parent_locator": parent_usage_key,
            "library_content_key": upstream_key,
        }, expect_response=expect_response)

    def _update_course_block_fields(self, usage_key: str, fields: dict[str, Any] = None):
        """ Update fields of an XBlock """
        return self._api('patch', f"/xblock/{usage_key}", {
            "metadata": fields,
        }, expect_response=200)

    def _get_downstream_links(
        self,
        course_id: str | None = None,
        ready_to_sync: bool | None = None,
        upstream_key: str | None = None,
        item_type: str | None = None,
        use_top_level_parents: bool | None = None,
    ):
        """
        Call the API to get the downstreams links
        """
        data = {}
        if course_id is not None:
            data["course_id"] = str(course_id)
        if ready_to_sync is not None:
            data["ready_to_sync"] = str(ready_to_sync)
        if upstream_key is not None:
            data["upstream_key"] = str(upstream_key)
        if item_type is not None:
            data["item_type"] = str(item_type)
        if use_top_level_parents is not None:
            data["use_top_level_parents"] = str(use_top_level_parents)
        return self.client.get("/api/contentstore/v2/downstreams/", data=data)

    def assertXmlEqual(self, xml_str_a: str, xml_str_b: str) -> bool:
        """ Assert that the given XML strings are equal, ignoring attribute order and some whitespace variations. """
        self.assertEqual(
            ElementTree.canonicalize(xml_str_a, strip_text=True),
            ElementTree.canonicalize(xml_str_b, strip_text=True),
        )

    # OLX attributes that will appear on capa problems when saved/exported. Excludes "markdown"
    standard_capa_attributes = """
        markdown_edited="false"
        matlab_api_key="null"
        name="null"
        rerandomize="never"
        source_code="null"
        tags="[]"
        use_latex_compiler="false"
    """

    ####################################################################################################################

    def test_problem_sync(self):
        """
        Test that we can sync a problem from a library into a course.
        """
        # 1️⃣ First, create the problem in the course, using the upstream problem as a template:
        downstream_problem1 = self._create_block_from_upstream(
            block_category="problem",
            parent_usage_key=str(self.course_subsection.usage_key),
            upstream_key=self.upstream_problem1["id"],
        )
        status = self._get_sync_status(downstream_problem1["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_problem1["id"],  # e.g. 'lb:CL-TEST:testlib:problem:prob1'
            'version_available': 2,
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,
            'error_message': None,
            'downstream_customized': [],
            # 'upstream_link': 'http://course-authoring-mfe/library/lib:CL-TEST:testlib/components?usageKey=...'
        })
        assert status["upstream_link"].startswith("http://course-authoring-mfe/library/")
        assert status["upstream_link"].endswith(f"/components?usageKey={self.upstream_problem1['id']}")

        # Check the OLX of the downstream block. Notice that:
        # (1) fields display_name and markdown, as well as the 'data' (content/body of the <problem>) are synced.
        # (2) per UpstreamSyncMixin.get_customizable_fields(), some fields like weight and max_attempts are
        #     DROPPED entirely from the upstream version when creating the downstream:
        self.assertXmlEqual(self._get_course_block_olx(downstream_problem1["locator"]), f"""
            <problem
                display_name="Problem 1 Display Name"
                markdown="MD 1"
                upstream="{self.upstream_problem1['id']}"
                upstream_display_name="Problem 1 Display Name"
                upstream_version="2"
                {self.standard_capa_attributes}
            >multiple choice...</problem>
        """)

        # 2️⃣ Now, lets modify the upstream problem AND the downstream problem:

        self._update_course_block_fields(downstream_problem1["locator"], {
            "display_name": "Custom Display Name",
            "max_attempts": 3,
            "markdown": "blow me away, scotty!",  # This change will be lost
        })

        self._set_library_block_olx(
            self.upstream_problem1["id"],
            '<problem display_name="Problem 1 NEW name" markdown="updated">multiple choice v2...</problem>'
        )
        self._publish_library_block(self.upstream_problem1["id"])

        # Here's how the downstream OLX looks now, before we sync:
        self.assertXmlEqual(self._get_course_block_olx(downstream_problem1["locator"]), f"""
            <problem
                display_name="Custom Display Name"
                markdown="blow me away, scotty!"
                max_attempts="3"
                upstream="{self.upstream_problem1['id']}"
                upstream_display_name="Problem 1 Display Name"
                upstream_version="2"
                downstream_customized="[&quot;display_name&quot;]"
                {self.standard_capa_attributes}
            >multiple choice...</problem>
        """)

        status = self._get_sync_status(downstream_problem1["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_problem1["id"],  # e.g. 'lb:CL-TEST:testlib:problem:prob1'
            'version_available': 3,  # <--- updated
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,  # <--- updated
            'error_message': None,
            'downstream_customized': ['display_name'],
        })

        # 3️⃣ Now, sync and check the resulting OLX of the downstream

        self._sync_downstream(downstream_problem1["locator"])

        # Here's how the downstream OLX looks now, after we synced it.
        # Notice:
        #   (1) content like "markdown" and the body XML content are synced
        #   (2) the "display_name" is left alone (customized downstream), but
        #   (3) "upstream_display_name" is updated.
        #   (4) The customized "max_attempts" is also still present.
        self.assertXmlEqual(self._get_course_block_olx(downstream_problem1["locator"]), f"""
            <problem
                display_name="Custom Display Name"
                markdown="updated"
                max_attempts="3"
                upstream="{self.upstream_problem1['id']}"
                upstream_display_name="Problem 1 NEW name"
                downstream_customized="[&quot;display_name&quot;]"
                upstream_version="3"
                {self.standard_capa_attributes}
            >multiple choice v2...</problem>
        """)

    def test_unit_sync(self):
        """
        Test that we can sync a unit from the library into the course
        """
        # pylint: disable=too-many-statements

        # 1️⃣ Create a "vertical" block in the course based on a "unit" container:
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        downstream_unit = self._create_block_from_upstream(
            # The API consumer needs to specify "vertical" here, even though upstream is "unit".
            # In the future we could create a nicer REST API endpoint for this that's not part of
            # the messy '/xblock/' API and which auto-detects the types based on the upstream_key.
            block_category="vertical",
            parent_usage_key=str(self.course_subsection.usage_key),
            upstream_key=self.upstream_unit["id"],
        )
        downstream_unit_block_key = get_block_key_string(
            UsageKey.from_string(downstream_unit["locator"]),
        )
        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,
            'error_message': None,
            'downstream_customized': [],
            # 'upstream_link': 'http://course-authoring-mfe/library/lib:CL-TEST:testlib/units/...'
        })
        assert status["upstream_link"].startswith("http://course-authoring-mfe/library/")
        assert status["upstream_link"].endswith(f"/units/{self.upstream_unit['id']}")

        # Check that the downstream container matches our expectations.
        # Note that:
        # (1) Every XBlock has an "upstream" field
        # (2) some "downstream only" fields like weight and max_attempts are omitted.
        # (3) The "top_level_downstream_parent" is the container created
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_data="This is the HTML."
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >This is the HTML.</html>
                <problem
                    display_name="Problem 1 Display Name"
                    upstream_display_name="Problem 1 Display Name"
                    markdown="MD 1"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)

        children_downstream_keys = self._get_course_block_children(downstream_unit["locator"])
        downstream_html1 = children_downstream_keys[0]
        assert "type@html" in downstream_html1
        downstream_problem1 = children_downstream_keys[1]
        assert "type@problem" in downstream_problem1
        downstream_problem2 = children_downstream_keys[2]
        assert "type@problem" in downstream_problem2

        # Check that: The downstream links are created as expected for each component and the container
        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 2,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 3,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem2,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem2["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_unit["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_unit["id"],
                'upstream_type': 'container',
                'downstream_customized': [],
            }
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 4)
        self.assertListEqual(data["results"], expected_downstreams)

        # 2️⃣ Now, lets modify the upstream problem 1:
        self._set_library_block_olx(
            self.upstream_problem1["id"],
            '<problem display_name="Problem 1 NEW name" markdown="updated">multiple choice v2...</problem>'
        )
        self._publish_container(self.upstream_unit["id"])

        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,  # <--- not updated since we didn't directly modify the unit
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,  # <--- It's the top-level parent of the block
            'error_message': None,
            'downstream_customized': [],
        })

        # Check the upstream/downstream status of [one of] the children

        self.assertDictContainsEntries(self._get_sync_status(downstream_problem1), {
            'upstream_ref': self.upstream_problem1["id"],
            'version_available': 3,  # <--- updated since we modified the problem
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,  # <-- It has top-level parent, the parent is the one who must synchronize
            'error_message': None,
            'downstream_customized': [],
        })

        # Sync and check the resulting OLX of the downstream
        self._sync_downstream(downstream_unit["locator"])

        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_data="This is the HTML."
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >This is the HTML.</html>
                <!-- 🟢 the problem below has been updated: -->
                <problem
                    display_name="Problem 1 NEW name"
                    upstream_display_name="Problem 1 NEW name"
                    markdown="updated"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="3"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice v2...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)

        # Check that: The downstream link of the problem is updated and no more links are created
        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 2,
                'upstream_context_title': self.library_title,
                'upstream_version': 3,  # <--- updated
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 3,  # <--- updated
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 3,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem2,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem2["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,  # <--- not updated since we didn't directly modify the unit
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_unit["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_unit["id"],
                'upstream_type': 'container',
                'downstream_customized': [],
            }
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 4)
        self.assertListEqual(data["results"], expected_downstreams)

        # 3️⃣ Now, add and delete a component
        upstream_problem3 = self._add_block_to_library(
            self.library["id"],
            "problem",
            "prob3",
            can_stand_alone=True
        )
        self._set_library_block_olx(
            upstream_problem3["id"],
            '<problem display_name="Problem 3 Display Name" max_attempts="22">single select...</problem>'
        )
        self._add_container_children(self.upstream_unit["id"], [upstream_problem3["id"]])
        self._remove_container_children(self.upstream_unit["id"], [self.upstream_problem2["id"]])
        self._commit_library_changes(self.library["id"])  # publish everything

        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 4,  # <--- updated twice, delete and add component
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,
            'error_message': None,
            'downstream_customized': [],
        })

        # Sync and check the resulting OLX of the downstream

        self._sync_downstream(downstream_unit["locator"])
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="4"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_data="This is the HTML."
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >This is the HTML.</html>
                <problem
                    display_name="Problem 1 NEW name"
                    upstream_display_name="Problem 1 NEW name"
                    markdown="updated"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="3"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice v2...</problem>
                <!-- 🟢 the problem 2 has been deleted: -->
                <!-- 🟢 the problem 3 has been added: -->
                <problem
                    display_name="Problem 3 Display Name"
                    upstream_display_name="Problem 3 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{upstream_problem3['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >single select...</problem>
            </vertical>
        """)

        # Check that: The downstream links are created deleted as expected
        downstream_problem3 = self._get_course_block_children(downstream_unit["locator"])[2]
        assert "type@problem" in downstream_problem3
        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 2,
                'upstream_context_title': self.library_title,
                'upstream_version': 3,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 3,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 4,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem3,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': upstream_problem3["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 4,  # <--- updated
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_unit["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 4,  # <--- updated
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_unit["id"],
                'upstream_type': 'container',
                'downstream_customized': [],
            }
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 4)
        self.assertListEqual(data["results"], expected_downstreams)

        # 4️⃣ Now, reorder components
        self._patch_container_children(self.upstream_unit["id"], [
            upstream_problem3["id"],
            self.upstream_problem1["id"],
            self.upstream_html1["id"],
        ])
        self._publish_container(self.upstream_unit["id"])

        # Sync and check the resulting OLX of the downstream

        self._sync_downstream(downstream_unit["locator"])
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="5"
            >
                <!-- 🟢 the problem 3 has been moved to top: -->
                <problem
                    display_name="Problem 3 Display Name"
                    upstream_display_name="Problem 3 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{upstream_problem3['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >single select...</problem>
                <!-- 🟢 the problem 1 has been moved to middle: -->
                <problem
                    display_name="Problem 1 NEW name"
                    upstream_display_name="Problem 1 NEW name"
                    markdown="updated"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="3"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice v2...</problem>
                <!-- 🟢 the html 1 has been moved to end: -->
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_data="This is the HTML."
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >This is the HTML.</html>
            </vertical>
        """)

        # Check that: The downstream link of the unit is updated and no more links are created
        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 2,
                'upstream_context_title': self.library_title,
                'upstream_version': 3,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem1,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 3,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_problem1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 4,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_problem3,
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': downstream_unit["locator"],
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': upstream_problem3["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 5,  # <--- updated
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_unit["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 5,  # <--- updated
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_unit["id"],
                'upstream_type': 'container',
                'downstream_customized': [],
            }
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 4)
        self.assertListEqual(data["results"], expected_downstreams)

    def test_unit_sync_with_modified_downstream(self):
        """
        Test that modified children component is not overridden when syncing parent container like unit
        """
        # pylint: disable=too-many-statements

        # 1️⃣ Create a "vertical" block in the course based on a "unit" container:
        downstream_unit = self._create_block_from_upstream(
            # The API consumer needs to specify "vertical" here, even though upstream is "unit".
            # In the future we could create a nicer REST API endpoint for this that's not part of
            # the messy '/xblock/' API and which auto-detects the types based on the upstream_key.
            block_category="vertical",
            parent_usage_key=str(self.course_subsection.usage_key),
            upstream_key=self.upstream_unit["id"],
        )
        downstream_unit_block_key = get_block_key_string(
            UsageKey.from_string(downstream_unit["locator"]),
        )
        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,
            'error_message': None,
            'downstream_customized': [],
            # 'upstream_link': 'http://course-authoring-mfe/library/lib:CL-TEST:testlib/units/...'
        })
        assert status["upstream_link"].startswith("http://course-authoring-mfe/library/")
        assert status["upstream_link"].endswith(f"/units/{self.upstream_unit['id']}")

        # Check that the downstream container matches our expectations.
        # Note that:
        # (1) Every XBlock has an "upstream" field
        # (2) some "downstream only" fields like weight and max_attempts are omitted.
        # (3) The "top_level_downstream_parent" is the container created
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_data="This is the HTML."
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >This is the HTML.</html>
                <problem
                    display_name="Problem 1 Display Name"
                    upstream_display_name="Problem 1 Display Name"
                    markdown="MD 1"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)

        children_downstream_keys = self._get_course_block_children(downstream_unit["locator"])
        downstream_html1 = children_downstream_keys[0]
        assert "type@html" in downstream_html1
        downstream_problem1 = children_downstream_keys[1]
        assert "type@problem" in downstream_problem1
        downstream_problem2 = children_downstream_keys[2]
        assert "type@problem" in downstream_problem2

        # 2️⃣ Now, lets modify the upstream problem 1 and upstream html 1:
        self._set_library_block_olx(
            self.upstream_problem1["id"],
            '<problem display_name="Problem 1 NEW name" markdown="updated">multiple choice v2...</problem>'
        )
        self._set_library_block_olx(
            self.upstream_html1["id"],
            '<html display_name="Text content upstream 1">updated content upstream 1</html>'
        )
        self._publish_container(self.upstream_unit["id"])

        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,  # <--- not updated since we didn't directly modify the unit
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,  # <--- It's the top-level parent of the block
            'error_message': None,
            'downstream_customized': [],
        })

        # Check the upstream/downstream status of [one of] the children

        self.assertDictContainsEntries(self._get_sync_status(downstream_problem1), {
            'upstream_ref': self.upstream_problem1["id"],
            'version_available': 3,  # <--- updated since we modified the problem
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,  # <-- It has top-level parent, the parent is the one who must synchronize
            'error_message': None,
            'downstream_customized': [],
        })

        self.assertDictContainsEntries(self._get_sync_status(downstream_html1), {
            'upstream_ref': self.upstream_html1["id"],
            'version_available': 3,  # <--- updated since we modified the problem
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,  # <-- It has top-level parent, the parent is the one who must synchronize
            'error_message': None,
            'downstream_customized': [],
        })

        # Now let's modify course html block
        self._update_course_block_fields(downstream_html1, {
            "data": "The new downstream data.",
        })

        # Sync and check the resulting OLX of the downstream
        self._sync_downstream(downstream_unit["locator"])

        # Notice how the customization of html block i.e. modified child block is preserved by
        # not updating any of the fields except for upstream_* fields
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
            >
                <html
                    display_name="Text content upstream 1"
                    upstream_display_name="Text content upstream 1"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="3"
                    upstream_data="updated content upstream 1"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                    downstream_customized="[&quot;data&quot;]"
                >The new downstream data.</html>
                <!-- 🟢 the problem below has been updated: -->
                <problem
                    display_name="Problem 1 NEW name"
                    upstream_display_name="Problem 1 NEW name"
                    markdown="updated"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="3"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice v2...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)

    def test_modified_html_copy_paste(self):
        """
        Test that we can sync a html from a library into a course.
        """
        # 1️⃣ First, create the html in the course, using the upstream problem as a template:
        date_format = self.now.isoformat().split("+")[0] + 'Z'
        downstream_html1 = self._create_block_from_upstream(
            block_category="html",
            parent_usage_key=str(self.course_subsection.usage_key),
            upstream_key=self.upstream_html1["id"],
        )
        status = self._get_sync_status(downstream_html1["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_html1["id"],  # e.g. 'lb:CL-TEST:testlib:html:html1'
            'version_available': 2,
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,
            'error_message': None,
            'downstream_customized': [],
            # 'upstream_link': 'http://course-authoring-mfe/library/lib:CL-TEST:testlib/components?usageKey=...'
        })
        assert status["upstream_link"].startswith("http://course-authoring-mfe/library/")
        assert status["upstream_link"].endswith(f"/components?usageKey={self.upstream_html1['id']}")

        # Check the OLX of the downstream block. Notice that:
        # (1) fields display_name and data (content/body of the <html>) are synced.
        self.assertXmlEqual(self._get_course_block_olx(downstream_html1["locator"]), f"""
            <html
                display_name="Text Content"
                editor="visual"
                upstream="{self.upstream_html1['id']}"
                upstream_display_name="Text Content"
                upstream_version="2"
                upstream_data="This is the HTML."
            >This is the HTML.</html>
        """)

        # Check that: The downstream links are created as expected for the component
        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 2,
                'ready_to_sync': False,
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': [],
            },
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 1)
        self.assertListEqual(data["results"], expected_downstreams)

        # 2️⃣ Now, lets modify the upstream html AND the downstream display_name:
        self._update_course_block_fields(downstream_html1["locator"], {
            "display_name": "New Text Content",
        })

        self._set_library_block_olx(
            self.upstream_html1["id"],
            '<html display_name="HTML 1 NEW name">The new upstream data.</html>'
        )
        self._publish_library_block(self.upstream_html1["id"])

        # Here's how the downstream OLX looks now, before we sync:
        self.assertXmlEqual(self._get_course_block_olx(downstream_html1["locator"]), f"""
            <html
                display_name="New Text Content"
                downstream_customized="[&quot;display_name&quot;]"
                editor="visual"
                upstream="{self.upstream_html1['id']}"
                upstream_display_name="Text Content"
                upstream_version="2"
                upstream_data="This is the HTML."
            >This is the HTML.</html>
        """)

        status = self._get_sync_status(downstream_html1["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_html1["id"],  # e.g. 'lb:CL-TEST:testlib:html:html1'
            'version_available': 3,  # <--- updated
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,  # <--- updated
            'error_message': None,
            'downstream_customized': ['display_name'],
        })

        downstreams = self._get_downstream_links(
            course_id=str(self.course.id)
        )
        expected_downstreams = [
            {
                'id': 1,
                'upstream_context_title': self.library_title,
                'upstream_version': 3,  # <--- updated
                'ready_to_sync': True,  # <--- updated
                'ready_to_sync_from_children': False,
                'upstream_context_key': self.library_id,
                'downstream_usage_key': downstream_html1["locator"],
                'downstream_context_key': str(self.course.id),
                'top_level_parent_usage_key': None,
                'version_synced': 2,
                'version_declined': None,
                'created': date_format,
                'updated': date_format,
                'upstream_key': self.upstream_html1["id"],
                'upstream_type': 'component',
                'downstream_customized': ["display_name"],  # <--- updated
            },
        ]
        data = downstreams.json()
        self.assertEqual(data["count"], 1)
        self.assertListEqual(data["results"], expected_downstreams)

        # 3️⃣ Now, sync and check the resulting OLX of the downstream

        self._sync_downstream(downstream_html1["locator"])

        # Here's how the downstream OLX looks now, after we synced it.
        # All customizations are preserved based on the post data
        self.assertXmlEqual(self._get_course_block_olx(downstream_html1["locator"]), f"""
            <html
                display_name="New Text Content"
                editor="visual"
                upstream="{self.upstream_html1['id']}"
                upstream_display_name="HTML 1 NEW name"
                upstream_version="3"
                upstream_data="The new upstream data."
                downstream_customized="[&quot;display_name&quot;]"
            >The new upstream data.</html>
        """)

        self._update_course_block_fields(downstream_html1["locator"], {
            "data": "The new downstream data.",
        })

        # Here's how the downstream OLX looks now
        self.assertXmlEqual(self._get_course_block_olx(downstream_html1["locator"]), f"""
            <html
                display_name="New Text Content"
                editor="visual"
                upstream="{self.upstream_html1['id']}"
                upstream_display_name="HTML 1 NEW name"
                upstream_version="3"
                upstream_data="The new upstream data."
                downstream_customized="[&quot;display_name&quot;, &quot;data&quot;]"
            >The new downstream data.</html>
        """)

        # Copy this modified html block
        self._copy_course_block(downstream_html1["locator"])
        # Paste it
        pasted_block = self._paste_course_block(str(self.course_subsection.usage_key))

        # The pasted block will have same fields as the above block except the
        # upstream_* fields will now have the original blocks base value, so
        # pasted_block.upstream_display_name == downstream_html1.display_name
        # pasted_block.upstream_data == downstream_html1.data
        # while still have `downstream_customized` same to avoid overriding during sync
        # to allow authors to revert back to back to the original copied customized value

        # See `upstream_data` below is same as how downstream_html1.data is set.
        self.assertXmlEqual(self._get_course_block_olx(pasted_block["locator"]), f"""
            <html
                display_name="New Text Content"
                editor="visual"
                upstream="{self.upstream_html1['id']}"
                upstream_display_name="New Text Content"
                upstream_version="3"
                upstream_data="The new downstream data."
                downstream_customized="[&quot;display_name&quot;, &quot;data&quot;]"
            >The new downstream data.</html>
        """)

    def test_unit_decline_sync(self):
        """
        Test that we can decline sync a unit from the library into the course
        """
        # 1️⃣ Create a "vertical" block in the course based on a "unit" container:
        downstream_unit = self._create_block_from_upstream(
            # The API consumer needs to specify "vertical" here, even though upstream is "unit".
            # In the future we could create a nicer REST API endpoint for this that's not part of
            # the messy '/xblock/' API and which auto-detects the types based on the upstream_key.
            block_category="vertical",
            parent_usage_key=str(self.course_subsection.usage_key),
            upstream_key=self.upstream_unit["id"],
        )
        downstream_unit_block_key = get_block_key_string(
            UsageKey.from_string(downstream_unit["locator"]),
        )
        children_downstream_keys = self._get_course_block_children(downstream_unit["locator"])
        downstream_problem1 = children_downstream_keys[1]
        assert "type@problem" in downstream_problem1
        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,
            'error_message': None,
            # 'upstream_link': 'http://course-authoring-mfe/library/lib:CL-TEST:testlib/units/...'
        })
        assert status["upstream_link"].startswith("http://course-authoring-mfe/library/")
        assert status["upstream_link"].endswith(f"/units/{self.upstream_unit['id']}")

        # Check that the downstream container matches our expectations.
        # Note that:
        # (1) Every XBlock has an "upstream" field
        # (2) some "downstream only" fields like weight and max_attempts are omitted.
        # (3) The "top_level_downstream_parent" is the container created
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                    upstream_data="This is the HTML."
                >This is the HTML.</html>
                <problem
                    display_name="Problem 1 Display Name"
                    upstream_display_name="Problem 1 Display Name"
                    markdown="MD 1"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)

        # 2️⃣ Now, lets modify the upstream problem 1:
        self._set_library_block_olx(
            self.upstream_problem1["id"],
            '<problem display_name="Problem 1 NEW name" markdown="updated">multiple choice v2...</problem>'
        )
        self._publish_container(self.upstream_unit["id"])

        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],  # e.g. 'lct:CL-TEST:testlib:unit:u1'
            'version_available': 2,  # <--- not updated since we didn't directly modify the unit
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': True,  # <--- It's the top-level parent of the block
            'error_message': None,
        })

        # Check the upstream/downstream status of [one of] the children
        self.assertDictContainsEntries(self._get_sync_status(downstream_problem1), {
            'upstream_ref': self.upstream_problem1["id"],
            'version_available': 3,  # <--- updated since we modified the problem
            'version_synced': 2,
            'version_declined': None,
            'ready_to_sync': False,  # <-- It has top-level parent, the parent is the one who must synchronize
            'error_message': None,
        })

        # Now, decline the sync
        self._decline_sync_downstream(downstream_unit["locator"])

        status = self._get_sync_status(downstream_unit["locator"])
        self.assertDictContainsEntries(status, {
            'upstream_ref': self.upstream_unit["id"],
            'version_available': 2,
            'version_synced': 2,
            'version_declined': 2,
            'ready_to_sync': False,
            'error_message': None,
        })

        # Check the upstream/downstream status of [one of] the children
        self.assertDictContainsEntries(self._get_sync_status(downstream_problem1), {
            'upstream_ref': self.upstream_problem1["id"],
            'version_available': 3,
            'version_synced': 2,
            'version_declined': 3,
            'ready_to_sync': False,
            'error_message': None,
        })

        # Check that the downstream container has not had any changes
        self.assertXmlEqual(self._get_course_block_olx(downstream_unit["locator"]), f"""
            <vertical
                display_name="Unit 1 Title"
                upstream_display_name="Unit 1 Title"
                upstream="{self.upstream_unit['id']}"
                upstream_version="2"
                upstream_version_declined="2"
            >
                <html
                    display_name="Text Content"
                    upstream_display_name="Text Content"
                    editor="visual"
                    upstream="{self.upstream_html1['id']}"
                    upstream_version="2"
                    upstream_version_declined="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                    upstream_data="This is the HTML."
                >This is the HTML.</html>
                <problem
                    display_name="Problem 1 Display Name"
                    upstream_display_name="Problem 1 Display Name"
                    markdown="MD 1"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem1['id']}"
                    upstream_version="2"
                    upstream_version_declined="3"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multiple choice...</problem>
                <problem
                    display_name="Problem 2 Display Name"
                    upstream_display_name="Problem 2 Display Name"
                    markdown="null"
                    {self.standard_capa_attributes}
                    upstream="{self.upstream_problem2['id']}"
                    upstream_version="2"
                    upstream_version_declined="2"
                    top_level_downstream_parent_key="{downstream_unit_block_key}"
                >multi select...</problem>
            </vertical>
        """)
