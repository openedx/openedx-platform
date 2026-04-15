""" Tests the contents of the __init__.py file """


from django.test import TestCase

from common.djangoapps.student.tests.factories import UserFactory
from openedx.features.calendar_sync import get_calendar_event_id

TEST_PASSWORD = 'test'


class TestCalendarSyncInit(TestCase):
    """ Tests for the contents of __init__.py """
    def setUp(self):
        super().setUp()
        self.user = UserFactory(password=TEST_PASSWORD)

    def test_get_calendar_event_id(self):
        block_key = 'block-v1:Org+Number+Term+type@sequential+block@gibberish'
        date_type = 'due'
        hostname = 'example.com'
        event_id = get_calendar_event_id(self.user, block_key, date_type, hostname)
        expected = '{user_id}.{block_key}.{date_type}@{hostname}'.format(  # noqa: UP032
            user_id=self.user.id, block_key=block_key, date_type=date_type, hostname=hostname
        )
        assert event_id == expected
