"""
Unit tests for Block Structure models.
"""
# pylint: disable=protected-access
import errno
from itertools import product
from unittest.mock import Mock, patch
from uuid import uuid4

import ddt
import pytest
from django.core.exceptions import SuspiciousOperation
from django.test import TestCase
from django.utils.timezone import now
from opaque_keys.edx.locator import BlockUsageLocator, CourseLocator

from ..exceptions import BlockStructureNotFound
from ..models import BlockStructureModel, _directory_name, _storage_error_handling


@ddt.ddt
class BlockStructureModelTestCase(TestCase):
    """
    Tests for BlockStructureModel.
    """
    def setUp(self):
        super().setUp()
        self.course_key = CourseLocator('org', 'course', str(uuid4()))
        self.usage_key = BlockUsageLocator(course_key=self.course_key, block_type='course', block_id='course')

        self.params = self._create_bsm_params()

    def tearDown(self):
        BlockStructureModel._prune_files(self.usage_key, num_to_keep=0)
        super().tearDown()

    def _assert_bsm_fields(self, bsm, expected_serialized_data):
        """
        Verifies that the field values and serialized data
        on the given bsm are as expected.
        """
        for field_name, field_value in self.params.items():
            assert field_value == getattr(bsm, field_name)

        assert bsm.get_serialized_data().decode('utf-8') == expected_serialized_data
        assert _directory_name(self.usage_key) in bsm.data.name

    def _assert_file_count_equal(self, expected_count):
        """
        Asserts the number of files for self.usage_key
        is as expected.
        """
        assert len(BlockStructureModel._get_all_files(self.usage_key)) == expected_count

    def _create_bsm_params(self):
        """
        Returns the parameters for creating a BlockStructureModel.
        """
        return dict(
            data_usage_key=self.usage_key,
            data_version='DV',
            data_edit_timestamp=now(),
            transformers_schema_version='TV',
            block_structure_schema_version=str(1),
        )

    def _verify_update_or_create_call(self, serialized_data, mock_log=None, expect_created=None):
        """
        Calls BlockStructureModel.update_or_create
        and verifies the response.
        """
        bsm, created = BlockStructureModel.update_or_create(serialized_data, **self.params)
        if mock_log:
            assert ('Created' if expect_created else 'Updated') == mock_log.info.call_args[0][1]
            assert len(serialized_data) == mock_log.info.call_args[0][6]
        self._assert_bsm_fields(bsm, serialized_data)
        if expect_created is not None:
            assert created == expect_created
        return bsm

    @patch('openedx.core.djangoapps.content.block_structure.models.log')
    def test_update_or_create(self, mock_log):
        serialized_data = 'initial data'

        # shouldn't already exist
        with pytest.raises(BlockStructureNotFound):
            BlockStructureModel.get(self.usage_key)
            assert 'BlockStructure: Not found in table;' in mock_log.info.call_args[0][0]

        # create an entry
        bsm = self._verify_update_or_create_call(serialized_data, mock_log, expect_created=True)

        # get entry
        found_bsm = BlockStructureModel.get(self.usage_key)
        self._assert_bsm_fields(found_bsm, serialized_data)
        assert 'Read' in mock_log.info.call_args[0][1]

        # update entry
        self.params.update(dict(data_version='new version'))
        updated_serialized_data = 'updated data'
        updated_bsm = self._verify_update_or_create_call(updated_serialized_data, mock_log, expect_created=False)
        assert bsm.data.name != updated_bsm.data.name

        # old files not pruned
        self._assert_file_count_equal(2)

    @patch('openedx.core.djangoapps.content.block_structure.config.num_versions_to_keep', Mock(return_value=1))
    def test_prune_files(self):
        self._verify_update_or_create_call('test data', expect_created=True)
        self._verify_update_or_create_call('updated data', expect_created=False)
        self._assert_file_count_equal(1)

    @patch('openedx.core.djangoapps.content.block_structure.config.num_versions_to_keep', Mock(return_value=1))
    @patch('openedx.core.djangoapps.content.block_structure.models.BlockStructureModel._delete_files')
    @patch('openedx.core.djangoapps.content.block_structure.models.log')
    def test_prune_exception(self, mock_log, mock_delete):
        mock_delete.side_effect = Exception
        self._verify_update_or_create_call('test data', expect_created=True)
        self._verify_update_or_create_call('updated data', expect_created=False)

        assert 'BlockStructure: Exception when deleting old files' in mock_log.exception.call_args[0][0]
        self._assert_file_count_equal(2)  # old files not pruned

    @ddt.data(
        *product(
            list(range(1, 3)),  # prune_keep_count
            list(range(4)),  # num_prior_edits
        )
    )
    @ddt.unpack
    def test_prune_keep_count(self, prune_keep_count, num_prior_edits):
        with patch(
            'openedx.core.djangoapps.content.block_structure.config.num_versions_to_keep',
            return_value=prune_keep_count,
        ):
            for x in range(num_prior_edits):
                self._verify_update_or_create_call(f'data_{x}')

            if num_prior_edits:
                self._assert_file_count_equal(min(num_prior_edits, prune_keep_count))

            self._verify_update_or_create_call('data_final')
            self._assert_file_count_equal(min(num_prior_edits + 1, prune_keep_count))

    @ddt.data(
        (IOError, errno.ENOENT, 'No such file or directory', BlockStructureNotFound, True),
        (IOError, errno.ENOENT, 'No such file or directory', IOError, False),
        (SuspiciousOperation, None, None, BlockStructureNotFound, True),
        (SuspiciousOperation, None, None, SuspiciousOperation, False),
        (OSError, errno.EACCES, 'Permission denied', OSError, True),
        (OSError, errno.EACCES, 'Permission denied', OSError, False),
    )
    @ddt.unpack
    def test_error_handling(self, error_raised_in_operation, errno_raised, message_raised,
                            expected_error_raised, is_read_operation):
        bs_model, _ = BlockStructureModel.update_or_create('test data', **self.params)
        with pytest.raises(expected_error_raised):
            with _storage_error_handling(bs_model, 'operation', is_read_operation):
                if errno_raised is not None:  # lint-amnesty, pylint: disable=no-else-raise
                    raise error_raised_in_operation(errno_raised, message_raised)
                else:
                    raise error_raised_in_operation

    @patch('openedx.core.djangoapps.content.block_structure.models.log')
    def test_old_mongo_keys(self, mock_log):
        self.course_key = CourseLocator('org2', 'course2', str(uuid4()), deprecated=True)
        self.usage_key = BlockUsageLocator(course_key=self.course_key, block_type='course', block_id='course')
        serialized_data = 'test data for old course'
        self.params['data_usage_key'] = self.usage_key

        with patch('xmodule.modulestore.mixed.MixedModuleStore.fill_in_run') as mock_fill_in_run:
            mock_fill_in_run.return_value = self.usage_key.course_key
            self._verify_update_or_create_call(serialized_data, mock_log, expect_created=True)
            found_bsm = BlockStructureModel.get(self.usage_key)

        self._assert_bsm_fields(found_bsm, serialized_data)
