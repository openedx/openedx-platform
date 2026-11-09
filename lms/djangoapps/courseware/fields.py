"""
Custom fields
"""


from django.db.models.fields import AutoField


class UnsignedBigIntAutoField(AutoField):
    """
    An unsigned 8-byte integer for auto-incrementing primary keys.
    """
    def get_internal_type(self):
        # Django 5.2's IntegerFieldOverflow lookup checks connection.ops.integer_field_range()
        # keyed by get_internal_type() before building SQL for exact-match lookups (e.g.
        # StudentModule.objects.get(id=...)), and silently excludes out-of-range values
        # (EmptyResultSet) instead of querying. The inherited 'AutoField' type maps to a 32-bit
        # signed range on MySQL, so on any instance whose ids grow past 2147483647, lookups and
        # updates by id would silently match no rows. Report 'BigAutoField' instead to get its
        # 64-bit signed range — wrong sign for an unsigned column, but wide enough to never clip
        # a real value. Deliberately a hardcoded string, not
        # `class UnsignedBigIntAutoField(BigAutoField)`: subclassing would also change
        # issubclass(_, AutoField) to False (BigAutoField doesn't inherit AutoField) and pull in
        # BigIntegerField's validators — a bigger, less auditable change than this override.
        return 'BigAutoField'

    def db_type(self, connection):
        if connection.settings_dict['ENGINE'] == 'django.db.backends.mysql':
            return "bigint UNSIGNED AUTO_INCREMENT"
        elif connection.settings_dict['ENGINE'] == 'django.db.backends.sqlite3':
            # Sqlite will only auto-increment the ROWID column. Any INTEGER PRIMARY KEY column
            # is an alias for that (https://www.sqlite.org/autoinc.html). An unsigned integer
            # isn't an alias for ROWID, so we have to give up on the unsigned part.
            return "integer"
        elif "postgresql" in connection.settings_dict['ENGINE']:
            # Pg's bigserial is implicitly unsigned (doesn't allow negative numbers) and
            # goes 1-9.2x10^18
            return "BIGINT"
        else:
            return None

    def rel_db_type(self, connection):
        if connection.settings_dict['ENGINE'] == 'django.db.backends.mysql':
            return "bigint UNSIGNED"
        elif connection.settings_dict['ENGINE'] == 'django.db.backends.sqlite3':
            return "integer"
        elif "postgresql" in connection.settings_dict['ENGINE']:
            return "BIGINT"
        else:
            return None
