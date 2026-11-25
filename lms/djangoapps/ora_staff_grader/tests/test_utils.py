"""
Tests for ESG utils
"""
import json

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate
from xblock.core import XBlock

from common.djangoapps.student.tests.factories import StaffFactory
from lms.djangoapps.ora_staff_grader.utils import call_xblock_json_handler
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory


class EchoUserXBlock(XBlock):
    """
    XBlock with a JSON handler that reports which user it ran as.
    """

    @XBlock.json_handler
    def echo_user(self, data, suffix=''):  # pylint: disable=unused-argument
        return {'user_id': self.scope_ids.user_id, 'data': data}


class TestCallXBlockJsonHandler(ModuleStoreTestCase):
    """
    Tests for call_xblock_json_handler
    """

    @XBlock.register_temp_plugin(EchoUserXBlock, identifier='echo_user')
    def test_handler_runs_as_request_user(self):
        """
        The handler runs as the user of the original request, which carries no CSRF token.
        """
        course = CourseFactory.create()
        chapter = BlockFactory.create(parent=course, category='chapter')
        block = BlockFactory.create(parent=chapter, category='echo_user')
        staff = StaffFactory.create(course_key=course.id)
        django_request = APIRequestFactory(enforce_csrf_checks=True).get('/')
        django_request.session = {}
        force_authenticate(django_request, user=staff)

        response = call_xblock_json_handler(Request(django_request), str(block.location), 'echo_user', {'a': 1})

        assert response.status_code == 200
        assert json.loads(response.content) == {'user_id': staff.id, 'data': {'a': 1}}
