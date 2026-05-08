"""
Custom forms-related types
"""


from django.core.exceptions import ValidationError
from django.forms import Field, MultipleHiddenInput, NullBooleanField, Select


class MultiValueField(Field):
    """
    Field class that supports a set of values for a single form field.

    The field input can be specified as:
        1. a comma-separated-list (foo:bar1,bar2,bar3), or
        2. a repeated field in a MultiValueDict (foo:bar1, foo:bar2, foo:bar3)
        3. a combination of the above (foo:bar1,bar2, foo:bar3)

    Note that there is currently no way to pass a value that includes a comma.

    The resulting field value is a python set of the values as strings.
    """
    widget = MultipleHiddenInput

    def to_python(self, list_of_string_values):  # pylint: disable=arguments-differ
        """
        Convert the form input to a list of strings
        """
        values = super().to_python(list_of_string_values) or set()

        if values:
            # combine all values if there were multiple specified individually
            values = ','.join(values)

            # parse them into a set
            values = set(values.split(',')) if values else set()

        return values

    def validate(self, values):  # pylint: disable=arguments-differ
        """
        Ensure no empty values were passed
        """
        if values and "" in values:
            raise ValidationError("This field cannot be empty.")


class ExtendedNullBooleanField(NullBooleanField):
    """
    A field whose valid values are None, True, 'True', 'true', '1',
    False, 'False', 'false' and '0'.
    """

    NULL_BOOLEAN_CHOICES = (
        (None, ""),
        (True, True),
        (True, "True"),
        (True, "true"),
        (True, "1"),
        (False, False),
        (False, "False"),
        (False, "false"),
        (False, "0"),
    )

    widget = Select(choices=NULL_BOOLEAN_CHOICES)

    def to_python(self, value):
        return to_bool(value)


def to_bool(value):
    """
    Explicitly checks for the string 'True', 'False', 'true',
    'false', '1' and '0' and returns boolean True or False.
    Returns None if value is not passed at all and raises an
    exception for any other value.
    """
    if value in (True, 'True', 'true', '1'):
        return True
    elif value in (False, 'False', 'false', '0'):
        return False
    elif not value:
        return None
    else:
        raise ValidationError("Invalid Boolean Value.")
