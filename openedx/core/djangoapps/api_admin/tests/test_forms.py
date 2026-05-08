#pylint: disable=missing-docstring

import ddt
from django.test import TestCase

from openedx.core.djangoapps.api_admin.forms import ApiAccessRequestForm, ViewersWidget
from openedx.core.djangoapps.api_admin.tests.utils import VALID_DATA
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
@ddt.ddt
class ApiAccessFormTest(TestCase):

    @ddt.data(
        (VALID_DATA, True),
        ({}, False),
        (dict(VALID_DATA, terms_of_service=False), False)
    )
    @ddt.unpack
    def test_form_valid(self, data, is_valid):
        form = ApiAccessRequestForm(data)
        assert form.is_valid() == is_valid


@skip_unless_lms
class ViewersWidgetTest(TestCase):
    widget = ViewersWidget()

    def test_render_value(self):
        """
        Verify that ViewersWidget always displays serialized value on rendering.
        """
        dummy_string_value = 'staff, verified'
        input_field_name = 'viewers'
        extra_formating = ''
        expected_widget_html = '<input type="text" name="{input_field_name}" value="{serialized_value}"{extra_formating}>'.format(  # pylint: disable=line-too-long  # noqa: UP032
            input_field_name=input_field_name,
            serialized_value=dummy_string_value,
            extra_formating=extra_formating,
        )
        output = self.widget.render(name=input_field_name, value=dummy_string_value)
        assert expected_widget_html == output

        dummy_list_value = ['staff', 'verified']
        output = self.widget.render(name=input_field_name, value=dummy_list_value)
        assert expected_widget_html == output
