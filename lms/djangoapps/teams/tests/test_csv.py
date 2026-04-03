""" Tests for the functionality in csv """
from csv import DictReader, DictWriter
from io import BytesIO, StringIO, TextIOWrapper

from common.djangoapps.student.tests.factories import CourseEnrollmentFactory, UserFactory
from common.djangoapps.util.testing import EventTestMixin
from lms.djangoapps.program_enrollments.tests.factories import ProgramCourseEnrollmentFactory, ProgramEnrollmentFactory
from lms.djangoapps.teams import csv
from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership
from lms.djangoapps.teams.tests.factories import CourseTeamFactory
from openedx.core.lib.teams_config import TeamsConfig
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import CourseFactory  # lint-amnesty, pylint: disable=wrong-import-order


def csv_import(course, csv_dict_rows):
    """
    Create a csv file with the given contents and pass it to the csv import manager to test the full
    csv import flow

    Parameters:
        - csv_dict_rows: list of dicts, representing a row of the csv file
    """
    # initialize import manager
    import_manager = csv.TeamMembershipImportManager(course)
    import_manager.teamset_ids = {ts.teamset_id for ts in course.teamsets}

    with BytesIO() as mock_csv_file:
        with TextIOWrapper(mock_csv_file, write_through=True) as text_wrapper:
            # pylint: disable=protected-access
            header_fields = csv._get_team_membership_csv_headers(course)
            csv_writer = DictWriter(text_wrapper, fieldnames=header_fields)
            csv_writer.writeheader()
            csv_writer.writerows(csv_dict_rows)
            mock_csv_file.seek(0)
            import_manager.set_team_membership_from_csv(mock_csv_file)


def csv_export(course):
    """
    Call csv.load_team_membership_csv for the given course, and return the result.
    The result is returned in the form of a dictionary keyed by the 'username' identifiers for each row,
    mapping to the full parsed dictionary for that row of the csv.

    Returns: DictReader for the returned csv file
    """
    with StringIO() as read_buf:
        csv.load_team_membership_csv(course, read_buf)
        read_buf.seek(0)
        return DictReader(read_buf.readlines())


def _user_keyed_dict(reader):
    """ create a dict of the rows of the csv, keyed by the "username" value """
    return {row['username']: row for row in reader}


def _csv_dict_row(username, external_user_id, mode, **kwargs):
    """
    Convenience method to create dicts to pass to csv_import
    """
    csv_dict_row = dict(kwargs)
    csv_dict_row['username'] = username
    csv_dict_row['external_user_id'] = external_user_id
    csv_dict_row['mode'] = mode
    return csv_dict_row


