"""
Test Structure pruning related Split Mongo code.

IMPORTANT: If you are making changes to this code, please re-enable the
TestSplitMongoBackend tests and run them locally against the MongoDB instance
in your Docker Devstack. See the TestSplitMongoBackend docstring for more info.
"""
import itertools
import sys
import textwrap
import unittest
from datetime import datetime
from io import StringIO
from os import path
from unittest.mock import patch

import ddt
from bson.objectid import ObjectId
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from pymongo import MongoClient

# Add top-level project path to sys.path before importing scripts code
sys.path.append(path.abspath(path.join(path.dirname(__file__), '../..')))

from scripts.structures_pruning.utils.splitmongo import (
    ActiveVersionBranch,
    ChangePlan,
    SplitMongoBackend,
    Structure,
    StructuresGraph,
)


def create_test_graph(*version_histories):
    """
    Given any number of lists, where each list represents a history of Structure
    IDs from oldest to newest, return a StructureGraph matching that
    specification. Course names, branch names, and other attributes that exist
    for debugging/reporting but do not change pruning behavior will be
    automatically generated with plausible values.
    """
    all_structures = {}
    all_active_version_branches = []

    active_id_pool = ("A{:023x}".format(i) for i in itertools.count(1))  # noqa: UP032
    course_key_pool = (
        CourseLocator('edx', 'splitmongo', str(i)) for i in itertools.count(1)
    )
    branch_pool = itertools.cycle(['draft-branch', 'published-branch'])

    for version_history in version_histories:
        assert version_history  # The history can't be empty
        structure_ids = [str(version) for version in version_history]

        # Create the Original
        original_id = structure_ids[0]
        history = [Structure(original_id, original_id, None)]

        # Create all other Structures (if any)
        for previous_id, current_id in zip(structure_ids, structure_ids[1:]):  # noqa: B905
            history.append(Structure(current_id, original_id, previous_id))

        # Add to our overall Structures dict (overwrites should be identical or
        # our test data is bad).
        for structure in history:
            if structure.id in all_structures:
                assert structure == all_structures[structure.id]
            else:
                all_structures[structure.id] = structure

        active_version_id = structure_ids[-1]
        all_active_version_branches.append(
            ActiveVersionBranch(
                id=next(active_id_pool),
                branch=next(branch_pool),
                structure_id=active_version_id,
                key=next(course_key_pool),
                edited_on=datetime(2012, 5, 2)

            )
        )

    return StructuresGraph(all_active_version_branches, all_structures)


