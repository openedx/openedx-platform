"""
Test for lms courseware app, module data (runtime data storage for XBlocks)
"""
import json
from functools import partial
from unittest.mock import Mock, patch
import pytest

from django.db import connections, DatabaseError
from django.test import TestCase
from xblock.core import XBlock
from xblock.exceptions import KeyValueMultiSaveError
from xblock.fields import BlockScope, Scope, ScopeIds

from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.courseware.model_data import DjangoKeyValueStore, FieldDataCache, InvalidScopeError
from lms.djangoapps.courseware.models import (
    StudentModule,
    XModuleStudentInfoField,
    XModuleStudentPrefsField,
    XModuleUserStateSummaryField
)
from lms.djangoapps.courseware.tests.factories import COURSE_KEY
from lms.djangoapps.courseware.tests.factories import LOCATION
from lms.djangoapps.courseware.tests.factories import StudentInfoFactory
from lms.djangoapps.courseware.tests.factories import StudentModuleFactory as cmfStudentModuleFactory
from lms.djangoapps.courseware.tests.factories import StudentPrefsFactory
from lms.djangoapps.courseware.tests.factories import UserStateSummaryFactory


def mock_field(scope, name):
    field = Mock()
    field.scope = scope
    field.name = name
    return field


def mock_block(fields=[]):  # lint-amnesty, pylint: disable=dangerous-default-value, missing-function-docstring
    block = Mock(entry_point=XBlock.entry_point)
    block.scope_ids = ScopeIds('user1', 'mock_problem', LOCATION('def_id'), LOCATION('usage_id'))
    block.module_class.fields.values.return_value = fields
    block.fields.values.return_value = fields
    block.module_class.__name__ = 'MockProblemModule'
    return block

# The user ids here are 1 because we make a student in the setUp functions, and
# they get an id of 1.  There's an assertion in setUp to ensure that assumption
# is still true.
user_state_summary_key = partial(DjangoKeyValueStore.Key, Scope.user_state_summary, None, LOCATION('usage_id'))
settings_key = partial(DjangoKeyValueStore.Key, Scope.settings, None, LOCATION('usage_id'))
user_state_key = partial(DjangoKeyValueStore.Key, Scope.user_state, 1, LOCATION('usage_id'))
prefs_key = partial(DjangoKeyValueStore.Key, Scope.preferences, 1, 'mock_problem')
user_info_key = partial(DjangoKeyValueStore.Key, Scope.user_info, 1, None)


class StudentModuleFactory(cmfStudentModuleFactory):
    module_state_key = LOCATION('usage_id')
    course_id = COURSE_KEY


