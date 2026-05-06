"""
ACE message types for the verify_student module.
"""
from openedx.core.djangoapps.ace_common.message import BaseMessageType


class VerificationApproved(BaseMessageType):
    """
    A message to the learner when their ID verification has been approved.
    """
    APP_LABEL = 'verify_student'
    Name = 'verificationapproved'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True


class VerificationExpiry(BaseMessageType):  # pylint: disable=missing-class-docstring
    APP_LABEL = 'verify_student'
    Name = 'verificationexpiry'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.options['transactional'] = True


class VerificationSubmitted(BaseMessageType):
    """
    A confirmation message to the learner when their ID verification has been submitted.
    """
    APP_LABEL = 'verify_student'
    Name = 'verificationsubmitted'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.options['transactional'] = True
