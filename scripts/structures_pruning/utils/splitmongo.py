"""
This module provides logic to clean up old, unused course content data for the
DraftVersioningModuleStore modulestore, more commonly referred to as the "Split
Mongo" or "Split" modulestore (DraftVersioningModuleStore subclasses
SplitMongoModuleStore). All courses and assets that have newer style locator
keys use DraftVersioningModuleStore. These keys start with "course-v1:",
"ccx-v1:", or "block-v1:".

The older modulestore is DraftModuleStore, sometimes called "Old Mongo". This
code does not address that modulestore in any way. That modulestore handles
courses that use the old "/" separator, such as "MITx/6.002x/2012_Spring", as
well as assets starting with "i4x://".

"Split" gets its name from the fact that it separates the Structure of a course
from the content in the leaf nodes. In theory, the Structure is an outline of
the course that contains all the parent/child relations for different content
blocks (chapters, sections, sub-sections, verticals, videos, etc.), as well as
small, commonly inherited metadata like due dates. More detailed information
about any particular block of content is stored in a separate collection as
Definitions.

Both Structures and Definitions are immutable in Split. When a course is edited,
a new Structure is created, and the Active Versions entry for a course is
updated to point to that new Structure. In that way, we never get a partially
applied edit -- it either succeeds or fails atomically. The Active Versions
entry for a Course has pointers to "published" and "draft" Structures. There is
also a special "library" pointer that is only used by Content Libraries. We do
not need to distinguish between these for the purposes of cleanup.

The problem is that Structure documents have become far larger than they were
intended to be, and we never created code to properly clean them up. As such, it
is not uncommon for the majority of Mongo storage space to be used by old
Structure documents that are completely unused (and are unreachable) by LMS or
Studio.

This module provides cleanup functionality with various tweakable options for
how much history to preserve. For simplicity, it reads all Structure IDs into
memory instead of working on subsets of the data. As a practical matter, this
means that it will work for databases with up to about 10 million Structures
before RAM usage starts to become a problem.
"""
import json
import logging
import os
import sys
import time
from collections import deque, namedtuple
from itertools import count, takewhile

from bson.objectid import ObjectId
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from pymongo import MongoClient, UpdateOne

LOG = logging.getLogger('structures')


class StructuresGraph(namedtuple('DatabaseSummary', 'branches structures')):
    """
    This summarizes the entire set of Structure relationships in a database.

    Each Structure represents a saved state for the Course or Content Library.
    For each branch ("published", "draft", or "library"), there is a sequence of
    Structures that starts with an Original and ends in an Active Structure::

      Original -> (Intermediate 1) -> (Intermediate 2) -> ... -> Active

    `branches` is a list of ActiveVersionBranch objects representing what's
    currently live on the LMS and Studio. Active Structures referenced in this
    list cannot be removed because it would break the site for users.

    `structures` is a dict of Structure IDs (Strings) to Structure objects
    (described above). All the Structure objects store ID locations to their
    parent and original Structures rather than having direct references to them.
    This is partly because we don't really need to traverse the vast majority of
    the graph. Look at `ChangePlan` for details on why that is.
    """
    def traverse_ids(self, start_id, limit=None, include_start=False):
        """
        Given a Structure ID to start from, this will iterate through the
        previous_id chain, for up to `limit` parent relationships. If `limit` is
        None, it will keep going until it gets through the Original.
        """
        if include_start:
            yield start_id

        current_id = start_id
        i = 0
        while current_id in self.structures:
            if limit is not None and i >= limit:
                return

            current_id = self.structures[current_id].previous_id
            if current_id is None:
                return

            yield current_id
            i += 1


