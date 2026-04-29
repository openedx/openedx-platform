"""
Unit Tests for Utils Class
"""


from unittest import TestCase

import ddt
from opaque_keys.edx.keys import CourseKey, UsageKey

from lms.djangoapps.utils import _get_key


@ddt.ddt
class UtilsTests(TestCase):  # pylint: disable=missing-class-docstring

    @ddt.data(
        ['edX/DemoX/Demo_Course', CourseKey.from_string('edX/DemoX/Demo_Course'), CourseKey],
        ['course-v1:edX+DemoX+Demo_Course', CourseKey.from_string('course-v1:edX+DemoX+Demo_Course'), CourseKey],
        [CourseKey.from_string('course-v1:edX+DemoX+Demo_Course'),
         CourseKey.from_string('course-v1:edX+DemoX+Demo_Course'), CourseKey],
        ['block-v1:edX+DemoX+Demo_Course+type@sequential+block@workflow',
         UsageKey.from_string('block-v1:edX+DemoX+Demo_Course+type@sequential+block@workflow'), UsageKey],
        [UsageKey.from_string('block-v1:edX+DemoX+Demo_Course+type@sequential+block@workflow'),
         UsageKey.from_string('block-v1:edX+DemoX+Demo_Course+type@sequential+block@workflow'), UsageKey],
    )
    @ddt.unpack
    def test_get_key(self, input_key, output_key, key_cls):
        assert _get_key(input_key, key_cls) == output_key
