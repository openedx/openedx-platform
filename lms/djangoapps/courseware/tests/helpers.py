"""
Helpers for courseware tests.
"""


import ast
import re
import json
from collections import OrderedDict
from datetime import timedelta
from unittest.mock import Mock

from django.contrib import messages
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.test import TestCase
from django.test.client import Client, RequestFactory
from django.urls import reverse
from django.utils.timezone import now
from xblock.field_data import DictFieldData

from common.djangoapps.edxmako.shortcuts import render_to_string
from lms.djangoapps.courseware.access import has_access
from lms.djangoapps.courseware.utils import verified_upgrade_deadline_link
from lms.djangoapps.courseware.masquerade import MasqueradeView
from lms.djangoapps.courseware.masquerade import setup_masquerade
from lms.djangoapps.lms_xblock.field_data import LmsFieldData
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.url_utils import quote_slashes
from common.djangoapps.student.models import CourseEnrollment, Registration
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.date_utils import strftime_localized_html
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.tests import get_test_descriptor_system, get_test_system, prepare_block_runtime  # lint-amnesty, pylint: disable=wrong-import-order


class BaseTestXmodule(ModuleStoreTestCase):
    """Base class for testing Xmodules with mongo store.

    This class prepares course and users for tests:
        1. create test course;
        2. create, enroll and login users for this course;

    Any xmodule should overwrite only next parameters for test:
        1. CATEGORY
        2. DATA or METADATA
        3. MODEL_DATA
        4. USER_COUNT if needed

    This class should not contain any tests, because CATEGORY
    should be defined in child class.
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    USER_COUNT = 2

    # Data from YAML xmodule/templates/NAME/default.yaml
    CATEGORY = "vertical"
    DATA = ''
    # METADATA must be overwritten for every instance that uses it. Otherwise,
    # if we'll change it in the tests, it will be changed for all other instances
    # of parent class.
    METADATA = {}
    MODEL_DATA = {'data': '<some_module></some_module>'}

    def new_module_runtime(self, runtime=None, **kwargs):
        """
        Generate a new ModuleStoreRuntime that is minimally set up for testing
        """
        if runtime:
            return prepare_block_runtime(runtime, course_id=self.course.id, **kwargs)
        return get_test_system(course_id=self.course.id, **kwargs)

    def new_descriptor_runtime(self, **kwargs):
        runtime = get_test_descriptor_system(**kwargs)
        runtime.get_block = modulestore().get_item
        return runtime

    def initialize_module(self, runtime_kwargs=None, **kwargs):  # lint-amnesty, pylint: disable=missing-function-docstring
        kwargs.update({
            'parent_location': self.section.location,
            'category': self.CATEGORY
        })

        self.block = BlockFactory.create(**kwargs)

        self.runtime = self.new_descriptor_runtime()

        field_data = {}
        field_data.update(self.MODEL_DATA)
        student_data = DictFieldData(field_data)
        self.block._field_data = LmsFieldData(self.block._field_data, student_data)  # lint-amnesty, pylint: disable=protected-access

        if runtime_kwargs is None:
            runtime_kwargs = {}
        self.new_module_runtime(runtime=self.block.runtime, **runtime_kwargs)

        self.item_url = str(self.block.location)

    def setup_course(self):  # lint-amnesty, pylint: disable=missing-function-docstring
        self.course = CourseFactory.create()

        # Turn off cache.
        modulestore().request_cache = None
        modulestore().metadata_inheritance_cache_subsystem = None

        chapter = BlockFactory.create(
            parent_location=self.course.location,
            category="sequential",
        )
        self.section = BlockFactory.create(
            parent_location=chapter.location,
            category="sequential"
        )

        # username = robot{0}, password = 'test'
        self.users = [
            UserFactory.create()
            for dummy0 in range(self.USER_COUNT)
        ]

        for user in self.users:
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        # login all users for acces to Xmodule
        self.clients = {user.username: Client() for user in self.users}
        self.login_statuses = [
            self.clients[user.username].login(
                username=user.username, password=self.TEST_PASSWORD)
            for user in self.users
        ]

        assert all(self.login_statuses)

    def setUp(self):
        super().setUp()
        self.setup_course()
        self.initialize_module(metadata=self.METADATA, data=self.DATA)

    def get_url(self, dispatch, handler_name='xmodule_handler'):
        """Return item url with dispatch."""
        return reverse(
            'xblock_handler',
            args=(str(self.course.id), quote_slashes(self.item_url), handler_name, dispatch)
        )


class XModuleRenderingTestBase(BaseTestXmodule):  # lint-amnesty, pylint: disable=missing-class-docstring

    # lint-amnesty, pylint: disable=arguments-differ
    def new_module_runtime(self, **kwargs):
        """
        Create a runtime that actually does html rendering
        """
        if 'render_template' not in kwargs:
            kwargs['render_template'] = render_to_string
        runtime = super().new_module_runtime(**kwargs)
        runtime.modulestore = Mock()
        return runtime


class LoginEnrollmentTestCase(TestCase):
    """
    Provides support for user creation,
    activation, login, and course enrollment.
    """
    user = None

    def setup_user(self):
        """
        Create a user account, activate, and log in.
        """
        self.email = 'foo@test.com'  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.password = 'Password1234'  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.username = 'test'  # lint-amnesty, pylint: disable=attribute-defined-outside-init
        self.user = self.create_account(
            self.username,
            self.email,
            self.password,
        )
        # activate_user re-fetches and returns the activated user record
        self.user = self.activate_user(self.email)
        self.login(self.email, self.password)

    def assert_request_status_code(self, status_code, url, method="GET", **kwargs):
        """
        Make a request to the specified URL and verify that it returns the
        expected status code.
        """
        make_request = getattr(self.client, method.lower())
        response = make_request(url, **kwargs)
        assert response.status_code == status_code, f'{method} request to {url} returned status code {response.status_code}, expected status code {status_code}'  # pylint: disable=line-too-long
        return response

    def assert_account_activated(self, url, method="GET", **kwargs):  # lint-amnesty, pylint: disable=missing-function-docstring
        make_request = getattr(self.client, method.lower())
        response = make_request(url, **kwargs)
        message_list = list(messages.get_messages(response.wsgi_request))
        assert len(message_list) == 1
        assert 'success' in message_list[0].tags
        assert 'You have activated your account.' in message_list[0].message

    # ============ User creation and login ==============

    def login(self, email, password):
        """
        Login, check that the corresponding view's response has a 200 status code.
        """
        resp = self.client.post(reverse('user_api_login_session', kwargs={'api_version': 'v1'}),
                                {'email': email, 'password': password})
        assert resp.status_code == 200

    def logout(self):
        """
        Logout; check that the HTTP response code indicates redirection
        as expected.
        """
        self.assert_request_status_code(200, reverse('logout'))

    def create_account(self, username, email, password):
        """
        Create the account and check that it worked.
        """
        url = reverse('user_api_registration')
        request_data = {
            'username': username,
            'email': email,
            'password': password,
            'name': 'username',
            'terms_of_service': 'true',
            'honor_code': 'true',
        }
        self.assert_request_status_code(200, url, method="POST", data=request_data)
        # Check both that the user is created, and inactive
        user = User.objects.get(email=email)
        assert not user.is_active
        return user

    def activate_user(self, email):
        """
        Look up the activation key for the user, then hit the activate view.
        No error checking.
        """
        activation_key = Registration.objects.get(user__email=email).activation_key
        # and now we try to activate
        url = reverse('activate', kwargs={'key': activation_key})
        self.assert_account_activated(url)
        # Now make sure that the user is now actually activated
        user = User.objects.get(email=email)
        assert user.is_active
        # And return the user we fetched.
        return user

    def enroll(self, course, verify=False):
        """
        Try to enroll and return boolean indicating result.
        `course` is an instance of CourseBlock.
        `verify` is an optional boolean parameter specifying whether we
        want to verify that the student was successfully enrolled
        in the course.
        """
        resp = self.client.post(reverse('change_enrollment'), {
            'enrollment_action': 'enroll',
            'course_id': str(course.id),
            'check_access': True,
        })
        result = resp.status_code == 200
        if verify:
            assert result
        return result

    def unenroll(self, course):
        """
        Unenroll the currently logged-in user, and check that it worked.
        `course` is an instance of CourseBlock.
        """
        url = reverse('change_enrollment')
        request_data = {
            'enrollment_action': 'unenroll',
            'course_id': str(course.id),
        }
        self.assert_request_status_code(200, url, method="POST", data=request_data)


class CourseAccessTestMixin(TestCase):
    """
    Utility mixin for asserting access (or lack thereof) to courses.
    If relevant, also checks access for courses' corresponding CourseOverviews.
    """

    def assertCanAccessCourse(self, user, action, course):
        """
        Assert that a user has access to the given action for a given course.

        Test with both the given course and with a CourseOverview of the given
        course.

        Arguments:
            user (User): a user.
            action (str): type of access to test.
            course (CourseBlock): a course.
        """
        assert has_access(user, action, course)
        assert has_access(user, action, CourseOverview.get_from_id(course.id))

    def assertCannotAccessCourse(self, user, action, course):
        """
        Assert that a user lacks access to the given action the given course.

        Test with both the given course and with a CourseOverview of the given
        course.

        Arguments:
            user (User): a user.
            action (str): type of access to test.
            course (CourseBlock): a course.

        Note:
            It may seem redundant to have one method for testing access
            and another method for testing lack thereof (why not just combine
            them into one method with a boolean flag?), but it makes reading
            stack traces of failed tests easier to understand at a glance.
        """
        assert not has_access(user, action, course)
        assert not has_access(user, action, CourseOverview.get_from_id(course.id))


class MasqueradeMixin:
    """
    Adds masquerade utilities for your TestCase.

    Your test case class must have self.client. And can optionally have self.course if you don't want
    to pass in the course parameter below.
    """

    def update_masquerade(self, course=None, course_id=None, role='student', group_id=None, username=None,
                          user_partition_id=None):
        """
        Installs a masquerade for the specified user and course, to enable
        the user to masquerade as belonging to the specific partition/group
        combination.

        Arguments:
            course (object): a course or None for self.course (or you can pass course_id instead)
            course_id (str|CourseKey): a course id, useful if you don't happen to have a full course object handy
            user_partition_id (int): the integer partition id, referring to partitions already
               configured in the course.
            group_id (int); the integer group id, within the specified partition.
            username (str): user to masquerade as
            role (str): role to masquerade as

        Returns: the response object for the AJAX call to update the user's masquerade.
        """
        course_id = str(course_id or (course and course.id) or self.course.id)
        masquerade_url = reverse(
            'masquerade_update',
            kwargs={
                'course_key_string': course_id,
            }
        )
        response = self.client.post(
            masquerade_url,
            json.dumps({
                'role': role,
                'group_id': group_id,
                'user_name': username,
                'user_partition_id': user_partition_id,
            }),
            'application/json'
        )
        assert response.status_code == 200
        assert response.json()['success'], response.json().get('error')
        return response


def masquerade_as_group_member(user, course, partition_id, group_id):
    """
    Installs a masquerade for the specified user and course, to enable
    the user to masquerade as belonging to the specific partition/group
    combination.

    Arguments:
        user (User): a user.
        course (CourseBlock): a course.
        partition_id (int): the integer partition id, referring to partitions already
           configured in the course.
        group_id (int); the integer group id, within the specified partition.

    Returns: the status code for the AJAX response to update the user's masquerade for
        the specified course.
    """
    request = _create_mock_json_request(
        user,
        data={"role": "student", "user_partition_id": partition_id, "group_id": group_id}
    )
    response = MasqueradeView.as_view()(request, str(course.id))
    setup_masquerade(request, course.id, True)
    return response.status_code


def _create_mock_json_request(user, data, method='POST'):
    """
    Returns a mock JSON request for the specified user.
    """
    factory = RequestFactory()
    request = factory.generic(method, '/', content_type='application/json', data=json.dumps(data))
    request.user = user
    request.session = {}
    return request


def get_expiration_banner_text(user, course, language='en'):  # lint-amnesty, pylint: disable=unused-argument
    """
    Get text for banner that messages user course expiration date
    for different tests that depend on it.
    """
    upgrade_link = verified_upgrade_deadline_link(user=user, course=course)
    enrollment = CourseEnrollment.get_enrollment(user, course.id)
    expiration_date = enrollment.created + timedelta(weeks=4)
    upgrade_deadline = enrollment.upgrade_deadline
    if upgrade_deadline is None or now() < upgrade_deadline:
        upgrade_deadline = enrollment.course_upgrade_deadline

    formatted_expiration_date = strftime_localized_html(expiration_date, 'SHORT_DATE')
    if upgrade_deadline:
        formatted_upgrade_deadline = strftime_localized_html(upgrade_deadline, 'SHORT_DATE')

        bannerText = '<strong>Audit Access Expires {expiration_date}</strong><br>\
                     You lose all access to this course, including your progress, on {expiration_date}.\
                     <br>Upgrade by {upgrade_deadline} to get unlimited access to the course as long as it exists\
                     on the site. <a id="FBE_banner" href="{upgrade_link}">Upgrade now<span class="sr-only"> to retain access past\
                     {expiration_date}</span></a>'.format(
            expiration_date=formatted_expiration_date,
            upgrade_link=upgrade_link,
            upgrade_deadline=formatted_upgrade_deadline
        )
    else:
        bannerText = '<strong>Audit Access Expires {expiration_date}</strong><br>\
                     You lose all access to this course, including your progress, on {expiration_date}.\
                     '.format(
            expiration_date=formatted_expiration_date
        )
    return bannerText


def get_context_dict_from_string(data):
    """
    Retrieve dictionary from string.
    """
    # Replace tuple and un-necessary info from inside string and get the dictionary.
    cleaned_data = data.split('((\'video.html\',')[1].replace("),\n {})", '').strip()
    # Omit user_id validation
    cleaned_data_without_user = re.sub(".*user_id.*\n?", '', cleaned_data)

    validated_data = ast.literal_eval(cleaned_data_without_user)
    validated_data['metadata'] = OrderedDict(
        sorted(json.loads(validated_data['metadata']).items(), key=lambda t: t[0])
    )
    return validated_data