class ActiveVersionBranch(namedtuple('ActiveVersionBranch', 'id branch structure_id key edited_on')):
    """
    An Active Version document can point to multiple branches (e.g. "published",
    "draft"). This object represensts one of those branches.

    The value for `branch` can be "draft-branch", "published-branch", or
    "library". All Courses have a draft-branch and a published-branch. Content
    Libraries have only a "library" branch.

    The value for `key` is the Opaque Key representing the Course or Library,
    mostly for debugging purposes (they're not a part of the plan file).

    The value for `edited_on` is a timestamp showing the last time the Active
    Version document was modified -- for a Course, this means when *either* the
    published-branch or draft-branch was most recently modified. Again, this is
    not used for pruning, but just provides debug information.
    """
    def __str__(self):
        return "Active Version {} [{}] {} for {}".format(
            self.id,
            self.edited_on.strftime('%Y-%m-%d %H:%M:%S'),
            self.branch,
            self.key,
        )


class Structure(namedtuple('Structure', 'id original_id previous_id')):
    """
    The parts of a SplitMongo Structure document that we care about, namely the
    ID (str'd version of the ObjectID), and the IDs of the Original and Previous
    structure documents. The previous_id may be None ()

    We use a namedtuple for this specifically because it's more space efficient
    than a dict, and we can have millions of Structures.
    """
    def is_original(self):
        """Is this Structure an original (i.e. should never be deleted)?"""
        return self.previous_id is None