class TeamMembershipCsvTests(SharedModuleStoreTestCase):
    """ Tests for functionality related to the team membership csv report """
    @classmethod
    def setUpClass(cls):
        # pylint: disable=no-member
        super().setUpClass()
        teams_config = TeamsConfig({
            'team_sets': [
                {
                    'id': f'teamset_{i}',
                    'name': f'teamset_{i}_name',
                    'description': f'teamset_{i}_desc',
                }
                for i in [1, 2, 3, 4]
            ]
        })
        cls.course = CourseFactory(teams_configuration=teams_config)
        cls.course_no_teamsets = CourseFactory()

        team1_1 = CourseTeamFactory(course_id=cls.course.id, name='team_1_1', topic_id='teamset_1')
        CourseTeamFactory(course_id=cls.course.id, name='team_1_2', topic_id='teamset_1')
        team2_1 = CourseTeamFactory(course_id=cls.course.id, name='team_2_1', topic_id='teamset_2')
        team2_2 = CourseTeamFactory(course_id=cls.course.id, name='team_2_2', topic_id='teamset_2')
        team3_1 = CourseTeamFactory(course_id=cls.course.id, name='team_3_1', topic_id='teamset_3')
        # protected team
        team3_2 = CourseTeamFactory(
            course_id=cls.course.id,
            name='team_3_2',
            topic_id='teamset_3',
            organization_protected=True
        )
        #  No teams in teamset 4

        user1 = UserFactory.create(username='user1')
        user2 = UserFactory.create(username='user2')
        user3 = UserFactory.create(username='user3')
        user4 = UserFactory.create(username='user4')
        user5 = UserFactory.create(username='user5')

        CourseEnrollmentFactory.create(user=user1, course_id=cls.course.id, mode='audit')
        CourseEnrollmentFactory.create(user=user2, course_id=cls.course.id, mode='verified')
        CourseEnrollmentFactory.create(user=user3, course_id=cls.course.id, mode='honors')
        CourseEnrollmentFactory.create(user=user4, course_id=cls.course.id, mode='masters')
        CourseEnrollmentFactory.create(user=user5, course_id=cls.course.id, mode='masters')

        team1_1.add_user(user1)
        team2_2.add_user(user1)
        team3_1.add_user(user1)

        team1_1.add_user(user2)
        team2_2.add_user(user2)
        team3_1.add_user(user2)

        team2_1.add_user(user3)
        team3_1.add_user(user3)

        team3_2.add_user(user4)

    def test_get_headers(self):
        # pylint: disable=protected-access
        headers = csv._get_team_membership_csv_headers(self.course)
        assert headers == ['username', 'external_user_id', 'mode', 'teamset_1', 'teamset_2', 'teamset_3', 'teamset_4']

    def test_get_headers_no_teamsets(self):
        # pylint: disable=protected-access
        headers = csv._get_team_membership_csv_headers(self.course_no_teamsets)
        assert headers == ['username', 'external_user_id', 'mode']

    def test_lookup_team_membership_data(self):
        with self.assertNumQueries(3):
            # pylint: disable=protected-access
            data = csv._lookup_team_membership_data(self.course)
        assert len(data) == 5
        self.assert_teamset_membership(data[0], 'user1', None, 'audit', 'team_1_1', 'team_2_2', 'team_3_1')
        self.assert_teamset_membership(data[1], 'user2', None, 'verified', 'team_1_1', 'team_2_2', 'team_3_1')
        self.assert_teamset_membership(data[2], 'user3', None, 'honors', None, 'team_2_1', 'team_3_1')
        self.assert_teamset_membership(data[3], 'user4', None, 'masters', None, None, 'team_3_2')
        self.assert_teamset_membership(data[4], 'user5', None, 'masters', None, None, None)

    def assert_teamset_membership(
        self,
        user_row,
        expected_username,
        expected_external_user_id,
        expected_mode,
        expected_teamset_1_team,
        expected_teamset_2_team,
        expected_teamset_3_team
    ):
        """
        Assert that user_row has the expected
            -username
            -mode
            -team name for teamset_(123)
        """
        assert user_row['username'] == expected_username
        assert user_row['external_user_id'] == expected_external_user_id
        assert user_row['mode'] == expected_mode
        assert user_row.get('teamset_1') == expected_teamset_1_team
        assert user_row.get('teamset_2') == expected_teamset_2_team
        assert user_row.get('teamset_3') == expected_teamset_3_team

    def test_load_team_membership_csv(self):
        expected_csv_headers = [
            'username',
            'external_user_id',
            'mode',
            'teamset_1',
            'teamset_2',
            'teamset_3',
            'teamset_4'
        ]
        expected_data = {}
        expected_data['user1'] = _csv_dict_row(
            'user1',
            '',
            'audit',
            teamset_1='team_1_1',
            teamset_2='team_2_2',
            teamset_3='team_3_1',
        )
        expected_data['user2'] = _csv_dict_row(
            'user2',
            '',
            'verified',
            teamset_1='team_1_1',
            teamset_2='team_2_2',
            teamset_3='team_3_1',
        )
        expected_data['user3'] = _csv_dict_row('user3', '', 'honors', teamset_2='team_2_1', teamset_3='team_3_1')
        expected_data['user4'] = _csv_dict_row('user4', '', 'masters', teamset_3='team_3_2')
        expected_data['user5'] = _csv_dict_row('user5', '', 'masters')
        self._add_blanks_to_expected_data(expected_data, expected_csv_headers)

        reader = csv_export(self.course)
        assert expected_csv_headers == reader.fieldnames
        actual_data = _user_keyed_dict(reader)
        self.assertDictEqual(expected_data, actual_data)

    def _add_blanks_to_expected_data(self, expected_data, headers):
        """ Helper method to fill in the "blanks" in test data """
        for user in expected_data:
            user_row = expected_data[user]
            for header in headers:
                if header not in user_row:
                    user_row[header] = ''


