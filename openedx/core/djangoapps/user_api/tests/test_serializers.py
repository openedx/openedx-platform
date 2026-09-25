"""Unit tests for the combined User/UserProfile serializer."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase

from common.djangoapps.student.models.user import UserProfile
from common.djangoapps.student.tests.factories import UserFactory

from ..serializers import UserProfileSerializer

User = get_user_model()


class UserProfileSerializerTest(TestCase):
    """Tests for updates spanning the User and UserProfile models."""

    def test_create_user_and_profile_fields(self):
        data = {
            "username": "new-user",
            "email": "new@example.com",
            "password": "Password1234",
            "name": "New User",
            "year_of_birth": UserProfile.VALID_YEARS[-1],
            "gender": "f",
            "level_of_education": "b",
            "country": "US",
        }

        serializer = UserProfileSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        user = serializer.save()

        assert user.username == data["username"]
        assert user.email == data["email"]
        assert user.check_password(data["password"])
        assert user.profile.name == data["name"]
        assert user.profile.year_of_birth == data["year_of_birth"]
        assert user.profile.gender == data["gender"]
        assert user.profile.level_of_education == data["level_of_education"]
        assert user.profile.country == data["country"]

    def test_create_user_with_no_profile_fields(self):
        data = {
            "username": "new-user",
            "email": "new@example.com",
            "password": "Password1234",
        }

        serializer = UserProfileSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        user = serializer.save()

        assert user.username == data["username"]
        assert user.email == data["email"]
        assert user.check_password(data["password"])
        assert UserProfile.objects.filter(user=user).exists()
        assert user.profile.name == ""
        assert user.profile.year_of_birth is None
        assert user.profile.gender is None
        assert user.profile.level_of_education is None
        assert user.profile.country is None

    def test_update_user_and_profile_fields(self):
        user = UserFactory.create(email="original@example.com", profile__name="Original Name")
        data = {
            "email": "updated@example.com",
            "name": "Updated Name",
            "password": "new-password",
            "year_of_birth": UserProfile.VALID_YEARS[-1],
            "gender": "f",
            "level_of_education": "b",
            "country": "US",
        }

        serializer = UserProfileSerializer(instance=user, data=data, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        user.refresh_from_db()
        user.profile.refresh_from_db()

        assert user.email == data["email"]
        assert user.check_password(data["password"])
        assert user.profile.name == data["name"]
        assert user.profile.year_of_birth == data["year_of_birth"]
        assert user.profile.gender == data["gender"]
        assert user.profile.level_of_education == data["level_of_education"]
        assert user.profile.country == data["country"]

    def test_invalid_profile_fields(self):
        invalid_values = {
            "year_of_birth": 1800,
            "gender": "invalid",
            "level_of_education": "invalid",
            "country": "invalid",
        }

        for field, value in invalid_values.items():
            serializer = UserProfileSerializer(data={field: value}, partial=True)
            assert not serializer.is_valid()
            assert (
                field in serializer.errors or "non_field_errors" in serializer.errors
            ), serializer.errors

    def test_blank_user_fields_are_rejected(self):
        for field in ("email", "username", "password", "name"):
            serializer = UserProfileSerializer(data={field: ""}, partial=True)
            assert not serializer.is_valid()
            assert field in serializer.errors

    def test_whitespace_only_fields_are_rejected(self):
        for field in ("email", "username", "name"):
            serializer = UserProfileSerializer(data={field: "   "}, partial=True)
            assert not serializer.is_valid()
            assert field in serializer.errors

    def test_missing_required_fields_are_rejected(self):
        for field, message in (("email", "Email is required."), ("username", "Username is required.")):
            data = {"email": "new@example.com", "username": "new-user"}
            data.pop(field)
            serializer = UserProfileSerializer(data=data)
            assert not serializer.is_valid(), serializer.errors
            assert "non_field_errors" in serializer.errors
            assert message in serializer.errors["non_field_errors"]

    def test_duplicate_email_and_username_are_rejected(self):
        existing = UserFactory.create(username="existing-user", email="existing@example.com")

        cases = (
            ("email", existing.email, "User already exists with this email"),
            ("username", existing.username, "User already exists with this username"),
        )

        for field, value, message in cases:
            serializer = UserProfileSerializer(
                data={"email": "new@example.com", "username": "new-user", field: value}
            )
            assert not serializer.is_valid(), serializer.errors
            assert "non_field_errors" in serializer.errors
            assert message in serializer.errors["non_field_errors"]

    def test_weak_password_is_rejected(self):
        serializer = UserProfileSerializer(data={
            "username": "new-user",
            "email": "new@example.com",
            "password": "weak",
        })
        assert not serializer.is_valid()
        assert (
            "password" in serializer.errors or "non_field_errors" in serializer.errors
        ), serializer.errors

    def test_no_password_is_accepted(self):
        serializer = UserProfileSerializer(data={
            "username": "new-user",
            "email": "new@example.com",
        })
        assert serializer.is_valid(), serializer.errors
        user = serializer.save()
        assert not user.has_usable_password()

    def test_username_cannot_be_changed(self):
        user = UserFactory.create(username="original-user", email="original@example.com")
        with pytest.raises(DjangoValidationError, match=r"Username cannot be changed\."):
            UserProfileSerializer().update(user, {"username": "new-user"})
