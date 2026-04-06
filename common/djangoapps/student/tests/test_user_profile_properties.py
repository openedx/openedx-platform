"""Unit tests for custom UserProfile properties."""


import datetime

import ddt
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from common.djangoapps.student.models import UserProfile
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase


@ddt.ddt
class UserProfilePropertiesTest(CacheIsolationTestCase):
    """Unit tests for age, gender_display, phone_number, and level_of_education_display properties ."""

    password = "test"

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create(password=self.password)
        self.profile = self.user.profile

    def _set_year_of_birth(self, year_of_birth):
        """
        Helper method that sets a birth year for the specified user.
        """
        self.profile.year_of_birth = year_of_birth
        self.profile.save()

    def _set_level_of_education(self, level_of_education):
        """
        Helper method that sets a level of education for the specified user.
        """
        self.profile.level_of_education = level_of_education
        self.profile.save()

    def _set_gender(self, gender):
        """
        Helper method that sets a gender for the specified user.
        """
        self.profile.gender = gender
        self.profile.save()

    @ddt.data(0, 1, 13, 20, 100)
    def test_age(self, years_ago):
        """Verify the age calculated correctly."""
        current_year = datetime.datetime.now().year
        self._set_year_of_birth(current_year - years_ago)

        # In the year that your turn a certain age you will also have been a
        # year younger than that in that same year.  We calculate age based off of
        # the youngest you could be that year.
        age = years_ago - 1
        assert self.profile.age == age

    def test_age_no_birth_year(self):
        """Verify nothing is returned."""
        assert self.profile.age is None

    @ddt.data(*UserProfile.LEVEL_OF_EDUCATION_CHOICES)
    @ddt.unpack
    def test_display_level_of_education(self, level_enum, display_level):
        """Verify the level of education is displayed correctly."""
        self._set_level_of_education(level_enum)

        assert self.profile.level_of_education_display == display_level

    def test_display_level_of_education_none_set(self):
        """Verify nothing is returned."""
        assert self.profile.level_of_education_display is None

    @ddt.data(*UserProfile.GENDER_CHOICES)
    @ddt.unpack
    def test_display_gender(self, gender_enum, display_gender):
        """Verify the gender displayed correctly."""
        self._set_gender(gender_enum)

        assert self.profile.gender_display == display_gender

    def test_display_gender_none_set(self):
        """Verify nothing is returned."""
        self._set_gender(None)

        assert self.profile.gender_display is None

    def test_invalidate_cache_user_profile_country_updated(self):

        country = 'US'
        self.profile.country = country
        self.profile.save()

        cache_key = UserProfile.country_cache_key_name(self.user.id)
        assert cache.get(cache_key) is None

        cache.set(cache_key, self.profile.country)
        assert cache.get(cache_key) == country

        country = 'bd'
        self.profile.country = country
        self.profile.save()

        assert cache.get(cache_key) != country
        assert cache.get(cache_key) is None

    def test_valid_phone_numbers(self):
        """
        Test that valid phone numbers are accepted.

        Expected behavior:
            - The phone number '+123456789' should be considered valid.
            - The phone number '123456789' (without '+') should also be valid.

        This test verifies that valid phone numbers are accepted by the profile model validation.
        """
        valid_numbers = ['+123456789', '123456789']

        for number in valid_numbers:
            self.profile.phone_number = number

            try:
                self.profile.full_clean()
            except ValidationError:
                self.fail("This phone number should be valid.")

    def test_invalid_phone_numbers(self):
        """
        Test that invalid phone numbers raise ValidationError.

        Expected behavior:
            - Phone numbers with letters, mixed digits/letters, whitespace,
              or special characters should raise a ValidationError.

        This test verifies that invalid phone numbers are rejected by the profile model validation.
        """
        invalid_phone_numbers = [
            'abc',          # Letters in the phone number
            '1234gb',       # Mixed digits and letters
            '   123',       # Whitespace
            '123!@#$%^&*'   # Special characters
        ]

        for number in invalid_phone_numbers:
            self.profile.phone_number = number
            pytest.raises(ValidationError, self.profile.full_clean)
