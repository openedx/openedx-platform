"""
Unit tests for the Mongo modulestore
"""


import logging

import ddt
import pytest

# pylint: disable=protected-access
from django.test import TestCase

# pylint: enable=E0611
from opaque_keys.edx.keys import CourseKey
from xblock.exceptions import InvalidScopeError
from xblock.fields import Scope
from xblock.runtime import KeyValueStore

from xmodule.modulestore.mongo import MongoKeyValueStore

log = logging.getLogger(__name__)


@ddt.ddt
class TestMongoKeyValueStore(TestCase):
    """
    Tests for MongoKeyValueStore.
    """

    def setUp(self):
        super().setUp()
        self.data = {'foo': 'foo_value'}
        self.course_id = CourseKey.from_string('org/course/run')
        self.parent = self.course_id.make_usage_key('parent', 'p')
        self.children = [self.course_id.make_usage_key('child', 'a'), self.course_id.make_usage_key('child', 'b')]
        self.metadata = {'meta': 'meta_val'}
        self.kvs = MongoKeyValueStore(self.data, self.metadata)

    def test_read_invalid_scope(self):
        for scope in (Scope.preferences, Scope.user_info, Scope.user_state):
            key = KeyValueStore.Key(scope, None, None, 'foo')
            with pytest.raises(InvalidScopeError):
                self.kvs.get(key)
            assert not self.kvs.has(key)

    def test_read_non_dict_data(self):
        self.kvs = MongoKeyValueStore('xml_data', self.metadata)
        assert self.kvs.get(KeyValueStore.Key(Scope.content, None, None, 'data')) == 'xml_data'

    def _check_write(self, key, value):
        self.kvs.set(key, value)
        assert self.kvs.get(key) == value

    @ddt.data(
        (Scope.content, "foo", "new_data"),
        (Scope.settings, "meta", "new_settings"),
    )
    @ddt.unpack
    def test_write(self, scope, key, expected):
        self._check_write(KeyValueStore.Key(scope, None, None, key), expected)

    def test_write_non_dict_data(self):
        self.kvs = MongoKeyValueStore('xml_data', self.metadata)
        self._check_write(KeyValueStore.Key(Scope.content, None, None, 'data'), 'new_data')

    def test_write_invalid_scope(self):
        for scope in (Scope.preferences, Scope.user_info, Scope.user_state):
            with pytest.raises(InvalidScopeError):
                self.kvs.set(KeyValueStore.Key(scope, None, None, 'foo'), 'new_value')

    @ddt.data(
        (Scope.content, "foo"),
        (Scope.settings, "meta"),
    )
    @ddt.unpack
    def test_delete_key_error(self, scope, expected):
        key = KeyValueStore.Key(scope, None, None, expected)
        self.kvs.delete(key)
        with pytest.raises(KeyError):
            self.kvs.get(key)
        assert not self.kvs.has(key)

    def test_delete_invalid_scope(self):
        for scope in (Scope.preferences, Scope.user_info, Scope.user_state, Scope.parent):
            with pytest.raises(InvalidScopeError):
                self.kvs.delete(KeyValueStore.Key(scope, None, None, 'foo'))