class TeamMembershipEventTestMixin(EventTestMixin):
    """ Mixin to provide functionality for testing signals emitted by csv code """

    def setUp(self):
        # pylint: disable=arguments-differ
        super().setUp('lms.djangoapps.teams.utils.tracker')

    def assert_learner_added_emitted(self, team_id, user_id):
        self.assert_event_emitted(
            'edx.team.learner_added',
            team_id=team_id,
            user_id=user_id,
            add_method='team_csv_import'
        )

    def assert_learner_removed_emitted(self, team_id, user_id):
        self.assert_event_emitted(
            'edx.team.learner_removed',
            team_id=team_id,
            user_id=user_id,
            remove_method='team_csv_import'
        )


# pylint: disable=no-member
class TeamMembershipImportManagerTests(TeamMembershipEventTestMixin, SharedModuleStoreTestCase):
    """ Tests for TeamMembershipImportManager """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        teams_config = TeamsConfig({
            'team_sets': [{
                'id': 'teamset_1',
                'name': 'teamset_name',
                'description': 'teamset_desc',
                'max_team_size': 3,
            }]
        })
        cls.course = CourseFactory(teams_configuration=teams_config)
        cls.second_course = CourseFactory(teams_configuration=teams_config)
        cls.import_manager = None

    def setUp(self):
        """ Initialize import manager """
        super().setUp()
        self.import_manager = csv.TeamMembershipImportManager(self.course)
        self.import_manager.teamset_ids = {ts.teamset_id for ts in self.course.teamsets}

    def test_load_course_teams(self):
        """
        Loading course teams should get the users by team with only 2 queries
        1 for teams, 1 for user count
        """
        for _ in range(4):
            CourseTeamFactory.create(course_id=self.course.id)

        with self.assertNumQueries(2):
            self.import_manager.load_course_teams()

    def test_add_user_to_new_protected_team(self):
        """Adding a masters learner to a new team should create a team with organization protected status"""
        masters_learner = UserFactory.create(username='masters_learner')
        CourseEnrollmentFactory.create(user=masters_learner, course_id=self.course.id, mode='masters')
        row = {
            'mode': 'masters',
            'teamset_1': 'new_protected_team',
            'user_model': masters_learner
        }

        self.import_manager.add_user_to_team(row)
        team = CourseTeam.objects.get(team_id__startswith='new_protected_team')
        assert team.organization_protected
        self.assert_learner_added_emitted(team.team_id, masters_learner.id)

    def test_add_user_to_new_unprotected_team(self):
        """Adding a non-masters learner to a new team should create a team with no organization protected status"""
        audit_learner = UserFactory.create(username='audit_learner')
        CourseEnrollmentFactory.create(user=audit_learner, course_id=self.course.id, mode='audit')
        row = {
            'mode': 'audit',
            'teamset_1': 'new_unprotected_team',
            'user_model': audit_learner
        }

        self.import_manager.add_user_to_team(row)
        team = CourseTeam.objects.get(team_id__startswith='new_unprotected_team')
        assert not team.organization_protected
        self.assert_learner_added_emitted(team.team_id, audit_learner.id)

    def test_team_removals_are_scoped_correctly(self):
        """ Team memberships should not search across topics in different courses """
        # Given a learner enrolled in similarly named teamsets across 2 courses
        audit_learner = UserFactory.create(username='audit_learner')

        CourseEnrollmentFactory.create(user=audit_learner, course_id=self.course.id, mode='audit')
        course_1_team = CourseTeamFactory(course_id=self.course.id, name='cross_course_test', topic_id='teamset_1')
        course_1_team.add_user(audit_learner)

        CourseEnrollmentFactory.create(user=audit_learner, course_id=self.second_course.id, mode='audit')
        course_2_team = CourseTeamFactory(
            course_id=self.second_course.id,
            name='cross_course_test',
            topic_id='teamset_1'
        )
        course_2_team.add_user(audit_learner)

        assert CourseTeamMembership.is_user_on_team(audit_learner, course_1_team)

        # When I try to remove them from the team
        row = {
            'mode': 'audit',
            'teamset_1': None,
            'user_model': audit_learner
        }
        self.import_manager.remove_user_from_team_for_reassignment(row)

        # They are successfully removed from the team
        assert not CourseTeamMembership.is_user_on_team(audit_learner, course_1_team)
        self.assert_learner_removed_emitted(course_1_team.team_id, audit_learner.id)

    def test_user_moved_to_another_team(self):
        """ We should be able to move a user from one team to another """
        # Create a learner, enroll in course
        audit_learner = UserFactory.create(username='audit_learner')
        CourseEnrollmentFactory.create(user=audit_learner, course_id=self.course.id, mode='audit')
        # Make two teams in the same teamset, enroll the user in one
        team_1 = CourseTeamFactory(course_id=self.course.id, name='test_team_1', topic_id='teamset_1')
        team_2 = CourseTeamFactory(course_id=self.course.id, name='test_team_2', topic_id='teamset_1')
        team_1.add_user(audit_learner)

        csv_row = _csv_dict_row(audit_learner, '', 'audit', teamset_1=team_2.name)
        csv_import(self.course, [csv_row])

        assert not CourseTeamMembership.is_user_on_team(audit_learner, team_1)
        assert CourseTeamMembership.is_user_on_team(audit_learner, team_2)

        self.assert_learner_removed_emitted(team_1.team_id, audit_learner.id)
        self.assert_learner_added_emitted(team_2.team_id, audit_learner.id)

    def test_exceed_max_size(self):
        # Given a bunch of students enrolled in a course
        users = []
        for i in range(5):
            user = UserFactory.create(username=f'max_size_{i}')
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode='audit')
            users.append(user)

        # When a team is already near capaciy
        team = CourseTeam.objects.create(
            name='team_1',
            course_id=self.course.id,
            topic_id='teamset_1',
            description='Team 1!',
        )
        for i in range(2):
            user = users[i]
            team.add_user(user)

        # ... and I try to add members in excess of capacity
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            ['max_size_0', 'audit', ''],
            ['max_size_2', 'audit', 'team_1'],
            ['max_size_3', 'audit', 'team_1'],
            ['max_size_4', 'audit', 'team_1'],
        ])

        result = self.import_manager.set_team_memberships(csv_data)

        # Then the import fails with no events emitted and a "team is full" error
        assert not result
        self.assert_no_events_were_emitted()
        assert self.import_manager.validation_errors[0] == 'New membership for team team_1 would exceed max size of 3.'

        # Confirm that memberships were not altered
        for i in range(2):
            assert CourseTeamMembership.is_user_on_team(user, team)

    def test_remove_from_team(self):
        # Given a user already in a course and on a team
        user = UserFactory.create(username='learner_1')
        mode = 'audit'
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode=mode)
        team = CourseTeamFactory(course_id=self.course.id, name='team_1', topic_id='teamset_1')
        team.add_user(user)
        assert CourseTeamMembership.is_user_on_team(user, team)

        # When I try to remove them from the team
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            [user.username, mode, ''],
        ])
        result = self.import_manager.set_team_memberships(csv_data)  # lint-amnesty, pylint: disable=unused-variable

        # Then they are removed from the team and the correct events are issued
        assert not CourseTeamMembership.is_user_on_team(user, team)
        self.assert_learner_removed_emitted(team.team_id, user.id)

    def test_switch_memberships(self):
        # Given a bunch of students enrolled in a course
        users = []
        for i in range(5):
            user = UserFactory.create(username=f'learner_{i}')
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode='audit')
            users.append(user)

        # When a team is already at/near capaciy
        for i in range(3):
            user = users[i]
            row = {'user_model': user, 'teamset_1': 'team_1', 'mode': 'audit'}
            self.import_manager.add_user_to_team(row)

        # ... and I try to switch membership (add/remove)
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            ['learner_4', 'audit', 'team_1'],
            ['learner_0', 'audit', 'team_2'],
        ])

        result = self.import_manager.set_team_memberships(csv_data)

        # Then membership size is calculated correctly, import finishes w/out error
        assert result

        # ... and the users are assigned to the correct teams
        team_1 = CourseTeam.objects.get(course_id=self.course.id, topic_id='teamset_1', name='team_1')
        assert CourseTeamMembership.is_user_on_team(users[4], team_1)
        self.assert_learner_added_emitted(team_1.team_id, users[4].id)

        team_2 = CourseTeam.objects.get(course_id=self.course.id, topic_id='teamset_1', name='team_2')
        assert CourseTeamMembership.is_user_on_team(users[0], team_2)
        self.assert_learner_added_emitted(team_2.team_id, users[0].id)

    def test_create_new_team_from_import(self):
        # Given a user in a course
        user = UserFactory.create(username='learner_1')
        mode = 'audit'
        CourseEnrollmentFactory.create(user=user, course_id=self.course.id, mode=mode)

        # When I add them to a team that does not exist
        assert CourseTeam.objects.all().count() == 0
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            [user.username, mode, 'new_exciting_team'],
        ])
        result = self.import_manager.set_team_memberships(csv_data)  # lint-amnesty, pylint: disable=unused-variable

        # Then a new team is created
        assert CourseTeam.objects.all().count() == 1

        # ... and the user is assigned to the team
        new_team = CourseTeam.objects.get(topic_id='teamset_1', name='new_exciting_team')
        assert CourseTeamMembership.is_user_on_team(user, new_team)
        self.assert_learner_added_emitted(new_team.team_id, user.id)

    # Team protection status tests
    def test_create_new_mixed_enrollment_team_fails(self):
        # Given users of different tracks
        verified_learner = self._create_and_enroll_test_user('verified_learner', mode='verified')
        masters_learner = self._create_and_enroll_test_user('masters_learner', mode='masters')

        # When I attempt to add them to the same team
        assert CourseTeam.objects.all().count() == 0
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            [verified_learner.username, 'verified', 'new_exciting_team'],
            [masters_learner.username, 'masters', 'new_exciting_team']
        ])
        result = self.import_manager.set_team_memberships(csv_data)

        # The import fails with "mixed users" error and no team was created
        assert not result
        self.assert_no_events_were_emitted()
        assert self.import_manager.validation_errors[0] ==\
               'Team new_exciting_team cannot have Master’s track users mixed with users in other tracks.'
        assert CourseTeam.objects.all().count() == 0

    def test_add_incompatible_mode_to_existing_unprotected_team_fails(self):
        # Given an existing unprotected team
        unprotected_team = CourseTeamFactory(course_id=self.course.id, name='unprotected_team', topic_id='teamset_1')
        verified_learner = self._create_and_enroll_test_user('verified_learner', mode='verified')
        unprotected_team.add_user(verified_learner)

        # When I attempt to add a student of an incompatible enrollment mode
        masters_learner = self._create_and_enroll_test_user('masters_learner', mode='masters')
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            [masters_learner.username, 'masters', 'unprotected_team']
        ])
        result = self.import_manager.set_team_memberships(csv_data)

        # The import fails with "mixed users" error and learner not added to team
        assert not result
        self.assert_no_events_were_emitted()
        assert self.import_manager.validation_errors[0] ==\
               'Team unprotected_team cannot have Master’s track users mixed with users in other tracks.'
        assert not CourseTeamMembership.is_user_on_team(masters_learner, unprotected_team)

    def test_add_incompatible_mode_to_existing_protected_team_fails(self):
        # Given an existing protected team
        protected_team = CourseTeamFactory(
            course_id=self.course.id,
            name='protected_team',
            topic_id='teamset_1',
            organization_protected=True,
        )
        masters_learner = self._create_and_enroll_test_user('masters_learner', mode='masters')
        protected_team.add_user(masters_learner)

        # When I attempt to add a student of an incompatible enrollment mode
        verified_learner = self._create_and_enroll_test_user('verified_learner', mode='verified')
        csv_data = self._csv_reader_from_array([
            ['username', 'mode', 'teamset_1'],
            [verified_learner.username, 'verified', 'protected_team']
        ])
        result = self.import_manager.set_team_memberships(csv_data)

        # The import fails with "mixed users" error and learner not added to team
        assert not result
        self.assert_no_events_were_emitted()
        assert self.import_manager.validation_errors[0] ==\
               'Team protected_team cannot have Master’s track users mixed with users in other tracks.'
        assert not CourseTeamMembership.is_user_on_team(verified_learner, protected_team)

    def _create_and_enroll_test_user(self, username, course_id=None, mode="audit"):
        """
        Create user and add to test course with mode, default is test course in audit mode.
        Returns user.
        """
        user = UserFactory.create(username=username)
        if not course_id:
            course_id = self.course.id
        CourseEnrollmentFactory.create(user=user, course_id=course_id, mode=mode)

        return user

    def _csv_reader_from_array(self, rows):
        """
        Given a 2D array, treat each element as a cell of a CSV file and construct a reader

        Example:
            [['header1', 'header2'], ['r1:c1', 'r1:c2'], ['r2:c2', 'r3:c3'] ... ]
        """
        return DictReader(','.join(row) for row in rows)


