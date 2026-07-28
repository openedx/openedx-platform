"""
Factories related to student verification.
"""
from factory.django import DjangoModelFactory

from lms.djangoapps.verify_student.models import SoftwareSecurePhotoVerification, SSOVerification, VerificationAttempt


class SoftwareSecurePhotoVerificationFactory(DjangoModelFactory):
    """
    Factory for SoftwareSecurePhotoVerification
    """
    class Meta:
        model = SoftwareSecurePhotoVerification

    status = 'approved'


class SSOVerificationFactory(DjangoModelFactory):
    class Meta():  # noqa: UP039
        model = SSOVerification


class VerificationAttemptFactory(DjangoModelFactory):
    class Meta:
        model = VerificationAttempt
