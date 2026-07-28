"""
Tests for bulk operations in Split Modulestore.
"""
# pylint: disable=protected-access


import copy
import unittest
from unittest.mock import MagicMock, Mock, call

import ddt
from bson.objectid import ObjectId
from opaque_keys.edx.locator import CourseLocator

from xmodule.modulestore.split_mongo.mongo_connection import MongoPersistenceBackend
from xmodule.modulestore.split_mongo.split import SplitBulkWriteMixin

VERSION_GUID_DICT = {
    'SAMPLE_VERSION_GUID': 'deadbeef1234' * 2,
    'SAMPLE_UNICODE_VERSION_GUID': 'deadbeef1234' * 2,
    'BSON_OBJECTID': ObjectId()
}
SAMPLE_GUIDS_LIST = ['SAMPLE_VERSION_GUID', 'SAMPLE_UNICODE_VERSION_GUID', 'BSON_OBJECTID']


class TestBulkWriteMixin(unittest.TestCase):  # pylint: disable=missing-class-docstring

    def setUp(self):
        super().setUp()
        self.bulk = SplitBulkWriteMixin()
        self.bulk.SCHEMA_VERSION = 1
        self.clear_cache = self.bulk._clear_cache = Mock(name='_clear_cache')
        self.conn = self.bulk.db_connection = MagicMock(name='db_connection', spec=MongoPersistenceBackend)
        self.conn.get_course_index.return_value = {'initial': 'index'}

        self.course_key = CourseLocator('org', 'course', 'run-a', branch='test')
        self.course_key_b = CourseLocator('org', 'course', 'run-b', branch='test')
        self.structure = {'this': 'is', 'a': 'structure', '_id': ObjectId()}
        self.definition = {'this': 'is', 'a': 'definition', '_id': ObjectId()}
        self.index_entry = {'this': 'is', 'an': 'index'}

    def assertConnCalls(self, *calls):
        assert list(calls) == self.conn.mock_calls

    def assertCacheNotCleared(self):
        assert not self.clear_cache.called


class TestBulkWriteMixinPreviousTransaction(TestBulkWriteMixin):
    """
    Verify that opening and closing a transaction doesn't affect later behaviour.
    """
    def setUp(self):
        super().setUp()
        self.bulk._begin_bulk_operation(self.course_key)
        self.bulk.insert_course_index(self.course_key, MagicMock('prev-index-entry'))
        self.bulk.update_structure(self.course_key, {'this': 'is', 'the': 'previous structure', '_id': ObjectId()})
        self.bulk._end_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.clear_cache.reset_mock()


