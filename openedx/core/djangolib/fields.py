"""
Custom Django fields.
"""

from django.db import models


class CharNullField(models.CharField):
    """
    CharField that stores NULL but returns ''
    """

    description = "CharField that stores NULL but returns ''"

    def to_python(self, value):
        """Converts the value into the correct Python object."""
        if isinstance(value, models.CharField):
            return value
        if value is None:
            return ""
        else:
            return value

    def get_db_prep_value(self, value, connection, prepared=False):
        """Converts value to a backend-specific value."""
        if not prepared:
            value = self.get_prep_value(value)
        if value == "":
            return None
        else:
            return value
