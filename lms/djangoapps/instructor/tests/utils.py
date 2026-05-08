"""
Utilities for instructor unit tests
"""


import datetime
import json
import random

from pytz import UTC


class FakeInfo:
    """Parent class for faking objects used in tests"""
    FEATURES = []

    def __init__(self):
        for feature in self.FEATURES:
            setattr(self, feature, 'expected')

    def to_dict(self):
        """ Returns a dict representation of the object """
        return {key: getattr(self, key) for key in self.FEATURES}


class FakeContentTask(FakeInfo):
    """ Fake task info needed for email content list """
    FEATURES = [
        'task_input',
        'task_output',
        'requester',
    ]

    def __init__(self, email_id, num_sent, num_failed, sent_to):  # pylint: disable=unused-argument
        super().__init__()
        self.task_id = random.randint(1, 15401)
        self.task_type = 'test_task'
        self.task_key = random.randint(1, 15401)
        self.task_input = {'email_id': email_id}
        self.task_input = json.dumps(self.task_input)
        self.task_output = {'succeeded': num_sent, 'failed': num_failed}
        self.task_output = json.dumps(self.task_output)
        self.requester = 'expected'

    def make_invalid_input(self):
        """Corrupt the task input field to test errors"""
        self.task_input = "THIS IS INVALID JSON"


class FakeEmail(FakeInfo):
    """ Corresponding fake email for a fake task """
    FEATURES = [
        'subject',
        'html_message',
        'id',
        'created',
    ]

    def __init__(self, email_id):
        super().__init__()
        self.id = str(email_id)  # pylint: disable=invalid-name
        # Select a random data for create field
        year = random.randint(1950, 2000)
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        hour = random.randint(0, 23)
        minute = random.randint(0, 59)
        self.created = datetime.datetime(year, month, day, hour, minute, tzinfo=UTC)
        self.targets = FakeTargetGroup()


class FakeTarget:
    """ Corresponding fake target for a fake email """
    target_type = "expected"

    def long_display(self):
        """ Mocks out a class method """
        return self.target_type


class FakeTargetGroup:
    """ Mocks out the M2M relationship between FakeEmail and FakeTarget """
    def all(self):
        """ Mocks out a django method """
        return [FakeTarget()]


class FakeEmailInfo(FakeInfo):
    """ Fake email information object """
    FEATURES = [
        'created',
        'sent_to',
        'email',
        'number_sent',
        'requester',
    ]

    EMAIL_FEATURES = [
        'subject',
        'html_message',
        'id'
    ]

    def __init__(self, fake_email, num_sent, num_failed):
        super().__init__()
        self.created = fake_email.created.strftime("%Y-%m-%dT%H:%M:%SZ")

        number_sent = str(num_sent) + ' sent'
        if num_failed > 0:
            number_sent += ', ' + str(num_failed) + " failed"

        self.number_sent = number_sent
        fake_email_dict = fake_email.to_dict()
        self.email = {feature: fake_email_dict[feature] for feature in self.EMAIL_FEATURES}
        self.requester = 'expected'
        self.sent_to = ['expected']
