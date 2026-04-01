# lint-amnesty, pylint: disable=missing-module-docstring

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from common.djangoapps.track.utils import DateTimeJSONEncoder


class TestDateTimeJSONEncoder(TestCase):  # lint-amnesty, pylint: disable=missing-class-docstring
    def test_datetime_encoding(self):
        a_naive_datetime = datetime(2012, 5, 1, 7, 27, 10, 20000)
        a_tz_datetime = datetime(2012, 5, 1, 7, 27, 10, 20000, tzinfo=ZoneInfo("UTC"))
        a_date = a_naive_datetime.date()
        an_iso_datetime = '2012-05-01T07:27:10.020000+00:00'
        an_iso_date = '2012-05-01'

        obj = {
            'number': 100,
            'string': 'hello',
            'object': {'a': 1},
            'a_datetime': a_naive_datetime,
            'a_tz_datetime': a_tz_datetime,
            'a_date': a_date,
        }

        to_json = json.dumps(obj, cls=DateTimeJSONEncoder)
        from_json = json.loads(to_json)

        assert from_json['number'] == 100
        assert from_json['string'] == 'hello'
        assert from_json['object'] == {'a': 1}

        assert from_json['a_datetime'] == an_iso_datetime
        assert from_json['a_tz_datetime'] == an_iso_datetime
        assert from_json['a_date'] == an_iso_date
