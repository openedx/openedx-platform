"""
Defines a form for providing validation of CourseEmail templates.
"""


import logging

from django import forms
from django.core.exceptions import ValidationError

from lms.djangoapps.bulk_email.models import COURSE_EMAIL_MESSAGE_BODY_TAG, CourseAuthorization, CourseEmailTemplate
from openedx.core.lib.courses import clean_course_id

log = logging.getLogger(__name__)


class CourseEmailTemplateForm(forms.ModelForm):
    """Form providing validation of CourseEmail templates."""

    name = forms.CharField(required=False)

    class Meta:
        model = CourseEmailTemplate
        fields = ('html_template', 'plain_template', 'name')

    def _validate_template(self, template):
        """Check the template for required tags."""
        index = template.find(COURSE_EMAIL_MESSAGE_BODY_TAG)
        if index < 0:
            msg = f'Missing tag: "{COURSE_EMAIL_MESSAGE_BODY_TAG}"'
            log.warning(msg)
            raise ValidationError(msg)
        if template.find(COURSE_EMAIL_MESSAGE_BODY_TAG, index + 1) >= 0:
            msg = f'Multiple instances of tag: "{COURSE_EMAIL_MESSAGE_BODY_TAG}"'
            log.warning(msg)
            raise ValidationError(msg)
        # TODO: add more validation here, including the set of known tags
        # for which values will be supplied.  (Email will fail if the template
        # uses tags for which values are not supplied.)

    def clean_html_template(self):
        """Validate the HTML template."""
        template = self.cleaned_data["html_template"]
        self._validate_template(template)
        return template

    def clean_plain_template(self):
        """Validate the plaintext template."""
        template = self.cleaned_data["plain_template"]
        self._validate_template(template)
        return template

    def clean_name(self):
        """Validate the name field. Enforce uniqueness constraint on 'name' field"""

        # Note that we get back a blank string in the Form for an empty 'name' field
        # we want those to be set to None in Python and NULL in the database
        name = self.cleaned_data.get("name").strip() or None

        # if we are creating a new CourseEmailTemplate, then we need to
        # enforce the uniquess constraint as part of the Form validation
        if not self.instance.pk:
            try:
                CourseEmailTemplate.get_template(name)
                # already exists, this is no good
                raise ValidationError(f'Name of "{name}" already exists, this must be unique.')
            except CourseEmailTemplate.DoesNotExist:
                # this is actually the successful validation
                pass
        return name


class CourseAuthorizationAdminForm(forms.ModelForm):
    """Input form for email enabling, allowing us to verify data."""

    class Meta:
        model = CourseAuthorization
        fields = '__all__'  # noqa: DJ007

    def clean_course_id(self):
        """
        Validate the course id
        """
        return clean_course_id(self)