@ddt.ddt
class TestCourseChangePlan(unittest.TestCase):
    """
    ChangePlans for single and multiple courses.
    """

    def test_simple(self):
        """Simple happy path ChangePlans."""
        graph = create_test_graph(["1", "2", "3", "4"])

        # Preserve no intermediate structures -- prune the middle structures.
        plan_no_intermediate = ChangePlan.create(graph, 0, False, False)
        self.assertEqual(plan_no_intermediate.delete, ["2", "3"])  # noqa: PT009
        self.assertEqual(plan_no_intermediate.update_parents, [("4", "1")])  # noqa: PT009

        # Preserve one intermediate structure
        plan_1_intermediate = ChangePlan.create(graph, 1, False, False)
        self.assertEqual(plan_1_intermediate.delete, ["2"])  # noqa: PT009
        self.assertEqual(plan_1_intermediate.update_parents, [("3", "1")])  # noqa: PT009

        # Preserve two intermediate structures -- Do nothing
        plan_2_intermediate = ChangePlan.create(graph, 2, False, False)
        self.assertEqual(plan_2_intermediate.delete, [])  # noqa: PT009
        self.assertEqual(plan_2_intermediate.update_parents, [])  # noqa: PT009

    @ddt.data(
        create_test_graph(["1"]),  # Original (is also Active)
        create_test_graph(["1", "2"]),  # "1" = Original, "2" = Active
    )
    def test_no_changes(self, graph):
        """These scenarios should result in no Changes."""
        plan_1 = ChangePlan.create(graph, 0, False, False)
        plan_2 = ChangePlan.create(graph, 2, False, False)
        self.assertEqual(plan_1, plan_2)  # noqa: PT009
        self.assertEqual(plan_1.delete, [])  # noqa: PT009
        self.assertEqual(plan_1.update_parents, [])  # noqa: PT009

    def test_overlapping_shared_history(self):
        """Test multiple branches that overlap in what history to preserve."""
        graph = create_test_graph(
            ["1", "2", "3"],
            ["1", "2", "3", "4", "5"],
            ["1", "2", "3", "6"],
            ["1", "2", "7", "8", "9", "10"],
        )
        plan = ChangePlan.create(graph, 1, False, False)

        # We specified only one intermediate structure in each branch should be
        # preserved. So why do we only delete "7" and "8" here?
        # "1" is the original structure, and will always be preserved.
        # "2" is the intermediate structure preserved by the first branch. It
        #     won't be deleted, even if other branches might want to flag it for
        #     deletion.
        # "3" would be deleted by the second branch, but it's Active in the
        #     first, and so is preserved. Active Structures are never deleted.
        # "4" is preserved by the second branch.
        # "5" is the Active Structure for the second branch.
        # "6" is the Active Structure for the third branch.
        # "7" is marked for deletion by the fourth branch.
        # "8" is marked for deletion by the fourth branch.
        # "9" is preserved by the fourth branch.
        # "10" is the Active Structure for the fourth branch.
        self.assertEqual(plan.delete, ["7", "8"])  # noqa: PT009
        self.assertEqual(plan.update_parents, [("9", "1")])  # noqa: PT009

    def test_non_overlapping_shared_history(self):
        """Test shared history, preserved intermediate set doesn't overlap."""
        graph = create_test_graph(
            ["1", "2", "3"],
            ["1", "2", "3", "4", "5", "6"],
        )
        plan = ChangePlan.create(graph, 0, False, False)
        self.assertEqual(plan.delete, ["2", "4", "5"])  # noqa: PT009
        self.assertEqual(plan.update_parents, [("3", "1"), ("6", "1")])  # noqa: PT009

        graph_save_1 = create_test_graph(
            ["1", "2", "3", "4"],
            ["1", "2", "3", "4", "5", "6", "7"],
        )
        plan_save_1 = ChangePlan.create(graph_save_1, 1, False, False)
        self.assertEqual(plan_save_1.delete, ["2", "5"])  # noqa: PT009
        self.assertEqual(plan_save_1.update_parents, [("3", "1"), ("6", "1")])  # noqa: PT009

    def test_details_output(self):
        """Test our details file output."""
        graph = create_test_graph(
            ["1"],
            ["2", "3"],
            ["4", "5", "6"]
        )
        buff = StringIO()
        buff.name = "test_file.txt"
        plan = ChangePlan.create(graph, 0, False, False, buff)
        details_txt = buff.getvalue()

        # pylint: disable=line-too-long
        expected_output = textwrap.dedent(
            """
            == Summary ==
            Active Version Branches: 3
            Total Structures: 6
            Structures to Save: 5
            Structures to Delete: 1
            Structures to Rewrite Parent Link: 1

            == Active Versions ==
            Active Version A00000000000000000000001 [2012-05-02 00:00:00] draft-branch for course-v1:edx+splitmongo+1
            + 1 (active) (original)

            Active Version A00000000000000000000002 [2012-05-02 00:00:00] published-branch for course-v1:edx+splitmongo+2
            + 3 (active)
            + 2 (original)

            Active Version A00000000000000000000003 [2012-05-02 00:00:00] draft-branch for course-v1:edx+splitmongo+3
            + 6 (active) (re-link to original)
            - 5
            + 4 (original)

            """
        ).lstrip()
        # pylint: enable=line-too-long
        self.assertEqual(expected_output, details_txt)  # noqa: PT009
        self.assertEqual(  # noqa: PT009
            plan,
            ChangePlan(
                delete=["5"],
                update_parents=[("6", "4")]
            )
        )