@ddt.ddt
class TestBulkWriteMixinClosed(TestBulkWriteMixin):
    """
    Tests of the bulk write mixin when bulk operations aren't active.
    """

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_no_bulk_read_structure(self, version_guid_name):
        # Reading a structure when no bulk operation is active should just call
        # through to the db_connection
        version_guid = VERSION_GUID_DICT[version_guid_name]
        result = self.bulk.get_structure(self.course_key, version_guid)
        self.assertConnCalls(
            call.get_structure(self.course_key.as_object_id(version_guid), self.course_key)
        )
        assert result == self.conn.get_structure.return_value
        self.assertCacheNotCleared()

    def test_no_bulk_write_structure(self):
        # Writing a structure when no bulk operation is active should just
        # call through to the db_connection. It should also clear the
        # system cache
        self.bulk.update_structure(self.course_key, self.structure)
        self.assertConnCalls(call.insert_structure(self.structure, self.course_key))
        self.clear_cache.assert_called_once_with(self.structure['_id'])

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_no_bulk_read_definition(self, version_guid_name):
        # Reading a definition when no bulk operation is active should just call
        # through to the db_connection
        version_guid = VERSION_GUID_DICT[version_guid_name]
        result = self.bulk.get_definition(self.course_key, version_guid)
        self.assertConnCalls(
            call.get_definition(
                self.course_key.as_object_id(version_guid),
                self.course_key
            )
        )
        assert result == self.conn.get_definition.return_value

    def test_no_bulk_write_definition(self):
        # Writing a definition when no bulk operation is active should just
        # call through to the db_connection.
        self.bulk.update_definition(self.course_key, self.definition)
        self.assertConnCalls(call.insert_definition(self.definition, self.course_key))

    @ddt.data(True, False)
    def test_no_bulk_read_index(self, ignore_case):
        # Reading a course index when no bulk operation is active should just call
        # through to the db_connection
        result = self.bulk.get_course_index(self.course_key, ignore_case=ignore_case)
        self.assertConnCalls(call.get_course_index(self.course_key, ignore_case))
        assert result == self.conn.get_course_index.return_value
        self.assertCacheNotCleared()

    def test_no_bulk_write_index(self):
        # Writing a course index when no bulk operation is active should just call
        # through to the db_connection
        self.bulk.insert_course_index(self.course_key, self.index_entry)
        self.assertConnCalls(call.insert_course_index(self.index_entry, self.course_key))
        self.assertCacheNotCleared()

    def test_out_of_order_end(self):
        # Calling _end_bulk_operation without a corresponding _begin...
        # is a noop
        self.bulk._end_bulk_operation(self.course_key)

    def test_write_new_index_on_close(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.insert_course_index(self.course_key, self.index_entry)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.conn.insert_course_index.assert_called_once_with(self.index_entry, self.course_key)

    def test_write_updated_index_on_close(self):
        old_index = {'this': 'is', 'an': 'old index'}
        self.conn.get_course_index.return_value = old_index
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.insert_course_index(self.course_key, self.index_entry)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.conn.update_course_index.assert_called_once_with(
            self.index_entry,
            from_index=old_index,
            course_context=self.course_key,
        )

    def test_write_structure_on_close(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_structure(self.course_key, self.structure)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertConnCalls(call.insert_structure(self.structure, self.course_key))

    def test_write_multiple_structures_on_close(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_structure(self.course_key.replace(branch='a'), self.structure)
        other_structure = {'another': 'structure', '_id': ObjectId()}
        self.bulk.update_structure(self.course_key.replace(branch='b'), other_structure)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertCountEqual(  # noqa: PT009
            [
                call.insert_structure(self.structure, self.course_key),
                call.insert_structure(other_structure, self.course_key)
            ],
            self.conn.mock_calls
        )

    def test_write_index_and_definition_on_close(self):
        original_index = {'versions': {}}
        self.conn.get_course_index.return_value = copy.deepcopy(original_index)
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_definition(self.course_key, self.definition)
        self.bulk.insert_course_index(self.course_key, {'versions': {self.course_key.branch: self.definition['_id']}})  # pylint: disable=no-member
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertConnCalls(
            call.insert_definition(self.definition, self.course_key),
            call.update_course_index(
                {'versions': {self.course_key.branch: self.definition['_id']}},  # pylint: disable=no-member
                from_index=original_index,
                course_context=self.course_key
            )
        )

    def test_write_index_and_multiple_definitions_on_close(self):
        original_index = {'versions': {'a': ObjectId(), 'b': ObjectId()}}
        self.conn.get_course_index.return_value = copy.deepcopy(original_index)
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_definition(self.course_key.replace(branch='a'), self.definition)
        other_definition = {'another': 'definition', '_id': ObjectId()}
        self.bulk.update_definition(self.course_key.replace(branch='b'), other_definition)
        self.bulk.insert_course_index(self.course_key, {'versions': {'a': self.definition['_id'], 'b': other_definition['_id']}})  # pylint: disable=line-too-long
        self.bulk._end_bulk_operation(self.course_key)
        self.assertCountEqual(  # noqa: PT009
            [
                call.insert_definition(self.definition, self.course_key),
                call.insert_definition(other_definition, self.course_key),
                call.update_course_index(
                    {'versions': {'a': self.definition['_id'], 'b': other_definition['_id']}},
                    from_index=original_index,
                    course_context=self.course_key,
                )
            ],
            self.conn.mock_calls
        )

    def test_write_definition_on_close(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_definition(self.course_key, self.definition)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertConnCalls(call.insert_definition(self.definition, self.course_key))

    def test_write_multiple_definitions_on_close(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_definition(self.course_key.replace(branch='a'), self.definition)
        other_definition = {'another': 'definition', '_id': ObjectId()}
        self.bulk.update_definition(self.course_key.replace(branch='b'), other_definition)
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertCountEqual(  # noqa: PT009
            [
                call.insert_definition(self.definition, self.course_key),
                call.insert_definition(other_definition, self.course_key)
            ],
            self.conn.mock_calls
        )

    def test_write_index_and_structure_on_close(self):
        original_index = {'versions': {}}
        self.conn.get_course_index.return_value = copy.deepcopy(original_index)
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_structure(self.course_key, self.structure)
        self.bulk.insert_course_index(self.course_key, {'versions': {self.course_key.branch: self.structure['_id']}})  # pylint: disable=no-member
        self.assertConnCalls()
        self.bulk._end_bulk_operation(self.course_key)
        self.assertConnCalls(
            call.insert_structure(self.structure, self.course_key),
            call.update_course_index(
                {'versions': {self.course_key.branch: self.structure['_id']}},  # pylint: disable=no-member
                from_index=original_index,
                course_context=self.course_key,
            )
        )

    def test_write_index_and_multiple_structures_on_close(self):
        original_index = {'versions': {'a': ObjectId(), 'b': ObjectId()}}
        self.conn.get_course_index.return_value = copy.deepcopy(original_index)
        self.bulk._begin_bulk_operation(self.course_key)
        self.conn.reset_mock()
        self.bulk.update_structure(self.course_key.replace(branch='a'), self.structure)
        other_structure = {'another': 'structure', '_id': ObjectId()}
        self.bulk.update_structure(self.course_key.replace(branch='b'), other_structure)
        self.bulk.insert_course_index(self.course_key, {'versions': {'a': self.structure['_id'], 'b': other_structure['_id']}})  # pylint: disable=line-too-long
        self.bulk._end_bulk_operation(self.course_key)
        self.assertCountEqual(  # noqa: PT009
            [
                call.insert_structure(self.structure, self.course_key),
                call.insert_structure(other_structure, self.course_key),
                call.update_course_index(
                    {'versions': {'a': self.structure['_id'], 'b': other_structure['_id']}},
                    from_index=original_index,
                    course_context=self.course_key,
                )
            ],
            self.conn.mock_calls
        )

    def test_version_structure_creates_new_version(self):
        assert self.bulk.version_structure(self.course_key, self.structure, 'user_id')['_id'] != self.structure['_id']

    def test_version_structure_new_course(self):
        self.conn.get_course_index.return_value = None
        self.bulk._begin_bulk_operation(self.course_key)
        version_result = self.bulk.version_structure(self.course_key, self.structure, 'user_id')
        get_result = self.bulk.get_structure(self.course_key, version_result['_id'])
        assert version_result == get_result


class TestBulkWriteMixinClosedAfterPrevTransaction(TestBulkWriteMixinClosed, TestBulkWriteMixinPreviousTransaction):  # pylint: disable=test-inherits-tests
    """
    Test that operations on with a closed transaction aren't affected by a previously executed transaction
    """
    pass  # pylint: disable=unnecessary-pass


@ddt.ddt
class TestBulkWriteMixinFindMethods(TestBulkWriteMixin):
    """
    Tests of BulkWriteMixin methods for finding many structures or indexes
    """

    def test_no_bulk_find_matching_course_indexes(self):
        branch = Mock(name='branch')
        search_targets = MagicMock(name='search_targets')
        org_targets = None
        self.conn.find_matching_course_indexes.return_value = [Mock(name='result')]
        result = self.bulk.find_matching_course_indexes(branch, search_targets)
        self.assertConnCalls(call.find_matching_course_indexes(
            branch,
            search_targets,
            org_targets,
            course_keys=None
        )
        )
        assert result == self.conn.find_matching_course_indexes.return_value
        self.assertCacheNotCleared()

    @ddt.data(
        (None, None, [], []),
        (
            'draft',
            None,
            [{'versions': {'draft': '123'}}],
            [
                {'versions': {'published': '123'}},
                {}
            ],
        ),
        (
            'draft',
            {'f1': 'v1'},
            [{'versions': {'draft': '123'}, 'search_targets': {'f1': 'v1'}}],
            [
                {'versions': {'draft': '123'}, 'search_targets': {'f1': 'value2'}},
                {'versions': {'published': '123'}, 'search_targets': {'f1': 'v1'}},
                {'search_targets': {'f1': 'v1'}},
                {'versions': {'draft': '123'}},
            ],
        ),
        (
            None,
            {'f1': 'v1'},
            [
                {'versions': {'draft': '123'}, 'search_targets': {'f1': 'v1'}},
                {'versions': {'published': '123'}, 'search_targets': {'f1': 'v1'}},
                {'search_targets': {'f1': 'v1'}},
            ],
            [
                {'versions': {'draft': '123'}, 'search_targets': {'f1': 'v2'}},
                {'versions': {'draft': '123'}, 'search_targets': {'f2': 'v1'}},
                {'versions': {'draft': '123'}},
            ],
        ),
        (
            None,
            {'f1': 'v1', 'f2': 2},
            [
                {'search_targets': {'f1': 'v1', 'f2': 2}},
                {'search_targets': {'f1': 'v1', 'f2': 2}},
            ],
            [
                {'versions': {'draft': '123'}, 'search_targets': {'f1': 'v1'}},
                {'search_targets': {'f1': 'v1'}},
                {'versions': {'draft': '123'}, 'search_targets': {'f1': 'v2'}},
                {'versions': {'draft': '123'}},
            ],
        ),
    )
    @ddt.unpack
    def test_find_matching_course_indexes(self, branch, search_targets, matching, unmatching):
        db_indexes = [{'org': 'what', 'course': 'this', 'run': 'needs'}]
        for n, index in enumerate(matching + unmatching):
            course_key = CourseLocator('org', 'course', f'run{n}')
            self.bulk._begin_bulk_operation(course_key)
            for attr in ['org', 'course', 'run']:
                index[attr] = getattr(course_key, attr)
            self.bulk.insert_course_index(course_key, index)

        expected = matching + db_indexes
        self.conn.find_matching_course_indexes.return_value = db_indexes
        result = self.bulk.find_matching_course_indexes(branch, search_targets)
        self.assertCountEqual(result, expected)  # noqa: PT009
        for item in unmatching:
            assert item not in result

    def test_no_bulk_find_structures_by_id(self):
        ids = [Mock(name='id')]
        self.conn.find_structures_by_id.return_value = [MagicMock(name='result')]
        result = self.bulk.find_structures_by_id(ids)
        self.assertConnCalls(call.find_structures_by_id(ids))
        assert result == self.conn.find_structures_by_id.return_value
        self.assertCacheNotCleared()

    @ddt.data(
        ([], [], []),
        ([1, 2, 3], [1, 2], [1, 2]),
        ([1, 2, 3], [1], [1, 2]),
        ([1, 2, 3], [], [1, 2]),
    )
    @ddt.unpack
    def test_find_structures_by_id(self, search_ids, active_ids, db_ids):
        db_structure = lambda _id: {'db': 'structure', '_id': _id}
        active_structure = lambda _id: {'active': 'structure', '_id': _id}

        db_structures = [db_structure(_id) for _id in db_ids if _id not in active_ids]
        for n, _id in enumerate(active_ids):
            course_key = CourseLocator('org', 'course', f'run{n}')
            self.bulk._begin_bulk_operation(course_key)
            self.bulk.update_structure(course_key, active_structure(_id))

        self.conn.find_structures_by_id.return_value = db_structures
        results = self.bulk.find_structures_by_id(search_ids)
        self.conn.find_structures_by_id.assert_called_once_with(list(set(search_ids) - set(active_ids)))
        for _id in active_ids:
            if _id in search_ids:
                assert active_structure(_id) in results
            else:
                assert active_structure(_id) not in results
        for _id in db_ids:
            if _id in search_ids and _id not in active_ids:
                assert db_structure(_id) in results
            else:
                assert db_structure(_id) not in results

    @ddt.data(
        ([], [], []),
        ([1, 2, 3], [1, 2], [1, 2]),
        ([1, 2, 3], [1], [1, 2]),
        ([1, 2, 3], [], [1, 2]),
    )
    @ddt.unpack
    def test_get_definitions(self, search_ids, active_ids, db_ids):
        db_definition = lambda _id: {'db': 'definition', '_id': _id}
        active_definition = lambda _id: {'active': 'definition', '_id': _id}

        db_definitions = [db_definition(_id) for _id in db_ids if _id not in active_ids]
        self.bulk._begin_bulk_operation(self.course_key)
        for _id in active_ids:
            self.bulk.update_definition(self.course_key, active_definition(_id))

        self.conn.get_definitions.return_value = db_definitions
        results = self.bulk.get_definitions(self.course_key, search_ids)
        definitions_gotten = list(set(search_ids) - set(active_ids))
        if len(definitions_gotten) > 0:
            self.conn.get_definitions.assert_called_once_with(definitions_gotten, self.course_key)
        else:
            # If no definitions to get, then get_definitions() should *not* have been called.
            assert self.conn.get_definitions.call_count == 0
        for _id in active_ids:
            if _id in search_ids:
                assert active_definition(_id) in results
            else:
                assert active_definition(_id) not in results
        for _id in db_ids:
            if _id in search_ids and _id not in active_ids:
                assert db_definition(_id) in results
            else:
                assert db_definition(_id) not in results

    def test_get_definitions_doesnt_update_db(self):
        test_ids = [1, 2]
        db_definition = lambda _id: {'db': 'definition', '_id': _id}

        db_definitions = [db_definition(_id) for _id in test_ids]
        self.conn.get_definitions.return_value = db_definitions
        self.bulk._begin_bulk_operation(self.course_key)
        self.bulk.get_definitions(self.course_key, test_ids)
        self.bulk._end_bulk_operation(self.course_key)
        assert not self.conn.insert_definition.called


@ddt.ddt
class TestBulkWriteMixinOpen(TestBulkWriteMixin):
    """
    Tests of the bulk write mixin when bulk write operations are open
    """

    def setUp(self):
        super().setUp()
        self.bulk._begin_bulk_operation(self.course_key)

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_structure_without_write_from_db(self, version_guid_name):
        # Reading a structure before it's been written (while in bulk operation mode)
        # returns the structure from the database
        version_guid = VERSION_GUID_DICT[version_guid_name]
        result = self.bulk.get_structure(self.course_key, version_guid)
        assert self.conn.get_structure.call_count == 1
        assert result == self.conn.get_structure.return_value
        self.assertCacheNotCleared()

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_structure_without_write_only_reads_once(self, version_guid_name):
        # Reading the same structure multiple times shouldn't hit the database
        # more than once
        version_guid = VERSION_GUID_DICT[version_guid_name]
        for _ in range(2):
            result = self.bulk.get_structure(self.course_key, version_guid)
            assert self.conn.get_structure.call_count == 1
            assert result == self.conn.get_structure.return_value
            self.assertCacheNotCleared()

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_structure_after_write_no_db(self, version_guid_name):
        # Reading a structure that's already been written shouldn't hit the db at all
        version_guid = VERSION_GUID_DICT[version_guid_name]
        self.structure['_id'] = version_guid
        self.bulk.update_structure(self.course_key, self.structure)
        result = self.bulk.get_structure(self.course_key, version_guid)
        assert self.conn.get_structure.call_count == 0
        assert result == self.structure

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_structure_after_write_after_read(self, version_guid_name):
        # Reading a structure that's been updated after being pulled from the db should
        # still get the updated value
        version_guid = VERSION_GUID_DICT[version_guid_name]
        self.structure['_id'] = version_guid
        self.bulk.get_structure(self.course_key, version_guid)
        self.bulk.update_structure(self.course_key, self.structure)
        result = self.bulk.get_structure(self.course_key, version_guid)
        assert self.conn.get_structure.call_count == 1
        assert result == self.structure

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_definition_without_write_from_db(self, version_guid_name):
        # Reading a definition before it's been written (while in bulk operation mode)
        # returns the definition from the database
        version_guid = VERSION_GUID_DICT[version_guid_name]
        result = self.bulk.get_definition(self.course_key, version_guid)
        assert self.conn.get_definition.call_count == 1
        assert result == self.conn.get_definition.return_value
        self.assertCacheNotCleared()

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_definition_without_write_only_reads_once(self, version_guid_name):
        # Reading the same definition multiple times shouldn't hit the database
        # more than once
        version_guid = VERSION_GUID_DICT[version_guid_name]
        for _ in range(2):
            result = self.bulk.get_definition(self.course_key, version_guid)
            assert self.conn.get_definition.call_count == 1
            assert result == self.conn.get_definition.return_value
            self.assertCacheNotCleared()

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_definition_after_write_no_db(self, version_guid_name):
        # Reading a definition that's already been written shouldn't hit the db at all
        version_guid = VERSION_GUID_DICT[version_guid_name]
        self.definition['_id'] = version_guid
        self.bulk.update_definition(self.course_key, self.definition)
        result = self.bulk.get_definition(self.course_key, version_guid)
        assert self.conn.get_definition.call_count == 0
        assert result == self.definition

    @ddt.data(*SAMPLE_GUIDS_LIST)
    def test_read_definition_after_write_after_read(self, version_guid_name):
        # Reading a definition that's been updated after being pulled from the db should
        # still get the updated value
        version_guid = VERSION_GUID_DICT[version_guid_name]
        self.definition['_id'] = version_guid
        self.bulk.get_definition(self.course_key, version_guid)
        self.bulk.update_definition(self.course_key, self.definition)
        result = self.bulk.get_definition(self.course_key, version_guid)
        assert self.conn.get_definition.call_count == 1
        assert result == self.definition

    @ddt.data(True, False)
    def test_read_index_without_write_from_db(self, ignore_case):
        # Reading the index without writing to it should pull from the database
        result = self.bulk.get_course_index(self.course_key, ignore_case=ignore_case)
        assert self.conn.get_course_index.call_count == 1
        assert self.conn.get_course_index.return_value == result

    @ddt.data(True, False)
    def test_read_index_without_write_only_reads_once(self, ignore_case):
        # Reading the index multiple times should only result in one read from
        # the database
        for _ in range(2):
            result = self.bulk.get_course_index(self.course_key, ignore_case=ignore_case)
            assert self.conn.get_course_index.call_count == 1
            assert self.conn.get_course_index.return_value == result

    @ddt.data(True, False)
    def test_read_index_after_write(self, ignore_case):
        # Reading the index after a write still should hit the database once to fetch the
        # initial index, and should return the written index_entry
        self.bulk.insert_course_index(self.course_key, self.index_entry)
        result = self.bulk.get_course_index(self.course_key, ignore_case=ignore_case)
        assert self.conn.get_course_index.call_count == 1
        assert self.index_entry == result

    def test_read_index_ignore_case(self):
        # Reading using ignore case should find an already written entry with a different case
        self.bulk.insert_course_index(self.course_key, self.index_entry)
        result = self.bulk.get_course_index(
            self.course_key.replace(
                org=self.course_key.org.upper(),
                course=self.course_key.course.title(),
                run=self.course_key.run.upper()
            ),
            ignore_case=True
        )
        assert self.conn.get_course_index.call_count == 1
        assert self.index_entry == result

    def test_version_structure_creates_new_version_before_read(self):
        assert self.bulk.version_structure(self.course_key, self.structure, 'user_id')['_id'] != self.structure['_id']

    def test_version_structure_creates_new_version_after_read(self):
        self.conn.get_structure.return_value = copy.deepcopy(self.structure)
        self.bulk.get_structure(self.course_key, self.structure['_id'])
        assert self.bulk.version_structure(self.course_key, self.structure, 'user_id')['_id'] != self.structure['_id']

    def test_copy_branch_versions(self):
        # Directly updating an index so that the draft branch points to the published index
        # version should work, and should only persist a single structure
        self.maxDiff = None
        published_structure = {'published': 'structure', '_id': ObjectId()}
        self.bulk.update_structure(self.course_key, published_structure)
        index = {'versions': {'published': published_structure['_id']}}
        self.bulk.insert_course_index(self.course_key, index)
        index_copy = copy.deepcopy(index)
        index_copy['versions']['draft'] = index['versions']['published']
        self.bulk.update_course_index(self.course_key, index_copy)
        self.bulk._end_bulk_operation(self.course_key)
        self.conn.insert_structure.assert_called_once_with(published_structure, self.course_key)
        self.conn.update_course_index.assert_called_once_with(
            index_copy,
            from_index=self.conn.get_course_index.return_value,
            course_context=self.course_key,
        )
        self.conn.get_course_index.assert_called_once_with(self.course_key, ignore_case=False)


class TestBulkWriteMixinOpenAfterPrevTransaction(TestBulkWriteMixinOpen, TestBulkWriteMixinPreviousTransaction):  # pylint: disable=test-inherits-tests
    """
    Test that operations on with an open transaction aren't affected by a previously executed transaction
    """
    pass  # pylint: disable=unnecessary-pass
