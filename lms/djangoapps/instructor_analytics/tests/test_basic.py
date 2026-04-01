"""
Tests for instructor.basic
"""


from unittest.mock import MagicMock, Mock, patch

import datetime
import random
import ddt
import json  # lint-amnesty, pylint: disable=wrong-import-order
from django.contrib.auth import get_user_model
from edx_proctoring.api import create_exam
from edx_proctoring.models import ProctoredExamStudentAttempt
from opaque_keys.edx.locator import UsageKey
from lms.djangoapps.instructor_analytics.basic import (  # lint-amnesty, pylint: disable=unused-import
    AVAILABLE_FEATURES,
    ENROLLMENT_FEATURES,
    PROFILE_FEATURES,
    PROGRAM_ENROLLMENT_FEATURES,
    STUDENT_FEATURES,
    StudentModule,
    issued_certificates,
    enrolled_students_features,
    get_available_features,
    get_proctored_exam_results,
    get_response_state,
    get_student_features_with_custom,
    list_may_enroll,
    list_problem_responses
)
from lms.djangoapps.program_enrollments.tests.factories import ProgramEnrollmentFactory
from lms.djangoapps.certificates.api import create_generated_certificate
from openedx.core.djangoapps.course_groups.tests.helpers import CohortFactory
from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory
from common.djangoapps.student.models import CourseEnrollment, CourseEnrollmentAllowed
from common.djangoapps.student.tests.factories import InstructorFactory
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order

User = get_user_model()