class TestSplitMongoBackendHelpers(unittest.TestCase):
    """
    Test the static helper methods of SplitMongoBackend.

    Requires no actual database connection.
    """

    def test_parse_structure_doc(self):
        """Test basic parsing of Structures."""
        original_structure = SplitMongoBackend.parse_structure_doc(
            {
                '_id': obj_id(1),
                'original_version': obj_id(1),
                'previous_version': None,
                'extra_data': "This is ignored"
            }
        )
        self.assertEqual(  # noqa: PT009
            original_structure,
            Structure(id=str_id(1), original_id=str_id(1), previous_id=None)
        )
        self.assertTrue(original_structure.is_original())  # noqa: PT009

        other_structure = SplitMongoBackend.parse_structure_doc(
            {
                '_id': obj_id(2),
                'original_version': obj_id(1),
                'previous_version': obj_id(1),
                'extra_data': "This is ignored"
            }
        )
        self.assertEqual(  # noqa: PT009
            other_structure,
            Structure(id=str_id(2), original_id=str_id(1), previous_id=str_id(1))
        )
        self.assertFalse(other_structure.is_original())  # noqa: PT009

    def test_batch(self):
        """Test the batch helper that breaks up iterables for DB operations."""
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.batch([], 1)),
            []
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.batch([1, 2, 3], 1)),
            [[1], [2], [3]]
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.batch([1, 2, 3], 2)),
            [[1, 2], [3]]
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.batch([1, 2, 3, 4], 2)),
            [[1, 2], [3, 4]]
        )

    def test_iter_from_start(self):
        """Test what we use to resume deletion from a given Structure ID."""
        all_ids = [1, 2, 3]
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.iter_from_start(all_ids, None)),
            all_ids
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.iter_from_start(all_ids, 1)),
            all_ids
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.iter_from_start(all_ids, 2)),
            [2, 3]
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.iter_from_start(all_ids, 3)),
            [3]
        )
        self.assertEqual(  # noqa: PT009
            list(SplitMongoBackend.iter_from_start(all_ids, 4)),
            []
        )


