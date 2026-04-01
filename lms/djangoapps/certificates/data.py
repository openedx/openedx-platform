"""
Certificates Data

This provides Data models to represent Certificates data.
"""

from dataclasses import dataclass
from opaque_keys.edx.keys import CourseKey
from django.contrib.auth import get_user_model

User = get_user_model()


class CertificateStatuses:
    """
    Enum for certificate statuses.

    Not all of these statuses are currently used. Some are kept for historical reasons and because existing course
    certificates may have been granted that status.

    audit_notpassing    - User is in the audit track and has not achieved a passing grade.
    audit_passing       - User is in the audit track and has achieved a passing grade.
    deleted             - The PDF certificate has been deleted.
    deleting            - A request has been made to delete the PDF certificate.
    downloadable        - The user has been granted this certificate and the certificate is ready and available.
    error               - An error occurred during PDF certificate generation.
    generating          - A request has been made to generate a PDF certificate, but it has not been generated yet.
    honor_passing       - User is in the honor track and has achieved a passing grade.
    invalidated         - Certificate is not valid.
    notpassing          - The user has not achieved a passing grade.
    requesting          - A request has been made to generate the PDF certificate.
    restricted          - The user is restricted from receiving a certificate.
    unavailable         - Certificate has been invalidated.
    unverified          - The user does not have an approved, unexpired identity verification.

    The following statuses are set by the current course certificates code:
      downloadable - See generation.py
      notpassing - See GeneratedCertificate.mark_notpassing()
      unavailable - See GeneratedCertificate.invalidate()
      unverified - See GeneratedCertificate.mark_unverified()
    """
    deleted = 'deleted'
    deleting = 'deleting'
    downloadable = 'downloadable'
    error = 'error'
    generating = 'generating'
    notpassing = 'notpassing'
    restricted = 'restricted'
    unavailable = 'unavailable'
    auditing = 'auditing'
    audit_passing = 'audit_passing'
    audit_notpassing = 'audit_notpassing'
    honor_passing = 'honor_passing'
    unverified = 'unverified'
    invalidated = 'invalidated'
    requesting = 'requesting'

    readable_statuses = {
        downloadable: "already received",
        notpassing: "didn't receive",
        error: "error states",
        audit_passing: "audit passing states",
        audit_notpassing: "audit not passing states",
    }

    PASSED_STATUSES = (downloadable, generating)
    NON_REFUNDABLE_STATUSES = (downloadable, generating, unavailable)

    @classmethod
    def is_passing_status(cls, status):
        """
        Given the status of a certificate, return a boolean indicating whether
        the student passed the course.
        """
        return status in cls.PASSED_STATUSES

    @classmethod
    def is_refundable_status(cls, status):
        """
        Given the status of a certificate, check to see if that certificate status can
        be refunded.

        Arguments:
            status (str): The status of the certificate that you are checking

        Returns:
            bool: True if the status is refundable.
        """
        return status not in cls.NON_REFUNDABLE_STATUSES


@dataclass
class GeneratedCertificateData:
    """
    A data representation of a generated course certificate.

    This class encapsulates the essential fields related to a user's generated
    certificate, including course information, user identity, certificate status,
    grade, and download metadata.

    Attributes:
        user (User): The user who earned the certificate.
        course_id (CourseKey): Identifier for the course associated with the certificate.
        verify_uuid (str): UUID used to verify the certificate.
        grade (str): The grade achieved in the course.
        key (str): Internal key identifier for the certificate.
        distinction (bool): Whether the certificate was issued with distinction.
        status (str): Current status of the certificate (e.g., 'downloadable', 'unavailable').
        mode (str): Enrollment mode at the time of certificate issuance (e.g., 'honor', 'verified').
        name (str): Full name as it appears on the certificate.
        created_date (str): Timestamp for when the certificate was created.
        modified_date (str): Timestamp for when the certificate was last modified.
        download_uuid (str): UUID used for generating the download URL.
        download_url (str): Direct URL to download the certificate.
        error_reason (str): Reason for any certificate generation failure, if applicable.

    Methods:
        validate_mode(): Validates that the mode is within the supported set of enrollment modes.
    """
    user: User
    course_id: CourseKey
    verify_uuid: str = ""
    grade: str = ""
    key: str = ""
    distinction: bool = False
    status: str = "unavailable"
    mode: str = "honor"
    name: str = ""
    created_date: str = None
    modified_date: str = None
    download_uuid: str = ""
    download_url: str = ""
    error_reason: str = ""

    def validate_mode(self):
        if self.mode not in self.MODES:
            raise ValueError(f"Invalid mode: {self.mode}")
