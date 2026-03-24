"""
Public data structures for this app.
"""

from datetime import datetime

import attr

from openedx.core.djangoapps.agreements.models import UserAgreement, UserAgreementRecord


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


@attr.s(frozen=True, auto_attribs=True)
class UserAgreementData:
    """
    Data for a user agreement record.
    """

    type: str
    name: str
    summary: str
    has_text: bool
    url: str | None

    @classmethod
    def from_model(cls, model: UserAgreement):
        return UserAgreementData(
            type=model.type,
            name=model.name,
            summary=model.summary,
            url=model.url,
            has_text=bool(model.text),
        )


@attr.s(frozen=True, auto_attribs=True)
class UserAgreementRecordData:
    """
    Data for a single user agreement record.
    """

    username: str
    agreement_type: str
    accepted_at: datetime | None = None
    is_current: bool = False

    @classmethod
    def from_model(cls, model: UserAgreementRecord):
        return UserAgreementRecordData(
            username=model.user.username,
            agreement_type=model.agreement.type,
            accepted_at=model.timestamp,
            is_current=model.is_current,
        )
