"""
Enterprise support admin forms.
"""

from django import forms
from django.utils.translation import gettext_lazy as _
from enterprise.admin.utils import validate_csv


class CSVImportForm(forms.Form):  # pylint: disable=missing-class-docstring
    csv_file = forms.FileField(
        required=True,
        label=_('CSV File'),
        help_text=_('CSV file should have 3 columns having names lms_user_id, course_id, opportunity_id')
    )

    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        csv_reader = validate_csv(csv_file, expected_columns=['lms_user_id', 'course_id', 'opportunity_id'])

        return csv_reader