class ChangePlan(namedtuple('ChangePlan', 'delete update_parents')):
    """
    Summary of the pruning actions we want a Backend to take.

    The idea of having this data structure and being able to serialize it is so
    that we can save our plan of action somewhere for debugging, failure
    recovery, and batching updates.

    `delete` is a list of Structure IDs we want to delete.

    `update_parents` is a list of (structure_id, new_previous_id) tuples
    representing the previous_id updates we need to make.

    A ChangePlan is just a declarative. It is the responsibility of the
    Backend to figure out how to implement a ChangePlan safely and efficiently
    in order to do the actual updates.
    """
    def dump(self, file_obj):
        """Serialize ChangePlan to a file (JSON format)."""
        json.dump(
            {
                "delete": self.delete,
                "update_parents": self.update_parents,
            },
            file_obj,
            indent=2,
        )
        LOG.info(
            "Wrote Change Plan: %s (%s deletions, %s parent updates)",
            os.path.realpath(file_obj.name),
            len(self.delete),
            len(self.update_parents)
        )

    @classmethod
    def load(cls, file_obj):
        """Load a ChangePlan from a JSON file. Takes a file object."""
        data = json.load(file_obj)
        return cls(
            delete=data["delete"], update_parents=data["update_parents"]
        )

    @classmethod
    def create(cls, structures_graph, num_intermediate_structures, ignore_missing, dump_structures, details_file=None):
        """
        Given a StructuresGraph and a target number for intermediate Structures
        to preserve, return a ChangePlan that represents the changes needed to
        prune the database. The overall strategy is to iterate through all
        Active Structures, walk back through the ancestors, and add all the
        Structure IDs we should save to a set. After we have our save set, we
        know that we can delete all other structures without worrying about
        whether those Structures are reachable or knowing what their
        relationships are. This keeps things simpler, and means that we should
        be more resilient to failures when pruning.

        Structure documents exist in chains of parent/child relationships,
        starting with an Original Structure, having some number of Intermediate
        Structures, and ending in an Active Structure::

          Original -> (Intermediate 1) -> (Intermediate 2) -> ... -> Active

        Pruning Rules:

        1. All Active Structures must be preserved, as those are being used by
           the LMS and Studio to serve course content.

        2. All Original Structures should be preserved, since those are used by
           the LMS and Studio to determine common shared ancestry between
           Structures.

        3. Up to `num_intermediate_structures` Intermediate Structures will be
           kept. These Structures are not actually used in edx-platform code,
           but they are sometimes used by developers to allow emergency reverts
           in course team support situations (e.g. someone accidentally wiped
           out their course with a bad import).

        4. The oldest preserved Intermediate Structure will be modified so that
           its `previous_id` is updated to point to the Original Structure. That
           way, we're not preserving references to the IDs of Structures that
           have been pruned.

        """
        structure_ids_to_save = set()
        set_parent_to_original = set()

        branches, structures = structures_graph

        # Figure out which Structures to save...
        for branch in branches:
            # Anything that's actively being pointed to (is the head of a branch)
            # must be preserved. This is what's being served by Studio and LMS.
            active_structure_id = branch.structure_id
            structure_ids_to_save.add(active_structure_id)

            # All originals will be saved.
            structure_ids_to_save.add(structures[active_structure_id].original_id)

            # Save up to `num_intermediate_structures` intermediate nodes
            int_structure_ids_to_save = structures_graph.traverse_ids(
                active_structure_id, limit=num_intermediate_structures
            )
            for int_structure_id in int_structure_ids_to_save:
                structure_ids_to_save.add(int_structure_id)

        missing_structure_ids = structure_ids_to_save - structures.keys()

        if ignore_missing:
            # Remove missing structures since we can't save them
            structure_ids_to_save -= missing_structure_ids
        elif len(missing_structure_ids) > 0:
            LOG.error("Missing structures detected")
            sys.exit(1)

        # Figure out what links to rewrite -- the oldest structure to save that
        # isn't an original.
        for branch in branches:
            rewrite_candidates = takewhile(
                lambda s: s in structure_ids_to_save and not structures[s].is_original(),
                structures_graph.traverse_ids(branch.structure_id, include_start=True)
            )
            # `last_seen` will have the last structure_id from the
            # `rewrite_candidates` iterable.
            last_seen = deque(rewrite_candidates, 1)
            if last_seen:
                structure = structures[last_seen.pop()]
                # Don't do a rewrite if it's just a no-op...
                if structure.original_id != structure.previous_id:
                    set_parent_to_original.add(structure.id)

        # Sort the items in the ChangePlan. This might not be helpful, but I'm
        # hoping that it will keep disk changes more localized and not thrash
        # things as much as randomly distributed deletes. Mongo ObjectIDs are
        # ordered (they have a timestamp component).
        change_plan = cls(
            delete=sorted(structures.keys() - structure_ids_to_save),
            update_parents=sorted(
                (s_id, structures[s_id].original_id)
                for s_id in set_parent_to_original
            )
        )

        if details_file:
            change_plan.write_details(
                details_file, structures_graph, structure_ids_to_save, set_parent_to_original
            )

        if dump_structures:
            active_structure_ids = {branch.structure_id for branch in branches}
            for sid in structures:
                save = sid in structure_ids_to_save
                active = sid in active_structure_ids
                relink = sid in set_parent_to_original
                prev_misssing = structures[sid].previous_id is not None and structures[sid].previous_id not in structures
                LOG.info(f"DUMP id: {sid}, original_id: {structures[sid].original_id}, previous_id: {structures[sid].previous_id}, save: {save}, active: {active}, prev_missing: {prev_misssing}, rewrite_previous_to_original: {relink}")

        for missing_structure_id in missing_structure_ids:
            active_structure_ids = {branch.structure_id for branch in branches}

            LOG.error(f"Missing structure ID: {missing_structure_id}")
            original_ids = set()
            for structure in structures.values():
                if structure.previous_id == missing_structure_id:
                    save = structure.id in structure_ids_to_save
                    active = structure.id in active_structure_ids
                    relink = structure.id in set_parent_to_original
                    prev_misssing = structure.previous_id is not None and structure.previous_id not in structures
                    LOG.info(f"Structure {structure.id} points to missing structure with ID: {structure.previous_id}")
                    original_ids.add(structure.original_id)

            active_structure_ids = {branch.structure_id for branch in branches}

            branches_to_log = []

            LOG.info(f"Looking for branches that lead to missing ID {missing_structure_id}")
            for branch in branches:
                structure = structures[branch.structure_id]
                if structure.original_id in original_ids:
                    for sid in structures_graph.traverse_ids(branch.structure_id):
                        if sid not in structures:
                            branches_to_log.append(branch)

            for branch in branches_to_log:
                structure = structures[branch.structure_id]

                LOG.info(f"Branch: {branch}")

                save = branch.structure_id in structure_ids_to_save
                active = branch.structure_id in active_structure_ids
                relink = branch.structure_id in set_parent_to_original
                prev_misssing = structure.previous_id is not None and structure.previous_id not in structures

                for sid in structures_graph.traverse_ids(branch.structure_id, include_start=True):
                    if sid in structures:
                        save = sid in structure_ids_to_save
                        active = sid in active_structure_ids
                        relink = sid in set_parent_to_original
                        prev_misssing = structures[sid].previous_id is not None and structures[sid].previous_id not in structures
                        LOG.info(f"id: {sid}, original_id: {structures[sid].original_id}, previous_id: {structures[sid].previous_id}, save: {save}, active: {active}, prev_missing: {prev_misssing}, rewrite_previous_to_original: {relink}")

        return change_plan

    @staticmethod
    def write_details(details_file, structures_graph, structure_ids_to_save, set_parent_to_original):
        """
        Simple dump of the changes we're going to make to the database.

        This method requires information that we don't actually keep in the
        ChangePlan file, such as the Course IDs and edit times. Because of this,
        it can only be created at the time the ChangePlan is being generated,
        and cannot be derived from an existing ChangePlan. The goal was to
        provide this debug information while keeping the ChangePlan file format
        as stupidly simple as possible.
        """
        branches, structures = structures_graph
        active_structure_ids = {branch.structure_id for branch in branches}

        def text_for(s_id):
            """Helper method to format Structures consistently."""
            action = "+" if s_id in structure_ids_to_save else "-"
            notes = []
            if s_id in active_structure_ids:
                notes.append("(active)")
            if s_id in set_parent_to_original:
                notes.append("(re-link to original)")
            if s_id in structures and structures[s_id].is_original():
                notes.append("(original)")

            if notes:
                return "{} {} {}".format(action, s_id, " ".join(notes))

            return "{} {}".format(action, s_id)  # noqa: UP032

        print("== Summary ==", file=details_file)
        print("Active Version Branches: {}".format(len(branches)), file=details_file)  # noqa: UP032
        print("Total Structures: {}".format(len(structures)), file=details_file)  # noqa: UP032
        print("Structures to Save: {}".format(len(structure_ids_to_save)), file=details_file)  # noqa: UP032
        print("Structures to Delete: {}".format(len(structures) - len(structure_ids_to_save)), file=details_file)  # noqa: UP032
        print("Structures to Rewrite Parent Link: {}".format(len(set_parent_to_original)), file=details_file)  # noqa: UP032
        print("\n== Active Versions ==", file=details_file)

        for branch in branches:
            print("{}".format(branch), file=details_file)  # noqa: UP032
            for structure_id in structures_graph.traverse_ids(branch.structure_id, include_start=True):
                print(text_for(structure_id), file=details_file)
            print("", file=details_file)

        LOG.info(
            "Wrote Change Details File: %s", os.path.realpath(details_file.name)
        )


