"""Tests for the email opt-in list management command. """


import csv
import os.path
import shutil
import tempfile
from collections import defaultdict

import ddt
from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.core.management import call_command
from django.core.management.base import CommandError

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from openedx.core.djangoapps.user_api.management.commands import email_opt_in_list
from openedx.core.djangoapps.user_api.models import UserOrgTag
from openedx.core.djangoapps.user_api.preferences.api import update_email_opt_in
from openedx.core.djangolib.testing.utils import skip_unless_lms
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
@skip_unless_lms
class EmailOptInListTest(ModuleStoreTestCase):
    """Tests for the email opt-in list management command. """
    USER_USERNAME = "test_user"
    USER_FIRST_NAME = "Ṫëṡẗ"
    USER_LAST_NAME = "Űśéŕ"

    TEST_ORG = "téśt_őŕǵ"

    OUTPUT_FILE_NAME = "test_org_email_opt_in.csv"
    OUTPUT_FIELD_NAMES = [
        "user_id",
        "username",
        "email",
        "full_name",
        "course_id",
        "is_opted_in_for_email",
        "preference_set_datetime"
    ]

    DEFAULT_DATETIME_STR = "2014-12-01 00:00:00"

    def setUp(self):
        super().setUp()

        self.user = UserFactory.create(
            username=self.USER_USERNAME,
            first_name=self.USER_FIRST_NAME,
            last_name=self.USER_LAST_NAME
        )
        self.courses = []
        self.enrollments = defaultdict(list)

    def test_not_enrolled(self):
        self._create_courses_and_enrollments((self.TEST_ORG, False))
        output = self._run_command(self.TEST_ORG)

        # The user isn't enrolled in the course, so the output should be empty
        self._assert_output(output)

    def test_enrolled_no_pref(self):
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        output = self._run_command(self.TEST_ORG)

        # By default, if no preference is set by the user is enrolled, opt in
        self._assert_output(output, (self.user, self.courses[0].id, True))

    def test_enrolled_pref_opted_in(self):
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)
        output = self._run_command(self.TEST_ORG)
        self._assert_output(output, (self.user, self.courses[0].id, True))

    def test_enrolled_pref_opted_out(self):
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, False)
        output = self._run_command(self.TEST_ORG)
        self._assert_output(output, (self.user, self.courses[0].id, False))

    def test_opt_in_then_opt_out(self):
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)
        self._set_opt_in_pref(self.user, self.TEST_ORG, False)
        output = self._run_command(self.TEST_ORG)
        self._assert_output(output, (self.user, self.courses[0].id, False))

    def test_exclude_non_org_courses(self):
        # Enroll in a course that's not in the org
        self._create_courses_and_enrollments(
            (self.TEST_ORG, True),
            ("other_org", True)
        )

        # Opt out of the other course
        self._set_opt_in_pref(self.user, "other_org", False)

        # The first course is included in the results,
        # but the second course is excluded,
        # so the user should be opted in by default.
        output = self._run_command(self.TEST_ORG)
        self._assert_output(
            output,
            (self.user, self.courses[0].id, True),
            expect_pref_datetime=False
        )

    def test_enrolled_conflicting_prefs(self):
        # Enroll in two courses, both in the org
        self._create_courses_and_enrollments(
            (self.TEST_ORG, True),
            ("org_alias", True)
        )

        # Opt into the first course, then opt out of the second course
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)
        self._set_opt_in_pref(self.user, "org_alias", False)

        # The second preference change should take precedence
        # Note that *both* courses are included in the list,
        # but they should have the same value.
        output = self._run_command(self.TEST_ORG, other_names=["org_alias"])
        self._assert_output(
            output,
            (self.user, self.courses[0].id, False),
            (self.user, self.courses[1].id, False)
        )

        # Opt into the first course
        # Even though the other course still has a preference set to false,
        # the newest preference takes precedence
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)
        output = self._run_command(self.TEST_ORG, other_names=["org_alias"])
        self._assert_output(
            output,
            (self.user, self.courses[0].id, True),
            (self.user, self.courses[1].id, True)
        )

    @ddt.data(True, False)
    def test_unenrolled_from_all_courses(self, opt_in_pref):
        # Enroll in the course and set a preference
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, opt_in_pref)

        # Unenroll from the course
        CourseEnrollment.unenroll(self.user, self.courses[0].id, skip_refund=True)

        # Enrollments should still appear in the outpu
        output = self._run_command(self.TEST_ORG)
        self._assert_output(output, (self.user, self.courses[0].id, opt_in_pref))

    def test_unenrolled_from_some_courses(self):
        # Enroll in several courses in the org
        self._create_courses_and_enrollments(
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
            ("org_alias", True)
        )

        # Set a preference for the aliased course
        self._set_opt_in_pref(self.user, "org_alias", False)

        # Unenroll from the aliased course
        CourseEnrollment.unenroll(self.user, self.courses[3].id, skip_refund=True)

        # Expect that the preference still applies,
        # and all the enrollments should appear in the list
        output = self._run_command(self.TEST_ORG, other_names=["org_alias"])
        self._assert_output(
            output,
            (self.user, self.courses[0].id, False),
            (self.user, self.courses[1].id, False),
            (self.user, self.courses[2].id, False),
            (self.user, self.courses[3].id, False)
        )

    def test_no_courses_for_org_name(self):
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)

        # No course available for this particular org
        with self.assertRaisesRegex(CommandError, "^No courses found for orgs:"):
            self._run_command("other_org")

    def test_specify_subset_of_courses(self):
        # Create several courses in the same org
        self._create_courses_and_enrollments(
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
        )

        # Execute the command, but exclude the second course from the list
        only_courses = [self.courses[0].id, self.courses[1].id]
        self._run_command(self.TEST_ORG, only_courses=only_courses)

    def test_specify_chunk_size(self):
        # Create several courses in the same org
        self._create_courses_and_enrollments(
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
            (self.TEST_ORG, True),
        )

        # Execute the command, but exclude the second course from the list
        output = self._run_command(self.TEST_ORG, chunk_size=2)
        course_ids = []
        for row in output:
            course_id = row['course_id'].strip()
            course_ids.append(course_id)

        for course in self.courses:
            assert str(course.id) in course_ids

    # Choose numbers before and after the query interval boundary
    @ddt.data(2, 3, 4, 5, 6, 7, 8, 9)
    def test_many_users(self, num_users):
        # Create many users and enroll them in the test course
        course = CourseFactory.create(org=self.TEST_ORG)
        usernames = []
        for _ in range(num_users):
            user = UserFactory.create()
            usernames.append(user.username)
            CourseEnrollmentFactory.create(course_id=course.id, user=user)

        # Generate the report
        output = self._run_command(self.TEST_ORG, query_interval=4)

        # Expect that every enrollment shows up in the report
        output_emails = [row["email"] for row in output]
        for email in output_emails:
            assert email in output_emails

    def test_org_capitalization(self):
        # Lowercase some of the org names in the course IDs
        self._create_courses_and_enrollments(
            ("MyOrg", True),
            ("myorg", True)
        )

        # Set preferences for both courses
        self._set_opt_in_pref(self.user, "MyOrg", True)
        self._set_opt_in_pref(self.user, "myorg", False)

        # Execute the command, expecting both enrollments to show up
        # We're passing in the uppercase org, but we set the lowercase
        # version more recently, so we expect the lowercase org
        # preference to apply.
        output = self._run_command("MyOrg")
        self._assert_output(
            output,
            (self.user, self.courses[0].id, False),
            (self.user, self.courses[1].id, False)
        )

    @ddt.data(0, 1)
    def test_not_enough_args(self, num_args):
        args = ["dummy"] * num_args
        if num_args == 1:
            expected_msg_regex = (
                "^Error: the following arguments are required: ORG_ALIASES$"
            )
        elif num_args == 0:
            expected_msg_regex = (
                "^Error: the following arguments are required: OUTPUT_FILENAME, ORG_ALIASES$"
            )
        with self.assertRaisesRegex(CommandError, expected_msg_regex):
            call_command('email_opt_in_list', *args)

    def test_file_already_exists(self):
        temp_file = tempfile.NamedTemporaryFile(delete=True)  # lint-amnesty, pylint: disable=consider-using-with

        def _cleanup():
            temp_file.close()

        with self.assertRaisesRegex(CommandError, "^File already exists"):
            call_command('email_opt_in_list', temp_file.name, self.TEST_ORG)

    def test_no_user_profile(self):
        """
        Tests that command does not break if a user has no profile.
        """
        self._create_courses_and_enrollments((self.TEST_ORG, True))
        self._set_opt_in_pref(self.user, self.TEST_ORG, True)

        # Remove the user profile, and re-fetch user
        assert hasattr(self.user, 'profile')
        self.user.profile.delete()
        user = User.objects.get(id=self.user.id)

        # Test that user do not have profile
        assert not hasattr(user, 'profile')

        output = self._run_command(self.TEST_ORG)
        self._assert_output(output, (user, self.courses[0].id, True))

    def _create_courses_and_enrollments(self, *args):
        """Create courses and enrollments.

        Created courses and enrollments are stored in instance variables
        so tests can refer to them later.

        Arguments:
            *args: Tuples of (course_org, should_enroll), where
                course_org is the name of the org in the course key
                and should_enroll is a boolean indicating whether to enroll
                the user in the course.

        Returns:
            None

        """
        for course_number, (course_org, should_enroll) in enumerate(args):
            course = CourseFactory.create(org=course_org, number=str(course_number))
            if should_enroll:
                enrollment = CourseEnrollmentFactory.create(
                    is_active=True,
                    course_id=course.id,
                    user=self.user
                )
                self.enrollments[course.id].append(enrollment)
            self.courses.append(course)

    def _set_opt_in_pref(self, user, org, is_opted_in):
        """Set the email opt-in preference.

        Arguments:
            user (User): The user model.
            org (unicode): The org in the course key.
            is_opted_in (bool): Whether the user is opted in or out of emails.

        Returns:
            None

        """
        update_email_opt_in(user, org, is_opted_in)

    def _latest_pref_set_datetime(self, user):
        """Retrieve the latest opt-in preference for the user,
        across all orgs and preference keys.

        Arguments:
            user (User): The user whos preference was set.

        Returns:
            ISO-formatted datetime string or empty string

        """
        pref = UserOrgTag.objects.filter(user=user).order_by("-modified")
        return pref[0].modified.isoformat(' ') if len(pref) > 0 else self.DEFAULT_DATETIME_STR

    def _run_command(self, org, other_names=None, only_courses=None, query_interval=None, chunk_size=None):
        """Execute the management command to generate the email opt-in list.

        Arguments:
            org (unicode): The org to generate the report for.

        Keyword Arguments:
            other_names (list): List of other aliases for the org.
            only_courses (list): If provided, include only these course IDs in the report.
            query_interval (int): If provided, override the default query interval.
            chunk_size (int): If provided, overrides the default number of chunks for query iteration.

        Returns:
            list: The rows of the generated CSV report.  Each item is a dictionary.

        """
        # Create a temporary directory for the output
        # Delete it when we're finished
        temp_dir_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir_path)

        # Sanitize the arguments
        if other_names is None:
            other_names = []

        output_path = os.path.join(temp_dir_path, self.OUTPUT_FILE_NAME)
        org_list = [org] + other_names
        if only_courses is not None:
            only_courses = ",".join(str(course_id) for course_id in only_courses)

        command = email_opt_in_list.Command()

        # Override the query interval to speed up the tests
        if query_interval is not None:
            command.QUERY_INTERVAL = query_interval

        # Execute the command
        kwargs = {'courses': only_courses}
        if chunk_size:
            kwargs['email_optin_chunk_size'] = chunk_size
        call_command('email_opt_in_list', output_path, *org_list, **kwargs)

        # Retrieve the output from the file
        try:
            with open(output_path) as output_file:
                reader = csv.DictReader(output_file, fieldnames=self.OUTPUT_FIELD_NAMES)
                rows = list(reader)
        except OSError:
            self.fail(f"Could not find or open output file at '{output_path}'")

        # Return the output as a list of dictionaries
        return rows

    def _assert_output(self, output, *args, **kwargs):
        """Check the output of the report.

        Arguments:
            output (list): List of rows in the output CSV file.
            *args: Tuples of (user, course_id, opt_in_pref)

        Keyword Arguments:
            expect_pref_datetime (bool): If false, expect the default datetime.

        Returns:
            None

        Raises:
            AssertionError

        """
        assert len(output) == (len(args) + 1)

        # Check the header row
        assert {'user_id': 'user_id', 'username': 'username', 'email': 'email', 'full_name': 'full_name',
                'course_id': 'course_id', 'is_opted_in_for_email': 'is_opted_in_for_email',
                'preference_set_datetime': 'preference_set_datetime'} == output[0]

        # Check data rows
        for user, course_id, opt_in_pref in args:
            assert {'user_id': str(user.id), 'username': user.username, 'email': user.email,
                    'full_name': (user.profile.name if hasattr(user, 'profile') else ''),
                    'course_id': str(course_id),
                    'is_opted_in_for_email': str(opt_in_pref),
                    'preference_set_datetime':
                        (self._latest_pref_set_datetime(self.user) if kwargs.get('expect_pref_datetime', True) else
                         self.DEFAULT_DATETIME_STR)} in output[1:]
