"""
Tests for Agreements models
"""

from django.db import IntegrityError
from django.test import TestCase
from openedx.core.djangoapps.agreements.models import UserAgreement


class UserAgreementModelTest(TestCase):
    """
    Tests for the UserAgreement model.
    """
    def test_agreement_must_have_text_or_url(self):
        """
        Verify that a UserAgreement must have at least a url or text.
        """
        # Case 1: Both text and url are provided (Success)
        agreement = UserAgreement.objects.create(
            type='type1',
            name='Name 1',
            summary='Summary 1',
            text='Some text',
            url='https://example.com'
        )
        self.assertIsNotNone(agreement.pk)

        # Case 2: Only text is provided (Success)
        agreement = UserAgreement.objects.create(
            type='type2',
            name='Name 2',
            summary='Summary 2',
            text='Some text',
            url=None
        )
        self.assertIsNotNone(agreement.pk)

        # Case 3: Only url is provided (Success)
        agreement = UserAgreement.objects.create(
            type='type3',
            name='Name 3',
            summary='Summary 3',
            text=None,
            url='https://example.com'
        )
        self.assertIsNotNone(agreement.pk)

        # Case 4: Neither text nor url is provided (Failure)
        with self.assertRaises(IntegrityError):
            UserAgreement.objects.create(
                type='type4',
                name='Name 4',
                summary='Summary 4',
                text=None,
                url=None
            )

    def test_agreement_with_empty_strings(self):
        """
        Verify behavior with empty strings.
        Since the constraint is `isnull=False`, empty strings should pass
        if the DB allows them as NOT NULL.
        """
        # Case 5: text is empty string, url is None (Success - because text is NOT NULL)
        agreement = UserAgreement.objects.create(
            type='type5',
            name='Name 5',
            summary='Summary 5',
            text='',
            url=None
        )
        self.assertIsNotNone(agreement.pk)

        # Case 6: text is None, url is empty string (Success - because url is NOT NULL)
        agreement = UserAgreement.objects.create(
            type='type6',
            name='Name 6',
            summary='Summary 6',
            text=None,
            url=''
        )
        self.assertIsNotNone(agreement.pk)
