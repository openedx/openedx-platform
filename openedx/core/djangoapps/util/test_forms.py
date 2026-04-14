"""
Mixins for testing forms.
"""


class FormTestMixin:
    """A mixin for testing forms"""
    def get_form(self, expected_valid):
        """
        Return a form bound to self.form_data, asserting its validity (or lack
        thereof) according to expected_valid
        """
        form = self.FORM_CLASS(self.form_data, initial=getattr(self, 'initial', None))
        assert form.is_valid() == expected_valid
        return form

    def assert_error(self, expected_field, expected_message):
        """
        Create a form bound to self.form_data, assert its invalidity, and assert
        that its error dictionary contains one entry with the expected field and
        message
        """
        form = self.get_form(expected_valid=False)
        assert form.errors == {expected_field: [expected_message]}

    def assert_valid(self, expected_cleaned_data):
        """
        Check that the form returns the expected data
        """
        form = self.get_form(expected_valid=True)
        self.assertDictEqual(form.cleaned_data, expected_cleaned_data)  # noqa: PT009

    def assert_field_value(self, field, expected_value):
        """
        Create a form bound to self.form_data, assert its validity, and assert
        that the given field in the cleaned data has the expected value
        """
        form = self.get_form(expected_valid=True)
        assert form.cleaned_data[field] == expected_value
