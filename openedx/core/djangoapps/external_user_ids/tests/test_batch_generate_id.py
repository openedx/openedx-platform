"""
Test batch_get_or_create in ExternalId model
"""

import django
from django.test import TransactionTestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.external_user_ids.models import ExternalId
from openedx.core.djangoapps.external_user_ids.tests.factories import ExternalIDTypeFactory


class TestBatchGenerateExternalIds(TransactionTestCase):
    """
    Test ExternalId.batch_get_or_create_user_ids
    """

    # Following are the queries
    # 1 - Get ExternalIdType
    # 2 - Find users for those external ids needs to be created
    # 3 - Get external ids that already exists
    # 4 - BEGIN (from bulk_create)
    # 5 - Create new external ids
    EXPECTED_NUM_OF_QUERIES = 5 if django.VERSION < (4, 2) else 6

    def test_batch_get_or_create_user_ids(self):
        """
        Test if batch_get_or_create creates ExternalIds in batch
        """
        id_type = ExternalIDTypeFactory.create(name='test')
        users = [UserFactory() for _ in range(10)]

        with self.assertNumQueries(self.EXPECTED_NUM_OF_QUERIES):
            result = ExternalId.batch_get_or_create_user_ids(users, id_type)

        assert len(result) == len(users)

        for user in users:
            assert result[user.id].external_id_type.name == 'test'
            assert result[user.id].user == user

    def test_batch_get_or_create_user_ids_existing_ids(self):
        """
        Test batch creation output when there are existing ids for some user
        """
        id_type = ExternalIDTypeFactory.create(name='test')

        # first let's create some user and externalids for them
        users = [UserFactory() for _ in range(10)]

        with self.assertNumQueries(self.EXPECTED_NUM_OF_QUERIES):
            result = ExternalId.batch_get_or_create_user_ids(users, id_type)

        # now create some new user and try to create externalids for all user
        new_users = [UserFactory() for _ in range(5)]
        all_users = users + new_users

        with self.assertNumQueries(self.EXPECTED_NUM_OF_QUERIES):
            result = ExternalId.batch_get_or_create_user_ids(all_users, id_type)

        assert len(result) == len(all_users)

    def test_batch_get_or_create_user_ids_wrong_type(self):
        """
        Test if batch_get_or_create returns None if wrong type given
        """
        users = [UserFactory() for _ in range(2)]
        external_ids = ExternalId.batch_get_or_create_user_ids(users, 'invalid')
        assert external_ids is None
