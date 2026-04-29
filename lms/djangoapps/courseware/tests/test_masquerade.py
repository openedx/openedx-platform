"""
Unit tests for masquerade.
"""

import json
import pickle
from datetime import datetime
from importlib import import_module
from operator import itemgetter  # pylint: disable=wrong-import-order
from unittest.mock import patch

import ddt
import pytest
from django.conf import settings
from django.test import RequestFactory, TestCase
from django.urls import reverse
from pytz import UTC
from xblock.runtime import DictKeyValueStore
from xblocks_contrib.problem.capa.tests.response_xml_factory import OptionResponseXMLFactory

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import StaffFactory, UserFactory
from lms.djangoapps.courseware.masquerade import (
    MASQUERADE_SETTINGS_KEY,
    CourseMasquerade,
    MasqueradingKeyValueStore,
    get_masquerading_user_group,
    setup_masquerade,
)
from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase, MasqueradeMixin, masquerade_as_group_member
from lms.djangoapps.courseware.tests.test_submitting_problems import ProblemSubmissionTestMixin
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.preferences.api import get_user_preference, set_user_preference
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)
from xmodule.partitions.partitions import Group, UserPartition  # pylint: disable=wrong-import-order


class MasqueradeTestCase(SharedModuleStoreTestCase, LoginEnrollmentTestCase, MasqueradeMixin):
    """
    Base class for masquerade tests that sets up a test course and enrolls a user in the course.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.course = CourseFactory.create(number='masquerade-test', metadata={'start': datetime.now(UTC)})
        cls.info_page = BlockFactory.create(
            category="course_info", parent_location=cls.course.location,
            data="OOGIE BLOOGIE", display_name="updates"
        )
        cls.chapter = BlockFactory.create(
            parent_location=cls.course.location,
            category="chapter",
            display_name="Test Section",
        )
        cls.sequential_display_name = "Test Masquerade Subsection"
        cls.sequential = BlockFactory.create(
            parent_location=cls.chapter.location,
            category="sequential",
            display_name=cls.sequential_display_name,
        )
        cls.vertical = BlockFactory.create(
            parent_location=cls.sequential.location,
            category="vertical",
            display_name="Test Unit",
        )
        problem_xml = OptionResponseXMLFactory().build_xml(
            question_text='The correct answer is Correct',
            num_inputs=2,
            weight=2,
            options=['Correct', 'Incorrect'],
            correct_option='Correct'
        )
        cls.problem_display_name = "TestMasqueradeProblem"
        cls.problem = BlockFactory.create(
            parent_location=cls.vertical.location,
            category='problem',
            data=problem_xml,
            display_name=cls.problem_display_name
        )

    def setUp(self):
        super().setUp()

        self.test_user = self.create_user()
        self.login(self.test_user.email, self.TEST_PASSWORD)
        self.enroll(self.course, True)

    def get_courseware_page(self):
        """
        Returns the server response for the courseware page.
        """
        url = reverse(
            'courseware_subsection',
            kwargs={
                'course_id': str(self.course.id),
                'section': self.chapter.location.block_id,
                'subsection': self.sequential.location.block_id,
            }
        )
        return self.client.get(url)

    def get_progress_page(self):
        """
        Returns the server response for progress page.
        """
        url = reverse(
            'progress',
            kwargs={
                'course_id': str(self.course.id),
            }
        )
        return self.client.get(url)

    def get_available_masquerade_identities(self):
        """
        Returns: the server response for masquerade options
        """
        url = reverse(
            'masquerade_update',
            kwargs={
                'course_key_string': str(self.course.id),
            }
        )
        return self.client.get(url)

    def verify_staff_debug_present(self, staff_debug_expected):
        """
        Verifies that the staff debug control visibility is as expected (for staff only).
        """
        content = self.get_courseware_page().content.decode('utf-8')
        assert self.sequential_display_name in content, 'Subsection should be visible'
        assert staff_debug_expected == ('Staff Debug Info' in content)

    def get_problem(self):
        """
        Returns the JSON content for the problem in the course.
        """
        problem_url = reverse(
            'xblock_handler',
            kwargs={
                'course_id': str(self.course.id),
                'usage_id': str(self.problem.location),
                'handler': 'xmodule_handler',
                'suffix': 'problem_get'
            }
        )
        return self.client.get(problem_url)

    def verify_show_answer_present(self, show_answer_expected):
        """
        Verifies that "Show answer" is only present when expected (for staff only).
        """
        problem_html = json.loads(self.get_problem().content.decode('utf-8'))['html']
        assert self.problem_display_name in problem_html
        assert show_answer_expected == ('Show answer' in problem_html)

    def ensure_masquerade_as_group_member(self, partition_id, group_id):
        """
        Installs a masquerade for the test_user and test course, to enable the
        user to masquerade as belonging to the specific partition/group combination.
        Also verifies that the call to install the masquerade was successful.

        Arguments:
            partition_id (int): the integer partition id, referring to partitions already
               configured in the course.
            group_id (int); the integer group id, within the specified partition.
        """
        assert 200 == masquerade_as_group_member(self.test_user, self.course, partition_id, group_id)


class StaffMasqueradeTestCase(MasqueradeTestCase):
    """
    Base class for tests of the masquerade behavior for a staff member.
    """

    def create_user(self):
        """
        Creates a staff user.
        """
        return StaffFactory(course_key=self.course.id)


@ddt.ddt
class TestMasqueradeLearnerOptions(StaffMasqueradeTestCase):
    """
    Check that 'View as Learner' option is available only if there are NO groups or partitions
    """

    @ddt.data(True, False)
    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_MASQUERADE': True})
    def test_masquerade_options_for_learner(self, partitions_enabled):
        """
        If there are partitions, then the View as Learner should NOT be available
        """
        with patch.dict('django.conf.settings.FEATURES',
                        {'ENABLE_ENROLLMENT_TRACK_USER_PARTITION': partitions_enabled}):
            response = self.get_available_masquerade_identities()
            is_learner_available = 'Learner' in map(itemgetter('name'), response.json()['available'])
            assert partitions_enabled != is_learner_available


@ddt.ddt
class TestMasqueradeOptionsNoContentGroups(StaffMasqueradeTestCase):
    """
    Test that split_test content groups (which are the partitions with a "random" scheme),
    do not show up in the masquerade options popup, but cohort groups do appear.
    """

    def setUp(self):
        super().setUp()

        self.user_partition = UserPartition(
            0, 'Test User Partition', '',
            [Group(0, 'Cohort Group 1'), Group(1, 'Cohort Group 2')],
            scheme_id='cohort'
        )
        self.course.user_partitions.append(self.user_partition)
        self.user_partition = UserPartition(
            0, 'Test User Partition 2', '',
            [Group(0, 'Content Group 1'), Group(1, 'Content Group 2')],
            scheme_id='random'
        )
        self.course.user_partitions.append(self.user_partition)

        modulestore().update_item(self.course, self.test_user.id)

    @ddt.data(['Cohort Group 1', True], ['Content Group 1', False])
    @ddt.unpack
    @patch.dict('django.conf.settings.FEATURES', {'ENABLE_MASQUERADE': True})
    def testMasqueradeCohortAvailable(self, target, expected):
        """
        Args:
            target: The partition to check for in masquerade options
            expected: Whether to partition should be in the list
        """
        response = self.get_available_masquerade_identities()
        is_target_available = target in map(itemgetter('name'), response.json()['available'])
        assert is_target_available == expected


@ddt.ddt
class TestStaffMasqueradeAsSpecificStudent(StaffMasqueradeTestCase, ProblemSubmissionTestMixin):
    """
    Check for staff being able to masquerade as a specific student.
    """

    def setUp(self):
        super().setUp()
        self.student_user = self.create_user()
        self.login_student()
        self.enroll(self.course, True)

    def login_staff(self):
        """ Login as a staff user """
        self.logout()
        self.login(self.test_user.email, self.TEST_PASSWORD)

    def login_student(self):
        """ Login as a student """
        self.logout()
        self.login(self.student_user.email, self.TEST_PASSWORD)

    def submit_answer(self, response1, response2):
        """
        Submit an answer to the single problem in our test course.
        """
        return self.submit_question_answer(
            self.problem_display_name,
            {'2_1': response1, '2_2': response2}
        )

    def get_progress_detail(self):
        """
        Return the reported progress detail for the problem in our test course.

        The return value is a string like u'1/2'.
        """
        json_data = json.loads(self.look_at_question(self.problem_display_name).content.decode('utf-8'))
        progress = '{}/{}'.format(str(json_data['current_score']), str(json_data['total_possible']))
        return progress

    def assertExpectedLanguageInPreference(self, user, expected_language_code):
        """
        This method is a custom assertion verifies that a given user has expected
        language code in the preference and in cookies.

        Arguments:
            user: User model instance
            expected_language_code: string indicating a language code
        """
        assert get_user_preference(user, LANGUAGE_KEY) == expected_language_code
        assert self.client.cookies[settings.LANGUAGE_COOKIE_NAME].value == expected_language_code

    @ddt.data(
        'john',  # Non-unicode username
        'fôô@bar',  # Unicode username with @, which is what the ENABLE_UNICODE_USERNAME feature allows
    )
    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_masquerade_as_specific_student(self, username):
        """
        Test masquerading as a specific user.

        We answer the problem in our test course as the student and as staff user, and we use the
        progress as a proxy to determine who's state we currently see.
        """
        student = UserFactory.create(username=username)
        CourseEnrollment.enroll(student, self.course.id)
        self.logout()
        self.login(student.email, self.TEST_PASSWORD)
        # Answer correctly as the student, and check progress.
        self.submit_answer('Correct', 'Correct')
        assert self.get_progress_detail() == '2/2'

        # Log in as staff, and check the problem is unanswered.
        self.login_staff()
        assert self.get_progress_detail() == '0/2'

        # Masquerade as the student, and check we can see the student state.
        self.update_masquerade(role='student', username=student.username)
        assert self.get_progress_detail() == '2/2'

        # Temporarily override the student state.
        self.submit_answer('Correct', 'Incorrect')
        assert self.get_progress_detail() == '1/2'

        # Reload the page and check we see the student state again.
        self.get_courseware_page()
        assert self.get_progress_detail() == '2/2'

        # Become the staff user again, and check the problem is still unanswered.
        self.update_masquerade(role='staff')
        assert self.get_progress_detail() == '0/2'

        # Verify the student state did not change.
        self.logout()
        self.login(student.email, self.TEST_PASSWORD)
        assert self.get_progress_detail() == '2/2'

    def test_masquerading_with_language_preference(self):
        """
        Tests that masquerading as a specific user for the course does not update preference language
        for the staff.

        Login as a staff user and set user's language preference to english and visit the courseware page.
        Set masquerade to view same page as a specific student having different language preference and
        revisit the courseware page.
        """
        english_language_code = 'en'
        set_user_preference(self.test_user, preference_key=LANGUAGE_KEY, preference_value=english_language_code)
        self.login_staff()

        # Reload the page and check we have expected language preference in system and in cookies.
        self.get_courseware_page()
        self.assertExpectedLanguageInPreference(self.test_user, english_language_code)

        # Set student language preference and set masquerade to view same page the student.
        set_user_preference(self.student_user, preference_key=LANGUAGE_KEY, preference_value='es-419')
        self.update_masquerade(role='student', username=self.student_user.username)

        # Reload the page and check we have expected language preference in system and in cookies.
        self.get_courseware_page()
        self.assertExpectedLanguageInPreference(self.test_user, english_language_code)

    def test_masquerade_as_specific_student_progress(self):
        """
        Test masquerading as a specific user for progress page.
        """
        # Give the student some correct answers, check their progress page
        self.login_student()
        self.submit_answer('Correct', 'Correct')
        student_progress = self.get_progress_page().content.decode('utf-8')
        assert '1 of 2 possible points' not in student_progress
        assert '2 of 2 possible points' in student_progress

        # Staff answers are slightly different
        self.login_staff()
        self.submit_answer('Incorrect', 'Correct')
        staff_progress = self.get_progress_page().content.decode('utf-8')
        assert '2 of 2 possible points' not in staff_progress
        assert '1 of 2 possible points' in staff_progress

        # Should now see the student's scores
        self.update_masquerade(role='student', username=self.student_user.username)
        masquerade_progress = self.get_progress_page().content.decode('utf-8')
        assert '1 of 2 possible points' not in masquerade_progress
        assert '2 of 2 possible points' in masquerade_progress


class TestGetMasqueradingGroupId(StaffMasqueradeTestCase):
    """
    Check for staff being able to masquerade as belonging to a group.
    """

    def setUp(self):
        super().setUp()
        self.user_partition = UserPartition(
            0, 'Test User Partition', '',
            [Group(0, 'Group 1'), Group(1, 'Group 2')],
            scheme_id='cohort'
        )
        self.course.user_partitions.append(self.user_partition)
        modulestore().update_item(self.course, self.test_user.id)

    @patch.dict('django.conf.settings.FEATURES', {'DISABLE_START_DATES': False})
    def test_get_masquerade_group(self):
        """
        Tests that a staff member can masquerade as being in a group in a user partition
        """
        # Verify there is no masquerading group initially
        group = get_masquerading_user_group(self.course.id, self.test_user, self.user_partition)
        assert group is None

        # Install a masquerading group
        self.ensure_masquerade_as_group_member(0, 1)

        # Verify that the masquerading group is returned
        group = get_masquerading_user_group(self.course.id, self.test_user, self.user_partition)
        assert group.id == 1


class ReadOnlyKeyValueStore(DictKeyValueStore):
    """
    A KeyValueStore that raises an exception on attempts to modify it.

    Used to make sure MasqueradingKeyValueStore does not try to modify the underlying KeyValueStore.
    """

    def set(self, key, value):
        assert False, "ReadOnlyKeyValueStore may not be modified."  # noqa: B011, PT015

    def delete(self, key):
        assert False, "ReadOnlyKeyValueStore may not be modified."  # noqa: B011, PT015

    def set_many(self, update_dict):  # pylint: disable=arguments-differ, unused-argument
        assert False, "ReadOnlyKeyValueStore may not be modified."  # noqa: B011, PT015


class FakeSession(dict):
    """ Mock for Django session object. """
    modified = False  # We need dict semantics with a writable 'modified' property


class MasqueradingKeyValueStoreTest(TestCase):
    """
    Unit tests for the MasqueradingKeyValueStore class.
    """

    def setUp(self):
        super().setUp()
        self.ro_kvs = ReadOnlyKeyValueStore({'a': 42, 'b': None, 'c': 'OpenCraft'})
        self.session = FakeSession()
        self.kvs = MasqueradingKeyValueStore(self.ro_kvs, self.session)

    def test_all(self):
        assert self.kvs.get('a') == 42
        assert self.kvs.get('b') is None
        assert self.kvs.get('c') == 'OpenCraft'
        with pytest.raises(KeyError):
            self.kvs.get('d')

        assert self.kvs.has('a')
        assert self.kvs.has('b')
        assert self.kvs.has('c')
        assert not self.kvs.has('d')

        self.kvs.set_many({'a': 'Norwegian Blue', 'd': 'Giraffe'})
        self.kvs.set('b', 7)

        assert self.kvs.get('a') == 'Norwegian Blue'
        assert self.kvs.get('b') == 7
        assert self.kvs.get('c') == 'OpenCraft'
        assert self.kvs.get('d') == 'Giraffe'

        for key in 'abd':
            assert self.kvs.has(key)
            self.kvs.delete(key)
            with pytest.raises(KeyError):
                self.kvs.get(key)

        assert self.kvs.get('c') == 'OpenCraft'


class CourseMasqueradeTest(TestCase):
    """
    Unit tests for the CourseMasquerade class.
    """

    def test_unpickling_sets_all_attributes(self):
        """
        Make sure that old CourseMasquerade objects receive missing attributes when unpickled from
        the session.
        """
        cmasq = CourseMasquerade(7)
        del cmasq.user_name
        pickled_cmasq = pickle.dumps(cmasq)
        unpickled_cmasq = pickle.loads(pickled_cmasq)
        assert unpickled_cmasq.user_name is None


class SetupMasqueradeTests(SharedModuleStoreTestCase, ):
    """
    Tests for the setup_masquerade function.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(number='setup-masquerade-test', metadata={'start': datetime.now(UTC)})
        self.request = RequestFactory().request()
        self.staff = StaffFactory(course_key=self.course.id)
        self.student = UserFactory()

        CourseEnrollment.enroll(self.student, self.course.id)

        session_key = "abcdef"
        self.request.user = self.staff
        self.request.session = import_module(settings.SESSION_ENGINE).SessionStore(session_key)

    def test_setup_masquerade(self):
        masquerade_settings = {
            self.course.id: CourseMasquerade(
                course_key=self.course.id,
                role='student',
                user_name=self.student.username
            )
        }
        self.request.session[MASQUERADE_SETTINGS_KEY] = masquerade_settings

        course_masquerade, masquerade_user = setup_masquerade(
            self.request,
            self.course.id,
            staff_access=True
        )

        # Warning: the SafeSessions middleware relies on the `real_user` attribute to see if a
        # user is masquerading as another user.  If the name of this attribute is changing, please update
        # the check in SafeSessionMiddleware._verify_user_unchanged as well.
        assert masquerade_user.real_user == self.staff
        assert masquerade_user == self.student
        assert self.request.user.masquerade_settings == masquerade_settings
        assert course_masquerade == masquerade_settings[self.course.id]