@ddt.ddt
class TestAnalyticsBasic(ModuleStoreTestCase):
    """ Test basic analytics functions. """

    def setUp(self):
        super().setUp()
        self.course_key = self.store.make_course_key('robot', 'course', 'id')
        self.users = tuple(UserFactory() for _ in range(30))
        self.ces = tuple(CourseEnrollment.enroll(user, self.course_key)
                         for user in self.users)
        self.instructor = InstructorFactory(course_key=self.course_key)
        for user in self.users:
            user.profile.meta = json.dumps({
                "position": f"edX expert {user.id}",
                "company": f"Open edX Inc {user.id}",
            })
            user.profile.save()
        self.students_who_may_enroll = list(self.users) + [UserFactory() for _ in range(5)]
        for student in self.students_who_may_enroll:
            CourseEnrollmentAllowed.objects.create(
                email=student.email,
                course_id=self.course_key,
                user=student if student in self.users else None,
            )

    @ddt.data(
        ('あなた', 'スの中'),
        ("ГЂіи lіиэ ъэтшээи", "Ђэаvэи аиↁ Ђэѓэ")
    )
    @ddt.unpack
    def test_get_response_state_with_ora(self, files_descriptions, saved_response):
        """
        Tests that ORA response state is transformed expectedly when the problem
        state contains unicode characters.
        """
        payload_state = json.dumps({
            'saved_response': json.dumps({'parts': [{'text': saved_response}]}),
            'saved_files_descriptions': json.dumps([files_descriptions]),
        })
        response = Mock(module_type='openassessment', student=Mock(username='staff'), state=payload_state)

        transformed_state = json.loads(get_response_state(response))
        assert transformed_state['saved_files_descriptions'][0] == files_descriptions
        assert transformed_state['saved_response']['parts'][0]['text'] == saved_response

    def test_list_problem_responses(self):
        def result_factory(result_id):
            """
            Return a dummy StudentModule object that can be queried for
            relevant info (student.username and state).
            """
            result = Mock(spec=['student', 'state'])
            result.student.username.return_value = f'user{result_id}'
            result.state.return_value = f'state{result_id}'
            return result

        # Ensure that UsageKey.from_string returns a problem key that list_problem_responses can work with
        # (even when called with a dummy location):
        mock_problem_key = Mock(return_value='')
        mock_problem_key.course_key = self.course_key
        with patch.object(UsageKey, 'from_string') as patched_from_string:
            patched_from_string.return_value = mock_problem_key

            # Ensure that StudentModule.objects.filter returns a result set that list_problem_responses can work with
            # (this keeps us from having to create fixtures for this test):
            mock_results = MagicMock(return_value=[result_factory(n) for n in range(5)])
            with patch.object(StudentModule, 'objects') as patched_manager:
                patched_manager.filter.return_value = mock_results

                mock_problem_location = ''
                problem_responses = list_problem_responses(self.course_key, problem_location=mock_problem_location)

                # Check if list_problem_responses called UsageKey.from_string to look up problem key:
                patched_from_string.assert_called_once_with(mock_problem_location)
                # Check if list_problem_responses called StudentModule.objects.filter to obtain relevant records:
                patched_manager.filter.assert_called_once_with(
                    course_id=self.course_key, module_state_key=mock_problem_key
                )

                # Check if list_problem_responses returned expected results:
                assert len(problem_responses) == len(mock_results)
                for mock_result in mock_results:
                    assert {'username': mock_result.student.username, 'state': mock_result.state} in problem_responses

    def test_enrolled_students_features_username(self):
        assert 'username' in AVAILABLE_FEATURES
        userreports = enrolled_students_features(self.course_key, ['username'])
        assert len(userreports) == len(self.users)
        for userreport in userreports:
            assert list(userreport.keys()) == ['username']
            assert userreport['username'] in [user.username for user in self.users]

    def test_issued_certificates_basic(self):
        """
        Test that the `issued_certificates` function returns the correct aggregated data
        for a single downloadable certificate.

        This test:
        - Creates a downloadable certificate for a user.
        - Verifies that the function returns a list with a single item.
        - Confirms that the returned certificate contains the expected course ID.
        - Ensures that the total count of issued certificates is correct.
        - Verifies that the 'report_run_date' field is present in the result.

        The test ensures that the `issued_certificates` function behaves as expected
        for a single downloadable certificate scenario.
        """
        cert_args = {
            "user": UserFactory(),
            "course_id": self.course_key,
            "mode": "honor",
            "status": "downloadable",
            "grade": "Pass"
        }
        create_generated_certificate(cert_args)
        features = ['course_id', 'mode', 'status', 'grade']
        results = issued_certificates(self.course_key, features)

        assert isinstance(results, list)
        assert len(results) == 1

        cert = results[0]
        assert cert['course_id'] == str(self.course_key)
        assert cert['total_issued_certificate'] == 1
        assert 'report_run_date' in cert

    def test_enrolled_students_features_keys(self):
        query_features = ('username', 'name', 'email', 'city', 'country',)
        for user in self.users:
            user.profile.city = f"Mos Eisley {user.id}"
            user.profile.country = f"Tatooine {user.id}"
            user.profile.save()
        for feature in query_features:
            assert feature in AVAILABLE_FEATURES
        with self.assertNumQueries(2):
            userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == len(self.users)

        userreports = sorted(userreports, key=lambda u: u["username"])
        users = sorted(self.users, key=lambda u: u.username)
        for userreport, user in zip(userreports, users):
            assert set(userreport.keys()) == set(query_features)
            assert userreport['username'] == user.username
            assert userreport['email'] == user.email
            assert userreport['name'] == user.profile.name
            assert userreport['city'] == user.profile.city
            assert userreport['country'] == user.profile.country

    def test_enrolled_student_with_no_country_city(self):
        userreports = enrolled_students_features(self.course_key, ('username', 'city', 'country',))
        for userreport in userreports:
            # This behaviour is somewhat inconsistent: None string fields
            # objects are converted to "None", but non-JSON serializable fields
            # are converted to an empty string.
            assert userreport['city'] == 'None'
            assert userreport['country'] == ''

    def test_enrolled_students_meta_features_keys(self):
        """
        Assert that we can query individual fields in the 'meta' field in the UserProfile
        """
        query_features = ('meta.position', 'meta.company')
        with self.assertNumQueries(2):
            userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == len(self.users)
        for userreport in userreports:
            assert set(userreport.keys()) == set(query_features)
            assert userreport['meta.position'] in [f"edX expert {user.id}" for user in self.users]
            assert userreport['meta.company'] in [f"Open edX Inc {user.id}" for user in self.users]

    def test_enrolled_students_enrollment_verification(self):
        """
        Assert that we can get enrollment mode and verification status
        """
        query_features = ('enrollment_mode', 'verification_status')
        userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == len(self.users)
        # by default all users should have "audit" as their enrollment mode
        # and "N/A" as their verification status
        for userreport in userreports:
            assert set(userreport.keys()) == set(query_features)
            assert userreport['enrollment_mode'] in ['audit']
            assert userreport['verification_status'] in ['N/A']
        # make sure that the user report respects whatever value
        # is returned by verification and enrollment code
        MODE_MTHD_NAME = "common.djangoapps.student.models.course_enrollment.CourseEnrollment.enrollment_mode_for_user"
        with patch(MODE_MTHD_NAME) as enrollment_patch:
            with patch(
                "lms.djangoapps.verify_student.services.IDVerificationService.verification_status_for_user"
            ) as verify_patch:
                enrollment_patch.return_value = ["verified"]
                verify_patch.return_value = "dummy verification status"
                userreports = enrolled_students_features(self.course_key, query_features)
                assert len(userreports) == len(self.users)
                for userreport in userreports:
                    assert set(userreport.keys()) == set(query_features)
                    assert userreport['enrollment_mode'] in ['verified']
                    assert userreport['verification_status'] in ['dummy verification status']

    def test_enrolled_students_features_keys_cohorted(self):
        course = CourseFactory.create(org="test", course="course1", display_name="run1")
        course.cohort_config = {'cohorted': True, 'auto_cohort': True, 'auto_cohort_groups': ['cohort']}
        self.store.update_item(course, self.instructor.id)
        cohorted_students = [UserFactory.create() for _ in range(10)]
        cohort = CohortFactory.create(name='cohort', course_id=course.id, users=cohorted_students)
        cohorted_usernames = [student.username for student in cohorted_students]
        non_cohorted_student = UserFactory.create()
        for student in cohorted_students:
            cohort.users.add(student)
            CourseEnrollment.enroll(student, course.id)
        CourseEnrollment.enroll(non_cohorted_student, course.id)
        instructor = InstructorFactory(course_key=course.id)
        self.client.login(username=instructor.username, password='test')

        query_features = ('username', 'cohort')
        # There should be a constant of 3 SQL queries when calling
        # enrolled_students_features. The first query comes from the call to
        # CourseEnrollment.objects.filter(...) with select_related for user and profile,
        # the second comes from prefetch_related('user__course_groups'), and the third
        # comes from configuration_helpers.get_value_for_org() for custom student attributes.
        with self.assertNumQueries(3):
            userreports = enrolled_students_features(course.id, query_features)
        assert len([r for r in userreports if r['username'] in cohorted_usernames]) == len(cohorted_students)
        assert len([r for r in userreports if r['username'] == non_cohorted_student.username]) == 1
        for report in userreports:
            assert set(report.keys()) == set(query_features)
            if report['username'] in cohorted_usernames:
                assert report['cohort'] == cohort.name
            else:
                assert report['cohort'] == '[unassigned]'

    def test_enrolled_student_features_external_user_keys(self):
        query_features = ('username', 'name', 'email', 'city', 'country', 'external_user_key')
        username_with_external_user_key_dict = {}
        for i in range(len(self.users)):
            # Setup some users with ProgramEnrollments
            if i % 2 == 0:
                user = self.users[i]
                external_user_key = f'{user.username}_{i}'
                ProgramEnrollmentFactory.create(user=user, external_user_key=external_user_key)
                username_with_external_user_key_dict[user.username] = external_user_key

        with self.assertNumQueries(3):
            userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == 30
        for report in userreports:
            username = report['username']
            external_key = username_with_external_user_key_dict.get(username)
            if external_key:
                assert external_key == report['external_user_key']
            else:
                assert '' == report['external_user_key']

    def test_available_features(self):
        assert len(AVAILABLE_FEATURES) == len(
            STUDENT_FEATURES +
            PROFILE_FEATURES +
            PROGRAM_ENROLLMENT_FEATURES +
            ENROLLMENT_FEATURES
        )
        assert set(AVAILABLE_FEATURES) == set(
            STUDENT_FEATURES +
            PROFILE_FEATURES +
            PROGRAM_ENROLLMENT_FEATURES +
            ENROLLMENT_FEATURES
        )

    def test_enrolled_students_enrollment_date(self):
        """Test that enrollment_date feature works correctly and returns the correct enrollment date."""
        query_features = ('username', 'enrollment_date',)
        for feature in query_features:
            assert feature in AVAILABLE_FEATURES
        with self.assertNumQueries(2):
            userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == len(self.users)

        enrollment_dict = {}
        for user in self.users:
            enrollment = CourseEnrollment.objects.get(user=user, course_id=self.course_key)
            enrollment_dict[user.username] = enrollment.created

        for userreport in userreports:
            expected_enrollment_date = enrollment_dict[userreport['username']]
            assert userreport['enrollment_date'] == expected_enrollment_date

    def test_enrolled_students_extended_model_age(self):
        """Test that custom age attribute works correctly with user profile year_of_birth."""
        SiteConfigurationFactory.create(
            site_values={
                'course_org_filter': ['robot'],
                'additional_student_profile_attributes': ['age'],
            }
        )

        def get_age(self):
            return datetime.datetime.now().year - self.profile.year_of_birth
        User.age = property(get_age)

        for user in self.users:
            user.profile.year_of_birth = random.randint(1900, 2000)
            user.profile.save()

        query_features = ('username', 'age',)
        with self.assertNumQueries(3):
            userreports = enrolled_students_features(self.course_key, query_features)
        assert len(userreports) == len(self.users)

        userreports = sorted(userreports, key=lambda u: u["username"])
        users = sorted(self.users, key=lambda u: u.username)
        for userreport, user in zip(userreports, users):
            assert set(userreport.keys()) == set(query_features)
            assert userreport['age'] == str(user.age)

        del User.age

    def test_list_may_enroll(self):
        may_enroll = list_may_enroll(self.course_key, ['email'])
        assert len(may_enroll) == (len(self.students_who_may_enroll) - len(self.users))
        email_adresses = [student.email for student in self.students_who_may_enroll]
        for student in may_enroll:
            assert list(student.keys()) == ['email']
            assert student['email'] in email_adresses

    def test_get_student_exam_attempt_features(self):
        query_features = [
            'email',
            'exam_name',
            'allowed_time_limit_mins',
            'is_sample_attempt',
            'started_at',
            'completed_at',
            'status',
            'Suspicious Count',
            'Suspicious Comments',
            'Rules Violation Count',
            'Rules Violation Comments',
            'track'
        ]

        proctored_exam_id = create_exam(self.course_key, 'Test Content', 'Test Exam', 1)
        ProctoredExamStudentAttempt.create_exam_attempt(
            proctored_exam_id, self.users[0].id,
            'Test Code 1', True, False, 'ad13'
        )
        ProctoredExamStudentAttempt.create_exam_attempt(
            proctored_exam_id, self.users[1].id,
            'Test Code 2', True, False, 'ad13'
        )
        ProctoredExamStudentAttempt.create_exam_attempt(
            proctored_exam_id, self.users[2].id,
            'Test Code 3', True, False, 'asd'
        )

        proctored_exam_attempts = get_proctored_exam_results(self.course_key, query_features)
        assert len(proctored_exam_attempts) == 3
        for proctored_exam_attempt in proctored_exam_attempts:
            assert set(proctored_exam_attempt.keys()) == set(query_features)

    def test_get_student_features_with_custom_attributes(self):
        """Test that get_student_features_with_custom works with custom attributes."""

        # Test without custom attributes - should return standard features
        features = get_student_features_with_custom(self.course_key)
        assert features == STUDENT_FEATURES

        # Test with custom attributes
        SiteConfigurationFactory.create(
            site_values={
                'course_org_filter': ['robot'],
                'additional_student_profile_attributes': ['employee_id', 'department'],
            }
        )

        features = get_student_features_with_custom(self.course_key)
        expected = STUDENT_FEATURES + ('employee_id', 'department')
        assert features == expected

    def test_enrolled_students_multiple_custom_fields(self):
        """Test that multiple custom fields work correctly together."""
        SiteConfigurationFactory.create(
            site_values={
                'course_org_filter': ['robot'],
                'additional_student_profile_attributes': [
                    'student_id',
                    'employment_status',
                    'graduation_year'
                ],
            }
        )

        def get_student_id(self):
            """Generate dummy student ID"""
            try:
                return f"ID{self.id:05d}"
            except AttributeError:
                return None

        def get_employment_status(self):
            """Generate dummy employment status"""
            try:
                statuses = ['Student', 'Employed', 'Unemployed', 'Self-employed', 'Retired']
                return statuses[self.id % len(statuses)]
            except AttributeError:
                return None

        def get_graduation_year(self):
            """Generate dummy graduation year"""
            try:
                return str(2020 + (self.id % 10))
            except AttributeError:
                return None

        User.student_id = property(get_student_id)
        User.employment_status = property(get_employment_status)
        User.graduation_year = property(get_graduation_year)

        query_features = ('username', 'student_id', 'employment_status', 'graduation_year')
        with self.assertNumQueries(3):
            userreports = enrolled_students_features(self.course_key, query_features)

        assert len(userreports) == len(self.users)

        for userreport in userreports:
            assert set(userreport.keys()) == set(query_features)
            # Verify all custom fields have values
            assert userreport['student_id'] is not None
            assert userreport['student_id'].startswith('ID')
            assert userreport['employment_status'] in ['Student', 'Employed', 'Unemployed', 'Self-employed', 'Retired']
            assert userreport['graduation_year'] in [str(year) for year in range(2020, 2030)]

        del User.student_id
        del User.employment_status
        del User.graduation_year

    def get_badge_count(self):
        """Generate dummy badge count"""
        try:
            return str(self.id % 10)  # 0-9 badges
        except AttributeError:
            return "0"

    def test_custom_attributes_without_org_filter(self):
        """Test that custom attributes require course_org_filter to work properly."""
        # Create configuration without course_org_filter
        SiteConfigurationFactory.create(
            site_values={
                'additional_student_profile_attributes': ['badge_count'],
            }
        )

        User.badge_count = property(self.get_badge_count)

        # Without org filter, custom attributes should NOT be added
        features = get_student_features_with_custom(self.course_key)
        # Should return only standard features (no badge_count)
        assert features == STUDENT_FEATURES

        # Clean up
        del User.badge_count

    def test_custom_attributes_with_non_matching_org_filter(self):
        """Test that custom attributes don't work with non-matching course_org_filter."""
        # Create configuration with course_org_filter that DOESN'T match our test course org
        SiteConfigurationFactory.create(
            site_values={
                'course_org_filter': ['different_org'],
                'additional_student_profile_attributes': ['badge_count'],
            }
        )

        # With non-matching org filter, custom attributes should NOT be added
        features = get_student_features_with_custom(self.course_key)
        # Should return only standard features (no badge_count)
        assert features == STUDENT_FEATURES

    def test_get_available_features_includes_additional_attributes(self):
        """
        get_available_features should include additional_student_profile_attributes
        for the org, on top of the standard features.
        """
        SiteConfigurationFactory.create(
            site_values={
                'course_org_filter': ['robot'],
                'additional_student_profile_attributes': ['employee_id', 'department'],
            }
        )

        features = get_available_features(self.course_key)

        # Decompose what we expect:
        # student part = STUDENT_FEATURES + additional
        expected_student = STUDENT_FEATURES + ('employee_id', 'department')
        expected_all = (
            expected_student
            + PROFILE_FEATURES
            + PROGRAM_ENROLLMENT_FEATURES
            + ENROLLMENT_FEATURES
        )

        assert features == expected_all