class ExternalKeyCsvTests(TeamMembershipEventTestMixin, SharedModuleStoreTestCase):
    """ Tests for functionality related to external_user_keys"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.teamset_id = 'teamset_id'
        teams_config = TeamsConfig({
            'team_sets': [
                {
                    'id': cls.teamset_id,
                    'name': 'teamset_name',
                    'description': 'teamset_desc',
                }
            ]
        })
        cls.course = CourseFactory(teams_configuration=teams_config)
        # pylint: disable=protected-access
        cls.header_fields = csv._get_team_membership_csv_headers(cls.course)

        cls.team = CourseTeamFactory(course_id=cls.course.id, name='team_name', topic_id=cls.teamset_id)

        cls.user_no_program = UserFactory.create()
        cls.user_in_program = UserFactory.create()
        cls.user_in_program_no_external_id = UserFactory.create()
        cls.user_in_program_not_enrolled_through_program = UserFactory.create()

        # user_no_program is only enrolled in the course
        cls.add_user_to_course_program_team(cls.user_no_program, enroll_in_program=False)

        # user_in_program is enrolled in the course and the program, with an external_id
        cls.external_user_key = 'externalProgramUserId-123'
        cls.add_user_to_course_program_team(cls.user_in_program, external_user_key=cls.external_user_key)

        # user_in_program is enrolled in the course and the program, with no external_id
        cls.add_user_to_course_program_team(cls.user_in_program_no_external_id)

        # user_in_program_not_enrolled_through_program is enrolled in a program and the course, but they not connected
        cls.add_user_to_course_program_team(
            cls.user_in_program_not_enrolled_through_program, connect_enrollments=False
        )

    @classmethod
    def add_user_to_course_program_team(
        cls, user, add_to_team=True, enroll_in_program=True, connect_enrollments=True, external_user_key=None
    ):
        """
        Set up a test user by enrolling them in self.course, and then optionaly:
            - enroll them in a program
            - link their program and course enrollments
            - give their program enrollment an external_user_key
        """
        course_enrollment = CourseEnrollmentFactory.create(user=user, course_id=cls.course.id)
        if add_to_team:
            cls.team.add_user(user)
        if enroll_in_program:
            program_enrollment = ProgramEnrollmentFactory.create(user=user, external_user_key=external_user_key)
            if connect_enrollments:
                ProgramCourseEnrollmentFactory.create(
                    program_enrollment=program_enrollment, course_enrollment=course_enrollment
                )

    def assert_user_on_team(self, user):
        assert CourseTeamMembership.is_user_on_team(user, self.team)

    def assert_user_not_on_team(self, user):
        assert not CourseTeamMembership.is_user_on_team(user, self.team)

    def test_add_user_to_team_with_external_key(self):
        # Make a new user with an external_user_key who is enrolled in the course and program, with an external_key,
        #  but is not on the team
        new_user = UserFactory.create()
        new_ext_key = "another-external-user-id-FKQP12345"
        self.add_user_to_course_program_team(new_user, add_to_team=False, external_user_key=new_ext_key)
        self.assert_user_not_on_team(new_user)

        csv_import_row = _csv_dict_row(new_user.username, new_ext_key, 'audit', teamset_id=self.team.name)

        csv_import(self.course, [csv_import_row])
        self.assert_user_on_team(new_user)
        self.assert_learner_added_emitted(self.team.team_id, new_user.id)

    def test_lookup_team_membership_data(self):
        with self.assertNumQueries(3):
            # pylint: disable=protected-access
            data = csv._lookup_team_membership_data(self.course)
        self._assert_test_users_on_team(_user_keyed_dict(data), None)

    def test_get_csv(self):
        reader = csv_export(self.course)
        self._assert_test_users_on_team(_user_keyed_dict(reader), '')

    def _assert_test_users_on_team(self, data, no_external_key_value):
        """
        Assert that the four test users should be listed as members of the team,
        and user_in_program should be identified by their external_user_key.

        no_external_key_value is used because _lookup_team_membership_data returns `None`
        to mean there is no external key, but the CsvWriter library writes `None`s as an empty string
        """
        assert len(data) == 4
        expected_data = {
            username: _csv_dict_row(username, external_id, 'audit', teamset_id=self.team.name)
            for username, external_id in [
                (self.user_no_program.username, no_external_key_value),
                (self.user_in_program_no_external_id.username, no_external_key_value),
                (self.user_in_program_not_enrolled_through_program.username, no_external_key_value),
                (self.user_in_program.username, self.external_user_key)
            ]
        }
        self.assertDictEqual(expected_data, data)
