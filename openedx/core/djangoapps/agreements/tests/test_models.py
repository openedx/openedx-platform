"""
Tests for Agreements models
"""

from datetime import datetime

from django.db import IntegrityError
from django.test import TestCase

from openedx.core.djangoapps.agreements.models import UserAgreement
from openedx.core.djangolib.testing.utils import skip_unless_lms


@skip_unless_lms
class UserAgreementModelTest(TestCase):
    """
    Tests for the UserAgreement model.
    """

    def test_agreement_must_have_text_or_url(self):
        """
        Verify that a UserAgreement must have at least a url or text.
        """
        agreement = UserAgreement.objects.create(
            type="type1",
            name="Name 1",
            summary="Summary 1",
            text="Some text",
            url="https://example.com",
            updated=datetime.now(),
        )
        assert agreement.pk is not None

        agreement = UserAgreement.objects.create(
            type="type2",
            name="Name 2",
            summary="Summary 2",
            text="Some text",
            url=None,
            updated=datetime.now(),
        )
        assert agreement.pk is not None

        agreement = UserAgreement.objects.create(
            type="type3",
            name="Name 3",
            summary="Summary 3",
            text=None,
            url="https://example.com",
            updated=datetime.now(),
        )
        assert agreement.pk is not None

        with self.assertRaises(IntegrityError):
            UserAgreement.objects.create(
                type="type4",
                name="Name 4",
                summary="Summary 4",
                text=None,
                url=None,
                updated=datetime.now(),
            )

    def test_agreement_with_empty_strings(self):
        """
        Verify behavior with empty strings
        """
        agreement = UserAgreement.objects.create(
            type="type5",
            name="Name 5",
            summary="Summary 5",
            text="",
            url=None,
            updated=datetime.now(),
        )
        assert agreement.pk is not None

        agreement = UserAgreement.objects.create(
            type="type6",
            name="Name 6",
            summary="Summary 6",
            text=None,
            url="",
            updated=datetime.now(),
        )
        assert agreement.pk is not None
