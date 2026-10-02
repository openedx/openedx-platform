"""
Tests for `fields` module.
"""
import unittest

from django.db.backends.mysql.operations import DatabaseOperations as MySQLDatabaseOperations

from ..fields import UnsignedBigIntAutoField

# A primary key value past the 32-bit signed int boundary (2147483647), which
# `courseware_studentmodule.id` can exceed on large instances.
LARGE_PK = 4_312_695_480


class UnsignedBigIntAutoFieldTests(unittest.TestCase):
    """
    Regression tests for Django 5.2's `IntegerFieldOverflow` check.

    Django 5.2 added a check, keyed off `Field.get_internal_type()`, that silently
    excludes exact-match lookup values (e.g. `pk=<value>`) outside of
    `connection.ops.integer_field_range(internal_type)` by raising `EmptyResultSet`
    before any SQL is built. `UnsignedBigIntAutoField` renders a MySQL
    `bigint UNSIGNED` column via `db_type()`/`rel_db_type()`, but previously left
    `get_internal_type()` at its inherited `'AutoField'` value, whose MySQL range is
    the 32-bit signed int range -- far smaller than the column's actual range.
    """

    def test_internal_type_range_contains_large_pk(self):
        mysql_ops = MySQLDatabaseOperations(connection=None)
        internal_type = UnsignedBigIntAutoField().get_internal_type()
        low, high = mysql_ops.integer_field_range(internal_type)

        assert high is not None
        assert high >= LARGE_PK, (
            f"MySQL's range for get_internal_type()={internal_type!r} tops out at {high}, "
            f"which excludes valid primary keys like {LARGE_PK}. Django 5.2's "
            "IntegerFieldOverflow check will silently treat exact-match lookups on such values "
            "as EmptyResultSet instead of querying the database."
        )

    def test_internal_type_is_big_auto_field(self):
        assert UnsignedBigIntAutoField().get_internal_type() == 'BigAutoField'