class TestInvalidScopes(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(username='user')
        self.field_data_cache = FieldDataCache(
            [mock_block([mock_field(Scope.user_state, 'a_field')])],
            COURSE_KEY,
            self.user,
        )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_invalid_scopes(self):
        for scope in (Scope(user=True, block=BlockScope.DEFINITION),
                      Scope(user=False, block=BlockScope.TYPE),
                      Scope(user=False, block=BlockScope.ALL)):
            key = DjangoKeyValueStore.Key(scope, None, None, 'field')

            self.assertRaises(InvalidScopeError, self.kvs.get, key)
            self.assertRaises(InvalidScopeError, self.kvs.set, key, 'value')
            self.assertRaises(InvalidScopeError, self.kvs.delete, key)
            self.assertRaises(InvalidScopeError, self.kvs.has, key)
            self.assertRaises(InvalidScopeError, self.kvs.set_many, {key: 'value'})


class OtherUserFailureTestMixin:
    """
    Mixin class to add test cases for failures when a user trying to use the kvs is not
    the one that instantiated the kvs.
    Doing a mixin rather than modifying StorageTestBase (below) because some scopes don't fail in this case, because
    they aren't bound to a particular user

    assumes that this is mixed into a class that defines other_key_factory and existing_field_name
    """
    def test_other_user_kvs_get_failure(self):
        """
        Test for assert failure when a user who didn't create the kvs tries to get from it it
        """
        with pytest.raises(AssertionError):
            self.kvs.get(self.other_key_factory(self.existing_field_name))

    def test_other_user_kvs_set_failure(self):
        """
        Test for assert failure when a user who didn't create the kvs tries to get from it it
        """
        with pytest.raises(AssertionError):
            self.kvs.set(self.other_key_factory(self.existing_field_name), "new_value")


class TestStudentModuleStorage(OtherUserFailureTestMixin, TestCase):
    """Tests for user_state storage via StudentModule"""
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.user_state, 2, LOCATION('usage_id'))  # user_id=2, not 1
    existing_field_name = "a_field"
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def setUp(self):
        super().setUp()
        student_module = StudentModuleFactory(state=json.dumps({'a_field': 'a_value', 'b_field': 'b_value'}))
        self.user = student_module.student
        assert self.user.id == 1
        # check our assumption hard-coded in the key functions above.

        # There should be only one query to load a single block with a single user_state field
        with self.assertNumQueries(1):
            self.field_data_cache = FieldDataCache(
                [mock_block([mock_field(Scope.user_state, 'a_field')])],
                COURSE_KEY,
                self.user,
            )

        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_get_existing_field(self):
        "Test that getting an existing field in an existing StudentModule works"
        # This should only read from the cache, not the database
        with self.assertNumQueries(0):
            assert 'a_value' == self.kvs.get(user_state_key('a_field'))

    def test_get_missing_field(self):
        "Test that getting a missing field from an existing StudentModule raises a KeyError"
        # This should only read from the cache, not the database
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, user_state_key('not_a_field'))

    def test_set_existing_field(self):
        "Test that setting an existing user_state field changes the value"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('a_field'), 'new_value')
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'new_value'} == json.loads(StudentModule.objects.all()[0].state)
        # lint-amnesty, pylint: disable=line-too-long

    def test_set_missing_field(self):
        "Test that setting a new user_state field changes the value"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('not_a_field'), 'new_value')
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'a_value', 'not_a_field': 'new_value'} == json.loads(StudentModule.objects.all()[0].state)
        # lint-amnesty, pylint: disable=line-too-long

    def test_delete_existing_field(self):
        "Test that deleting an existing field removes it from the StudentModule"
        # We are updating a problem, so we write to courseware_studentmodulehistory
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        with self.assertNumQueries(2, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.delete(user_state_key('a_field'))
        assert 1 == StudentModule.objects.all().count()
        self.assertRaises(KeyError, self.kvs.get, user_state_key('not_a_field'))

    def test_delete_missing_field(self):
        "Test that deleting a missing field from an existing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, user_state_key('not_a_field'))
        assert 1 == StudentModule.objects.all().count()
        assert {'b_field': 'b_value', 'a_field': 'a_value'} == json.loads(StudentModule.objects.all()[0].state)

    def test_has_existing_field(self):
        "Test that `has` returns True for existing fields in StudentModules"
        with self.assertNumQueries(0):
            assert self.kvs.has(user_state_key('a_field'))

    def test_has_missing_field(self):
        "Test that `has` returns False for missing fields in StudentModule"
        with self.assertNumQueries(0):
            assert not self.kvs.has(user_state_key('not_a_field'))

    def construct_kv_dict(self):
        """Construct a kv_dict that can be passed to set_many"""
        key1 = user_state_key('field_a')
        key2 = user_state_key('field_b')
        new_value = 'new value'
        newer_value = 'newer value'
        return {key1: new_value, key2: newer_value}

    def test_set_many(self):
        "Test setting many fields that are scoped to Scope.user_state"
        kv_dict = self.construct_kv_dict()

        # Scope.user_state is stored in a single row in the database, so we only
        # need to send a single update to that table.
        # We also are updating a problem, so we write to courseware student module history
        # We also need to read the database to discover if something other than the
        # DjangoXBlockUserStateClient has written to the StudentModule (such as
        # UserStateCache setting the score on the StudentModule).
        with self.assertNumQueries(4, using="default"):
            with self.assertNumQueries(2, using="student_module_history"):
                self.kvs.set_many(kv_dict)

        for key in kv_dict:
            assert self.kvs.get(key) == kv_dict[key]

    def test_set_many_failure(self):
        "Test failures when setting many fields that are scoped to Scope.user_state"
        kv_dict = self.construct_kv_dict()
        # because we're patching the underlying save, we need to ensure the
        # fields are in the cache
        for key in kv_dict:
            self.kvs.set(key, 'test_value')

        with patch('django.db.models.Model.save', side_effect=DatabaseError):
            with pytest.raises(KeyValueMultiSaveError) as exception_context:
                self.kvs.set_many(kv_dict)
        assert exception_context.value.saved_field_names == []


class TestMissingStudentModule(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    # Tell Django to clean out all databases, not just default
    databases = set(connections)

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create(username='user')
        assert self.user.id == 1
        # check our assumption hard-coded in the key functions above.

        # The block has no fields, so FDC shouldn't send any queries
        with self.assertNumQueries(0):
            self.field_data_cache = FieldDataCache(
                [mock_block()],
                COURSE_KEY,
                self.user,
            )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_get_field_from_missing_student_module(self):
        "Test that getting a field from a missing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, user_state_key('a_field'))

    def test_set_field_in_missing_student_module(self):
        "Test that setting a field in a missing StudentModule creates the student module"
        assert 0 == len(self.field_data_cache)
        assert 0 == StudentModule.objects.all().count()

        # We are updating a problem, so we write to courseware_studentmodulehistoryextended
        # as well as courseware_studentmodule. We also need to read the database
        # to discover if something other than the DjangoXBlockUserStateClient
        # has written to the StudentModule (such as UserStateCache setting the score
        # on the StudentModule).
        # Django 1.8 also has a number of other BEGIN and SAVESTATE queries.
        with self.assertNumQueries(4, using='default'):
            with self.assertNumQueries(2, using='student_module_history'):
                self.kvs.set(user_state_key('a_field'), 'a_value')

        assert 1 == sum(len(cache) for cache in self.field_data_cache.cache.values())
        assert 1 == StudentModule.objects.all().count()

        student_module = StudentModule.objects.all()[0]
        assert {'a_field': 'a_value'} == json.loads(student_module.state)
        assert self.user == student_module.student
        assert LOCATION('usage_id').replace(run=None) == student_module.module_state_key
        assert COURSE_KEY == student_module.course_id

    def test_delete_field_from_missing_student_module(self):
        "Test that deleting a field from a missing StudentModule raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, user_state_key('a_field'))

    def test_has_field_for_missing_student_module(self):
        "Test that `has` returns False for missing StudentModules"
        with self.assertNumQueries(0):
            assert not self.kvs.has(user_state_key('a_field'))


class StorageTestBase:
    """
    A base class for that gets subclassed when testing each of the scopes.
    """
    # Disable pylint warnings that arise because of the way the child classes call
    # this base class -- pylint's static analysis can't keep up with it.
    # pylint: disable=no-member, not-callable

    factory = None
    scope = None
    key_factory = None
    storage_class = None

    def setUp(self):
        field_storage = self.factory.create()
        if hasattr(field_storage, 'student'):
            self.user = field_storage.student
        else:
            self.user = UserFactory.create()
        self.mock_block = mock_block([
            mock_field(self.scope, 'existing_field'),
            mock_field(self.scope, 'other_existing_field')])
        # Each field is stored as a separate row in the table,
        # but we can query them in a single query
        with self.assertNumQueries(1):
            self.field_data_cache = FieldDataCache(
                [self.mock_block],
                COURSE_KEY,
                self.user,
            )
        self.kvs = DjangoKeyValueStore(self.field_data_cache)

    def test_set_and_get_existing_field(self):
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('existing_field'), 'test_value')
        with self.assertNumQueries(0):
            assert 'test_value' == self.kvs.get(self.key_factory('existing_field'))

    def test_get_existing_field(self):
        "Test that getting an existing field in an existing Storage Field works"
        with self.assertNumQueries(0):
            assert 'old_value' == self.kvs.get(self.key_factory('existing_field'))

    def test_get_missing_field(self):
        "Test that getting a missing field from an existing Storage Field raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.get, self.key_factory('missing_field'))

    def test_set_existing_field(self):
        "Test that setting an existing field changes the value"
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('existing_field'), 'new_value')
        assert 1 == self.storage_class.objects.all().count()
        assert 'new_value' == json.loads(self.storage_class.objects.all()[0].value)

    def test_set_missing_field(self):
        "Test that setting a new field changes the value"
        with self.assertNumQueries(1):
            self.kvs.set(self.key_factory('missing_field'), 'new_value')
        assert 2 == self.storage_class.objects.all().count()
        assert 'old_value' == json.loads(self.storage_class.objects.get(field_name='existing_field').value)
        assert 'new_value' == json.loads(self.storage_class.objects.get(field_name='missing_field').value)

    def test_delete_existing_field(self):
        "Test that deleting an existing field removes it"
        with self.assertNumQueries(1):
            self.kvs.delete(self.key_factory('existing_field'))
        assert 0 == self.storage_class.objects.all().count()

    def test_delete_missing_field(self):
        "Test that deleting a missing field from an existing Storage Field raises a KeyError"
        with self.assertNumQueries(0):
            self.assertRaises(KeyError, self.kvs.delete, self.key_factory('missing_field'))
        assert 1 == self.storage_class.objects.all().count()

    def test_has_existing_field(self):
        "Test that `has` returns True for an existing Storage Field"
        with self.assertNumQueries(0):
            assert self.kvs.has(self.key_factory('existing_field'))

    def test_has_missing_field(self):
        "Test that `has` return False for an existing Storage Field"
        with self.assertNumQueries(0):
            assert not self.kvs.has(self.key_factory('missing_field'))

    def construct_kv_dict(self):
        """Construct a kv_dict that can be passed to set_many"""
        key1 = self.key_factory('existing_field')
        key2 = self.key_factory('other_existing_field')
        new_value = 'new value'
        newer_value = 'newer value'
        return {key1: new_value, key2: newer_value}

    def test_set_many(self):
        """Test that setting many regular fields at the same time works"""
        kv_dict = self.construct_kv_dict()

        # Each field is a separate row in the database, hence
        # a separate query
        with self.assertNumQueries(len(kv_dict)):
            self.kvs.set_many(kv_dict)
        for key in kv_dict:
            assert self.kvs.get(key) == kv_dict[key]

    def test_set_many_failure(self):
        """Test that setting many regular fields with a DB error """
        kv_dict = self.construct_kv_dict()
        for key in kv_dict:
            with self.assertNumQueries(1):
                self.kvs.set(key, 'test value')

        with patch('django.db.models.Model.save', side_effect=[None, DatabaseError]):
            with pytest.raises(KeyValueMultiSaveError) as exception_context:
                self.kvs.set_many(kv_dict)

        exception = exception_context.value
        assert exception.saved_field_names == ['existing_field', 'other_existing_field']


class TestUserStateSummaryStorage(StorageTestBase, TestCase):
    """Tests for UserStateSummaryStorage"""
    factory = UserStateSummaryFactory
    scope = Scope.user_state_summary
    key_factory = user_state_summary_key
    storage_class = XModuleUserStateSummaryField


class TestStudentPrefsStorage(OtherUserFailureTestMixin, StorageTestBase, TestCase):
    """Tests for StudentPrefStorage"""
    factory = StudentPrefsFactory
    scope = Scope.preferences
    key_factory = prefs_key
    storage_class = XModuleStudentPrefsField
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.preferences, 2, 'mock_problem')  # user_id=2, not 1
    existing_field_name = "existing_field"


class TestStudentInfoStorage(OtherUserFailureTestMixin, StorageTestBase, TestCase):
    """Tests for StudentInfoStorage"""
    factory = StudentInfoFactory
    scope = Scope.user_info
    key_factory = user_info_key
    storage_class = XModuleStudentInfoField
    other_key_factory = partial(DjangoKeyValueStore.Key, Scope.user_info, 2, 'mock_problem')  # user_id=2, not 1
    existing_field_name = "existing_field"
