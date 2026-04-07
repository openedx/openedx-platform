"""
CSV processing and generation utilities for Teams LMS app.
"""

import csv
from collections import Counter

from django.contrib.auth.models import User  # lint-amnesty, pylint: disable=imported-auth-user
from django.db.models import Prefetch

from common.djangoapps.student.models import CourseEnrollment
from lms.djangoapps.program_enrollments.models import ProgramCourseEnrollment
from lms.djangoapps.teams.api import (
    ORGANIZATION_PROTECTED_MODES,
    OrganizationProtectionStatus,
    user_organization_protection_status,
    user_protection_status_matches_team,
)
from lms.djangoapps.teams.models import CourseTeam, CourseTeamMembership

from .utils import emit_team_event


def load_team_membership_csv(course, response):
    """
    Load a CSV detailing course membership.

    Arguments:
        course (CourseBlock): Course block for which CSV
            download has been requested.
        response (HttpResponse): Django response object to which
            the CSV content will be written.
    """
    headers = _get_team_membership_csv_headers(course)
    writer = csv.DictWriter(response, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    team_membership_data = _lookup_team_membership_data(course)
    writer.writerows(team_membership_data)


def _get_team_membership_csv_headers(course):
    """
    Get headers for team membership csv.
    ['username', 'external_user_id', 'mode', <teamset_id_1>, ..., ,<teamset_id_n>]
    """
    headers = ['username', 'external_user_id', 'mode']
    for teamset in sorted(course.teams_configuration.teamsets, key=lambda ts: ts.teamset_id):
        headers.append(teamset.teamset_id)
    return headers


def _lookup_team_membership_data(course):
    """
    Returns a list of dicts, in the following form:
    [
        {
            'username': <edX User username>
            'external_user_id': If the user is enrolled in this course as a part of a program,
                    this will be <external_user_id> if the user has one, otherwise, blank.
            'mode': <student enrollment mode for the given course>,
            <teamset id>: <team name> for each teamset in which the given user is on a team
        }
        for student in course
    ]
    """
    # Get course enrollments and team memberships for the given course
    course_enrollments = _fetch_course_enrollments_with_related_models(course.id)
    course_team_memberships = CourseTeamMembership.objects.filter(
        team__course_id=course.id
    ).select_related('team', 'user').all()
    teamset_memberships_by_user = _group_teamset_memberships_by_user(course_team_memberships)

    team_membership_data = []
    for course_enrollment in course_enrollments:
        # This dict contains all the user's team memberships keyed by teamset
        student_row = teamset_memberships_by_user.get(course_enrollment.user, {})
        student_row['username'] = course_enrollment.user.username
        student_row['external_user_id'] = _get_external_user_key(course_enrollment)
        student_row['mode'] = course_enrollment.mode
        team_membership_data.append(student_row)
    return team_membership_data


def _fetch_course_enrollments_with_related_models(course_id):
    """
    Look up active course enrollments for this course. Fetch the user.
    Fetch the ProgramCourseEnrollment and ProgramEnrollment if any of the CourseEnrollments are associated with
        a program enrollment (so we have access to an external_user_id if it exists).
    Order by the username of the enrolled user.

    Returns a QuerySet
    """
    return CourseEnrollment.objects.filter(
        course_id=course_id,
        is_active=True
    ).prefetch_related(
        Prefetch(
            'programcourseenrollment_set',
            queryset=ProgramCourseEnrollment.objects.select_related('program_enrollment')
        )
    ).select_related(
        'user'
    ).order_by('user__username')


def _get_external_user_key(course_enrollment):
    """
    If a user is enrolled in the course as a part of a program and the program identifies them
        with an external_user_key, return that value for the 'external_user_key' column.
    Otherwise, return None.
    """
    program_course_enrollments = course_enrollment.programcourseenrollment_set
    if program_course_enrollments.exists():
        # A user should only have one or zero ProgramCourseEnrollments associated with a given CourseEnrollment
        program_course_enrollment = program_course_enrollments.all()[0]
        external_user_key = program_course_enrollment.program_enrollment.external_user_key
        if external_user_key:
            return external_user_key
    return None


def _group_teamset_memberships_by_user(course_team_memberships):
    """
    Parameters:
        - course_team_memberships: a collection of CourseTeamMemberships.

    Returns:
        {
            <User>: {
                <teamset_id>: <team_name>
                for CourseTeamMembership in input corresponding to <User>
            }
            per user represented in input
        }
    """
    teamset_memberships_by_user = {}
    for team_membership in course_team_memberships:
        user = team_membership.user
        if user not in teamset_memberships_by_user:
            teamset_memberships_by_user[user] = {}
        topic_id = team_membership.team.topic_id
        team_name = team_membership.team.name
        teamset_memberships_by_user[user][topic_id] = team_name
    return teamset_memberships_by_user


class TeamMembershipImportManager:
    """
    A manager class that is responsible the import process of csv file including validation and creation of
    team_courseteam and teams_courseteammembership objects.
    """

    def __init__(self, course):
        self.validation_errors = []
        self.teamset_ids = []
        self.course = course
        self.max_errors = 0
        self.existing_course_team_memberships = {}
        self.existing_course_teams = {}
        self.user_count_by_team = Counter()
        self.user_enrollment_by_team = {}
        self.number_of_learners_assigned = 0
        self.user_to_actual_enrollment_mode = {}

    @property
    def import_succeeded(self):
        """
        Helper wrapper that tells us the status of the import
        """
        return not self.validation_errors

    def set_team_membership_from_csv(self, input_file):
        """
        Parse an input CSV file and pass to `set_team_memberships` for processing
        """
        csv_reader = csv.DictReader(line.decode('utf-8-sig').strip() for line in input_file.readlines())
        return self.set_team_memberships(csv_reader)

    def set_team_memberships(self, csv_reader):
        """
        Assigns team membership based on the data from an uploaded CSV file.
        Returns true if there were no issues.
        """
        # File-level validation
        if not self.validate_header(csv_reader):
            return False
        if not self.validate_teamsets(csv_reader):
            return False

        self.teamset_ids = self.get_teamset_ids_from_reader(csv_reader)
        row_dictionaries = []
        csv_usernames = set()

        # Get existing team membership data
        self.load_course_team_memberships()
        self.load_course_teams()

        # process student rows:
        for row in csv_reader:
            if not self.validate_teams_have_matching_teamsets(row):
                return False
            username = row['username']
            if not username:
                continue
            if not self.is_username_unique(username, csv_usernames):
                return False
            csv_usernames.add(username)
            user = self.get_user(username)
            if user is None:
                continue
            if not self.validate_user_enrollment_is_valid(user, row['mode']):
                row['user_model'] = None
                continue
            row['user_model'] = user
            if not self.validate_user_assignment_to_team_and_teamset(row):
                return False
            row_dictionaries.append(row)

        if not self.validate_team_sizes_not_exceeded():
            return False

        if not self.validation_errors:
            for row in row_dictionaries:
                self.remove_user_from_team_for_reassignment(row)
                self.add_user_to_team(row)
            self.number_of_learners_assigned = len(row_dictionaries)
            return True
        else:
            return False

    def load_course_team_memberships(self):
        """
        Caches existing team memberships by (user_id, teamset_id)
        """
        for membership in CourseTeamMembership.get_memberships(course_ids=[self.course.id]):
            user_id = membership.user_id
            teamset_id = membership.team.topic_id
            self.existing_course_team_memberships[(user_id, teamset_id)] = membership.team

    def load_course_teams(self):
        """
        Caches existing course teams by (team_name, topic_id)
        and existing membership counts by (topic_id, team_name)
        """
        for team in CourseTeam.objects.filter(course_id=self.course.id).prefetch_related('users'):
            self.existing_course_teams[(team.name, team.topic_id)] = team
            self.user_count_by_team[(team.topic_id, team.name)] = team.users.count()

    def validate_header(self, csv_reader):
        """
        Validates header row to ensure that it contains at a minimum columns called 'username', 'mode'.
        Teamset validation is handled separately
        """
        header = csv_reader.fieldnames
        if 'username' not in header:
            self.validation_errors.append("Header must contain column 'username'.")
            return False
        if 'mode' not in header:
            self.validation_errors.append("Header must contain column 'mode'.")
            return False
        return True

    def get_teamset_ids_from_reader(self, csv_reader):
        """
        The teamsets currently will be directly after 'mode'
        """
        mode_index = csv_reader.fieldnames.index('mode')
        return csv_reader.fieldnames[mode_index + 1:]

    def validate_teamsets(self, csv_reader):
        """
        Validates team set ids. Returns true if there are no errors.
        The following conditions result in errors:
        Teamset does not exist
        Teamset id is duplicated
        """
        teamset_ids = self.get_teamset_ids_from_reader(csv_reader)
        valid_teamset_ids = {ts.teamset_id for ts in self.course.teams_configuration.teamsets}

        dupe_set = set()
        for teamset_id in teamset_ids:
            if teamset_id in dupe_set:
                self.validation_errors.append("Teamset with id " + teamset_id + " is duplicated.")
                return False
            dupe_set.add(teamset_id)
            if teamset_id not in valid_teamset_ids:
                self.validation_errors.append("Teamset with id " + teamset_id + " does not exist.")
                return False
        return True

    def validate_user_enrollment_is_valid(self, user, supplied_enrollment):
        """
        Invalid states:
            user not enrolled in course
            enrollment mode from csv doesn't match actual user enrollment
        """
        actual_enrollment_mode, user_enrolled = CourseEnrollment.enrollment_mode_for_user(user, self.course.id)
        if not user_enrolled:
            self.validation_errors.append('User ' + user.username + ' is not enrolled in this course.')
            return False
        if actual_enrollment_mode != supplied_enrollment.strip():
            self.validation_errors.append('User ' + user.username + ' enrollment mismatch.')
            return False
        self.user_to_actual_enrollment_mode[user.id] = actual_enrollment_mode
        return True

    def is_username_unique(self, username, usernames_found_so_far):
        """
        Ensures that username exists only once in an input file
        """
        if username in usernames_found_so_far:
            error_message = f'Username {username} listed more than once in file.'
            if self.add_error_and_check_if_max_exceeded(error_message):
                return False
        return True

    def validate_teams_have_matching_teamsets(self, row):
        """
        It's possible for a user to create a row that has more team names in it
        than there are teamset ids provided in the header.
        In that case, `row` will have one or more null keys mapping to team names, for example:
        {'teamset-1': 'team-a', 'teamset-2': 'team-beta', None: 'team-37'}

        This method will add a validation error and return False if this is the case.
        """
        if None in row:
            error_message = "Team(s) {} don't have matching teamsets.".format(
                row[None]
            )
            if self.add_error_and_check_if_max_exceeded(error_message):
                return False
        return True

    def validate_user_assignment_to_team_and_teamset(self, row):
        """
        Validates a user entry relative to an existing team.
        row is a dictionary where key is column name and value is the row value
        [andrew],masters,team1,,team3
        [joe],masters,,team2,team3
        """
        user = row['user_model']
        for teamset_id in self.teamset_ids:
            # See if the user is already on a team in the teamset
            if (user.id, teamset_id) in self.existing_course_team_memberships:
                current_team_name = self.existing_course_team_memberships[(user.id, teamset_id)].name
            else:
                current_team_name = None

            team_name = row[teamset_id]

            # We don't need to do anything if the user isn't moving to a different team
            if current_team_name == team_name:
                continue

            # If the user is on a team currently, remove them in from the updated count
            if current_team_name is not None:
                self.user_count_by_team[(teamset_id, current_team_name)] -= 1

            # If we aren't moving them to a new team, we can go to the next team-set
            if not team_name:
                continue

            # Check that user enrollment mode is compatible for the target team
            if not self.validate_compatible_enrollment_modes(user, team_name, teamset_id):
                return False

            # Update proposed team counts, initializing the team count if it doesn't exist
            if (teamset_id, team_name) not in self.user_count_by_team:
                self.user_count_by_team[(teamset_id, team_name)] = 1
            else:
                self.user_count_by_team[(teamset_id, team_name)] += 1

        return True

    def validate_compatible_enrollment_modes(self, user, team_name, teamset_id):
        """
        Validates that only students enrolled in a masters track are on a single team. Disallows mixing of masters
        with other enrollment modes on a single team.
        Masters track students can't be added to existing non-protected teams
        """
        if(teamset_id, team_name) not in self.user_enrollment_by_team:
            self.user_enrollment_by_team[teamset_id, team_name] = set()
        self.user_enrollment_by_team[teamset_id, team_name].add(self.user_to_actual_enrollment_mode[user.id])
        if self.is_FERPA_bubble_breached(teamset_id, team_name) or \
                not self.is_enrollment_protection_for_existing_team_matches_user(user, team_name, teamset_id):
            error_message = \
                f'Team {team_name} cannot have Master’s track users mixed with users in other tracks.'
            self.add_error_and_check_if_max_exceeded(error_message)
            return False
        return True

    def is_enrollment_protection_for_existing_team_matches_user(self, user, team_name, teamset_id):
        """
        Applies only to existing teams.
        Returns True if no violations
        False if there is a mismatch
        """
        try:
            team = self.existing_course_teams[(team_name, teamset_id)]
            return user_protection_status_matches_team(user, team)
        except KeyError:
            return True

    def is_FERPA_bubble_breached(self, teamset_id, team_name):
        """
        Ensures that FERPA bubble is not breached.
        Checks that we are not trying to violate FERPA proctection by mixing masters
        track students with other enrollment tracks.
        """

        team_enrollment_modes = self.user_enrollment_by_team[teamset_id, team_name]
        protected_modes = set(ORGANIZATION_PROTECTED_MODES)

        if team_enrollment_modes.isdisjoint(protected_modes):
            return False
        elif team_enrollment_modes.issubset(protected_modes):
            return False
        else:
            return True

    def validate_team_sizes_not_exceeded(self):
        """
        Validates that the number of users we want to add to a team won't exceed maximum team size.
        """
        for teamset_id in self.teamset_ids:
            # Get max size for team-set
            if self.course.teams_configuration.teamsets_by_id[teamset_id].max_team_size is None:
                max_team_size = self.course.teams_configuration.default_max_team_size
            else:
                max_team_size = self.course.teams_configuration.teamsets_by_id[teamset_id].max_team_size

            # Get teams in team-set
            team_names = [
                teamset_to_team[1] for teamset_to_team in self.user_count_by_team
                if teamset_to_team[0] == teamset_id
            ]

            # Calculate proposed team size and return False if it exceeds capacity
            for team_name in team_names:
                key = (teamset_id, team_name)
                if self.user_count_by_team[key] > max_team_size:
                    self.add_error_and_check_if_max_exceeded(
                        'New membership for team {} would exceed max size of {}.'.format(
                            team_name, max_team_size
                        )
                    )
                    return False

        return True

    def remove_user_from_team_for_reassignment(self, row):
        """
        Remove a user from a team if:
        a. The user's current team is different from the team specified in csv for the same teamset (this user will
           then be assigned to a new team in 'add_user_to_team`.
        b. The team value in the CSV is blank - the user should be removed from the current team in teamset.
        Also, if there is no change in user's membership, the input row's team name will be nulled out so that no
        action will take place further in the processing chain.
        """
        user = row['user_model']
        for ts_id in self.teamset_ids:
            if row[ts_id] is None:
                # remove this student from the teamset
                try:
                    self._remove_user_from_teamset_and_emit_signal(user.id, ts_id, self.course.id)
                except CourseTeamMembership.DoesNotExist:
                    pass
            else:
                # reassignment happens only if proposed team membership is different from existing team membership
                if (user.id, ts_id) in self.existing_course_team_memberships:
                    current_user_teams_name = self.existing_course_team_memberships[user.id, ts_id].name
                    if current_user_teams_name != row[ts_id]:
                        try:
                            self._remove_user_from_teamset_and_emit_signal(user.id, ts_id, self.course.id)
                            del self.existing_course_team_memberships[user.id, ts_id]
                        except CourseTeamMembership.DoesNotExist:
                            pass
                    else:
                        # the user will remain in the same team. In order to avoid validation/attempting
                        # to readd the user, null out the team name
                        row[ts_id] = None

    def _remove_user_from_teamset_and_emit_signal(self, user_id, ts_id, course_id):
        """
        If a team membership exists for the specified user, in the specified course and teamset, delete it.
        This removes the user from the team.
        Then, emit an event.

        If the membership doesn't exist, don't emit the event and instead raise CourseTeamMembership.DoesNotExist
        """
        membership = CourseTeamMembership.objects.select_related('team').get(
            user_id=user_id,
            team__topic_id=ts_id,
            team__course_id=course_id
        )
        membership.delete()
        emit_team_event(
            'edx.team.learner_removed',
            course_id,
            {
                'team_id': membership.team.team_id,
                'user_id': membership.user_id,
                'remove_method': 'team_csv_import'
            }
        )

    def add_error_and_check_if_max_exceeded(self, error_message):
        """
        Adds an error to the error collection.
        :param error_message:
        :return: True if maximum error threshold is exceeded and processing must stop
                 False if maximum error threshold is NOT exceeded and processing can continue
        """
        self.validation_errors.append(error_message)
        return len(self.validation_errors) >= self.max_errors

    def add_user_to_team(self, user_row):
        """
        Creates a CourseTeamMembership entry - i.e: a relationship between a user and a team.
        user_row is a dictionary where key is column name and value is the row value.
        {'mode': ' masters','topic_0': '','topic_1': 'team 2','topic_2': None,'user_model': <user_obj>}
         andrew,masters,team1,,team3
        joe,masters,,team2,team3
        """
        user = user_row['user_model']
        for teamset_id in self.teamset_ids:
            team_name = user_row[teamset_id]
            if not team_name:
                continue
            if (team_name, teamset_id) not in self.existing_course_teams:
                protection_status = user_organization_protection_status(user, self.course.id)
                team = CourseTeam.create(
                    name=team_name,
                    course_id=self.course.id,
                    description='Import from csv',
                    topic_id=teamset_id,
                    organization_protected=protection_status == OrganizationProtectionStatus.protected
                )
                team.save()
                self.existing_course_teams[(team_name, teamset_id)] = team
            else:
                team = self.existing_course_teams[(team_name, teamset_id)]
            team.add_user(user)
            emit_team_event(
                'edx.team.learner_added',
                team.course_id,
                {
                    'team_id': team.team_id,
                    'user_id': user.id,
                    'add_method': 'team_csv_import'
                }
            )

    def get_user(self, username):
        """
        Gets the user object from user_name/email/locator
        user_name: the user_name/email/user locator
        """
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            self.validation_errors.append('User ' + username + ' does not exist.')
            return None
