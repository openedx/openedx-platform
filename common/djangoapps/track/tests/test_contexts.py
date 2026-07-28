# pylint: disable=missing-module-docstring
from unittest import TestCase

import ddt

from common.djangoapps.track import contexts


@ddt.ddt
class TestContexts(TestCase):  # pylint: disable=missing-class-docstring

    COURSE_ID = 'test/course_name/course_run'
    SPLIT_COURSE_ID = 'course-v1:test+course_name+course_run'
    ORG_ID = 'test'

    @ddt.data(
        (COURSE_ID, ''),
        (COURSE_ID, '/more/stuff'),
        (COURSE_ID, '?format=json'),
        (SPLIT_COURSE_ID, ''),
        (SPLIT_COURSE_ID, '/more/stuff'),
        (SPLIT_COURSE_ID, '?format=json')
    )
    @ddt.unpack
    def test_explicit_course_id(self, course_id, postfix):
        url = f'http://foo.bar.com/courses/not-a-course-id{postfix}'
        self.assert_parses_explicit_course_id(url, course_id)

    def assert_parses_explicit_course_id(self, url, course_id):
        assert contexts.course_context_from_url(url, course_id_string=course_id) ==\
               {'course_id': course_id, 'org_id': self.ORG_ID, 'enterprise_uuid': ''}

    @ddt.data(
        (COURSE_ID, ''),
        (COURSE_ID, '/more/stuff'),
        (COURSE_ID, '?format=json'),
        (SPLIT_COURSE_ID, ''),
        (SPLIT_COURSE_ID, '/more/stuff'),
        (SPLIT_COURSE_ID, '?format=json')
    )
    @ddt.unpack
    def test_course_id_from_url(self, course_id, postfix):
        url = f'http://foo.bar.com/courses/{course_id}{postfix}'
        self.assert_parses_course_id_from_url(url, course_id)

    def assert_parses_course_id_from_url(self, format_string, course_id):
        assert contexts.course_context_from_url(format_string.format(course_id=course_id)) ==\
               {'course_id': course_id, 'org_id': self.ORG_ID, 'enterprise_uuid': ''}

    def test_no_course_id_in_url(self):
        self.assert_empty_context_for_url('http://foo.bar.com/dashboard')

    def assert_empty_context_for_url(self, url):
        assert contexts.course_context_from_url(url) == {'course_id': '', 'org_id': '', 'enterprise_uuid': ''}

    @ddt.data('', '/', '/?', '?format=json')
    def test_malformed_course_id(self, postfix):
        self.assert_empty_context_for_url(f'http://foo.bar.com/courses/test/course_name{postfix}')

    @ddt.data(
        (COURSE_ID, ''),
        (COURSE_ID, '/more/stuff'),
        (COURSE_ID, '?format=json'),
        (SPLIT_COURSE_ID, ''),
        (SPLIT_COURSE_ID, '/more/stuff'),
        (SPLIT_COURSE_ID, '?format=json')
    )
    @ddt.unpack
    def test_course_id_later_in_url(self, course_id, postfix):
        url = f'http://foo.bar.com/x/y/z/courses/{course_id}{postfix}'
        self.assert_parses_course_id_from_url(url, course_id)

    def test_no_url(self):
        self.assert_empty_context_for_url(None)