@unittest.skip("Requires local MongoDB instance (run manually).")
class TestSplitMongoBackend(unittest.TestCase):
    """
    Tests the MongoDB-specific portions of the code.

    These tests should be about simple read/write from the database. Complex
    trees of Structures can be created and tested in TestSingleCourseChangePlan
    without invoking the database.

    These tests will be disabled by default because I didn't want to add MongoDB
    as a test-time dependency for tubular, and the only decent looking MongoDB
    mocking library I could find was no longer being maintained. Given how
    isolated Split Mongo related code is in tubular (nothing else touches it),
    the main danger of breakage comes from file format changes in edx-platform,
    which automated testing at this level wouldn't catch anyway.

    So basically, if you want to work on this code, please run these tests
    locally by spinning up the MongoDB server used for Docker Devstack and
    commenting out the unittest.skip decorator above.
    """
    CONNECT_STR = "mongodb://localhost:27017"
    DATABASE_NAME = "splitmongo_test"

    def setUp(self):
        """Clear our test MongoDB instance of data."""
        super().setUp()

        self.client = MongoClient(self.CONNECT_STR)
        database = self.client[self.DATABASE_NAME]

        # Remove anything that might have been there from a previous test.
        database.drop_collection('modulestore.active_versions')
        database.drop_collection('modulestore.structures')

        # Convenince pointers to our collections.
        self.active_versions = database['modulestore.active_versions']
        self.structures = database['modulestore.structures']

        # The backend we should use in our tests for querying.
        self.backend = SplitMongoBackend(self.CONNECT_STR, self.DATABASE_NAME)
        self.seed_data()

    def seed_data(self):
        """Create a Course and Library."""
        structure_docs = [
            # Branch 1
            dict(_id=obj_id(1), original_version=obj_id(1), previous_version=None),
            dict(_id=obj_id(2), original_version=obj_id(1), previous_version=obj_id(1)),
            dict(_id=obj_id(3), original_version=obj_id(1), previous_version=obj_id(2)),
            dict(_id=obj_id(4), original_version=obj_id(1), previous_version=obj_id(3)),

            # Branch 2
            dict(_id=obj_id(10), original_version=obj_id(10), previous_version=None),
            dict(_id=obj_id(11), original_version=obj_id(10), previous_version=obj_id(10)),

            # Branch 3
            dict(_id=obj_id(20), original_version=obj_id(20), previous_version=None),
        ]
        active_versions_docs = [
            {
                '_id': obj_id(100),
                'edited_on': datetime(2012, 5, 2),
                'org': 'edx',
                'course': 'split_course',
                'run': '2017',
                'versions': {
                    'draft-branch': obj_id(4),
                    'published-branch': obj_id(11)
                }
            },
            {
                '_id': obj_id(101),
                'edited_on': datetime(2012, 5, 3),
                'org': 'edx',
                'course': 'split_library',
                'run': 'library',
                'versions': {
                    'library': obj_id(20),
                }
            }
        ]
        self.structures.insert_many(structure_docs)
        self.active_versions.insert_many(active_versions_docs)

    def test_structures_graph(self):
        """Test pulling a full graph out."""
        graph = self.backend.structures_graph(0, 100)
        self.assertEqual(  # noqa: PT009
            graph.branches,
            [
                ActiveVersionBranch(
                    id=str_id(100),
                    branch='draft-branch',
                    structure_id=str_id(4),
                    key=CourseLocator('edx', 'split_course', '2017'),
                    edited_on=datetime(2012, 5, 2),
                ),
                ActiveVersionBranch(
                    id=str_id(100),
                    branch='published-branch',
                    structure_id=str_id(11),
                    key=CourseLocator('edx', 'split_course', '2017'),
                    edited_on=datetime(2012, 5, 2),
                ),
                ActiveVersionBranch(
                    id=str_id(101),
                    branch='library',
                    structure_id=str_id(20),
                    key=LibraryLocator('edx', 'split_library'),
                    edited_on=datetime(2012, 5, 3),
                ),
            ]
        )
        self.assertEqual(  # noqa: PT009
            list(graph.structures.keys()),
            [str_id(i) for i in [1, 2, 3, 4, 10, 11, 20]]
        )

    def test_update(self):
        """Execute a simple update."""
        self.backend.update(
            ChangePlan(
                delete=[str_id(i) for i in [2, 3]],
                update_parents=[(str_id(4), str_id(1))]
            ),
            delay=0
        )
        graph = self.backend.structures_graph(0, 100)
        self.assertEqual(  # noqa: PT009
            list(graph.structures.keys()),
            [str_id(i) for i in [1, 4, 10, 11, 20]]
        )
        self.assertEqual(  # noqa: PT009
            graph.structures,
            {
                str_id(1): Structure(id=str_id(1), original_id=str_id(1), previous_id=None),
                # This one got its previous_id rewritten from 3 -> 1
                str_id(4): Structure(id=str_id(4), original_id=str_id(1), previous_id=str_id(1)),
                str_id(10): Structure(id=str_id(10), original_id=str_id(10), previous_id=None),
                str_id(11): Structure(id=str_id(11), original_id=str_id(10), previous_id=str_id(10)),
                str_id(20): Structure(id=str_id(20), original_id=str_id(20), previous_id=None),
            }
        )

    def test_race_condition(self):
        """Create new Structures are during ChangePlan creation."""
        # Get the real method before we patch it...
        real_all_structures_fn = SplitMongoBackend._all_structures  # pylint: disable=protected-access

        def add_structures(backend, delay, batch_size):
            """Do what _all_structures() would do, then add new Structures."""
            structures = real_all_structures_fn(backend, delay, batch_size)

            # Create new Structures
            self.structures.insert_one(
                dict(_id=obj_id(5), original_version=obj_id(1), previous_version=obj_id(4)),
            )
            self.structures.insert_one(
                dict(_id=obj_id(6), original_version=obj_id(1), previous_version=obj_id(5)),
            )
            self.structures.insert_one(
                dict(_id=obj_id(7), original_version=obj_id(1), previous_version=obj_id(6)),
            )

            # Update the Draft branch of course-v1:edx+split_course+2017 to
            # point to one of the new Structures
            self.active_versions.update_one(
                {'_id': obj_id(100)},
                {'$set': {'versions.draft-branch': obj_id(5)}}
            )

            # Create an entirely new ActiveVersion and point it to the newest
            # Structure.
            self.active_versions.insert_one(
                {
                    '_id': obj_id(102),
                    'edited_on': datetime(2012, 5, 3),
                    'org': 'edx',
                    'course': 'split_library_race',
                    'run': 'library',
                    'versions': {
                        'library': obj_id(7),
                    }
                }
            )

            return structures

        with patch.object(SplitMongoBackend, '_all_structures', autospec=True) as all_structures_mock:
            all_structures_mock.side_effect = add_structures
            graph = self.backend.structures_graph(0, 100)
            self.assertEqual(len(graph.structures), 10)  # noqa: PT009
            self.assertEqual(len(graph.branches), 4)  # noqa: PT009

            plan = ChangePlan.create(graph, 0, False, False)
            self.assertNotIn(str_id(5), plan.delete)  # Active updated to this for our course.  # noqa: PT009
            self.assertNotIn(str_id(7), plan.delete)  # Active for our new Library  # noqa: PT009
            self.assertIn(str_id(4), plan.delete)  # Was our Active before  # noqa: PT009
            self.assertIn(str_id(6), plan.delete)  # Intermediate structure to new Library  # noqa: PT009


def str_id(int_id):
    """Return the string version of Object IDs that PyMongo will accept."""
    return "{:024}".format(int_id)  # noqa: UP032


def obj_id(int_id):
    """Helper to create Object IDs that PyMongo will accept."""
    return ObjectId(str_id(int_id))