class SplitMongoBackend:
    """
    Interface to the MongoDB backend. This is currently the only supported KV
    store for the Split(DraftVersioning)ModuleStore, but having this as a
    separate class makes it easier to stub in test data.

    The methods on this class should accept and return backend-agnostic data
    structures, so no BSON details should leak out.
    """
    def __init__(self, mongo_connection_str, db_name):
        self._db = MongoClient(
            mongo_connection_str,
            connectTimeoutMS=2000,
            socketTimeoutMS=300000,  # *long* operations
            serverSelectionTimeoutMS=2000
        )
        self._active_versions = self._db[db_name].modulestore.active_versions
        self._structures = self._db[db_name].modulestore.structures

    def structures_graph(self, delay, batch_size):
        """
        Return StructuresGraph for the entire modulestore.

        `batch_size` is the number of structure documents we pull at a time.
        `delay` is the delay in seconds between batch queries.

        This has one slight complication. A StructuresGraph is expected to be a
        consistent view of the database, but MongoDB doesn't offer a "repeatable
        read" transaction isolation mode. That means that Structures may be
        added at any time between our database calls. Because of this, we have
        to be careful in stitching together something that is safe. The
        guarantees we try to make about the StructuresGraph being returned are:

          1. Every Structure ID in `active_structure_ids` is also in `structures`
          2. If `branches` is stale and there is a new Structure that is Active
             in the database, it is *not* in `structures`.

        Scenario A: We fetch branches, then structures
          1. Get Branches (and thus Active Structure IDs)
          2. New Structures created by Studio
          3. Get all Structures

        It is almost certainly the case that the new Structures created in (2)
        should be active. Our algorithm works by starting from the Active
        Structure IDs that we know about, making a "save" list, and then
        deleting all other Structures. The problem in this scenario is that we
        fetch the new Structures in (3), but we don't know that they're Active
        because our `active_structure_ids` comes from (1) and is stale. So we
        would in fact delete what should be Active Structures.

        Scenario B: We fetch structures, then branches
          1. Get all Structures
          2. New Structures created by Studio
          3. Get Branches (and thus Active Structure IDs)

        In this scenario, we may see Active Structure IDs that are not in
        our Structures dict. This is bad because we won't know how to crawl
        their ancestry and mark the appropriate Structure IDs to be saved.

        So the approach we take is Scenario B with a fallback. After we fetch
        everything, we go through the Active Structure IDs and make sure that
        those Structures and their ancestors exist in `structures`. If they
        don't, we make extra fetches to get them. Misses should be rare, so it
        shouldn't have a drastic performance impact overall.

        Note that it's safe if the ChangePlan as a whole is a little stale, so
        long as it's internally consistent. We only ever delete Structures that
        are in the `structures` doc, so a new Active Version that we're
        completely unaware of will be left alone.
        """
        structures = self._all_structures(delay, batch_size)
        branches = self._all_branches()

        # Guard against the race condition that branch.structure_id or its
        # ancestors are not in `structures`. Make sure that we add those.
        LOG.info(
            "Checking for missing Structures (a small number are expected "
            "unless edits are disabled during change plan creation)."
        )
        missing_count = 0
        for branch in branches:
            structure_id = branch.structure_id
            while structure_id and (structure_id not in structures):
                structures[structure_id] = self._get_structure(structure_id)
                missing_count += 1
                LOG.warning(
                    "Structure %s linked from Active Structure %s (%s) fetched.",
                    structure_id,
                    branch.structure_id,
                    branch.key,
                )
                structure_id = structures[structure_id].previous_id

        LOG.info("Finished checking for missing Structures, found %s", missing_count)

        return StructuresGraph(branches, structures)

    def _all_structures(self, delay, batch_size):
        """
        Return a dict mapping Structure IDs to Structures for all Structures in
        the database.

        `batch_size` is the number of structure documents we pull at a time.
        `delay` is the delay in seconds between batch queries.
        """
        LOG.info("Fetching all known Structures (this might take a while)...")
        LOG.info("Delay in seconds: %s, Batch size: %s", delay, batch_size)

        # Important to keep this as a generator to limit memory usage.
        parsed_docs = (
            self.parse_structure_doc(doc)
            for doc
            in self._structures_from_db(delay, batch_size)
        )
        structures = {structure.id: structure for structure in parsed_docs}
        LOG.info("Fetched %s Structures", len(structures))

        return structures

    def _structures_from_db(self, delay, batch_size):
        """
        Iterate through all Structure documents in the database.

        `batch_size` is the number of structure documents we pull at a time.
        `delay` is the delay in seconds between batch queries.
        """
        cursor = self._structures.find(
            projection=['original_version', 'previous_version']
        )
        cursor.batch_size(batch_size)
        for i, structure_doc in enumerate(cursor, start=1):
            yield structure_doc
            if i % batch_size == 0:
                LOG.info("Structure Cursor at %s (%s)", i, structure_doc['_id'])
                time.sleep(delay)

    def _all_branches(self):
        """Retrieve list of all ActiveVersionBranch objects in the database."""
        branches = []
        LOG.info("Fetching all Active Version Branches...")

        for av_doc in self._active_versions.find():
            for branch, obj_id in av_doc['versions'].items():
                structure_id = str(obj_id)
                if branch == 'library':
                    key = LibraryLocator(av_doc['org'], av_doc['course'])
                else:
                    key = CourseLocator(av_doc['org'], av_doc['course'], av_doc['run'])

                branches.append(
                    ActiveVersionBranch(
                        str(av_doc['_id']),
                        branch,
                        structure_id,
                        key,
                        av_doc['edited_on'],
                    )
                )

        LOG.info("Fetched %s Active Version Branches", len(branches))

        return sorted(branches)

    def _get_structure(self, structure_id):
        """Get an individual Structure from the database."""
        structure_doc = self._structures.find_one(
            {'_id': ObjectId(structure_id)},
            projection=['original_version', 'previous_version']
        )
        return self.parse_structure_doc(structure_doc)

    def update(self, change_plan, delay=1000, batch_size=1000, start=None):
        """
        Update the backend according to the relinking and deletions specified in
        the change_plan.
        """
        # Step 1: Relink - Change the previous pointer for the oldest structure
        # we want to keep, so that it points back to the original. We never
        # delete the original. Relinking happens before deletion so that we
        # never leave our course in a broken state (at worst, parts of it
        # become unreachable).
        self._update_parents(change_plan.update_parents, delay, batch_size)

        # Step 2: Delete unused Structures
        self._delete(change_plan.delete, delay, batch_size, start)

    def _update_parents(self, id_parent_pairs, delay, batch_size):
        """
        Update Structure parent relationships.

        `id_parent_pairs` is a list of tuples, where the first element of each
        tuple is a Structure ID (str) to target, and the second element is the
        Structure ID that will be the new parent of the first element.
        """
        for id_parent_pairs_batch in self.batch(id_parent_pairs, batch_size):
            updates = [
                UpdateOne(
                    {'_id': ObjectId(structure_id)},
                    {'$set': {'previous_version': ObjectId(previous_id)}}
                )
                for structure_id, previous_id in id_parent_pairs_batch
            ]
            result = self._structures.bulk_write(updates)
            LOG.info(
                "Updated %s/%s parent relationships.",
                result.bulk_api_result['nModified'],
                result.bulk_api_result['nMatched'],
            )
            time.sleep(delay)

    def _delete(self, structure_ids, delay, batch_size, start=None):
        """
        Delete old structures in batches.

        `structure_ids` is a list of Structure IDs to delete.
        `delay` is the delay in seconds (floats are ok) between batch deletes.
        `batch_size` is how many we try to delete in each batch statement.
        """
        s_ids_with_offset = self.iter_from_start(structure_ids, start)
        for structure_ids_batch in self.batch(s_ids_with_offset, batch_size):
            result = self._structures.delete_many(
                {
                    '_id': {
                        '$in': [ObjectId(s_id) for s_id in structure_ids_batch]
                    }
                }
            )
            LOG.info(
                "Deleted %s/%s Structures: %s - %s",
                result.deleted_count,
                len(structure_ids_batch),
                structure_ids_batch[0],
                structure_ids_batch[-1],
            )
            time.sleep(delay)

    @staticmethod
    def parse_structure_doc(structure_doc):
        """
        Structure docs are pretty big, but we only care about three top level
        fields, all of which are ObjectIds:

          _id: The Structure ID

          previous_version: The Structure ID for the parent. An Original
                            Structure will have None for this field.

          original_version: The Original Structure that this Structure and all
                            its ancestors are ultimately dervied from. An
                            Original Structure points to itself with this field.
        """
        _id = str(structure_doc['_id'])
        original_id = str(structure_doc['original_version'])
        previous_id = structure_doc['previous_version']
        if previous_id is not None:
            previous_id = str(previous_id)
        return Structure(_id, original_id, previous_id)

    @staticmethod
    def batch(iterable, batch_size):
        """Yield lists of up to `batch_size` in length from `iterable`."""
        iterator = iter(iterable)
        curr_batch = []
        for i in count(1):
            try:
                curr_batch.append(next(iterator))
                if i % batch_size == 0:
                    yield curr_batch
                    curr_batch = []
            except StopIteration:
                break
        if curr_batch:
            yield curr_batch

    @staticmethod
    def iter_from_start(structure_ids, start=None):
        """
        Yields from an iterable once it encounters the `start` value. If `start`
        is None, just yields from the beginning.
        """
        if start is None:
            for structure_id in structure_ids:
                yield structure_id
            return

        for structure_id in structure_ids:
            if structure_id < start:
                continue
            yield structure_id
