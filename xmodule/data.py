"""
Public data structures for this app.

See OEP-49 for details
"""
from enum import Enum


class CertificatesDisplayBehaviors(str, Enum):  # noqa: UP042
    """
    Options for the certificates_display_behavior field of a course

    end: Certificates are available at the end of the course
    end_with_date: Certificates are available after the certificate_available_date (post course end)
    early_no_info: Certificates are available immediately after earning them.

    Only in affect for instructor based courses.
    """
    END = "end"
    END_WITH_DATE = "end_with_date"
    EARLY_NO_INFO = "early_no_info"

    @classmethod
    def includes_value(cls, value):
        return value in set(item.value for item in cls)
