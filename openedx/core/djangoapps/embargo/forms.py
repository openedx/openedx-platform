"""
Defines forms for providing validation of embargo admin details.
"""


import ipaddress

from django import forms
from django.utils.translation import gettext as _
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from xmodule.modulestore.django import modulestore

from .models import IPFilter, RestrictedCourse


class RestrictedCourseForm(forms.ModelForm):
    """Validate course keys for the RestrictedCourse model.

    The default behavior in Django admin is to:
    * Save course keys for courses that do not exist.
    * Return a 500 response if the course key format is invalid.

    Using this form ensures that we display a user-friendly
    error message instead.

    """
    class Meta:
        model = RestrictedCourse
        fields = '__all__'

    def clean_course_key(self):
        """Validate the course key.

        Checks that the key format is valid and that
        the course exists.  If not, displays an error message.

        Arguments:
            field_name (str): The name of the field to validate.

        Returns:
            CourseKey

        """
        cleaned_id = self.cleaned_data['course_key']
        error_msg = _('COURSE NOT FOUND.  Please check that the course ID is valid.')

        try:
            course_key = CourseKey.from_string(cleaned_id)
        except InvalidKeyError:
            raise forms.ValidationError(error_msg)  # lint-amnesty, pylint: disable=raise-missing-from

        if not modulestore().has_course(course_key):
            raise forms.ValidationError(error_msg)

        return course_key


class IPFilterForm(forms.ModelForm):
    """Form validating entry of IP addresses"""

    class Meta:
        model = IPFilter
        fields = '__all__'

    def _is_valid_ip(self, address):
        """Whether or not address is a valid ipv4 address or ipv6 address"""
        try:
            # Is this an valid ip address?
            ipaddress.ip_network(address)
        except ValueError:
            return False
        return True

    def _valid_ip_addresses(self, addresses):
        """
        Checks if a csv string of IP addresses contains valid values.

        If not, raises a ValidationError.
        """
        if addresses == '':
            return ''
        error_addresses = []
        for addr in addresses.split(','):
            address = addr.strip()
            if not self._is_valid_ip(address):
                error_addresses.append(address)
        if error_addresses:
            msg = f'Invalid IP Address(es): {error_addresses}'
            msg += ' Please fix the error(s) and try again.'
            raise forms.ValidationError(msg)

        return addresses

    def clean_whitelist(self):
        """Validates the whitelist"""
        whitelist = self.cleaned_data["whitelist"]
        return self._valid_ip_addresses(whitelist)

    def clean_blacklist(self):
        """Validates the blacklist"""
        blacklist = self.cleaned_data["blacklist"]
        return self._valid_ip_addresses(blacklist)
