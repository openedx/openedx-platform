"""
Public data structures for this app.
"""
from dataclasses import dataclass
from datetime import datetime

import attr

from .models import UserAgreementRecord


@attr.s(frozen=True, auto_attribs=True)
class LTIToolsReceivingPIIData:
    """
    Class that stores data about the list of LTI tools sharing PII
    """
    lii_tools_receiving_pii: {}


@attr.s(frozen=True, auto_attribs=True)
class LTIPIISignatureData:
    """
    Class that stores an lti pii signature
    """
    user: str
    course_id: str
    lti_tools: str
    lti_tools_hash: str


@dataclass
class UserAgreementRecordData:
    """
    Data for a single user agreement record.
    """
    username: str
    agreement_type: str
    accepted_at: datetime

    @classmethod
    def from_model(cls, model: UserAgreementRecord):
        return UserAgreementRecordData(
            username=model.user.username,
            agreement_type=model.agreement_type,
            accepted_at=model.timestamp,
        )
